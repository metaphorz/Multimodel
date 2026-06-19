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

// ---- compute SRC for the selected model, all categories ------------------
function computeSRC(model) {
  const inputs = state.inputs, nv = SA_VARS.length;
  const result = {};   // cat -> { src: {var:val}, r2 }
  for (const cat of SA_CATS) {
    const recs = inputs[cat];
    // build input columns + output y (mean land peak wind)
    const cols = SA_VARS.map(v => recs.map(r => r[v]));
    const y = recs.map((_, i) => landMeanWind(computeWindFor(model, cat, i)));
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
  if (!analysisState.cache || analysisState.cache.model !== model ||
      analysisState.cache.land !== land) {
    analysisState.cache = { model, land, data: computeSRC(model) };
  }
  return analysisState.cache;
}

function bringFront(el) { el.style.zIndex = ++zTop; }

function makeDraggable(el, handle) {
  let sx, sy, ox, oy, dragging = false;
  handle.addEventListener("mousedown", e => {
    if (e.target.classList.contains("ap-close")) return;
    dragging = true; sx = e.clientX; sy = e.clientY;
    const r = el.getBoundingClientRect(), pr = el.parentElement.getBoundingClientRect();
    ox = r.left - pr.left; oy = r.top - pr.top;
    el.style.right = "auto"; bringFront(el); e.preventDefault();
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
  if (!panels[mode]) panels[mode] = createPanel(mode);
  panels[mode].el.style.display = "flex";
  bringFront(panels[mode].el);
  panels[mode].body.innerHTML = "<p class='note'>Computing…</p>";
  setTimeout(() => drawChart(mode), 20);
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
  // faithful EPR for Powell when the UA precompute is available; else SRC^2
  const model = analysisState.cache.model;
  const epr = (isEPR && model === "powell") ? faithfulEPR() : null;

  // values per var per cat
  const series = {};
  let vmin = 0, vmax = 0;
  for (const v of SA_VARS) {
    series[v] = cats.map(c => {
      const src = data["cat" + c].src[v];
      const val = isEPR ? (epr ? epr["cat" + c][v] : src * src * 100) : src;
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
  p.body.innerHTML =
    svg + `<div class="legend2">${legend}</div>` +
    `<p class="note">${analysisState.cache.model} · metric: mean peak wind over 682 land pts` +
    ` · land effect: ${analysisState.cache.land}<br>${r2}` +
    (isEPR ? (epr ? "<br>EPR = Var(Y|Xᵢ)/Var(Y) from UA sheets (faithful, marine)"
                  : "<br>EPR ≈ SRC² (variance share)") : "") + "</p>";
}

function setupAnalysis() {
  document.getElementById("btnSRC").addEventListener("click", () => openPanel("src"));
  document.getElementById("btnEPR").addEventListener("click", () => openPanel("epr"));
  // invalidate cache when the model or land effect changes; redraw open panels
  ["model", "landEffect"].forEach(id =>
    document.getElementById(id).addEventListener("change", () => {
      analysisState.cache = null;
      Object.keys(panels).forEach(mode => {
        if (panels[mode].el.style.display !== "none") drawChart(mode);
      });
    }));
}
