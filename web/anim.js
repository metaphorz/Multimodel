/* Optional storm animation: sweep the INSTANTANEOUS windfield east->west across
   the fixed geography over the passage (t=-12 .. +24 h) as a semi-transparent
   moving contour — the ground-frame view of the same field the left-click popup
   shows storm-relative. Off by default; the static peak-wind footprint stays the
   default view and nothing animates until the user presses Play or scrubs.

   At t=-12 the eye is well east of the grid (offshore Atlantic, ewc=VT*t < 0), so
   the render domain is EXTENDED east far enough to show the approach, and the map
   zooms out to fit it while animating (restoring the prior view on Reset).

   Phase 1: WIND only, the selected input vector, all three models
   (Holland/Willoughby live via fieldFnFor; Powell samples its stored
   storm-relative field). Roughness applies as a static per-vertex factor on land
   grid points, Kaplan-DeMaria decay as a scalar s(t); both per frame. */

const ANIM = {
  tMin: -12, tMax: 24, dt: 0.5,   // frame time step (h)
  marginMi: 90, maxExtraCols: 130, upsample: 4, fillOpacity: 0.5,
  mode: "narrow",                 // "narrow" (grid only, default zoom) | "wide" (offshore + zoom out)
  frames: 0, i: 0, speed: 5,      // speed 1..10 -> frame interval via animFrameMs()
  playing: false, timer: null, active: false,
  fields: null, key: null, ext: null, eye: null, savedView: null,
};

// playback frame interval (ms) from the speed slider: 1 (slow ~500 ms) .. 10 (fast ~50 ms)
function animFrameMs() { return Math.round(500 / Math.max(1, ANIM.speed)); }

function animTimeAt(i) { return ANIM.tMin + i * ANIM.dt; }

function animStormKey() {
  const { model, cat, vIdx } = currentSelection();
  const rough = document.getElementById("landRoughness").checked;
  const decay = document.getElementById("landDecay").checked;
  return [model, cat, vIdx, "r" + rough, "d" + decay, ANIM.mode].join("|");
}

// bilinear sample of a Powell storm-relative field Z (n x n over +/-halfKm) at
// (xkm, ykm); mirrors powellTimeSeries so the animation matches the popup.
function animSamplePowell(Z, n, halfKm, xkm, ykm) {
  const step = (2 * halfKm) / (n - 1);
  const fc = (xkm + halfKm) / step, fr = (ykm + halfKm) / step;
  if (fc < 0 || fc > n - 1 || fr < 0 || fr > n - 1) return 0;
  const c0 = Math.min(Math.floor(fc), n - 2), r0 = Math.min(Math.floor(fr), n - 2);
  const tx = fc - c0, ty = fr - r0;
  const z00 = Z[r0 * n + c0], z01 = Z[r0 * n + c0 + 1];
  const z10 = Z[(r0 + 1) * n + c0], z11 = Z[(r0 + 1) * n + c0 + 1];
  return (z00 * (1 - tx) + z01 * tx) * (1 - ty) + (z10 * (1 - tx) + z11 * tx) * ty;
}

// Extended grid = the real grid plus offshore (ew<0) columns east of ew=0, far
// enough to show the eye at t=-12 for this storm's VT. The lattice is regular
// (lat constant per ns row, lon linear in ew), so offshore lat/lon extrapolate
// cleanly. Returns {grid, lattice, gridIdx (Int32: real grid index or -1), bounds}.
function animBuildExtGrid(rec, extraCols) {
  const g = state.grid, pts = g.points;
  const nsAsc = [...g.ns_values].sort((a, b) => a - b);
  const ewGrid = [...g.ew_values].sort((a, b) => a - b);   // 0..117
  // regular-mesh geometry
  const latByNs = new Map(); const gridByEwNs = new Map();
  let lon0 = 0, ew1 = ewGrid[1];
  pts.forEach((p, idx) => {
    if (p.ns === nsAsc[0]) { /* noop */ }
    if (!latByNs.has(p.ns)) latByNs.set(p.ns, p.lat);
    gridByEwNs.set(p.ew + "," + p.ns, idx);
    if (p.ew === 0 && p.ns === 0) lon0 = p.lon;
  });
  const lonAt0ns0 = pts.find(p => p.ew === 0 && p.ns === 0).lon;
  const lonAt3ns0 = pts.find(p => p.ew === ew1 && p.ns === 0).lon;
  const dlonDew = (lonAt3ns0 - lonAt0ns0) / ew1;           // per mile of ew
  const lonAtEw = ew => lonAt0ns0 + ew * dlonDew;

  const step = ewGrid[1] - ewGrid[0];                      // 3 mi
  const ewOff = [];
  for (let c = extraCols; c >= 1; c--) ewOff.push(-c * step);   // -..-3, ascending
  const ewAll = ewOff.concat(ewGrid);

  const points = [], gridIdx = [];
  let minLat = Infinity, maxLat = -Infinity, minLon = Infinity, maxLon = -Infinity;
  for (const ns of nsAsc) {
    const lat = latByNs.get(ns);
    for (const ew of ewAll) {
      const gi = gridByEwNs.has(ew + "," + ns) ? gridByEwNs.get(ew + "," + ns) : -1;
      const lon = lonAtEw(ew);
      points.push({ ew, ns, lat, lon, land: gi >= 0 ? pts[gi].land : false });
      gridIdx.push(gi);
      if (lat < minLat) minLat = lat; if (lat > maxLat) maxLat = lat;
      if (lon < minLon) minLon = lon; if (lon > maxLon) maxLon = lon;
    }
  }
  const extGrid = { ew_values: ewAll, ns_values: nsAsc, points };
  return {
    grid: extGrid, lattice: buildLatticeFrom(extGrid), gridIdx,
    bounds: [[minLat, minLon], [maxLat, maxLon]],
  };
}

// precompute the instantaneous wind field (mph) over the extended domain for each
// frame; returns false if the selection can't be simulated. Cached on storm key.
function animPrecompute() {
  const key = animStormKey();
  if (ANIM.fields && ANIM.key === key) return true;
  const { model, cat, vIdx, rec } = currentSelection();
  if (!rec || !state.grid) return false;

  // wide mode extends east to show the offshore approach at t=-12; narrow renders
  // only on the grid (default zoom, storm enters from the east edge)
  const step = Math.abs(state.grid.ew_values[1] - state.grid.ew_values[0]) || 3;
  const extraCols = ANIM.mode === "wide"
    ? Math.min(ANIM.maxExtraCols, Math.ceil((12 * rec.VT + ANIM.marginMi) / step)) : 0;
  const ext = animBuildExtGrid(rec, extraCols);
  const P = ext.grid.points, N = P.length, gi = ext.gridIdx;
  ANIM.frames = Math.round((ANIM.tMax - ANIM.tMin) / ANIM.dt) + 1;
  const rough = document.getElementById("landRoughness").checked && !!state.roughness;

  // Kaplan-DeMaria decay scalar s(t) from the storm's marine peak (matches popup)
  let sched = null, schedN = 0, schedDt = 0;
  if (document.getElementById("landDecay").checked) {
    const marine = { powell: state.powell, holland: state.holland, willoughby: state.willoughby }[model];
    let V0 = 0;
    if (marine && marine[cat] && marine[cat][vIdx])
      for (const v of marine[cat][vIdx]) if (v > V0) V0 = v;
    if (V0 > 0) {
      sched = intensitySchedule(V0, rec.VT, state.grid.points);
      schedN = sched.length - 1; schedDt = (PHYS.T_MAX - PHYS.T_MIN) / schedN;
    }
  }
  const decayAt = t => sched
    ? sched[Math.max(0, Math.min(schedN, Math.round((t - PHYS.T_MIN) / schedDt)))] : 1;

  let fn = null, Z = null, pn = 0, phalf = 0;
  if (model === "powell") {
    Z = state.powellField && state.powellField[cat] && state.powellField[cat][vIdx];
    if (!Z) return false;
    pn = state.powellField.n; phalf = state.powellField.halfKm;
  } else {
    fn = fieldFnFor(model, rec, quantileToB(rec.WSP));
  }

  const fields = [];
  for (let i = 0; i < ANIM.frames; i++) {
    const t = animTimeAt(i), ewc = rec.VT * t, s = decayAt(t);
    const F = new Float32Array(N);
    for (let k = 0; k < N; k++) {
      const p = P[k];
      let w = model === "powell"
        ? animSamplePowell(Z, pn, phalf, -(p.ew - ewc), p.ns)     // mi==km axis (popup)
        : fn(-(p.ew - ewc) * PHYS.MILE_M, p.ns * PHYS.MILE_M);    // metres (physical)
      w *= s;
      if (rough && gi[k] >= 0 && p.land) w *= state.roughness.factors[gi[k]];
      F[k] = w;
    }
    fields.push(F);
  }
  ANIM.fields = fields; ANIM.key = key; ANIM.ext = ext;
  return true;
}

// eye lat/lon at time t (constant-lat track; lon linear in ewc)
function animEyeLatLng(t, rec) {
  const row = state._animRow0 ||
    (state._animRow0 = state.grid.points.filter(p => p.ns === 0).sort((a, b) => a.ew - b.ew));
  const ewc = rec.VT * t, a = row[0], b = row[1];
  const f = (ewc - a.ew) / ((b.ew - a.ew) || 1);           // extrapolates for ewc<0
  return [a.lat + (b.lat - a.lat) * f, a.lon + (b.lon - a.lon) * f];
}

function animRenderFrame(i) {
  if (ANIM.key !== animStormKey() && !animPrecompute()) return;
  ANIM.i = Math.max(0, Math.min(ANIM.frames - 1, i));
  const ext = ANIM.ext;
  if (state.animContour) { state.map.removeLayer(state.animContour); state.animContour = null; }
  const thr = WIND_STOPS.map(s => s[0]).filter(v => v > 0);
  state.animContour = buildContourLayer(ext.grid, ANIM.fields[ANIM.i], thr, windColor,
    { lattice: ext.lattice, upsample: ANIM.upsample, fillOpacity: ANIM.fillOpacity }).addTo(state.map);
  if (state.layers.trackLines) state.layers.trackLines.forEach(l => l.bringToFront());
  if (state.layers.landfall) state.layers.landfall.bringToFront();

  const { rec } = currentSelection();
  const ll = animEyeLatLng(animTimeAt(ANIM.i), rec);
  if (!ANIM.eye)
    ANIM.eye = L.circleMarker(ll, { radius: 5, color: "#fff", weight: 2,
      fillColor: "#111", fillOpacity: 0.85, interactive: false }).addTo(state.map);
  else ANIM.eye.setLatLng(ll);
  ANIM.eye.bringToFront();

  document.getElementById("simSlider").value = ANIM.i;
  const t = animTimeAt(ANIM.i);
  document.getElementById("simTime").textContent = `t = ${t >= 0 ? "+" : ""}${t.toFixed(1)} h`;
}

function animEnter() {
  if (!animPrecompute()) { document.getElementById("simTime").textContent = "unavailable for this selection"; return false; }
  ANIM.active = true;
  document.getElementById("simBar").classList.add("active");
  ANIM.savedView = { center: state.map.getCenter(), zoom: state.map.getZoom() };
  // keep the grid points visible (they show through the translucent windfield)
  if (state.markers) state.markers.forEach(m => m.setStyle({ opacity: 1, fillOpacity: 0.9 }));
  if (state.contour) { state.map.removeLayer(state.contour); state.contour = null; }
  animApplyZoom();
  animRenderFrame(ANIM.i);
  return true;
}

// wide -> fit the extended offshore domain; narrow -> the default grid view
function animApplyZoom() {
  if (ANIM.mode === "wide") state.map.fitBounds(ANIM.ext.bounds, { padding: [24, 24], animate: false });
  else if (ANIM.savedView) state.map.setView(ANIM.savedView.center, ANIM.savedView.zoom, { animate: false });
}

function animSetMode(m) {
  if (ANIM.mode === m) return;
  ANIM.mode = m;
  updateSimModeButtons();
  if (ANIM.active) { animPrecompute(); animApplyZoom(); animRenderFrame(ANIM.i); }
}

function updateSimModeButtons() {
  const nb = document.getElementById("simNarrow"), wb = document.getElementById("simWide");
  if (nb) nb.classList.toggle("active", ANIM.mode === "narrow");
  if (wb) wb.classList.toggle("active", ANIM.mode === "wide");
}

function animExit() {
  animPause();
  ANIM.active = false;
  const bar = document.getElementById("simBar"); if (bar) bar.classList.remove("active");
  if (state.animContour) { state.map.removeLayer(state.animContour); state.animContour = null; }
  if (ANIM.eye) { state.map.removeLayer(ANIM.eye); ANIM.eye = null; }
  if (ANIM.savedView) { state.map.setView(ANIM.savedView.center, ANIM.savedView.zoom, { animate: false }); ANIM.savedView = null; }
  updateField();   // restore the static peak-wind footprint at the default zoom
}

// (re)start the playback timer at the current speed; used by play and on speed change
function animStartTimer() {
  if (ANIM.timer) clearInterval(ANIM.timer);
  ANIM.timer = setInterval(() => {
    if (ANIM.i >= ANIM.frames - 1) { animPause(); return; }
    animRenderFrame(ANIM.i + 1);
  }, animFrameMs());
}

function animPlay() {
  if (!ANIM.active && !animEnter()) return;
  if (ANIM.i >= ANIM.frames - 1) ANIM.i = 0;   // replay from the start
  ANIM.playing = true;
  document.getElementById("simPlay").textContent = "❚❚";
  animStartTimer();
}

function animPause() {
  ANIM.playing = false;
  if (ANIM.timer) { clearInterval(ANIM.timer); ANIM.timer = null; }
  const b = document.getElementById("simPlay"); if (b) b.textContent = "▶";
}

function wireSim() {
  const bar = document.getElementById("simBar");
  const play = document.getElementById("simPlay");
  const slider = document.getElementById("simSlider");
  const speed = document.getElementById("simSpeed");
  const opacity = document.getElementById("simOpacity");
  const narrow = document.getElementById("simNarrow");
  const wide = document.getElementById("simWide");
  const reset = document.getElementById("simReset");
  if (!bar || !play || !slider || !speed || !opacity || !narrow || !wide || !reset) return;
  // keep mouse/scroll events on the control bar from reaching the map underneath,
  // so dragging the slider scrubs instead of panning the map.
  if (window.L) {
    L.DomEvent.disableClickPropagation(bar);
    L.DomEvent.disableScrollPropagation(bar);
  }
  slider.min = 0;
  slider.max = Math.round((ANIM.tMax - ANIM.tMin) / ANIM.dt);
  slider.value = 0;
  speed.value = ANIM.speed;
  opacity.value = Math.round(ANIM.fillOpacity * 100);
  updateSimModeButtons();
  play.addEventListener("click", () => { ANIM.playing ? animPause() : animPlay(); });
  slider.addEventListener("input", () => {
    if (!ANIM.active && !animEnter()) return;
    animPause(); animRenderFrame(+slider.value);
  });
  speed.addEventListener("input", () => {
    ANIM.speed = +speed.value;
    if (ANIM.playing) animStartTimer();   // apply the new rate immediately
  });
  opacity.addEventListener("input", () => {
    ANIM.fillOpacity = Math.max(0.05, Math.min(1, +opacity.value / 100));
    if (ANIM.active && !ANIM.playing) animRenderFrame(ANIM.i);   // live preview when paused
  });
  narrow.addEventListener("click", () => animSetMode("narrow"));
  wide.addEventListener("click", () => animSetMode("wide"));
  reset.addEventListener("click", animExit);
}

wireSim();   // script is at end of <body>, so the DOM is already parsed
