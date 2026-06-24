/* Form S-6 grid viewer — grid, track, windfield coloring, popups. */

const state = {
  grid: null,
  inputs: null,
  powell: null,
  powellKd: null,         // Powell Kaplan–DeMaria decayed peaks (after UA run)
  powellField: null,      // Powell storm-relative isotach fields (after UA run)
  powellUa: null,         // Powell UA-sheet outputs for faithful EPR
  vuln: null,             // vulnerability curve {xs, mdr} (MDR vs 3-sec gust)
  roughness: null,        // per-point marine->land multiplier
  map: null,
  markers: [],            // circleMarker per grid point, in grid.json order
  wind: null,             // Float array of current per-point wind (mph)
  contour: null,          // current filled-contour layerGroup
  tiles: null,            // basemap tile layer (swapped on theme change)
  hoverTip: null,         // free-floating tooltip for nearest-point hover
  pixelPts: null,         // cached container-pixel coords of grid points
  metamodels: null,       // Phase B: precomputed GPR + NN params (default config)
  meanMode: false,        // true -> show per-point mean wind over all 100 vectors
  layers: { track: null, landfall: null },
};

const HOVER_PX = 16;      // generous hover radius (px) around each grid dot

// ---- WSP quantile -> Holland B, user-configurable distribution -----------
const B_DEFAULTS = {
  uniform:    { min: 1.0, max: 2.5 },
  triangular: { min: 1.0, mode: 1.7, max: 2.5 },
  normal:     { mean: 1.7, std: 0.4 },
};

function bParamInputs(dist) {
  const cfg = B_DEFAULTS[dist];
  const box = document.getElementById("bparams");
  box.innerHTML = "";
  for (const [key, val] of Object.entries(cfg)) {
    const label = document.createElement("label");
    label.textContent = key;
    const inp = document.createElement("input");
    inp.type = "number"; inp.step = "0.1"; inp.value = val;
    inp.dataset.key = key;
    inp.addEventListener("input", updateField);
    label.appendChild(inp);
    box.appendChild(label);
  }
}

function quantileToB(p) {
  const dist = document.getElementById("bdist").value;
  const vals = {};
  document.querySelectorAll("#bparams input").forEach(i => vals[i.dataset.key] = parseFloat(i.value));
  if (dist === "uniform") {
    return vals.min + p * (vals.max - vals.min);
  } else if (dist === "triangular") {
    const { min, mode, max } = vals;
    const fc = (mode - min) / (max - min);
    return p < fc
      ? min + Math.sqrt(p * (max - min) * (mode - min))
      : max - Math.sqrt((1 - p) * (max - min) * (max - mode));
  } else {
    return Math.max(0.8, Math.min(2.5, vals.mean + invNorm(p) * vals.std));
  }
}

function invNorm(p) {
  if (p <= 0) return -3.5; if (p >= 1) return 3.5;
  const a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
             1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00];
  const b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
             6.680131188771972e+01, -1.328068155288572e+01];
  const c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
             -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00];
  const d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
             3.754408661907416e+00];
  const pl = 0.02425, ph = 1 - pl;
  let q, r;
  if (p < pl) { q = Math.sqrt(-2 * Math.log(p));
    return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) /
           ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1); }
  if (p > ph) { q = Math.sqrt(-2 * Math.log(1-p));
    return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) /
            ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1); }
  q = p - 0.5; r = q*q;
  return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q /
         (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1);
}

// ---- color scale (Saffir-Simpson-ish, mph) -------------------------------
const WIND_STOPS = [
  [0,   "#3b4a5a"], [39,  "#2c7fb8"], [74,  "#41b6c4"], [96,  "#ffffb2"],
  [111, "#fecc5c"], [130, "#fd8d3c"], [157, "#e31a1c"], [185, "#7a0177"],
];
function windColor(mph) {
  if (mph == null || isNaN(mph)) return "#2b2f36";
  let c = WIND_STOPS[0][1];
  for (const [thr, col] of WIND_STOPS) { if (mph >= thr) c = col; else break; }
  return c;
}

// ---- loss (vulnerability curve) ------------------------------------------
const EXPOSURE_VALUE = 100000;   // $ per land vertex (ROA p.186)
const GUST_FACTOR = 1.0;         // peak surface wind -> 3-sec gust input (adjustable)

// MDR at a wind speed, linear-interpolated from the vulnerability curve
function mdrAt(windMph) {
  const v = state.vuln;
  if (!v) return null;
  const g = windMph * GUST_FACTOR, xs = v.xs, m = v.mdr;
  if (g <= xs[0]) return m[0];
  if (g >= xs[xs.length - 1]) return m[m.length - 1];
  let lo = 0, hi = xs.length - 1;
  while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (xs[mid] <= g) lo = mid; else hi = mid; }
  const t = (g - xs[lo]) / (xs[hi] - xs[lo]);
  return m[lo] + t * (m[hi] - m[lo]);
}
function lossDollars(windMph) {
  const mdr = mdrAt(windMph);
  return mdr == null ? null : mdr * EXPOSURE_VALUE;
}

// loss color scale (MDR 0..0.6+), greys->reds
const LOSS_STOPS = [
  [0, "#3b4a5a"], [0.02, "#fee5d9"], [0.05, "#fcae91"], [0.10, "#fb6a4a"],
  [0.20, "#de2d26"], [0.35, "#a50f15"], [0.50, "#67000d"],
];
function lossColor(mdr) {
  if (mdr == null || isNaN(mdr)) return "#2b2f36";
  let c = LOSS_STOPS[0][1];
  for (const [thr, col] of LOSS_STOPS) { if (mdr >= thr) c = col; else break; }
  return c;
}

function renderLegend(mode) {
  const el = document.getElementById("legend");
  if (mode === "loss") {
    el.innerHTML = LOSS_STOPS.map(([thr, col]) =>
      `<div class="lg"><span style="background:${col}"></span>&ge; ${(thr * 100).toFixed(0)}% MDR</div>`).join("");
    return;
  }
  if (mode === "landwater") {
    el.innerHTML = `<div class="lg"><span style="background:#6b7785"></span>Land</div>` +
                   `<div class="lg"><span style="background:#2b6cb0"></span>Water</div>`;
    return;
  }
  if (mode === "sensitivity") {
    el.innerHTML = SA_VARS.map(v =>
      `<div class="lg"><span style="background:${VAR_COLORS[v]}"></span>${v}</div>`).join("");
    return;
  }
  el.innerHTML = WIND_STOPS.map(([thr, col]) =>
    `<div class="lg"><span style="background:${col}"></span>&ge; ${thr} mph</div>`).join("");
}

// ---- current input vector + wind field -----------------------------------
function currentSelection() {
  const model = document.getElementById("model").value;
  const cat = "cat" + document.getElementById("category").value;
  const vIdx = parseInt(document.getElementById("vector").value, 10) - 1;
  const rec = state.inputs ? state.inputs[cat][vIdx] : null;
  return { model, cat, vIdx, rec };
}

// per-point peak wind (mph) for any (model, category, vector index),
// respecting the current land-effect selector. cat = "cat1|cat3|cat5".
// Returns the string "kd-pending" if Powell K&D is selected but not yet precomputed.
function computeWindFor(model, cat, vIdx) {
  const land = document.getElementById("landEffect").value;
  const rec = state.inputs ? state.inputs[cat][vIdx] : null;

  if (model === "powell") {
    if (land === "kd") {
      if (!state.powellKd || !state.powellKd[cat]) return "kd-pending";
      return state.powellKd[cat][vIdx];
    }
    if (!state.powell || !state.powell[cat]) return null;
    let wind = state.powell[cat][vIdx];
    if (land === "roughness" && state.roughness) {
      const f = state.roughness.factors;
      wind = Array.from(wind, (w, i) => w * f[i]);
    }
    return wind;
  }

  // live Holland / Willoughby
  if (!rec) return null;
  const B = quantileToB(rec.WSP);
  if (land === "kd") return computeLiveWindKD(model, rec, B, state.grid.points);
  let wind = computeLiveWind(model, rec, B, state.grid.points);
  if (land === "roughness" && state.roughness) {
    const f = state.roughness.factors;
    wind = Array.from(wind, (w, i) => w * f[i]);
  }
  return wind;
}

function computeWind() {
  const { model, cat, vIdx } = currentSelection();
  if (state.meanMode) return computeMeanWind(model, cat);
  return computeWindFor(model, cat, vIdx);
}

// per-point mean peak wind (mph) averaged over all 100 input vectors,
// respecting the current model/category/land-effect/B selection.
// Propagates null / "kd-pending" if no vector field is available yet.
function computeMeanWind(model, cat) {
  const recs = state.inputs ? state.inputs[cat] : null;
  if (!recs) return null;
  let sum = null, count = 0, first = null;
  for (let v = 0; v < recs.length; v++) {
    const w = computeWindFor(model, cat, v);
    if (first === null) first = w;
    if (!w || typeof w === "string") continue;   // null or "kd-pending"
    if (!sum) sum = new Float64Array(w.length);
    for (let i = 0; i < w.length; i++) sum[i] += w[i];
    count++;
  }
  if (!sum) return first;                          // nothing computable yet
  const out = new Float32Array(sum.length);
  for (let i = 0; i < out.length; i++) out[i] = sum[i] / count;
  return out;
}

// Right-click a grid point -> per-point loss-cost CSV over all 100 input vectors.
// 8 columns: CP, Rmax, VT, WSP, CF, FFP, %LC(i,x,y), %TLC(i)
//   %LC(i,x,y) = LC(i,x,y) / EXPOSURE_VALUE   (loss cost at this point / $/vertex)
//   TLC(i)     = sum_x sum_y LC(i,x,y)         (total loss cost over all land points)
//   %TLC(i)    = TLC(i) / (total exposure)
function downloadGridPointCsv(idx) {
  if (!state.inputs || !state.vuln) { alert("Need inputs + vulnerability curve loaded for a loss-cost CSV."); return; }
  const { model, cat } = currentSelection();
  const recs = state.inputs[cat] || [];
  const pt = state.grid.points[idx];
  const cols = ["CP", "Rmax", "VT", "WSP", "CF", "FFP"];
  const totalExposure = state.grid.n_land * EXPOSURE_VALUE;
  const rows = [[...cols, "%LC", "%TLC"].join(",")];
  for (let v = 0; v < recs.length; v++) {
    const w = computeWindFor(model, cat, v);
    if (!w || typeof w === "string") {       // null or "kd-pending" — no field yet
      alert(`Wind field unavailable for ${model} ${cat.toUpperCase()} — cannot build CSV.`);
      return;
    }
    const pctLC = pt.land ? mdrAt(w[idx]) : 0;     // LC/EXPOSURE_VALUE = MDR on land, 0 on water
    let tlc = 0;
    state.grid.points.forEach((q, j) => { if (q.land) tlc += mdrAt(w[j]) * EXPOSURE_VALUE; });
    rows.push([...cols.map(c => recs[v][c]), pctLC, tlc / totalExposure].join(","));
  }
  const blob = new Blob([rows.join("\n")], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = `formS6_losscost_${cat}_x${pt.ew}_y${pt.ns}.csv`;
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
}

// mean peak wind over land vertices — the SA/UA output metric
function landMeanWind(wind) {
  if (!wind || typeof wind === "string") return null;   // null or "kd-pending"
  let s = 0, n = 0;
  state.grid.points.forEach((p, i) => { if (p.land) { s += wind[i]; n++; } });
  return n ? s / n : null;
}

// ---- theme ---------------------------------------------------------------
function trackCasingColor(mode) {
  // halo under the red track line: white on the dark basemap, near-black on light
  return mode === "light" ? "#1a1a1a" : "#ffffff";
}

function applyTheme(mode) {
  document.body.classList.toggle("theme-light", mode === "light");
  const style = mode === "light" ? "light_all" : "dark_all";
  if (state.tiles) state.map.removeLayer(state.tiles);
  state.tiles = L.tileLayer(
    `https://{s}.basemaps.cartocdn.com/${style}/{z}/{x}/{y}{r}.png`,
    { attribution: "&copy; OpenStreetMap &copy; CARTO", subdomains: "abcd", maxZoom: 19 }
  ).addTo(state.map);
  state.tiles.bringToBack();
  if (state.layers.trackCasing) state.layers.trackCasing.setStyle({ color: trackCasingColor(mode) });
}

// ---- map -----------------------------------------------------------------
function buildMap() {
  const g = state.grid;
  const lats = g.points.map(p => p.lat), lons = g.points.map(p => p.lon);
  const bounds = [[Math.min(...lats), Math.min(...lons)],
                  [Math.max(...lats), Math.max(...lons)]];

  state.map = L.map("map").fitBounds(bounds, { padding: [20, 20] });
  applyTheme(document.getElementById("theme").value);

  state.markers = g.points.map(p => {
    const m = L.circleMarker([p.lat, p.lon], {
      radius: 3.4, weight: p.land ? 0 : 0.8, color: "#1b3a5b",
      fillColor: p.land ? "#6b7785" : "#2b6cb0", fillOpacity: 0.9,
      interactive: false,
    });
    m.addTo(state.map);
    return m;
  });

  const row0 = g.points.filter(p => p.ns === 0).sort((a, b) => a.ew - b.ew);
  const east = row0[0], west = row0[row0.length - 1];
  const trackPath = [[east.lat, east.lon], [west.lat, west.lon]];
  // theme-aware casing under a red dashed line -> readable over basemap and contour bands
  const casingColor = trackCasingColor(document.getElementById("theme").value);
  const trackCasing = L.polyline(trackPath, { color: casingColor, weight: 5, opacity: 0.85 });
  const trackLine = L.polyline(trackPath, { color: "#e53e3e", weight: 2.5, dashArray: "6 4" });
  const t0Marker = L.marker([east.lat, east.lon]).bindPopup("Storm center at t=0 (0,0)");
  state.layers.track = L.layerGroup([trackCasing, trackLine, t0Marker]).addTo(state.map);
  state.layers.trackCasing = trackCasing;               // recolored on theme change
  state.layers.trackLines = [trackCasing, trackLine];   // for bringToFront over contour

  state.layers.landfall = L.circleMarker([g.landfall.lat, g.landfall.lon], {
    radius: 6, color: "#f0abfc", fillColor: "#c026d3", fillOpacity: 0.9,
  }).addTo(state.map).bindPopup(`Landfall<br>${g.landfall.lat}, ${g.landfall.lon}`);
}

// ---- nearest-point hover (generous virtual radius) -----------------------
function pointInfoHTML(i) {
  const p = state.grid.points[i];
  const colorBy = document.getElementById("colorBy").value;
  const { rec } = currentSelection();
  const w = state.wind ? state.wind[i] : null;
  let wtxt = "";
  if (w != null) {
    wtxt = `<b>${w.toFixed(1)} mph</b>${state.meanMode ? " (mean of 100)" : ""}`;
    if (state.vuln && state.grid.points[i].land) {
      const mdr = mdrAt(w);
      wtxt += ` &middot; loss <b>${(mdr * 100).toFixed(1)}%</b> ($${Math.round(mdr * EXPOSURE_VALUE).toLocaleString()})`;
    }
    wtxt += "<br>";
  }
  const params = state.meanMode
    ? `<hr>mean over all 100 input vectors`
    : (rec
      ? `<hr>CP ${rec.CP} mb &middot; Rmax ${rec.Rmax} mi<br>` +
        `VT ${rec.VT} mph &middot; FFP ${rec.FFP} mb<br>` +
        `CF ${rec.CF} &middot; WSP ${rec.WSP} (B=${quantileToB(rec.WSP).toFixed(2)})`
      : "");
  return `${wtxt}(${p.ew}, ${p.ns}) mi<br>${p.lat.toFixed(4)}, ${p.lon.toFixed(4)}<br>` +
         `${p.land ? "land" : "water"}${p.place ? " &middot; " + p.place : ""}${params}`;
}

function rebuildPixelIndex() {
  state.pixelPts = state.grid.points.map(p =>
    state.map.latLngToContainerPoint([p.lat, p.lon]));
}

function setupHover() {
  state.hoverTip = L.tooltip({ direction: "top", offset: [0, -2], opacity: 0.95 });
  rebuildPixelIndex();
  state.map.on("zoomend moveend resize", rebuildPixelIndex);
  state.map.on("mousemove", e => {
    const cp = e.containerPoint, pts = state.pixelPts;
    let best = -1, bd = HOVER_PX * HOVER_PX;
    for (let i = 0; i < pts.length; i++) {
      const dx = pts[i].x - cp.x, dy = pts[i].y - cp.y, d = dx * dx + dy * dy;
      if (d < bd) { bd = d; best = i; }
    }
    if (best >= 0) {
      const p = state.grid.points[best];
      state.hoverTip.setLatLng([p.lat, p.lon]).setContent(pointInfoHTML(best));
      if (!state.map.hasLayer(state.hoverTip)) state.hoverTip.addTo(state.map);
    } else if (state.map.hasLayer(state.hoverTip)) {
      state.hoverTip.remove();
    }
  });
  state.map.on("mouseout", () => {
    if (state.map.hasLayer(state.hoverTip)) state.hoverTip.remove();
  });
  // left-click the nearest dot -> windfield popup (generous radius)
  state.map.on("click", e => {
    const cp = e.containerPoint, pts = state.pixelPts;
    let best = -1, bd = (HOVER_PX + 4) * (HOVER_PX + 4);
    for (let i = 0; i < pts.length; i++) {
      const dx = pts[i].x - cp.x, dy = pts[i].y - cp.y, d = dx * dx + dy * dy;
      if (d < bd) { bd = d; best = i; }
    }
    if (best >= 0) openWindfieldPopup(best);
  });
  // right-click the nearest dot -> per-point loss-cost CSV (100 input vectors)
  state.map.on("contextmenu", e => {
    const cp = e.containerPoint, pts = state.pixelPts;
    let best = -1, bd = (HOVER_PX + 4) * (HOVER_PX + 4);
    for (let i = 0; i < pts.length; i++) {
      const dx = pts[i].x - cp.x, dy = pts[i].y - cp.y, d = dx * dx + dy * dy;
      if (d < bd) { bd = d; best = i; }
    }
    if (best >= 0) downloadGridPointCsv(best);
  });
}

// ---- recolor + popups ----------------------------------------------------
function updateField() {
  const g = state.grid;
  const colorBy = document.getElementById("colorBy").value;
  const { model, rec } = currentSelection();
  renderLegend(colorBy);

  const needWind = colorBy === "wind" || colorBy === "loss";
  let wind = needWind ? computeWind() : null;
  const kdPending = wind === "kd-pending";
  if (kdPending) wind = null;
  state.wind = wind;

  // grid-point-level sensitivity: dominant input per land vertex (linear SRC)
  const sensMode = colorBy === "sensitivity";
  const sens = sensMode ? computeGridSensitivity(model, currentSelection().cat) : null;

  const showWater = document.getElementById("showWater").checked;
  const showGrid = document.getElementById("showGrid").checked;
  const display = document.getElementById("display").value;
  const lossMode = colorBy === "loss";
  const contourMode = display === "contour" && needWind && !!wind;

  // refresh contour overlay (wind bands, or loss-MDR bands)
  if (state.contour) { state.map.removeLayer(state.contour); state.contour = null; }
  if (contourMode) {
    if (lossMode && state.vuln) {
      const mdrField = Array.from(wind, w => mdrAt(w));
      const thr = LOSS_STOPS.map(s => s[0]).filter(t => t > 0);
      state.contour = buildContourLayer(g, mdrField, thr, lossColor).addTo(state.map);
    } else {
      const thr = WIND_STOPS.map(s => s[0]).filter(t => t > 0);
      state.contour = buildContourLayer(g, wind, thr, windColor).addTo(state.map);
    }
    // keep the storm track + landfall visible above the filled bands
    if (state.layers.trackLines && state.map.hasLayer(state.layers.track)) {
      state.layers.trackLines.forEach(l => l.bringToFront());
    }
    if (state.layers.landfall) state.layers.landfall.bringToFront();
  }

  let wmax = 0, wsum = 0, n = 0, lossTotal = 0;
  g.points.forEach((p, i) => {
    const w = wind ? wind[i] : null;

    // summary stats from the field itself — independent of marker visibility,
    // so the readout stays correct in filled-contour mode (dots hidden).
    if (sensMode) {
      if (p.land && sens && sens[i] >= 0) n++;
    } else if (lossMode) {
      const mdr = w != null ? mdrAt(w) : null;
      if (mdr != null && p.land) { lossTotal += mdr * EXPOSURE_VALUE; if (w > wmax) wmax = w; n++; }
    } else if (w != null) {                          // wind mode
      if (w > wmax) wmax = w; if (p.land) { wsum += w; n++; }
    }

    // marker styling (hidden in contour mode or when "Grid points" is off)
    const m = state.markers[i];
    const visible = !contourMode && showGrid && (p.land || showWater);
    m.setStyle({ opacity: visible ? 1 : 0, fillOpacity: visible ? 0.9 : 0 });
    if (!visible) { m.closeTooltip && m.unbindTooltip(); return; }

    let fill;
    if (colorBy === "landwater") {
      fill = p.land ? "#6b7785" : "#2b6cb0";
    } else if (sensMode) {
      fill = (p.land && sens && sens[i] >= 0) ? VAR_COLORS[SA_VARS[sens[i]]] : "#243244";
    } else if (lossMode) {
      const mdr = w != null ? mdrAt(w) : null;
      fill = p.land ? lossColor(mdr) : "#243244";   // loss only meaningful on land
    } else {
      fill = windColor(w);
    }
    m.setStyle({ fillColor: fill });
  });

  const info = document.getElementById("info");
  const tag = `${model.charAt(0).toUpperCase() + model.slice(1)} · ` +
              `${currentSelection().cat.toUpperCase()} ` +
              `${state.meanMode ? "mean (100 vectors)" : "v" + document.getElementById("vector").value}`;
  if (lossMode && wind && state.vuln) {
    const pct = lossTotal / (state.grid.n_land * EXPOSURE_VALUE) * 100;
    info.innerHTML = `${tag}<br>Loss over ${n} land pts <b>$${(lossTotal / 1e6).toFixed(2)}M</b>` +
      `<br>= <b>${pct.toFixed(2)}%</b> of $${(state.grid.n_land * EXPOSURE_VALUE / 1e6).toFixed(1)}M exposure`;
  } else if (lossMode && kdPending) {
    info.textContent = "Powell Kaplan–DeMaria field: precompute pending.";
  } else if (lossMode) {
    info.textContent = "Vulnerability curve not loaded…";
  } else if (colorBy === "wind" && wind) {
    info.innerHTML = `${tag}<br>Peak wind <b>${wmax.toFixed(1)} mph</b> · ` +
      `land mean ${n ? (wsum / n).toFixed(1) : "–"} mph`;
  } else if (colorBy === "wind" && kdPending) {
    info.textContent = "Powell Kaplan–DeMaria field: exact precompute scheduled after the UA run.";
  } else if (colorBy === "wind") {
    info.textContent = "Powell field not loaded yet…";
  } else if (sensMode && sens) {
    info.innerHTML = `${tag}<br>Grid-point sensitivity · dominant input over ${n} land pts` +
      `<br><span class="note">linear SRC per vertex</span>`;
  } else if (sensMode) {
    info.textContent = "Sensitivity needs a wind field — Powell K&D precompute pending.";
  } else {
    info.innerHTML = `${g.n_points} vertices &middot; ${g.n_land} land / ${g.n_water} water`;
  }
}

// ---- controls ------------------------------------------------------------
function wireControls() {
  ["model", "category", "colorBy", "bdist", "display"].forEach(id =>
    document.getElementById(id).addEventListener("change", () => {
      if (id === "bdist") bParamInputs(document.getElementById("bdist").value);
      updateField();
    }));

  const vec = document.getElementById("vector");
  vec.addEventListener("input", () => {
    document.getElementById("vectorLabel").textContent = vec.value;
    updateField();
  });

  document.getElementById("btnMean").addEventListener("click", () => {
    state.meanMode = !state.meanMode;
    document.getElementById("btnMean").classList.toggle("active", state.meanMode);
    vec.disabled = state.meanMode;
    if (state.meanMode) {
      // defer so the "Computing…" status repaints before the (live-model) averaging
      document.getElementById("info").textContent = "Computing mean over 100 vectors…";
      setTimeout(updateField, 20);
    } else {
      updateField();
    }
  });

  document.getElementById("showWater").addEventListener("change", updateField);
  document.getElementById("showGrid").addEventListener("change", updateField);
  document.getElementById("landEffect").addEventListener("change", updateField);
  document.getElementById("theme").addEventListener("change", e => applyTheme(e.target.value));
  document.getElementById("showTrack").addEventListener("change", e => {
    if (e.target.checked) state.layers.track.addTo(state.map);
    else state.map.removeLayer(state.layers.track);
  });

  bParamInputs(document.getElementById("bdist").value);
}

// ---- init ----------------------------------------------------------------
async function init() {
  wireControls();
  try {
    // pipeline-regenerated data: never serve a stale cached copy
    const NC = { cache: "no-store" };
    state.grid = await (await fetch("../outputs/web/grid.json", NC)).json();
    state.inputs = await (await fetch("../outputs/web/inputs.json", NC)).json();
    try { state.roughness = await (await fetch("../outputs/web/roughness.json", NC)).json(); }
    catch (e) { state.roughness = null; }
    try { state.powellKd = await (await fetch("../outputs/web/powell_kd.json", NC)).json(); }
    catch (e) { state.powellKd = null; }   // generated after the UA run
    try { state.powellField = await (await fetch("../outputs/web/powell_field.json", NC)).json(); }
    catch (e) { state.powellField = null; }  // generated after the UA run
    try { state.powellUa = await (await fetch("../outputs/web/powell_ua.json", NC)).json(); }
    catch (e) { state.powellUa = null; }     // faithful EPR (Option 1)
    try { state.vuln = await (await fetch("../outputs/web/vulnerability.json", NC)).json(); }
    catch (e) { state.vuln = null; }         // MDR vs wind (loss)
    try { state.metamodels = await (await fetch("../outputs/web/metamodels.json", NC)).json(); }
    catch (e) { state.metamodels = null; }   // Phase B: precomputed GPR + NN (default config)
    buildMap();
    setupHover();
    setupAnalysis();
    // powell.json may still be precomputing; load if present
    try {
      state.powell = await (await fetch("../outputs/web/powell.json", NC)).json();
    } catch (e) { state.powell = null; }
    updateField();
  } catch (err) {
    document.getElementById("info").textContent = "Failed to load data: " + err;
  }
}

init();
