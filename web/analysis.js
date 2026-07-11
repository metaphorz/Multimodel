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
// saMethod: which sensitivity method the Sensitivity panel shows ('src' | 'sobol')
const analysisState = { mode: null, cache: null, saMethod: "src" };
const profilerState = {           // metamodel + reference point + view/scale state
  mm: null, ref: null, view: "profiler",      // view: profiler | matrix
  scale: "footprint",             // footprint (metamodel) | point (direct simulation)
  pt: null, picking: false, marker: null,     // single-point selection (map click)
  pred: null,                     // active predictor {predict, ymin, ymax, available}
};

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
  return el ? el.value : "wind";          // 'wind' | 'windmax' | 'tlc' | 'dwell' | ...
}

// Human labels for the response Y, in one place so every panel says the same thing.
// `long` spells out that Y is an aggregate over the grid points of ONE input vector.
const RESP_LABEL = {
  wind:    { short: "mean peak wind (mph)", long: "mean peak wind over 682 land pts" },
  windmax: { short: "max peak wind (mph)",  long: "max peak wind over 682 land pts" },
  tlc:     { short: "%TLC",                 long: "%TLC (loss cost, % of exposure)" },
};
function respLabel(kind) {
  const e = RESP_LABEL[responseVar()];
  return e ? e[kind] : responseVar();
}

// wind speed (mph, surface) at which structural damage begins to accumulate
// (HAZUS-style ~40 mph). Below this the storm does no loss; above it, loss
// accrues for as long as the wind stays elevated (the meteorologist's point:
// loss is accumulated over the passage, not a function of the single peak).
const DAMAGE_THRESHOLD_MPH = 40;

// duration-of-exposure diagnostics from a per-point wind time series {t,w}:
//   dwell  = hours the surface wind stays at/above the damage threshold
//   dosage = ∫ (V − threshold)+ dt  (mph·h) — the excess-wind "area" that
//            accumulates while the eye passes (dwell weighted by how far over
//            threshold the wind is). Both re-express VT / size sensitivity that
//            a peak-only metric collapses. Location-level (single point) only.
function durationMetrics(ts) {
  const dt = PHYS.T_DT;                    // hours per sample
  let hours = 0, dosage = 0;
  for (const v of ts.w) if (v >= DAMAGE_THRESHOLD_MPH) {
    hours += dt; dosage += (v - DAMAGE_THRESHOLD_MPH) * dt;
  }
  return { hours, dosage };
}

// Integrated Kinetic Energy at ONE grid cell (Powell & Reinhold 2007 energy
// density ½ρV², but integrated in TIME at a fixed cell rather than over area).
// A 1-m-deep surface layer over the 3-mi×3-mi cell holds instantaneous energy
// E(t)=½ρV(t)²·A_cell·h (J) whenever V≥TS force (≈40 mph). Reported:
//   integ = ∫ E dt   over V≥V0  (TJ·h — energy accumulated over the passage)
//   peak  = max E     over V≥V0  (TJ   — snapshot; behaves like peak wind)
// V0 = DAMAGE_THRESHOLD_MPH (40 mph doubles as tropical-storm force here).
const IKE_CELL_M = 3 * PHYS.MILE_M;                 // 3-mi cell side (m)
const IKE_AREA = IKE_CELL_M * IKE_CELL_M;           // cell area (m²)
const IKE_DEPTH_M = 1;                              // surface-layer depth (m)
function ikeMetrics(ts) {
  const dt = PHYS.T_DT;                             // hours per sample
  const k = 0.5 * PHYS.RHO * IKE_AREA * IKE_DEPTH_M; // J per (m/s)² in this cell
  let integ = 0, peak = 0;
  for (const vmph of ts.w) {
    if (vmph < DAMAGE_THRESHOLD_MPH) continue;
    const vms = vmph / PHYS.MS_TO_MPH;
    const e = k * vms * vms;                        // instantaneous IKE (J)
    integ += e * dt;                                // J·h
    if (e > peak) peak = e;                         // J
  }
  return { integ: integ / 1e12, peak: peak / 1e12 };  // TJ·h, TJ
}

// ---- rate-integrated (duration-accumulated) loss --------------------------
// The HAZUS/ARA MDR curve is single-valued in peak gust, so peak-based %LC ignores
// how LONG a point stays in damaging wind. This accumulates damage every timestep
// above the threshold at the instantaneous-MDR rate (the meteorologist's request):
//   A(x) = ∫_{V≥V0} MDR(V(t)) dt    (MDR·hours)
//   %LC_acc = 100·min(A/τ, MDR_max)
// τ is a per-point reference exposure time, calibrated so the 100-storm mean of the
// accumulated loss equals the mean of the peak-based %LC — no net bias vs HAZUS, a
// duration REDISTRIBUTION (long-dwell/slow storms up, fast storms down). MDR_max is
// the curve's plateau (a structure can't exceed full damage).
function accMDRIntegral(ts) {
  const dt = PHYS.T_DT;
  let A = 0;
  for (const v of ts.w) if (v >= DAMAGE_THRESHOLD_MPH) { const m = mdrAt(v); if (m != null) A += m * dt; }
  return A;                                 // MDR·hours
}
function mdrCeiling() {
  if (typeof damageModelSel === "function" && damageModelSel() === "logistic") return 1.0;
  return state.vuln ? state.vuln.mdr[state.vuln.mdr.length - 1] : 0.6;
}

// per-point calibration cache for τ (see above). Recomputed when the vertex, model,
// or land settings change.
let _accCal = null;
function accCalibration(model, cat, pt) {
  const rough = document.getElementById("landRoughness").checked;
  const decay = document.getElementById("landDecay").checked;
  const dmg = damageModelSel() === "logistic"
    ? "logistic:" + Object.values(logisticParams()).join(",") : "vickery";
  const key = [model, cat, pt.idx, "r" + rough, "d" + decay, dmg].join("|");
  if (_accCal && _accCal.key === key) return _accCal;
  const recs = state.inputs[cat], mdrMax = mdrCeiling();
  const A = [], peakMDR = [];
  for (const rec of recs) {
    const ts = pointSeriesAt(model, rec, pt);
    A.push(accMDRIntegral(ts));
    let peak = 0; for (const w of ts.w) if (w > peak) peak = w;
    peakMDR.push(mdrAt(peak) || 0);
  }
  const target = mean(peakMDR);
  let tau = 1;
  if (target > 1e-9 && A.some(a => a > 0)) {
    // meanD(τ) = mean(min(A_i/τ, mdrMax)) is decreasing in τ; bisect (log scale)
    const meanD = t => mean(A.map(a => Math.min(a / t, mdrMax)));
    let lo = 1e-6, hi = 1e6;
    for (let it = 0; it < 60; it++) { const mid = Math.sqrt(lo * hi);
      if (meanD(mid) > target) lo = mid; else hi = mid; }
    tau = Math.sqrt(lo * hi);
  }
  _accCal = { key, tau, mdrMax };
  return _accCal;
}

// these responses live only at single-point scale — the footprint stores hold
// precomputed peak wind per vertex, with no time series to integrate.
function isPointOnlyResp() {
  const r = responseVar();
  return r === "dwell" || r === "dosage" || r === "ike" || r === "ikepeak" || r === "accloss";
}

// %TLC(i) = TLC(i)/total exposure as percent = 100 * mean MDR over land points
// (TLC = Σ_land MDR·$100k, total exposure = 682·$100k = $68.2M; ROA definition).
function pctTLC(wind) {
  if (!wind || typeof wind === "string" || !state.vuln) return null;
  let loss = 0, any = false;
  state.grid.points.forEach((p, i) => {
    if (p.land) { const m = mdrAt(wind[i]); if (m != null) { loss += m * exposureAt(i); any = true; } }
  });
  return any ? loss / totalExposure() * 100 : null;   // value-weighted under Census
}

// scalar output metric per vector for the current Response selector
function outputMetric(model, cat, vIdx) {
  const wind = computeWindFor(model, cat, vIdx);
  if (!wind || typeof wind === "string") return null;     // null / "kd-pending"
  const r = responseVar();
  if (r === "tlc") return pctTLC(wind);
  if (r === "windmax") return landMaxWind(wind);
  return landMeanWind(wind);
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
  const land = (document.getElementById("landRoughness").checked ? "r" : "") +
               (document.getElementById("landDecay").checked ? "d" : "");
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
  const y = state.inputs[cat].map((_, i) => outputMetric(model, cat, i));
  if (y.some(v => v == null)) return null;         // metric unavailable yet
  return fitRSMFromY(cat, y);
}

// fit the 2nd-order surface for an explicit per-vector response y (same math as
// fitRSM, but the caller supplies y). Used for Powell single-point sensitivity,
// where y is one vertex's peak wind across the 100 vectors.
function fitRSMFromY(cat, y) {
  const recs = state.inputs[cat], n = recs.length;
  if (!y || y.some(v => v == null)) return null;
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

// Powell single-point sensitivity: fit a per-vertex 2nd-order RSM from the 100
// precomputed peak winds at vertex idx (Powell has no live field to re-simulate).
// Uses computeWindFor, so it respects the current land settings (marine vs KD
// decay, roughness). Returns an RSM fit predicting peak wind at the vertex, null
// if the field isn't loaded (Powell / KD precompute pending).
function pointRSMForPoint(model, cat, idx) {
  const y = state.inputs[cat].map((_, i) => {
    const w = computeWindFor(model, cat, i);
    return (!w || typeof w === "string") ? null : w[idx];
  });
  return fitRSMFromY(cat, y);
}

// ---- Phase B: evaluate precomputed GPR / NN metamodels (default config) ---
// Training happens offline (pipeline/fit_metamodels.py -> metamodels.json);
// the browser only evaluates. GPR/NN are fit for the DEFAULT config only
// (Powell + roughness, "Option A"); Linear/RSM stays live for every config.
const METAMODEL_LABEL = { rsm: "Linear (RSM)", gpr: "Gaussian process", mlp: "Neural net" };
const METAMODEL_COLOR = { rsm: "#2563eb", gpr: "#059669", mlp: "#b45309" };
// Low/high encoding across the Statistics plots: a temperature metaphor, so red
// always reads as the high end of a scale and blue as the low end.
const LOW_COLOR = "#3b82f6";    // blue  = low  (input at min, weak interaction)
const HIGH_COLOR = "#ef4444";   // red   = high (input at max, strong interaction)

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
  // duration responses have no precomputed (GPR/MLP) metamodel and are single-point
  // only; the live RSM fit still yields the input-stat scaffold the point-scale
  // profiler needs (its footprint surface is gated off below, never shown).
  if (isPointOnlyResp()) type = "rsm";
  // the precomputed GPR/MLP loss metamodels encode the Vickery curve, so under a
  // non-Vickery damage model the loss response must use the live RSM (which recomputes
  // %TLC through the current mdrAt); the wind response is damage-model-independent.
  if (responseVar() === "tlc" && damageModelSel() !== "vickery") type = "rsm";
  if (type === "rsm") {
    const fit = fitRSM(model, cat);
    if (!fit) return null;
    return { type, stats: fit.stats, predict: raw => rsmPredict(fit, raw),
             r2: fit.r2, ymin: fit.ymin, ymax: fit.ymax, note: "" };
  }
  const entry = mmEntry(responseVar(), cat);
  if (!entry) return null;                    // metamodels.json not loaded
  const block = entry[type];
  const rough = document.getElementById("landRoughness").checked;
  const decay = document.getElementById("landDecay").checked;
  const isDefault = (model === "powell" && rough && !decay);
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
    // size so the full plot + axis titles + legend + note fit without resizing
    const big = mode === "prof" || mode === "cmp";
    panels[mode].el.style.width = big ? "580px" : (mode === "fin" ? "500px" : "480px");
    // "src" is taller than "epr": its Sobol' view carries the method tabs plus a
    // multi-line note (sum S1, resolved interactions, independence caveat).
    panels[mode].el.style.height = big ? "580px"
      : (mode === "fin" ? "600px" : (mode === "src" ? "580px" : "480px"));
  }
  panels[mode].el.style.display = "flex";
  bringFront(panels[mode].el);
  if (document.getElementById("model").value === "powelldyn") {
    panels[mode].body.innerHTML = "<p class='note'>Analysis panels use the steady " +
      "Powell metamodels / live Holland&ndash;Willoughby fields. Powell (dynamic) is a " +
      "peaks-only precompute &mdash; switch to Powell (steady) for analysis.</p>";
    return;
  }
  panels[mode].body.innerHTML = "<p class='note'>Computing…</p>";
  setTimeout(() => renderPanel(mode), 20);
}

// dispatch a panel to its renderer
function renderPanel(mode) {
  if (mode === "prof") return drawProfiler();
  if (mode === "cmp") return drawCompare();
  if (mode === "cdf") return drawCDF();
  if (mode === "fin") return drawFinancial();
  return drawChart(mode);
}

// ---- SVG line chart: x = category {1,3,5}, one line per variable ---------
function drawChart(mode) {
  const p = panels[mode];
  if (!p || p.el.style.display === "none") return;
  if (isPointOnlyResp()) {                    // footprint SRC/EPR has no time series
    p.title.textContent = mode === "epr" ? "Uncertainty — EPR" : "Sensitivity — SRC";
    p.body.innerHTML = "<p class='note'>Location-level metrics (dwell / dosage / IKE) are " +
      "location-level. Open the Interaction Profiler, set Scale = Single point, and " +
      "pick a vertex to see how dwell/dosage responds to each storm parameter. " +
      "Footprint-wide SRC/EPR use precomputed peak-wind fields, which carry no " +
      "time series to integrate.</p>";
    return;
  }
  const data = getData().data;
  if (!data) {
    p.title.textContent = mode === "epr" ? "Uncertainty — EPR" : "Sensitivity — SRC";
    p.body.innerHTML = "<p class='note'>Powell Kaplan–DeMaria field: exact precompute " +
      "scheduled after the UA run. Switch land effect to None/Roughness, or use " +
      "Holland/Willoughby, to run the analysis now.</p>";
    return;
  }
  const isEPR = mode === "epr";
  // Sensitivity offers two methods: SRC (first-order regression) and Sobol'
  // (variance-based). Sobol' lives here, not under EPR: EPR is defined as SRC^2,
  // a regression quantity, so folding a variance-based total index into it would
  // conflate two different definitions of "share of variance".
  if (!isEPR && analysisState.saMethod === "sobol") return drawSobol(p);

  const cats = [1, 3, 5];
  // faithful EPR (wind-variance based) only for Powell + wind response; else SRC^2
  const model = analysisState.cache.model;
  const epr = (isEPR && model === "powell" && responseVar() === "wind")
    ? faithfulEPR() : null;

  // values per var per cat
  const series = {};
  let vmin = 0, vmax = 0;
  for (const v of SA_VARS) {
    series[v] = cats.map(c => {
      const src = data["cat" + c].src[v];
      const val = !isEPR ? src : (epr ? epr["cat" + c][v] : src * src * 100);
      vmin = Math.min(vmin, val); vmax = Math.max(vmax, val);
      return val;
    });
  }
  if (isEPR) { vmin = 0; }
  const pad = (vmax - vmin) * 0.1 || 1; vmax += pad; vmin -= (isEPR ? 0 : pad);

  const W = 440, H = 312, mL = 58, mR = 12, mT = 14, mB = 44;
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
  // axis titles
  const yTitle = isEPR ? "EPR (% of output variance)" : "SRC (standardized regression coeff.)";
  const yMid = (mT + H - mB) / 2;
  svg += `<text x="13" y="${yMid}" text-anchor="middle" transform="rotate(-90 13 ${yMid})" class="ax">${yTitle}</text>`;
  svg += `<text x="${(mL + W - mR) / 2}" y="${H - 5}" text-anchor="middle" class="ax">Hurricane category</text>`;
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
  const metricTxt = respLabel("long");
  p.body.innerHTML =
    (isEPR ? "" : methodTabs()) +
    svg + `<div class="legend2">${legend}</div>` +
    `<p class="note">${analysisState.cache.model} · metric: ${metricTxt}` +
    ` · land effect: ${analysisState.cache.land}<br>${r2}` +
    (isEPR ? (epr ? "<br>EPR = Var(Y|Xᵢ)/Var(Y) from UA sheets (faithful, marine)"
                  : "<br>EPR ≈ SRC² (variance share)") : "") + "</p>";
  if (!isEPR) wireMethodTabs(p);
}

// ---- Sensitivity method switch: SRC (regression) vs Sobol' (variance) -----
function methodTabs() {
  const m = analysisState.saMethod || "src";
  const b = (id, lbl) => `<button class="sa-tab${m === id ? " on" : ""}" data-m="${id}">${lbl}</button>`;
  return `<div class="sa-tabs">${b("src", "SRC")}${b("sobol", "Sobol'")}</div>`;
}

function wireMethodTabs(p) {
  p.body.querySelectorAll(".sa-tab").forEach(btn =>
    btn.addEventListener("click", () => {
      analysisState.saMethod = btn.dataset.m;
      drawChart("src");
    }));
}

// ---- Sobol' indices: stacked bar per input, main effect + interaction -----
// Sobol' (2001) variance decomposition, estimated on the GPR emulator following
// Francom & Nachtsheim (2025): with only 100 model runs, direct estimation is
// hopeless, but the emulator makes it cheap. S1 = main effect; ST = total effect;
// ST - S1 = the share of variance the input carries only through interactions --
// exactly what SRC, being first-order and linear, cannot see.
function drawSobol(p) {
  p.title.textContent = "Sensitivity — Sobol' indices (variance-based)";
  const s = sobolBlock();
  if (!s) {
    p.body.innerHTML = methodTabs() +
      "<p class='note'>Sobol' indices are computed on a GPR emulator fit to the " +
      "Powell (steady) footprint with Surface roughness on (with or without Kaplan–DeMaria " +
      "decay). Select that model and land effect, or use SRC, which runs live for any " +
      "configuration.</p>";
    wireMethodTabs(p);
    return;
  }
  const n = SA_VARS.length;
  const W = 440, H = 312, mL = 52, mR = 12, mT = 16, mB = 52;
  const top = Math.max(1e-6, Math.max(...s.ST) * 1.25);
  const bw = (W - mL - mR) / n * 0.58;
  const xc = i => mL + (i + 0.5) * (W - mL - mR) / n;
  const yv = v => mT + (1 - v / top) * (H - mT - mB);

  let svg = `<svg viewBox="0 0 ${W} ${H}" width="100%">`;
  svg += `<line x1="${mL}" y1="${yv(0)}" x2="${W - mR}" y2="${yv(0)}" stroke="#64748b"/>`;
  svg += `<line x1="${mL}" y1="${mT}" x2="${mL}" y2="${H - mB}" stroke="#64748b"/>`;
  for (let t = 0; t <= 4; t++) {
    const val = (t / 4) * top;
    svg += `<line x1="${mL}" y1="${yv(val)}" x2="${W - mR}" y2="${yv(val)}" stroke="#94a3b8" stroke-dasharray="2 4" opacity="0.4"/>`;
    svg += `<text x="${mL - 5}" y="${yv(val) + 3}" text-anchor="end" class="ax">${val.toFixed(2)}</text>`;
  }
  SA_VARS.forEach((v, i) => {
    const S1 = s.S1[i], ST = s.ST[i], gap = Math.max(0, ST - S1);
    const c = VAR_COLORS[v];
    // solid = main effect S1; hatched cap = interaction (ST - S1)
    svg += `<rect x="${xc(i) - bw / 2}" y="${yv(S1)}" width="${bw}" height="${yv(0) - yv(S1)}" fill="${c}">` +
           `<title>${v}  S₁ (main) = ${S1.toFixed(3)}</title></rect>`;
    if (gap > 0) {
      svg += `<rect x="${xc(i) - bw / 2}" y="${yv(ST)}" width="${bw}" height="${yv(S1) - yv(ST)}" ` +
             `fill="${c}" opacity="0.32" stroke="${c}" stroke-dasharray="2 2">` +
             `<title>${v}  interaction Sₜ−S₁ = ${gap.toFixed(3)}${s.resolved && s.resolved[i] ? " (resolved)" : " (within noise)"}</title></rect>`;
    }
    // Monte-Carlo error bar on the total index
    const se = (s.se_ST && s.se_ST[i]) || 0;
    if (se > 0) {
      const x0 = xc(i);
      svg += `<line x1="${x0}" y1="${yv(ST - 2 * se)}" x2="${x0}" y2="${yv(ST + 2 * se)}" stroke="#334155" stroke-width="1"/>`;
      svg += `<line x1="${x0 - 3}" y1="${yv(ST + 2 * se)}" x2="${x0 + 3}" y2="${yv(ST + 2 * se)}" stroke="#334155"/>`;
      svg += `<line x1="${x0 - 3}" y1="${yv(ST - 2 * se)}" x2="${x0 + 3}" y2="${yv(ST - 2 * se)}" stroke="#334155"/>`;
    }
    svg += `<text x="${xc(i)}" y="${H - mB + 16}" text-anchor="middle" class="ax">${v}</text>`;
  });
  const yMid = (mT + H - mB) / 2;
  svg += `<text x="13" y="${yMid}" text-anchor="middle" transform="rotate(-90 13 ${yMid})" class="ax">Proportion of output variance</text>`;
  svg += `<text x="${(mL + W - mR) / 2}" y="${H - 6}" text-anchor="middle" class="ax">Form S-6 input</text>`;
  svg += `</svg>`;

  const inter = 1 - s.sum_S1;
  const hits = SA_VARS.filter((_, i) => s.resolved && s.resolved[i]);
  const metricTxt = respLabel("long");
  // The indices now track the land configuration (a separate emulator is fit for the
  // Kaplan-DeMaria-decayed field), so name the config the numbers actually describe.
  const cfg = "Powell + roughness" +
    (document.getElementById("landDecay").checked ? " + Kaplan–DeMaria decay" : "");
  p.body.innerHTML = methodTabs() + svg +
    `<div class="legend2">` +
    `<span class="lgi"><span style="background:#64748b"></span>S₁ main effect</span>` +
    `<span class="lgi"><span style="background:#64748b;opacity:.32"></span>Sₜ−S₁ interaction</span>` +
    `<span class="lgi">⌶ ±2 s.e.</span></div>` +
    `<p class="note">GPR emulator · ${cfg} · Cat ${document.getElementById("category").value} · metric: ${metricTxt}` +
    `<br>Σ S₁ = <b>${s.sum_S1.toFixed(3)}</b> — main effects alone explain ` +
    `${(s.sum_S1 * 100).toFixed(1)}% of output variance; the remaining ` +
    `<b>${(inter * 100).toFixed(1)}%</b> is interaction.` +
    `<br>Interactions resolved above Monte-Carlo noise: ${hits.length ? "<b>" + hits.join(", ") + "</b>" : "none"}.` +
    `<br>Sobol' (2001) on ${(s.n).toLocaleString()}×${s.reps} emulator samples; assumes independent inputs ` +
    `(max |r| = ${s.max_input_corr.toFixed(2)}, ${s.max_input_corr_pair}).</p>`;
  wireMethodTabs(p);
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

// direct single-point response (no metamodel): peak wind at the picked vertex for an
// input record, or its MDR (per-point %LC, in %) when the loss response is active.
// This preserves the true S-shape that a quadratic metamodel would round away.
// storm peak surface wind V0 = max of the storm-relative field, the denominator of
// the Kaplan–DeMaria intensity schedule (matches the precompute, windfield_grid.py:
// V0 = surf.max()). Memoized on the intensity/shape params (independent of VT and
// the vertex), so a single-variable profiler sweep recomputes the field only when
// those change. Holland/Willoughby only (Powell single-point uses the RSM path).
const _v0Cache = new Map();
function stormV0(model, rec, B) {
  const key = [model, rec.CP, rec.FFP, rec.Rmax, rec.WSP, rec.CF].join(",");
  let v0 = _v0Cache.get(key);
  if (v0 === undefined) {
    const field = stormRelativeField(model, rec, B, 90, 81);
    v0 = 0; for (const z of field.Z) if (z > v0) v0 = z;
    _v0Cache.set(key, v0);
    if (_v0Cache.size > 512) _v0Cache.clear();       // bound memory
  }
  return v0;
}

// live wind time series at the picked vertex for a record; applies surface
// roughness and/or Kaplan–DeMaria decay to match whatever land toggles are set (so
// single-point stays consistent with the footprint for any roughness×decay combo).
// Shared by pointResponse and the accumulated-loss calibration.
function pointSeriesAt(model, rec, pt) {
  const B = quantileToB(rec.WSP);
  const opts = {};
  if (document.getElementById("landRoughness").checked && state.roughness)
    opts.factor = state.roughness.factors[pt.idx];
  if (document.getElementById("landDecay").checked)
    opts.sched = intensitySchedule(stormV0(model, rec, B), rec.VT, state.grid.points);
  return pointTimeSeries(model, rec, B, pt.ew, pt.ns, opts);
}

function pointResponse(model, rec, pt) {
  const ts = pointSeriesAt(model, rec, pt);
  const resp = responseVar();
  if (resp === "dwell" || resp === "dosage") {                 // duration-aware loss proxy
    const d = durationMetrics(ts);
    return resp === "dwell" ? d.hours : d.dosage;
  }
  if (resp === "ike" || resp === "ikepeak") {                  // integrated kinetic energy
    const e = ikeMetrics(ts);
    return resp === "ike" ? e.integ : e.peak;
  }
  if (resp === "accloss") {                                    // duration-accumulated %LC
    const A = accMDRIntegral(ts);
    const cal = accCalibration(model, currentSelection().cat, pt);
    return Math.min(A / cal.tau, cal.mdrMax) * 100;
  }
  let peak = 0; for (const w of ts.w) if (w > peak) peak = w;
  if (resp === "tlc") { const m = mdrAt(peak); return m == null ? 0 : m * 100; }
  return peak;
}

// pick the predictor for the active scale. footprint -> metamodel; single point ->
// direct wind-field simulation at the picked vertex (live models, None/Roughness only).
function profilerPredictor() {
  const mm = profilerState.mm, model = document.getElementById("model").value;
  if (profilerState.scale !== "point") {
    if (isPointOnlyResp())
      return { available: false, why: "Location-level metrics (dwell / dosage / IKE) are " +
        "location-level — set Scale to Single point and pick one of the vertices. " +
        "Footprint fields are precomputed peak wind, with no time series to integrate." };
    return { available: true, predict: mm.predict, ymin: mm.ymin, ymax: mm.ymax };
  }
  if (!profilerState.pt)
    return { available: false, why: "Pick a point: click “Pick on map”, then a grid vertex." };

  const resp = responseVar();
  let predict, direct;
  if (model === "powell") {
    // Powell has no live field to re-simulate; fit a per-vertex 2nd-order RSM from
    // the 100 precomputed peaks at this vertex (peak wind / %LC only).
    if (resp !== "wind" && resp !== "tlc")
      return { available: false, why: "Powell single-point supports peak wind and %LC " +
        "(per-vertex metamodel); dwell / dosage / IKE need a live model (Holland/Willoughby)." };
    const cat = currentSelection().cat;
    const fit = pointRSMForPoint(model, cat, profilerState.pt.idx);
    if (!fit) return { available: false, why: "Powell field not loaded yet, or " +
      "Kaplan–DeMaria precompute pending." };
    predict = raw => { let peak = rsmPredict(fit, raw); if (peak < 0) peak = 0;
      if (resp === "tlc") { const m = mdrAt(peak); return m == null ? 0 : m * 100; } return peak; };
    direct = false;
  } else {
    // Holland/Willoughby: direct live simulation at the vertex, with roughness and/or
    // Kaplan–DeMaria decay applied per pointSeriesAt() to match the footprint.
    const keys = mm.stats.map(s => s.v);
    predict = raw => { const rec = {}; keys.forEach((k, i) => rec[k] = raw[i]);
                       return pointResponse(model, rec, profilerState.pt); };
    direct = true;
  }
  const means = mm.stats.map(s => s.m); let lo = Infinity, hi = -Infinity;
  mm.stats.forEach((s, i) => { for (let k = 0; k <= 12; k++) {
    const raw = means.slice(); raw[i] = s.min + (k / 12) * (s.max - s.min);
    const y = predict(raw); if (y < lo) lo = y; if (y > hi) hi = y; } });
  const pad = (hi - lo) * 0.06 || 1;
  return { available: true, predict, ymin: lo - pad, ymax: hi + pad, direct };
}

// set the single-point vertex from a map click, drop a crosshair marker, re-render
function profilerPickPoint(idx) {
  const q = state.grid.points[idx];
  profilerState.pt = { ew: q.ew, ns: q.ns, idx };
  profilerState.picking = false;
  document.getElementById("map").style.cursor = "";
  if (profilerState.marker) state.map.removeLayer(profilerState.marker);
  profilerState.marker = L.marker([q.lat, q.lon], { icon: L.divIcon({
    className: "prof-pin", html: "✕", iconSize: [22, 22], iconAnchor: [11, 11] }) }).addTo(state.map);
  buildProfilerDOM();
  // the financial panel's single-point EP shares this vertex — refresh it if open
  if (panels.fin && panels.fin.el.style.display !== "none") drawFinancial();
}

function buildProfilerDOM() {
  const p = panels["prof"], mm = profilerState.mm;
  const model = document.getElementById("model").value;
  const catN = document.getElementById("category").value;
  const metricTxt =
    responseVar() === "tlc" ? (profilerState.scale === "point" ? "%LC at point" : "%TLC") :
    responseVar() === "dwell" ? "hours V≥40 mph" :
    responseVar() === "dosage" ? "wind dosage (mph·h)" :
    responseVar() === "ike" ? "IKE (TJ·h)" :
    responseVar() === "ikepeak" ? "peak IKE (TJ)" :
    responseVar() === "accloss" ? "%LC accumulated" :
    responseVar() === "windmax" ? (profilerState.scale === "point"
      ? "peak wind (mph)" : "max peak wind (mph)") :
    "peak wind (mph)";
  const view = profilerState.view, scale = profilerState.scale;
  profilerState.pred = profilerPredictor();
  const pred = profilerState.pred;

  const ptTxt = profilerState.pt ? `(${profilerState.pt.ew},${profilerState.pt.ns})` : "none";
  const toggles =
    `<div class="prof-toggle">` +
    `<button class="prof-tab${view === "profiler" ? " active" : ""}" data-view="profiler">Profiler</button>` +
    `<button class="prof-tab${view === "matrix" ? " active" : ""}" data-view="matrix">Interaction matrix</button>` +
    `</div>` +
    `<div class="prof-toggle">` +
    `<button class="prof-tab${scale === "footprint" ? " active" : ""}" data-scale="footprint">Footprint mean</button>` +
    `<button class="prof-tab${scale === "point" ? " active" : ""}" data-scale="point">Single point</button>` +
    (scale === "point"
      ? `<button class="prof-pick${profilerState.picking ? " active" : ""}" id="profPick">Pick on map</button>` +
        `<span class="prof-ptlbl">${ptTxt}</span>` : "") +
    `</div>`;

  const srcTxt = scale === "point"
    ? (pred.direct === false ? "per-vertex RSM (Powell)" : "direct simulation (no metamodel)")
    : METAMODEL_LABEL[mm.type];
  const rng = pred.available ? ` · Y range [${pred.ymin.toFixed(2)}, ${pred.ymax.toFixed(2)}]` : "";
  const fit = scale === "point" ? "" : ` · R²=${mm.r2.toFixed(2)}${mm.note}`;
  const head = `${srcTxt} · ${model} · Cat ${catN} · Y = ${metricTxt}${fit}${rng}`;

  if (!pred.available) {                              // point mode w/ Powell/KD or no point
    p.body.innerHTML = toggles + `<p class='note'>${pred.why}</p>`;
    wireProfTabs(p);
    return;
  }

  if (view === "matrix") {
    p.body.innerHTML = toggles + sobolBanner() +
      `<div class="prof-axis">Cell (row <b>r</b>, col <b>c</b>): effect of <b>c</b> on ${metricTxt} ` +
      `with <b>r</b> at <span style="color:${LOW_COLOR}">min (blue)</span> / ` +
      `<span style="color:${HIGH_COLOR}">max (red)</span>, others at mean. ` +
      (scale === "point" ? `Single point ${ptTxt}, ${pred.direct === false ? "per-vertex RSM" : "direct simulation"}. ` : "") +
      `Parallel = no interaction; diverging = interaction.</div>` +
      `<div class="prof-matrix" style="grid-template-columns:repeat(${mm.stats.length},1fr)"></div>` +
      `<p class="note">${head}</p>`;
    wireProfTabs(p);
    drawInteractionMatrix();
    return;
  }

  // ---- profiler view (partial-dependence + sliders) ----
  const ref = profilerState.ref;
  let sliders = "";
  mm.stats.forEach((s, i) => {
    sliders += `<label class="prof-sl"><span style="color:${VAR_COLORS[s.v]}">${s.v}</span>` +
      `<input type="range" data-i="${i}" min="${s.min}" max="${s.max}" ` +
      `step="${((s.max - s.min) / 100) || 0.01}" value="${ref[i]}"/>` +
      `<b data-v="${i}">${ref[i].toFixed(2)}</b></label>`;
  });
  p.body.innerHTML = toggles + sobolBanner() +
    `<div class="prof-axis">Each panel — <b>y</b>: ${metricTxt} &nbsp;·&nbsp; ` +
    `<b>x</b>: the named input over its range (dashed line = reference value)` +
    (scale === "point" ? ` &nbsp;·&nbsp; single point ${ptTxt}` : "") + `</div>` +
    `<div class="prof-grid"></div>` +
    `<div class="prof-sliders">${sliders}</div>` +
    `<p class="note">${head}` +
    `<br>Move a slider: another variable's curve changing slope = its interaction with the moved variable.</p>`;
  wireProfTabs(p);
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

function wireProfTabs(p) {
  p.body.querySelectorAll(".prof-tab[data-view]").forEach(b =>
    b.addEventListener("click", () => { profilerState.view = b.dataset.view; buildProfilerDOM(); }));
  p.body.querySelectorAll(".prof-tab[data-scale]").forEach(b =>
    b.addEventListener("click", () => {
      profilerState.scale = b.dataset.scale;
      profilerState.picking = false;
      document.getElementById("map").style.cursor = "";
      if (profilerState.scale !== "point" && profilerState.marker) {
        state.map.removeLayer(profilerState.marker); profilerState.marker = null;
      }
      buildProfilerDOM();
    }));
  const pick = p.body.querySelector("#profPick");
  if (pick) pick.addEventListener("click", () => {
    profilerState.picking = !profilerState.picking;
    document.getElementById("map").style.cursor = profilerState.picking ? "crosshair" : "";
    pick.classList.toggle("active", profilerState.picking);
  });
}

// N×N interaction matrix: cell (r,c) = effect of input c on the response with input
// r fixed at min (red) and max (blue), all other inputs at their mean. Diagonal = the
// variable name + its min→max range (the red/blue levels). Reuses mm.predict — no refit.
function drawInteractionMatrix() {
  const p = panels["prof"], mm = profilerState.mm;
  const grid = p && p.body.querySelector(".prof-matrix");
  if (!grid) return;
  const stats = mm.stats, N = stats.length, means = stats.map(s => s.m);
  const pred = profilerState.pred, predict = pred.predict;
  const ylo = pred.ymin, yhi = pred.ymax, yspan = (yhi - ylo) || 1;
  const clampY = y => Math.max(ylo, Math.min(yhi, y));
  const NS = 24, W = 120, H = 80, mL = 20, mR = 6, mT = 7, mB = 14;
  const ypix = y => mT + (1 - (y - ylo) / yspan) * (H - mT - mB);
  const fmt = v => Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(2);
  let html = "";
  for (let r = 0; r < N; r++) {
    for (let c = 0; c < N; c++) {
      if (r === c) {
        html += `<div class="prof-cell prof-diag"><div style="color:${VAR_COLORS[stats[r].v]}">${stats[r].v}</div>` +
          `<div class="prof-diag-rng"><span style="color:${LOW_COLOR}">${fmt(stats[r].min)}</span> → ` +
          `<span style="color:${HIGH_COLOR}">${fmt(stats[r].max)}</span></div></div>`;
        continue;
      }
      const sc = stats[c], xlo = sc.min, xhi = sc.max, xspan = (xhi - xlo) || 1;
      const xpix = x => mL + ((x - xlo) / xspan) * (W - mL - mR);
      const curve = level => {
        let pts = "";
        for (let k = 0; k <= NS; k++) {
          const xv = xlo + (k / NS) * xspan;
          const raw = means.slice(); raw[c] = xv; raw[r] = level;
          pts += `${xpix(xv).toFixed(1)},${ypix(clampY(predict(raw))).toFixed(1)} `;
        }
        return pts.trim();
      };
      // Second-order Sobol' S_ij quantifies what the diverging curves show by eye.
      // Available only where the emulator is fit (default config), so it is a
      // gated annotation, not a requirement for the cell to draw.
      const s2 = sobolS2(r, c);
      let svg = `<svg viewBox="0 0 ${W} ${H}" width="100%"><title>${sc.v} vs ${stats[r].v}` +
        (s2 === null ? "" : `\nSobol' S_ij = ${s2.toFixed(4)}`) + `</title>`;
      svg += `<line x1="${mL}" y1="${ypix(ylo)}" x2="${W - mR}" y2="${ypix(ylo)}" stroke="#e2e8f0"/>`;
      svg += `<line x1="${mL}" y1="${mT}" x2="${mL}" y2="${H - mB}" stroke="#e2e8f0"/>`;
      svg += `<polyline points="${curve(stats[r].min)}" fill="none" stroke="${LOW_COLOR}" stroke-width="1.6"/>`;
      svg += `<polyline points="${curve(stats[r].max)}" fill="none" stroke="${HIGH_COLOR}" stroke-width="1.6"/>`;
      svg += `<text x="${(mL + W - mR) / 2}" y="${H - 3}" text-anchor="middle" class="ax">${sc.v}</text>`;
      if (s2 !== null) {
        // white halo so the value stays legible where a curve runs beneath it
        svg += `<text x="${W - mR}" y="${mT + 6}" text-anchor="end" class="ax" ` +
          `paint-order="stroke" stroke="#fff" stroke-width="3" stroke-linejoin="round" ` +
          `style="font-weight:${s2 >= S2_STRONG ? 700 : 400}">S=${s2.toFixed(3)}</text>`;
      }
      svg += `</svg>`;
      // heat tint: red = strong interaction, transparent = none (temperature scale)
      const tint = s2 === null ? "" :
        ` style="background:rgba(239,68,68,${Math.min(0.5, Math.max(0, s2) / S2_STRONG * 0.5).toFixed(3)})"`;
      html += `<div class="prof-cell"${tint}>${svg}</div>`;
    }
  }
  grid.innerHTML = html;
}

// One line telling the user whether the Sobol' overlays are on, and if not, exactly
// which control to change. Without this the gate is invisible: the numbers simply
// fail to appear and there is nothing on screen explaining why.
function sobolBanner() {
  if (sobolBlock()) {
    const kd = document.getElementById("landDecay").checked;
    return `<div class="prof-axis sobol-on">Sobol' indices shown (GPR emulator, ` +
      `roughness${kd ? " + Kaplan–DeMaria decay" : ""}): <b>S₁</b> main effect, ` +
      `<b>Sₜ</b> total, <b>S</b> = pairwise Sᵢⱼ. Cells shaded red carry a real ` +
      `interaction.</div>`;
  }
  return `<div class="prof-axis sobol-off">Sobol' indices hidden — they are computed ` +
    `on a GPR emulator fit to the <b>Powell (steady)</b> footprint with <b>Surface ` +
    `roughness</b> on. Select that model and land effect to overlay S₁/Sₜ and the ` +
    `pairwise Sᵢⱼ heat map.</div>`;
}

// The Sobol' block matching the CURRENT land configuration. Both are precomputed:
// `sobol` for roughness alone, `sobol_kd` for roughness + Kaplan-DeMaria decay, so
// the indices follow the decay toggle rather than being pinned to one config.
// Returns null only where no emulator exists at all (non-Powell model, or roughness
// off -- the emulator is always fit on the roughness-adjusted field).
function sobolBlock() {
  if (document.getElementById("model").value !== "powell") return null;
  if (!document.getElementById("landRoughness").checked) return null;
  const e = mmEntry(responseVar(), "cat" + document.getElementById("category").value);
  if (!e) return null;
  return document.getElementById("landDecay").checked
    ? (e.sobol_kd || null) : (e.sobol || null);
}

const S2_STRONG = 0.02;         // |S_ij| at which a pair reads as a real interaction
function sobolS2(i, j) {
  const s = sobolBlock();
  return (s && s.S2) ? s.S2[i][j] : null;
}

function updateProfilerPlots() {
  const p = panels["prof"], mm = profilerState.mm, ref = profilerState.ref;
  if (!p || !mm) return;
  const grid = p.body.querySelector(".prof-grid");
  if (!grid) return;
  const pred = profilerState.pred, predict = pred.predict;
  const NS = 40, W = 150, H = 96, mL = 26, mR = 6, mT = 8, mB = 16;
  const ylo = pred.ymin, yhi = pred.ymax, yspan = (yhi - ylo) || 1;
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
      pts += `${xpix(xv).toFixed(1)},${ypix(clampY(predict(raw))).toFixed(1)} `;
    }
    const refY = clampY(predict(ref));
    let svg = `<svg viewBox="0 0 ${W} ${H}" width="100%">`;
    svg += `<line x1="${mL}" y1="${ypix(ylo)}" x2="${W - mR}" y2="${ypix(ylo)}" stroke="#cbd5e1"/>`;
    svg += `<line x1="${mL}" y1="${mT}" x2="${mL}" y2="${H - mB}" stroke="#cbd5e1"/>`;
    svg += `<line x1="${xpix(ref[i]).toFixed(1)}" y1="${mT}" x2="${xpix(ref[i]).toFixed(1)}" y2="${H - mB}" stroke="#94a3b8" stroke-dasharray="2 2"/>`;
    svg += `<polyline points="${pts.trim()}" fill="none" stroke="${VAR_COLORS[s.v]}" stroke-width="2"/>`;
    svg += `<circle cx="${xpix(ref[i]).toFixed(1)}" cy="${ypix(refY).toFixed(1)}" r="2.5" fill="${VAR_COLORS[s.v]}"/>`;
    svg += `<text x="${mL}" y="${H - 3}" class="ax">${s.v}</text>`;
    // The curve shows the SHAPE of the effect; Sobol' gives its MAGNITUDE. St > S1
    // means part of this input's influence is only visible through interactions --
    // which is exactly what the sliders reveal by changing this curve's slope.
    const sb = sobolFor(i);
    if (sb) {
      svg += `<text x="${W - mR}" y="${mT + 6}" text-anchor="end" class="ax" ` +
        `paint-order="stroke" stroke="#fff" stroke-width="3" stroke-linejoin="round">` +
        `S₁=${sb.S1.toFixed(2)} Sₜ=${sb.ST.toFixed(2)}</text>`;
    }
    svg += `</svg>`;
    html += `<div class="prof-cell">${svg}</div>`;
  });
  grid.innerHTML = html;
}

// First-order / total Sobol' index for input i, or null where no emulator applies.
function sobolFor(i) {
  const s = sobolBlock();
  return s ? { S1: s.S1[i], ST: s.ST[i] } : null;
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
  const W = 440, H = 294, mL = 54, mR = 12, mT = 12, mB = 48;
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
  // axis titles
  const yMid = (mT + H - mB) / 2;
  svg += `<text x="13" y="${yMid}" text-anchor="middle" transform="rotate(-90 13 ${yMid})" class="ax">cumulative probability F(x)</text>`;
  svg += `<text x="${(mL + W - mR) / 2}" y="${H - 6}" text-anchor="middle" class="ax">%TLC (loss cost, % of $68.2M exposure)</text>`;
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
  if (isPointOnlyResp()) {                     // footprint metamodels are peak-based
    p.body.innerHTML = "<p class='note'>Location-level metrics (dwell / dosage / IKE) are " +
      "location-level and have no footprint metamodel. Use the Interaction Profiler " +
      "at Single-point scale.</p>";
    return;
  }
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
  const metricTxt = respLabel("short");
  p.body.innerHTML =
    `<div class="prof-axis">Each panel — <b>y</b>: ${metricTxt} &nbsp;·&nbsp; ` +
    `<b>x</b>: the named input over its range</div>` +
    `<div class="prof-grid">${gridHtml}</div>` +
    `<div class="legend2">${legend}</div>` +
    `<table class="cmp-tbl"><tr><th>metamodel</th><th>R²</th><th>5-fold CV R²</th></tr>${rows}</table>` +
    `<p class="note">${model} · Cat ${catN} · Y = ${metricTxt} · curves at the input means` +
    `${mms.gpr ? mms.gpr.note : (mms.mlp ? mms.mlp.note : "")}` +
    `<br>Overlaid partial-dependence: where Linear diverges from GPR/NN it is missing curvature/interaction.</p>`;
}

// ---- Loss EP / Financial (actuarial layer) -------------------------------
// Adds the financial leg on top of hazard (wind) + vulnerability (MDR). Net loss
// per location applies a deductible then a limit; severity is aggregated to net
// TLC$ per input vector. Conditional mode = severity for the selected category;
// Annualized mode = an OEP curve combining categories by their event rates.
const finState = {
  mode: "annual",                         // "annual" | "cond"
  scale: "domain",                        // "domain" (682-pt aggregate) | "point" (one vertex)
  rates: { 1: 0.20, 3: 0.05, 5: 0.01 },   // events/yr by category (editable assumptions)
  ded: 0,                                  // per-location deductible ($)
  lim: null,                               // per-location limit ($); null -> exposure
};

// net TLC ($) per input vector for a category, after per-location deductible + limit
function tlcSeries(model, cat, ded, lim) {
  const recs = state.inputs ? state.inputs[cat] : null;
  if (!recs || !state.vuln) return null;
  const out = [];
  for (let i = 0; i < recs.length; i++) {
    const w = computeWindFor(model, cat, i);
    if (!w || typeof w === "string") return null;     // null / "kd-pending"
    let tlc = 0;
    state.grid.points.forEach((p, j) => {
      if (!p.land) return;
      const lc = mdrAt(w[j]) * exposureAt(j);         // ground-up per-location loss
      let net = Math.max(lc - ded, 0);                // deductible
      if (lim != null) net = Math.min(net, lim);      // optional per-location limit
      tlc += net;
    });
    out.push(tlc);
  }
  return out;
}

// net loss ($) at ONE land vertex per input vector, after per-location deductible +
// limit — the single-point analogue of tlcSeries(). Same 100 storms/category as the
// aggregate; only the spatial sum is dropped. Scenario-conditional on the fixed
// track (parameter uncertainty only, no landfall/heading sampling).
function pointLossSeries(model, cat, idx, ded, lim) {
  const recs = state.inputs ? state.inputs[cat] : null;
  if (!recs || !state.vuln) return null;
  const pt = state.grid.points[idx];
  if (!pt || !pt.land) return null;                   // loss only defined on land
  const exp = exposureAt(idx);
  const out = [];
  for (let i = 0; i < recs.length; i++) {
    const w = computeWindFor(model, cat, i);
    if (!w || typeof w === "string") return null;     // null / "kd-pending"
    const lc = mdrAt(w[idx]) * exp;                   // ground-up loss at this vertex
    let net = Math.max(lc - ded, 0);                  // deductible
    if (lim != null) net = Math.min(net, lim);        // optional per-location limit
    out.push(net);
  }
  return out;
}

const fmtM = d => fmtMoney(d);   // adaptive $ (B/M/k), shared with viewer.js

// loss at a target annual exceedance frequency from rate-weighted event samples
// (each sample contributes annual frequency weight; invert the descending λ(x))
function rpLoss(samples, targetFreq) {
  const arr = samples.slice().sort((a, b) => b.loss - a.loss);   // high loss first
  let cum = 0;
  for (const s of arr) {
    cum += s.w;
    if (cum >= targetFreq) return s.loss;
  }
  return arr.length ? arr[arr.length - 1].loss : 0;
}

function drawFinancial() {
  const p = panels["fin"];
  if (!p || p.el.style.display === "none") return;
  const model = document.getElementById("model").value;
  const selCat = document.getElementById("category").value;
  p.title.textContent = "Loss EP / Financial";

  // single-point scale reuses the profiler's map-picked vertex
  const pPt = (finState.scale === "point" && profilerState && profilerState.pt)
    ? profilerState.pt : null;
  const ptTxt = pPt ? `(${pPt.ew},${pPt.ns})` : "none";

  const controls =
    `<div class="fin-ctl">` +
    `<div class="fin-scale">` +
    `<button class="fin-tab${finState.scale === "domain" ? " active" : ""}" data-scale="domain">Domain</button>` +
    `<button class="fin-tab${finState.scale === "point" ? " active" : ""}" data-scale="point">Single point</button>` +
    (finState.scale === "point" ? `<span class="prof-ptlbl">${ptTxt}</span>` : "") +
    `</div>` +
    `<div class="fin-mode">` +
    `<button class="fin-tab${finState.mode === "cond" ? " active" : ""}" data-mode="cond">Conditional</button>` +
    `<button class="fin-tab${finState.mode === "annual" ? " active" : ""}" data-mode="annual">Annualized</button>` +
    `</div>` +
    `<div class="fin-rates${finState.mode === "cond" ? " disabled" : ""}">Event rate /yr ` +
    [1, 3, 5].map(c =>
      `<label>Cat${c}<input type="number" step="0.01" min="0" data-rate="${c}" ` +
      `value="${finState.rates[c]}" ${finState.mode === "cond" ? "disabled" : ""}/></label>`).join("") +
    `</div>` +
    `<div class="fin-terms">` +
    `<label>Deductible $<input type="number" step="1000" min="0" data-fin="ded" value="${finState.ded}"/></label>` +
    `<label>Limit $<input type="number" step="1000" min="0" data-fin="lim" ` +
    `placeholder="no cap" value="${finState.lim == null ? "" : finState.lim}"/></label>` +
    `</div></div>`;

  // single-point scale needs a picked vertex (shared with the profiler)
  if (finState.scale === "point" && !pPt) {
    p.body.innerHTML = controls +
      "<p class='note'>Single-point EP: pick a grid vertex first — open the " +
      "Interaction Profiler, set Scale to Single point, and click “Pick on map”. " +
      "The financial panel then uses that vertex.</p>";
    wireFinControls(p);
    return;
  }

  // gather net-loss severity samples per category (domain aggregate, or one vertex)
  const cats = [1, 3, 5];
  const sev = {};
  for (const c of cats) {
    const s = finState.scale === "point"
      ? pointLossSeries(model, "cat" + c, pPt.idx, finState.ded, finState.lim)
      : tlcSeries(model, "cat" + c, finState.ded, finState.lim);
    if (!s) {
      p.body.innerHTML = controls +
        "<p class='note'>Loss unavailable — vulnerability curve not loaded, or Powell " +
        "Kaplan–DeMaria precompute pending. Try Holland/Willoughby or land effect None/Roughness.</p>";
      wireFinControls(p);
      return;
    }
    sev[c] = s;
  }

  const W = 440, H = 270, mL = 64, mR = 14, mT = 14, mB = 46;
  let svg, metrics;

  if (finState.mode === "cond") {
    // conditional severity exceedance for the selected category
    const s = sev[+selCat].slice().sort((a, b) => a - b);
    const n = s.length, lo = s[0], hi = s[n - 1], span = (hi - lo) || 1;
    const xpix = q => mL + q * (W - mL - mR);                  // exceedance prob 0..1
    const ypix = v => mT + (1 - (v - lo) / span) * (H - mT - mB);
    svg = `<svg viewBox="0 0 ${W} ${H}" width="100%">`;
    for (let t = 0; t <= 4; t++) {
      const v = lo + (t / 4) * span;
      svg += `<line x1="${mL}" y1="${ypix(v)}" x2="${W - mR}" y2="${ypix(v)}" stroke="#e2e8f0" stroke-dasharray="2 3"/>`;
      svg += `<text x="${mL - 6}" y="${ypix(v) + 3}" text-anchor="end" class="ax">${fmtM(v)}</text>`;
      svg += `<text x="${xpix(t / 4)}" y="${H - mB + 16}" text-anchor="middle" class="ax">${(t / 4).toFixed(2)}</text>`;
    }
    let path = "";
    s.forEach((v, i) => {                                       // P(L > v) = (n-1-i)/n
      path += `${i ? "L" : "M"}${xpix((n - 1 - i) / n).toFixed(1)},${ypix(v).toFixed(1)}`;
    });
    svg += `<line x1="${mL}" y1="${ypix(lo)}" x2="${W - mR}" y2="${ypix(lo)}" stroke="#64748b"/>`;
    svg += `<line x1="${mL}" y1="${mT}" x2="${mL}" y2="${H - mB}" stroke="#64748b"/>`;
    svg += `<path d="${path}" fill="none" stroke="#2563eb" stroke-width="2"/>`;
    const yMid = (mT + H - mB) / 2;
    svg += `<text x="14" y="${yMid}" text-anchor="middle" transform="rotate(-90 14 ${yMid})" class="ax">net loss per event</text>`;
    svg += `<text x="${(mL + W - mR) / 2}" y="${H - 5}" text-anchor="middle" class="ax">exceedance probability  P(L &gt; x)</text></svg>`;
    const mu = mean(s), sd = Math.sqrt(mean(s.map(v => (v - mu) ** 2)));
    const pct = q => s[Math.min(n - 1, Math.floor(q * n))];
    metrics =
      `<table class="cmp-tbl"><tr><th>metric (Cat ${selCat}, per event)</th><th>value</th></tr>` +
      `<tr><td>mean</td><td>${fmtM(mu)}</td></tr>` +
      `<tr><td>std dev · CoV</td><td>${fmtM(sd)} · ${(sd / mu).toFixed(2)}</td></tr>` +
      `<tr><td>50th / 90th / 99th pct</td><td>${fmtM(pct(0.5))} · ${fmtM(pct(0.9))} · ${fmtM(pct(0.99))}</td></tr>` +
      `</table>`;
  } else {
    // annualized OEP: λ(x) = Σ_c rate_c · P(L_c > x); each sample weight = rate_c/N_c
    const wsamp = [];
    let aal = 0;
    for (const c of cats) {
      const r = finState.rates[c] || 0, s = sev[c], n = s.length;
      aal += r * mean(s);
      s.forEach(loss => wsamp.push({ loss, w: r / n }));
    }
    const losses = wsamp.map(s => s.loss);
    const hi = Math.max(...losses), lo = 0, span = hi || 1;
    const totFreq = wsamp.reduce((a, s) => a + s.w, 0);
    const sortAsc = wsamp.slice().sort((a, b) => a.loss - b.loss);
    const xlogLo = Math.log10(Math.max(1, 1 / totFreq));        // shortest RP shown
    const xlogHi = Math.log10(Math.max(10, 1 / (sortAsc[0] ? sortAsc[0].w : 0.004)));
    const xpix = rp => mL + (Math.log10(rp) - xlogLo) / ((xlogHi - xlogLo) || 1) * (W - mL - mR);
    const ypix = v => mT + (1 - (v - lo) / span) * (H - mT - mB);
    // build EP points: cumulate weight from the top -> freq -> RP
    let cum = 0; const pts = [];
    wsamp.slice().sort((a, b) => b.loss - a.loss).forEach(s => {
      cum += s.w; const rp = 1 / cum;
      if (rp >= 1) pts.push([rp, s.loss]);
    });
    svg = `<svg viewBox="0 0 ${W} ${H}" width="100%">`;
    const rpTicks = [2, 5, 10, 25, 50, 100, 250, 500].filter(rp =>
      Math.log10(rp) >= xlogLo - 1e-9 && Math.log10(rp) <= xlogHi + 1e-9);
    rpTicks.forEach(rp => {
      svg += `<line x1="${xpix(rp)}" y1="${mT}" x2="${xpix(rp)}" y2="${H - mB}" stroke="#eef2f7"/>`;
      svg += `<text x="${xpix(rp)}" y="${H - mB + 16}" text-anchor="middle" class="ax">${rp}</text>`;
    });
    for (let t = 0; t <= 4; t++) {
      const v = lo + (t / 4) * span;
      svg += `<text x="${mL - 6}" y="${ypix(v) + 3}" text-anchor="end" class="ax">${fmtM(v)}</text>`;
    }
    const poly = pts.map(([rp, v]) => `${xpix(rp).toFixed(1)},${ypix(v).toFixed(1)}`).join(" ");
    svg += `<line x1="${mL}" y1="${ypix(lo)}" x2="${W - mR}" y2="${ypix(lo)}" stroke="#64748b"/>`;
    svg += `<line x1="${mL}" y1="${mT}" x2="${mL}" y2="${H - mB}" stroke="#64748b"/>`;
    svg += `<polyline points="${poly}" fill="none" stroke="#2563eb" stroke-width="2"/>`;
    [50, 100, 250].forEach(rp => {
      const v = rpLoss(wsamp, 1 / rp);
      if (Math.log10(rp) >= xlogLo && Math.log10(rp) <= xlogHi)
        svg += `<circle cx="${xpix(rp).toFixed(1)}" cy="${ypix(v).toFixed(1)}" r="3.2" fill="#ef4444"/>`;
    });
    const yMid = (mT + H - mB) / 2;
    svg += `<text x="14" y="${yMid}" text-anchor="middle" transform="rotate(-90 14 ${yMid})" class="ax">annual loss (OEP)</text>`;
    svg += `<text x="${(mL + W - mR) / 2}" y="${H - 5}" text-anchor="middle" class="ax">return period (years, log)</text></svg>`;
    const l100 = rpLoss(wsamp, 1 / 100);
    const tvar = (() => {                                       // mean loss in the >100-yr tail
      const tail = wsamp.filter(s => s.loss >= l100);
      const wt = tail.reduce((a, s) => a + s.w, 0);
      return wt ? tail.reduce((a, s) => a + s.w * s.loss, 0) / wt : l100;
    })();
    metrics =
      `<table class="cmp-tbl"><tr><th>annual metric</th><th>value</th></tr>` +
      `<tr><td>AAL (avg annual loss)</td><td>${fmtM(aal)}</td></tr>` +
      `<tr><td>50 / 100 / 250-yr loss</td><td>${fmtM(rpLoss(wsamp, 1 / 50))} · ${fmtM(l100)} · ${fmtM(rpLoss(wsamp, 1 / 250))}</td></tr>` +
      `<tr><td>TVaR (100-yr tail)</td><td>${fmtM(tvar)}</td></tr>` +
      `</table>`;
  }

  const scaleTxt = finState.scale === "point"
    ? `single point ${ptTxt}, exposure ${fmtM(exposureAt(pPt.idx))}`
    : `domain aggregate · exposure ${exposureMode()} · total ${fmtM(totalExposure())}`;
  const ptCaveat = finState.scale === "point"
    ? `<br><b>Scenario-conditional:</b> all storms run the same fixed track, so this ` +
      `per-point EP samples storm-parameter uncertainty only — not landfall/heading. ` +
      `Per-point AAL is biased and its variability understated relative to a ` +
      `track-sampled model; read it as “given these 100 storms on this track.”`
    : "";
  p.body.innerHTML = controls + svg + metrics +
    `<p class="note">${model} · severity over 100 input vectors / category · ${scaleTxt}` +
    `${finState.mode === "annual" ? " · red dots = 50/100/250-yr losses" : " · Cat " + selCat}` +
    `<br>Deductible/limit are per-location (under Census, a location is the cell ` +
    `aggregate). Terms are scoped to this panel; the map shows ground-up loss.${ptCaveat}</p>`;
  wireFinControls(p);
}

function wireFinControls(p) {
  // rates + deductible/limit also drive the AAL map (colorBy=aal); refresh it if active
  const refreshAALMap = () => {
    if (typeof updateField === "function" &&
        document.getElementById("colorBy").value === "aal") updateField();
  };
  p.body.querySelectorAll(".fin-tab[data-mode]").forEach(b =>
    b.addEventListener("click", () => { finState.mode = b.dataset.mode; drawFinancial(); }));
  p.body.querySelectorAll(".fin-tab[data-scale]").forEach(b =>
    b.addEventListener("click", () => { finState.scale = b.dataset.scale; drawFinancial(); }));
  p.body.querySelectorAll("[data-rate]").forEach(inp =>
    inp.addEventListener("change", () => {
      finState.rates[+inp.dataset.rate] = Math.max(0, parseFloat(inp.value) || 0);
      drawFinancial(); refreshAALMap();
    }));
  p.body.querySelectorAll("[data-fin]").forEach(inp =>
    inp.addEventListener("change", () => {
      const blank = inp.value.trim() === "";
      // blank Limit = no cap (null); deductible blank = 0
      finState[inp.dataset.fin] = (blank && inp.dataset.fin === "lim")
        ? null : Math.max(0, parseFloat(inp.value) || 0);
      drawFinancial(); refreshAALMap();
    }));
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
  document.getElementById("btnFin").addEventListener("click", () => openPanel("fin"));
  // collapsible Statistics / Actuarial groups
  document.querySelectorAll(".analysis-group").forEach(g =>
    g.addEventListener("click", () => {
      g.classList.toggle("open");
      document.getElementById(g.dataset.grp).classList.toggle("open");
    }));
  // model / land effect / response / exposure / damage-model change -> invalidate + redraw
  ["model", "landRoughness", "landDecay", "response", "exposureModel", "damageModel"].forEach(id =>
    document.getElementById(id).addEventListener("change", () => {
      analysisState.cache = null;
      redrawOpenPanels();
    }));
  // logistic damage-model parameters also change the loss surface
  ["logV50", "logK", "logVmax"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("input", () => { analysisState.cache = null; redrawOpenPanels(); });
  });
  // metamodel choice drives profiler/compare; no cache invalidation needed
  document.getElementById("metamodel").addEventListener("change",
    () => redrawOpenPanels(["prof", "cmp"]));
  // category change affects the per-category panels. "src" is included because its
  // Sobol' view is per-category (the SRC view itself spans all three categories).
  document.getElementById("category").addEventListener("change",
    () => redrawOpenPanels(["prof", "cmp", "cdf", "fin", "src"]));
}
