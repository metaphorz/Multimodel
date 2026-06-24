/* Form S-6 Sensitivity & Uncertainty Analysis (Iman/Johnson/Schroeder 2001).

   SA -> SRC (standardized regression coefficients): regress the standardized
        output on the 6 standardized inputs over the 100 "SA all Variables"
        vectors, per category. SRC = sign(direction) x magnitude(influence).
   UA -> EPR (expected % reduction in output variance): EPR_i = SRC_i^2 x 100%
        (variance share; Option 2 approximation, valid for ~independent inputs).

   Output metric per vector: mean peak wind over the 682 land vertices
   (wind proxy; swaps to loss cost in Phase 5). Uses the currently selected
   model + roughness toggle (what you see is what you analyze).
*/

const SA_VARS = ["CP", "Rmax", "VT", "WSP", "CF", "FFP"];
const SA_CATS = ["cat1", "cat3", "cat5"];
const VAR_COLORS = {
  CP: "#3b82f6", Rmax: "#22c55e", VT: "#f59e0b",
  WSP: "#111827", CF: "#ef4444", FFP: "#7c3aed",
};
const analysisState = { mode: null, cache: null };  // mode: 'src' | 'epr'
const profilerState = { mm: null, ref: null };       // metamodel + reference point

// ---- linear algebra ------------------------------------------------------
function mean(a) { return a.reduce((s, v) => s + v, 0) / a.length; }
function corr(a, b) {
  const ma = mean(a), mb = mean(b);
  let sab = 0, saa = 0, sbb = 0;
  for (let i = 0; i < a.length; i++) {
    const da = a[i] - ma, db = b[i] - mb;
    sab += da * db; saa += da * da; sbb += db * db;
  }
  return (saa === 0 || sbb === 0) ? 0 : sab / Math.sqrt(saa * sbb);
}
// solve A x = b (n x n) via Gaussian elimination with partial pivoting
function solve(A, b) {
  const n = b.length, M = A.map((r, i) => [...r, b[i]]);
  for (let c = 0; c < n; c++) {
    let p = c;
    for (let r = c + 1; r < n; r++) if (Math.abs(M[r][c]) > Math.abs(M[p][c])) p = r;
    [M[c], M[p]] = [M[p], M[c]];
    const piv = M[c][c] || 1e-12;
    for (let r = 0; r < n; r++) {
      if (r === c) continue;
      const f = M[r][c] / piv;
      for (let k = c; k <= n; k++) M[r][k] -= f * M[c][k];
    }
  }
  return M.map((row, i) => row[n] / (M[i][i] || 1e-12));
}

// ---- response variable Y (user-toggleable) -------------------------------
function responseVar() {
  const el = document.getElementById("response");
  return el ? el.value : "wind";          // 'wind' | 'tlc'
}

// %TLC(i) = TLC(i)/total exposure as percent = 100 * mean MDR over land points
// (TLC = Σ_land MDR·$100k, total exposure = 682·$100k = $68.2M; ROA definition).
function pctTLC(wind) {
  if (!wind || typeof wind === "string" || !state.vuln) return null;
  let s = 0, n = 0;
  state.grid.points.forEach((p, i) => {
    if (p.land) { const m = mdrAt(wind[i]); if (m != null) { s += m; n++; } }
  });
  return n ? (s / n) * 100 : null;
}

// scalar output metric per vector for the current Response selector
function outputMetric(model, cat, vIdx) {
  const wind = computeWindFor(model, cat, vIdx);
  if (!wind || typeof wind === "string") return null;     // null / "kd-pending"
  return responseVar() === "tlc" ? pctTLC(wind) : landMeanWind(wind);
}

// ---- compute SRC for the selected model, all categories ------------------
function computeSRC(model) {
  const inputs = state.inputs, nv = SA_VARS.length;
  const result = {};   // cat -> { src: {var:val}, r2 }
  for (const cat of SA_CATS) {
    const recs = inputs[cat];
    // build input columns + output y (selected response metric)
    const cols = SA_VARS.map(v => recs.map(r => r[v]));
    const y = recs.map((_, i) => outputMetric(model, cat, i));
    if (y.some(v => v == null)) return null;   // e.g. Powell K&D precompute pending
    // correlation matrix R (nv x nv) and r (input vs output)
    const R = cols.map(ci => cols.map(cj => corr(ci, cj)));
    const r = cols.map(ci => corr(ci, y));
    const beta = solve(R, r);                 // SRC
    const r2 = beta.reduce((s, b, i) => s + b * r[i], 0);
    const src = {}; SA_VARS.forEach((v, i) => src[v] = beta[i]);
    result[cat] = { src, r2 };
  }
  return result;
}

// ---- grid-point-level sensitivity ----------------------------------------
// Per land vertex, regress that vertex's wind (over the 100 vectors) on the six
// standardized inputs and return the dominant input index (SA at the grid-point
// level, not after averaging — the deck's recommendation). Water/unavailable = -1.
function computeGridSensitivity(model, cat) {
  const recs = state.inputs[cat], n = recs.length;
  const fields = [];
  for (let i = 0; i < n; i++) {
    const w = computeWindFor(model, cat, i);
    if (!w || typeof w === "string") return null;     // metric not ready
    fields.push(w);
  }
  const cols = SA_VARS.map(v => recs.map(r => r[v]));
  const R = cols.map(ci => cols.map(cj => corr(ci, cj)));  // input corr matrix (fixed)
  const pts = state.grid.points, dom = new Int16Array(pts.length).fill(-1);
  for (let p = 0; p < pts.length; p++) {
    if (!pts[p].land) continue;
    const y = fields.map(f => f[p]);
    const r = cols.map(ci => corr(ci, y));
    const beta = solve(R, r);                            // standardized regression coeffs
    let bi = 0, bv = -1;
    beta.forEach((b, k) => { const a = Math.abs(b); if (a > bv) { bv = a; bi = k; } });
    dom[p] = bi;
  }
  return dom;
}

// ---- faithful EPR (Powell, Option 1): variance share from the UA sheets ---
// EPR_i = Var(Y when only X_i varies) / Var(Y_SA), from powell_ua.json + powell.json.
const UA_KEY = { CP: "cp", Rmax: "rmax", VT: "vt", WSP: "wsp", CF: "cf", FFP: "ffp" };
function variance(a) { const m = mean(a); return mean(a.map(v => (v - m) * (v - m))); }
function faithfulEPR() {
  const ua = state.powellUa, pk = state.powell;
  if (!ua || !pk) return null;
  const land = state.grid.points.map((p, i) => p.land ? i : -1).filter(i => i >= 0);
  const res = {};
  for (const cat of SA_CATS) {
    const ysa = pk[cat].map(v => mean(land.map(i => v[i])));   // marine land-mean per SA vector
    const vsa = variance(ysa) || 1e-9;
    res[cat] = {};
    for (const v of SA_VARS) res[cat][v] = variance(ua[UA_KEY[v]][cat]) / vsa * 100;
  }
  return res;
}

// ---- draggable/resizable floating panels (SRC + EPR can coexist) ---------
const panels = {};          // mode -> { el, body, title }
let zTop = 1000;

function getData() {
  const model = document.getElementById("model").value;
  const land = document.getElementById("landEffect").value;
  const resp = responseVar();
  if (!analysisState.cache || analysisState.cache.model !== model ||
      analysisState.cache.land !== land || analysisState.cache.resp !== resp) {
    analysisState.cache = { model, land, resp, data: computeSRC(model) };
  }
  return analysisState.cache;
}

// ---- second-order response-surface metamodel (for the profiler) ----------
// Ŷ = b0 + Σ bᵢzᵢ + Σ bᵢᵢzᵢ² + Σ_{i<j} bᵢⱼzᵢzⱼ on standardized inputs z.
// The quadratic + cross terms let the profiler curves bend and reveal
// interactions (a linear fit would draw flat, parallel lines).
function rsmFeatures(z) {
  const n = SA_VARS.length, f = [1];
  for (let i = 0; i < n; i++) f.push(z[i]);
  for (let i = 0; i < n; i++) f.push(z[i] * z[i]);
  for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) f.push(z[i] * z[j]);
  return f;
}

function fitRSM(model, cat) {
  const recs = state.inputs[cat], n = recs.length;
  const y = recs.map((_, i) => outputMetric(model, cat, i));
  if (y.some(v => v == null)) return null;        // metric unavailable yet
  const stats = SA_VARS.map(v => {
    const col = recs.map(r => r[v]);
    const m = mean(col), sd = Math.sqrt(variance(col)) || 1;
    return { v, m, sd, min: Math.min(...col), max: Math.max(...col) };
  });
  const z = recs.map(r => stats.map(s => (r[s.v] - s.m) / s.sd));
  const X = z.map(rsmFeatures), p = X[0].length;
  // normal equations XᵀX β = Xᵀy with a tiny ridge for stability
  const XtX = Array.from({ length: p }, () => new Array(p).fill(0));
  const Xty = new Array(p).fill(0);
  for (let k = 0; k < n; k++) {
    const xk = X[k], yk = y[k];
    for (let a = 0; a < p; a++) {
      Xty[a] += xk[a] * yk;
      for (let b = 0; b < p; b++) XtX[a][b] += xk[a] * xk[b];
    }
  }
  for (let a = 1; a < p; a++) XtX[a][a] += 1e-6;
  const beta = solve(XtX, Xty);
  const yhat = X.map(xk => xk.reduce((s, xa, a) => s + xa * beta[a], 0));
  const yb = mean(y);
  let ssr = 0, sst = 0;
  for (let k = 0; k < n; k++) { ssr += (y[k] - yhat[k]) ** 2; sst += (y[k] - yb) ** 2; }
  return { stats, beta, r2: sst ? 1 - ssr / sst : 0,
           ymin: Math.min(...y), ymax: Math.max(...y) };
}

function rsmPredict(fit, raw) {
  const z = fit.stats.map((s, i) => (raw[i] - s.m) / s.sd);
  return rsmFeatures(z).reduce((s, fa, a) => s + fa * fit.beta[a], 0);
}

// ---- Phase B: evaluate precomputed GPR / NN metamodels (default config) ---
// Training happens offline (pipeline/fit_metamodels.py -> metamodels.json);
// the browser only evaluates. GPR/NN are fit for the DEFAULT config only
// (Powell + roughness, "Option A"); Linear/RSM stays live for every config.
const METAMODEL_LABEL = { rsm: "Linear (RSM)", gpr: "Gaussian process", mlp: "Neural net" };
const METAMODEL_COLOR = { rsm: "#2563eb", gpr: "#059669", mlp: "#b45309" };

function currentMetamodel() {
  const el = document.getElementById("metamodel");
  return el ? el.value : "rsm";       // 'rsm' | 'gpr' | 'mlp'
}

function mmEntry(response, cat) {
  const mm = state.metamodels;
  return (mm && mm.responses[response] && mm.responses[response][cat])
    ? mm.responses[response][cat] : null;
}

function standardizeRaw(raw, scaler) {
  return raw.map((v, i) => (v - scaler.mean[i]) / scaler.std[i]);
}

// GPR posterior mean from exported params (matches scikit-learn predict)
function gprPredictRaw(entry, raw) {
  const g = entry.gpr, z = standardizeRaw(raw, entry.scaler), ls = g.length_scale;
  let acc = 0;
  for (let t = 0; t < g.x_train.length; t++) {
    const xt = g.x_train[t]; let d2 = 0;
    for (let d = 0; d < z.length; d++) { const e = (z[d] - xt[d]) / ls[d]; d2 += e * e; }
    acc += g.const * Math.exp(-0.5 * d2) * g.alpha[t];
  }
  return acc * g.y_std + g.y_mean;
}

// MLP forward pass (tanh hidden, identity output; standardized in + out)
function mlpPredictRaw(entry, raw) {
  const m = entry.mlp, W = m.weights, B = m.biases;
  let a = standardizeRaw(raw, entry.scaler);
  for (let l = 0; l < W.length; l++) {
    const Wl = W[l], Bl = B[l], out = new Array(Wl[0].length).fill(0);
    for (let o = 0; o < out.length; o++) {
      let s = Bl[o];
      for (let i = 0; i < a.length; i++) s += a[i] * Wl[i][o];
      out[o] = (l < W.length - 1) ? Math.tanh(s) : s;
    }
    a = out;
  }
  return a[0] * m.y_std + m.y_mean;
}

// per-input observed stats (min/max/mean/sd) — config-independent axis ranges
function inputStats(cat) {
  const recs = state.inputs[cat];
  return SA_VARS.map(v => {
    const col = recs.map(r => r[v]);
    return { v, m: mean(col), sd: Math.sqrt(variance(col)) || 1,
             min: Math.min(...col), max: Math.max(...col) };
  });
}

// unified metamodel object: { type, stats, predict(raw), r2, cv?, ymin, ymax, note }
function buildMetamodel(model, cat, type) {
  type = type || currentMetamodel();
  if (type === "rsm") {
    const fit = fitRSM(model, cat);
    if (!fit) return null;
    return { type, stats: fit.stats, predict: raw => rsmPredict(fit, raw),
             r2: fit.r2, ymin: fit.ymin, ymax: fit.ymax, note: "" };
  }
  const entry = mmEntry(responseVar(), cat);
  if (!entry) return null;                    // metamodels.json not loaded
  const block = entry[type];
  const land = document.getElementById("landEffect").value;
  const isDefault = (model === "powell" && land === "roughness");
  const predict = type === "gpr"
    ? raw => gprPredictRaw(entry, raw) : raw => mlpPredictRaw(entry, raw);
  return { type, stats: inputStats(cat), predict, r2: block.r2, cv: block.cv_r2,
           ymin: entry.y_range[0], ymax: entry.y_range[1],
           note: isDefault ? "" : " · default config (Powell+roughness)" };
}

function bringFront(el) { el.style.zIndex = ++zTop; }

function makeDraggable(el, handle) {
  let sx, sy, ox, oy, dragging = false;
  handle.addEventListener("mousedown", e => {
    // don't start a drag from an interactive control in the header (close, reset…)
    if (["BUTTON", "INPUT", "SELECT"].includes(e.target.tagName)) return;
    dragging = true; sx = e.clientX; sy = e.clientY;
    const r = el.getBoundingClientRect(), pr = el.parentElement.getBoundingClientRect();
    ox = r.left - pr.left; oy = r.top - pr.top;
    el.style.right = "auto"; el.style.bottom = "auto"; bringFront(el); e.preventDefault();
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", up);
  });
  function move(e) {
    if (!dragging) return;
    el.style.left = (ox + e.clientX - sx) + "px";
    el.style.top = Math.max(0, oy + e.clientY - sy) + "px";
  }
  function up() {
    dragging = false;
    document.removeEventListener("mousemove", move);
    document.removeEventListener("mouseup", up);
  }
}

function createPanel(mode) {
  const el = document.createElement("div");
  el.className = "analysis-panel";
  const i = Object.keys(panels).length;
  el.style.left = (16 + i * 36) + "px";
  el.style.top = (16 + i * 36) + "px";
  el.innerHTML =
    `<div class="ap-header"><span class="ap-title"></span>` +
    `<button class="ap-close" title="close">&times;</button></div>` +
    `<div class="ap-body"></div>`;
  document.getElementById("map").appendChild(el);
  if (window.L) {                       // don't pan/zoom the map under the panel
    L.DomEvent.disableClickPropagation(el);
    L.DomEvent.disableScrollPropagation(el);
  }
  el.querySelector(".ap-close").addEventListener("click", () => { el.style.display = "none"; });
  el.addEventListener("mousedown", () => bringFront(el));
  makeDraggable(el, el.querySelector(".ap-header"));
  return { el, body: el.querySelector(".ap-body"), title: el.querySelector(".ap-title") };
}

function openPanel(mode) {
  if (!panels[mode]) {
    panels[mode] = createPanel(mode);
    if (mode === "prof" || mode === "cmp") {
      panels[mode].el.style.width = "580px";
      panels[mode].el.style.height = "540px";
    }
  }
  panels[mode].el.style.display = "flex";
  bringFront(panels[mode].el);
  panels[mode].body.innerHTML = "<p class='note'>Computing…</p>";
  setTimeout(() => renderPanel(mode), 20);
}

// dispatch a panel to its renderer
function renderPanel(mode) {
  if (mode === "prof") return drawProfiler();
  if (mode === "cmp") return drawCompare();
  if (mode === "cdf") return drawCDF();
  return drawChart(mode);
}

// ---- SVG line chart: x = category {1,3,5}, one line per variable ---------
function drawChart(mode) {
  const p = panels[mode];
  if (!p || p.el.style.display === "none") return;
  const data = getData().data;
  if (!data) {
    p.title.textContent = mode === "epr" ? "Uncertainty — EPR" : "Sensitivity — SRC";
    p.body.innerHTML = "<p class='note'>Powell Kaplan–DeMaria field: exact precompute " +
      "scheduled after the UA run. Switch land effect to None/Roughness, or use " +
      "Holland/Willoughby, to run the analysis now.</p>";
    return;
  }
  const isEPR = mode === "epr";
  const cats = [1, 3, 5];
  // faithful EPR (wind-variance based) only for Powell + wind response; else SRC^2
  const model = analysisState.cache.model;
  const epr = (isEPR && model === "powell" && responseVar() === "wind")
    ? faithfulEPR() : null;
  // GPR variance-based (Sobol total-effect) EPR when the GPR metamodel is selected
  const useSobol = isEPR && currentMetamodel() === "gpr" && state.metamodels;

  // values per var per cat
  const series = {};
  let vmin = 0, vmax = 0;
  for (const v of SA_VARS) {
    series[v] = cats.map(c => {
      const src = data["cat" + c].src[v];
      let val;
      if (!isEPR) val = src;
      else if (useSobol) {
        const e = mmEntry(responseVar(), "cat" + c);
        val = e ? e.sobol.ST[SA_VARS.indexOf(v)] * 100 : src * src * 100;
      } else val = epr ? epr["cat" + c][v] : src * src * 100;
      vmin = Math.min(vmin, val); vmax = Math.max(vmax, val);
      return val;
    });
  }
  if (isEPR) { vmin = 0; }
  const pad = (vmax - vmin) * 0.1 || 1; vmax += pad; vmin -= (isEPR ? 0 : pad);

  const W = 440, H = 300, mL = 48, mR = 12, mT = 14, mB = 30;
  const x = c => mL + (cats.indexOf(c) / (cats.length - 1)) * (W - mL - mR);
  const yv = v => mT + (1 - (v - vmin) / (vmax - vmin)) * (H - mT - mB);

  let svg = `<svg viewBox="0 0 ${W} ${H}" width="100%" >`;
  // axes
  svg += `<line x1="${mL}" y1="${yv(0)}" x2="${W - mR}" y2="${yv(0)}" stroke="#94a3b8" stroke-dasharray="3 3"/>`;
  svg += `<line x1="${mL}" y1="${mT}" x2="${mL}" y2="${H - mB}" stroke="#64748b"/>`;
  // y ticks
  for (let t = 0; t <= 4; t++) {
    const val = vmin + (t / 4) * (vmax - vmin);
    svg += `<text x="${mL - 5}" y="${yv(val) + 3}" text-anchor="end" class="ax">${val.toFixed(isEPR ? 0 : 2)}${isEPR ? "%" : ""}</text>`;
  }
  // x labels
  cats.forEach(c => svg += `<text x="${x(c)}" y="${H - mB + 16}" text-anchor="middle" class="ax">Cat ${c}</text>`);
  // lines + points
  for (const v of SA_VARS) {
    const pts = cats.map(c => `${x(c)},${yv(series[v][cats.indexOf(c)])}`).join(" ");
    svg += `<polyline points="${pts}" fill="none" stroke="${VAR_COLORS[v]}" stroke-width="2"/>`;
    cats.forEach((c, i) => {
      const val = series[v][i];
      svg += `<circle cx="${x(c)}" cy="${yv(val)}" r="3" fill="${VAR_COLORS[v]}"><title>${v} Cat${c}: ${val.toFixed(isEPR ? 1 : 3)}${isEPR ? "%" : ""}</title></circle>`;
    });
  }
  svg += `</svg>`;

  // legend + r2
  const legend = SA_VARS.map(v =>
    `<span class="lgi"><span style="background:${VAR_COLORS[v]}"></span>${v}</span>`).join("");
  const r2 = cats.map(c => `Cat${c} R²=${data["cat" + c].r2.toFixed(2)}`).join(" · ");
  const title = isEPR ? "Uncertainty — EPR (% of output variance)"
                      : "Sensitivity — SRC (standardized regression coeff.)";
  p.title.textContent = title;
  const metricTxt = responseVar() === "tlc"
    ? "%TLC (loss cost, % of $68.2M exposure)" : "mean peak wind over 682 land pts";
  p.body.innerHTML =
    svg + `<div class="legend2">${legend}</div>` +
    `<p class="note">${analysisState.cache.model} · metric: ${metricTxt}` +
    ` · land effect: ${analysisState.cache.land}<br>${r2}` +
    (isEPR ? (useSobol ? "<br>EPR = Sobol total-effect index Sₜᵢ (GPR metamodel, default config)"
                       : epr ? "<br>EPR = Var(Y|Xᵢ)/Var(Y) from UA sheets (faithful, marine)"
                             : "<br>EPR ≈ SRC² (variance share)") : "") + "</p>";
}

// ---- interaction profiler (JMP-style prediction profiler) ----------------
// A row of partial-dependence curves (one per variable) with reference-point
// sliders. Moving a slider re-renders the OTHER curves; a curve whose slope
// changes is interacting with the moved variable.
function drawProfiler() {
  const p = panels["prof"];
  if (!p || p.el.style.display === "none") return;
  const model = document.getElementById("model").value;
  const cat = "cat" + document.getElementById("category").value;
  p.title.textContent = "Interaction Profiler — metamodel";
  const mm = buildMetamodel(model, cat);
  if (!mm) {
    const why = currentMetamodel() === "rsm"
      ? "Metric unavailable — Powell Kaplan–DeMaria precompute pending, or (for %TLC) " +
        "the vulnerability curve is not loaded. Try Holland/Willoughby, or land effect None/Roughness."
      : "GPR / neural-net metamodels not loaded — run pipeline/fit_metamodels.py to " +
        "generate outputs/web/metamodels.json.";
    p.body.innerHTML = `<p class='note'>${why}</p>`;
    return;
  }
  profilerState.mm = mm;
  profilerState.ref = mm.stats.map(s => s.m);       // reference point = input means
  buildProfilerDOM();
}

function buildProfilerDOM() {
  const p = panels["prof"], mm = profilerState.mm, ref = profilerState.ref;
  const model = document.getElementById("model").value;
  const catN = document.getElementById("category").value;
  const metricTxt = responseVar() === "tlc" ? "%TLC" : "mean peak wind (mph)";
  let sliders = "";
  mm.stats.forEach((s, i) => {
    sliders += `<label class="prof-sl"><span style="color:${VAR_COLORS[s.v]}">${s.v}</span>` +
      `<input type="range" data-i="${i}" min="${s.min}" max="${s.max}" ` +
      `step="${((s.max - s.min) / 100) || 0.01}" value="${ref[i]}"/>` +
      `<b data-v="${i}">${ref[i].toFixed(2)}</b></label>`;
  });
  const cvTxt = mm.cv != null ? ` cv=${mm.cv.toFixed(2)}` : "";
  p.body.innerHTML =
    `<div class="prof-grid"></div>` +
    `<div class="prof-sliders">${sliders}</div>` +
    `<p class="note">${METAMODEL_LABEL[mm.type]} · ${model} · Cat ${catN} · Y = ${metricTxt} · ` +
    `R²=${mm.r2.toFixed(2)}${cvTxt} · Y range [${mm.ymin.toFixed(2)}, ${mm.ymax.toFixed(2)}]${mm.note}` +
    `<br>Move a slider: another variable's curve changing slope = its interaction with the moved variable.</p>`;
  p.body.querySelectorAll(".prof-sliders input").forEach(inp => {
    inp.addEventListener("input", () => {
      const idx = +inp.dataset.i;
      profilerState.ref[idx] = parseFloat(inp.value);
      const lbl = p.body.querySelector(`b[data-v="${idx}"]`);
      if (lbl) lbl.textContent = profilerState.ref[idx].toFixed(2);
      updateProfilerPlots();      // redraw curves only — sliders stay intact mid-drag
    });
  });
  updateProfilerPlots();
}

function updateProfilerPlots() {
  const p = panels["prof"], mm = profilerState.mm, ref = profilerState.ref;
  if (!p || !mm) return;
  const grid = p.body.querySelector(".prof-grid");
  if (!grid) return;
  const NS = 40, W = 150, H = 96, mL = 26, mR = 6, mT = 8, mB = 16;
  const ylo = mm.ymin, yhi = mm.ymax, yspan = (yhi - ylo) || 1;
  const clampY = y => Math.max(ylo, Math.min(yhi, y));
  let html = "";
  mm.stats.forEach((s, i) => {
    const xlo = s.min, xhi = s.max, xspan = (xhi - xlo) || 1;
    const xpix = x => mL + ((x - xlo) / xspan) * (W - mL - mR);
    const ypix = y => mT + (1 - (y - ylo) / yspan) * (H - mT - mB);
    let pts = "";
    for (let k = 0; k <= NS; k++) {
      const xv = xlo + (k / NS) * xspan;
      const raw = ref.slice(); raw[i] = xv;
      pts += `${xpix(xv).toFixed(1)},${ypix(clampY(mm.predict(raw))).toFixed(1)} `;
    }
    const refY = clampY(mm.predict(ref));
    let svg = `<svg viewBox="0 0 ${W} ${H}" width="100%">`;
    svg += `<line x1="${mL}" y1="${ypix(ylo)}" x2="${W - mR}" y2="${ypix(ylo)}" stroke="#cbd5e1"/>`;
    svg += `<line x1="${mL}" y1="${mT}" x2="${mL}" y2="${H - mB}" stroke="#cbd5e1"/>`;
    svg += `<line x1="${xpix(ref[i]).toFixed(1)}" y1="${mT}" x2="${xpix(ref[i]).toFixed(1)}" y2="${H - mB}" stroke="#94a3b8" stroke-dasharray="2 2"/>`;
    svg += `<polyline points="${pts.trim()}" fill="none" stroke="${VAR_COLORS[s.v]}" stroke-width="2"/>`;
    svg += `<circle cx="${xpix(ref[i]).toFixed(1)}" cy="${ypix(refY).toFixed(1)}" r="2.5" fill="${VAR_COLORS[s.v]}"/>`;
    svg += `<text x="${mL}" y="${H - 3}" class="ax">${s.v}</text>`;
    svg += `</svg>`;
    html += `<div class="prof-cell">${svg}</div>`;
  });
  grid.innerHTML = html;
}

// ---- empirical CDF of %TLC(i) over the 100 vectors (ROA Figure 5) ---------
function drawCDF() {
  const p = panels["cdf"];
  if (!p || p.el.style.display === "none") return;
  const model = document.getElementById("model").value;
  const cat = "cat" + document.getElementById("category").value;
  const catN = document.getElementById("category").value;
  p.title.textContent = "Empirical CDF — %TLC";
  const recs = state.inputs[cat];
  const vals = recs.map((_, i) => pctTLC(computeWindFor(model, cat, i)));
  if (vals.some(v => v == null)) {
    p.body.innerHTML = "<p class='note'>%TLC unavailable — vulnerability curve not " +
      "loaded, or Powell Kaplan–DeMaria precompute pending.</p>";
    return;
  }
  const sorted = vals.slice().sort((a, b) => a - b), n = sorted.length;
  const xlo = sorted[0], xhi = sorted[n - 1], xspan = (xhi - xlo) || 1;
  const W = 440, H = 280, mL = 44, mR = 12, mT = 12, mB = 34;
  const xpix = x => mL + ((x - xlo) / xspan) * (W - mL - mR);
  const ypix = q => mT + (1 - q) * (H - mT - mB);
  let path = `M ${xpix(xlo).toFixed(1)} ${ypix(0).toFixed(1)}`;
  sorted.forEach((v, i) => {
    path += ` L ${xpix(v).toFixed(1)} ${ypix(i / n).toFixed(1)}` +
            ` L ${xpix(v).toFixed(1)} ${ypix((i + 1) / n).toFixed(1)}`;
  });
  let svg = `<svg viewBox="0 0 ${W} ${H}" width="100%">`;
  for (let t = 0; t <= 4; t++) {
    const q = t / 4;
    svg += `<line x1="${mL}" y1="${ypix(q)}" x2="${W - mR}" y2="${ypix(q)}" stroke="#e2e8f0" stroke-dasharray="2 3"/>`;
    svg += `<text x="${mL - 5}" y="${ypix(q) + 3}" text-anchor="end" class="ax">${q.toFixed(2)}</text>`;
  }
  for (let t = 0; t <= 4; t++) {
    const xv = xlo + (t / 4) * xspan;
    svg += `<text x="${xpix(xv)}" y="${H - mB + 16}" text-anchor="middle" class="ax">${xv.toFixed(2)}</text>`;
  }
  svg += `<line x1="${mL}" y1="${ypix(0)}" x2="${W - mR}" y2="${ypix(0)}" stroke="#64748b"/>`;
  svg += `<line x1="${mL}" y1="${mT}" x2="${mL}" y2="${H - mB}" stroke="#64748b"/>`;
  svg += `<path d="${path}" fill="none" stroke="#2563eb" stroke-width="2"/>`;
  svg += `</svg>`;
  const mu = mean(sorted), md = sorted[Math.floor(n / 2)];
  p.body.innerHTML = svg +
    `<p class="note">${model} · Cat ${catN} · %TLC over ${n} input vectors` +
    `<br>x = %TLC (loss cost, % of $68.2M exposure) · mean ${mu.toFixed(3)}% · median ${md.toFixed(3)}%</p>`;
}

// ---- compare metamodels: Linear (RSM) vs GPR vs NN -----------------------
function drawCompare() {
  const p = panels["cmp"];
  if (!p || p.el.style.display === "none") return;
  const model = document.getElementById("model").value;
  const cat = "cat" + document.getElementById("category").value;
  const catN = document.getElementById("category").value;
  p.title.textContent = "Compare metamodels — Linear / GPR / NN";
  const types = ["rsm", "gpr", "mlp"];
  const mms = {};
  types.forEach(t => { const m = buildMetamodel(model, cat, t); if (m) mms[t] = m; });
  if (!mms.rsm && !mms.gpr && !mms.mlp) {
    p.body.innerHTML = "<p class='note'>No metamodel available — for GPR/NN run " +
      "pipeline/fit_metamodels.py to create metamodels.json.</p>";
    return;
  }
  const have = types.filter(t => mms[t]);
  const stats = (mms.rsm || mms.gpr || mms.mlp).stats;
  const ref = stats.map(s => s.m);
  const ylo = Math.min(...have.map(t => mms[t].ymin));
  const yhi = Math.max(...have.map(t => mms[t].ymax));
  const yspan = (yhi - ylo) || 1;
  const clampY = y => Math.max(ylo, Math.min(yhi, y));
  const NS = 40, W = 150, H = 96, mL = 26, mR = 6, mT = 8, mB = 16;

  let gridHtml = "";
  stats.forEach((s, i) => {
    const xlo = s.min, xhi = s.max, xspan = (xhi - xlo) || 1;
    const xpix = x => mL + ((x - xlo) / xspan) * (W - mL - mR);
    const ypix = y => mT + (1 - (y - ylo) / yspan) * (H - mT - mB);
    let svg = `<svg viewBox="0 0 ${W} ${H}" width="100%">`;
    svg += `<line x1="${mL}" y1="${ypix(ylo)}" x2="${W - mR}" y2="${ypix(ylo)}" stroke="#cbd5e1"/>`;
    svg += `<line x1="${mL}" y1="${mT}" x2="${mL}" y2="${H - mB}" stroke="#cbd5e1"/>`;
    have.forEach(t => {
      let pts = "";
      for (let k = 0; k <= NS; k++) {
        const xv = xlo + (k / NS) * xspan;
        const raw = ref.slice(); raw[i] = xv;
        pts += `${xpix(xv).toFixed(1)},${ypix(clampY(mms[t].predict(raw))).toFixed(1)} `;
      }
      svg += `<polyline points="${pts.trim()}" fill="none" stroke="${METAMODEL_COLOR[t]}" stroke-width="1.8"/>`;
    });
    svg += `<text x="${mL}" y="${H - 3}" class="ax">${s.v}</text></svg>`;
    gridHtml += `<div class="prof-cell">${svg}</div>`;
  });

  const legend = have.map(t =>
    `<span class="lgi"><span style="background:${METAMODEL_COLOR[t]}"></span>${METAMODEL_LABEL[t]}</span>`).join("");
  const rows = have.map(t =>
    `<tr><td style="color:${METAMODEL_COLOR[t]}">${METAMODEL_LABEL[t]}</td>` +
    `<td>${mms[t].r2.toFixed(3)}</td><td>${mms[t].cv != null ? mms[t].cv.toFixed(3) : "—"}</td></tr>`).join("");
  const metricTxt = responseVar() === "tlc" ? "%TLC" : "mean peak wind (mph)";
  p.body.innerHTML =
    `<div class="prof-grid">${gridHtml}</div>` +
    `<div class="legend2">${legend}</div>` +
    `<table class="cmp-tbl"><tr><th>metamodel</th><th>R²</th><th>5-fold CV R²</th></tr>${rows}</table>` +
    `<p class="note">${model} · Cat ${catN} · Y = ${metricTxt} · curves at the input means` +
    `${mms.gpr ? mms.gpr.note : (mms.mlp ? mms.mlp.note : "")}` +
    `<br>Overlaid partial-dependence: where Linear diverges from GPR/NN it is missing curvature/interaction.</p>`;
}

function redrawOpenPanels(modes) {
  (modes || Object.keys(panels)).forEach(mode => {
    if (panels[mode] && panels[mode].el.style.display !== "none") renderPanel(mode);
  });
}

// ---- mouse-wheel zoom on every plot the app produces (SVG viewBox) --------
// Applies to all .ap-body svgs: the analysis charts (SRC/EPR/profiler/compare/
// CDF) AND the windfield popup (isotachs + time series) — both share .ap-body.
// The Leaflet map is untouched (its svg is in .leaflet-overlay-pane). Wheel up =
// zoom in, wheel down = out, double-click = reset; each svg zooms independently.
// Capture phase is REQUIRED: Leaflet's disableScrollPropagation on each floating
// panel stops the wheel event during bubble, so a bubble-phase listener never
// sees it — capturing runs first, top-down.
const PLOT_MAX_ZOOM = 12;

function setupPlotZoom() {
  document.addEventListener("wheel", e => {
    const svg = e.target.closest && e.target.closest(".ap-body svg");
    if (!svg) return;
    const vb = svg.getAttribute("viewBox");
    if (!vb) return;
    e.preventDefault();
    const [x, y, w, h] = vb.split(/[ ,]+/).map(Number);
    if (!svg._vb0) svg._vb0 = vb;
    const w0 = +svg._vb0.split(/[ ,]+/)[2];
    const r = svg.getBoundingClientRect();
    const px = (e.clientX - r.left) / r.width, py = (e.clientY - r.top) / r.height;
    const cx = x + px * w, cy = y + py * h;
    const k = e.deltaY < 0 ? 0.85 : 1 / 0.85;
    const nw = Math.min(w0, Math.max(w0 / PLOT_MAX_ZOOM, w * k));
    const nh = h * (nw / w);
    svg.setAttribute("viewBox", `${cx - px * nw} ${cy - py * nh} ${nw} ${nh}`);
  }, { passive: false, capture: true });

  document.addEventListener("dblclick", e => {
    const svg = e.target.closest && e.target.closest(".ap-body svg");
    if (svg && svg._vb0) svg.setAttribute("viewBox", svg._vb0);
  }, { capture: true });

  // drag to pan a zoomed plot. mousedown is captured (Leaflet stops it on panels
  // during bubble); the pan is clamped to the original viewBox, so dragging does
  // nothing until you have zoomed in.
  let pan = null;
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  document.addEventListener("mousedown", e => {
    const svg = e.target.closest && e.target.closest(".ap-body svg");
    if (!svg) return;
    const vb = svg.getAttribute("viewBox");
    if (!vb) return;
    const [x, y, w, h] = vb.split(/[ ,]+/).map(Number);
    const [bx, by, bw, bh] = (svg._vb0 || vb).split(/[ ,]+/).map(Number);
    const r = svg.getBoundingClientRect();
    pan = { svg, sx: e.clientX, sy: e.clientY, x, y, w, h,
            ux: w / r.width, uy: h / r.height, bx, by, bw, bh };
    svg.style.cursor = "grabbing";
    e.preventDefault();
  }, { capture: true });
  document.addEventListener("mousemove", e => {
    if (!pan) return;
    const nx = clamp(pan.x - (e.clientX - pan.sx) * pan.ux, pan.bx, pan.bx + pan.bw - pan.w);
    const ny = clamp(pan.y - (e.clientY - pan.sy) * pan.uy, pan.by, pan.by + pan.bh - pan.h);
    pan.svg.setAttribute("viewBox", `${nx} ${ny} ${pan.w} ${pan.h}`);
  });
  document.addEventListener("mouseup", () => {
    if (pan) { pan.svg.style.cursor = ""; pan = null; }
  });
}

function setupAnalysis() {
  setupPlotZoom();
  document.getElementById("btnSRC").addEventListener("click", () => openPanel("src"));
  document.getElementById("btnEPR").addEventListener("click", () => openPanel("epr"));
  document.getElementById("btnProf").addEventListener("click", () => openPanel("prof"));
  document.getElementById("btnCompare").addEventListener("click", () => openPanel("cmp"));
  document.getElementById("btnCDF").addEventListener("click", () => openPanel("cdf"));
  // model / land effect / response change -> invalidate cache + redraw open panels
  ["model", "landEffect", "response"].forEach(id =>
    document.getElementById(id).addEventListener("change", () => {
      analysisState.cache = null;
      redrawOpenPanels();
    }));
  // metamodel choice drives profiler/compare (and EPR Sobol); no cache invalidation needed
  document.getElementById("metamodel").addEventListener("change",
    () => redrawOpenPanels(["prof", "cmp", "epr"]));
  // category change affects the per-category panels only (SRC/EPR span all cats)
  document.getElementById("category").addEventListener("change",
    () => redrawOpenPanels(["prof", "cmp", "cdf"]));
}
