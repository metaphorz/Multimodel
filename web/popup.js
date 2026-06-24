/* Left-click a grid dot -> floating windfield popup:
   (1) storm-relative isotach plot (filled bands, like the windfields Charley fig),
       with the clicked vertex's position marked at its peak-wind time + its track,
   (2) a per-dot time series of wind over the 12-hour passage.
   Holland/Willoughby render live; Powell uses powell_field.json (post-UA). */

let wfPanel = null;

function ensureWfPanel() {
  if (wfPanel) return wfPanel;
  const el = document.createElement("div");
  el.className = "analysis-panel wf-panel";
  el.style.left = "80px"; el.style.top = "80px";
  el.style.width = "430px"; el.style.height = "560px";
  el.innerHTML =
    `<div class="ap-header"><span class="ap-title">Windfield</span>` +
    `<button class="ap-close" title="close">&times;</button></div>` +
    `<div class="ap-body"></div>`;
  document.getElementById("map").appendChild(el);
  if (window.L) { L.DomEvent.disableClickPropagation(el); L.DomEvent.disableScrollPropagation(el); }
  el.querySelector(".ap-close").addEventListener("click", () => { el.style.display = "none"; });
  el.addEventListener("mousedown", () => bringFront(el));
  makeDraggable(el, el.querySelector(".ap-header"));
  wfPanel = { el, body: el.querySelector(".ap-body"), title: el.querySelector(".ap-title") };
  return wfPanel;
}

// Build the windfield body (isotach + time series SVGs + note) for a vertex,
// for the current model/category/vector/land selection. Shared by the left-click
// popup and the Points-of-Interest detail panel. Returns an HTML string, or a
// short <p class='note'> message string if the exact field isn't available yet.
function windfieldBodyHTML(idx) {
  const { model, cat, vIdx, rec } = currentSelection();
  const pt = state.grid.points[idx];
  const land = document.getElementById("landEffect").value;

  let field, ts;
  if (model === "powell") {
    const pf = state.powellField && state.powellField[cat] && state.powellField[cat][vIdx];
    if (!pf) {
      return "<p class='note'>Powell windfield popup: exact field precompute " +
        "scheduled after the UA run (powell_field.json). Holland/Willoughby work now.</p>";
    }
    field = { Z: pf, n: state.powellField.n, halfKm: state.powellField.halfKm };
    ts = powellTimeSeries(field, pt, rec, land, idx);
  } else {
    const B = quantileToB(rec.WSP);
    field = stormRelativeField(model, rec, B, 90, 81);
    const opts = {};
    if (land === "roughness" && state.roughness) opts.factor = state.roughness.factors[idx];
    if (land === "kd") {
      let V0 = 0; for (const z of field.Z) if (z > V0) V0 = z;
      opts.sched = intensitySchedule(V0, rec.VT, state.grid.points);
    }
    ts = pointTimeSeries(model, rec, B, pt.ew, pt.ns, opts);
  }
  return isotachSVG(field, ts, pt) + timeSeriesSVG(ts) +
    `<p class="note">isotachs = storm-relative marine surface wind (mph); ` +
    `marker = vertex at peak time; land effect (${land}) applied to the time series.</p>`;
}

function openWindfieldPopup(idx) {
  const p = ensureWfPanel();
  p.el.style.display = "flex";
  bringFront(p.el);
  const { model, cat, vIdx } = currentSelection();
  const pt = state.grid.points[idx];
  p.title.textContent =
    `Windfield — ${model} ${cat.toUpperCase()} v${vIdx + 1} · (${pt.ew},${pt.ns}) mi`;
  p.body.innerHTML = windfieldBodyHTML(idx);
}

// sample a stored Powell storm-relative field for the per-dot time series
function powellTimeSeries(field, pt, rec, land, idx) {
  const { Z, n, halfKm } = field;
  const step = (2 * halfKm) / (n - 1);
  // bilinear interpolation over the 4 surrounding cells (smooth vs nearest-cell)
  const sample = (xkm, ykm) => {
    const fc = (xkm + halfKm) / step, fr = (ykm + halfKm) / step;
    if (fc < 0 || fc > n - 1 || fr < 0 || fr > n - 1) return 0;
    const c0 = Math.min(Math.floor(fc), n - 2), r0 = Math.min(Math.floor(fr), n - 2);
    const tx = fc - c0, ty = fr - r0;
    const z00 = Z[r0 * n + c0], z01 = Z[r0 * n + c0 + 1];
    const z10 = Z[(r0 + 1) * n + c0], z11 = Z[(r0 + 1) * n + c0 + 1];
    return (z00 * (1 - tx) + z01 * tx) * (1 - ty) + (z10 * (1 - tx) + z11 * tx) * ty;
  };
  const nT = 121, dt = 0.1;
  const t = [], w = [], rx = [], ry = []; let imax = 0;
  let sched = null;
  if (land === "kd") {
    let V0 = 0; for (const z of Z) if (z > V0) V0 = z;
    sched = intensitySchedule(V0, rec.VT, state.grid.points);
  }
  const factor = (land === "roughness" && state.roughness) ? state.roughness.factors[idx] : 1;
  for (let s = 0; s < nT; s++) {
    const tt = s * dt, ewc = rec.VT * tt;
    const xE = -(pt.ew - ewc), yN = pt.ns;          // km east, north (mi==stored km axis)
    let val = sample(xE, yN) * factor;
    if (sched) val *= sched[s];
    t.push(tt); w.push(val); rx.push(xE); ry.push(yN);
    if (val > w[imax]) imax = s;
  }
  return { t, w, rx, ry, imax };
}

// ---- isotach plot (filled bands via d3-contour) --------------------------
function isotachSVG(field, ts, pt) {
  const { Z, n, halfKm } = field;
  const W = 400, H = 300, m = 34;
  const idxToKm = g => -halfKm + g * (2 * halfKm / (n - 1));
  const xpx = xkm => m + (xkm + halfKm) / (2 * halfKm) * (W - 2 * m);
  const ypx = ykm => m + (1 - (ykm + halfKm) / (2 * halfKm)) * (H - 2 * m);  // north up

  const thr = WIND_STOPS.map(s => s[0]).filter(v => v > 0);
  const contours = window.d3.contours().size([n, n]).thresholds(thr)(Array.from(Z));
  let svg = `<svg viewBox="0 0 ${W} ${H}" width="100%">`;
  svg += `<rect x="${m}" y="${m}" width="${W - 2 * m}" height="${H - 2 * m}" fill="#0b1622"/>`;
  contours.forEach(ct => {
    const col = windColor(ct.value);
    ct.coordinates.forEach(poly => {
      const d = poly.map(ring =>
        "M" + ring.map(([gx, gy]) =>
          `${xpx(idxToKm(gx - 0.5)).toFixed(1)},${ypx(idxToKm(gy - 0.5)).toFixed(1)}`).join("L") + "Z"
      ).join("");
      svg += `<path d="${d}" fill="${col}" fill-opacity="0.85" stroke="none"/>`;
    });
  });
  // eye + axes
  svg += `<line x1="${xpx(0)}" y1="${m}" x2="${xpx(0)}" y2="${H - m}" stroke="#456" stroke-dasharray="2 3"/>`;
  svg += `<line x1="${m}" y1="${ypx(0)}" x2="${W - m}" y2="${ypx(0)}" stroke="#456" stroke-dasharray="2 3"/>`;
  // dot relative track + peak marker
  const path = ts.rx.map((x, i) => `${xpx(x).toFixed(1)},${ypx(ts.ry[i]).toFixed(1)}`).join(" ");
  svg += `<polyline points="${path}" fill="none" stroke="#fff" stroke-width="1" stroke-dasharray="3 2" opacity="0.7"/>`;
  const mx = xpx(ts.rx[ts.imax]), my = ypx(ts.ry[ts.imax]);
  svg += `<circle cx="${mx}" cy="${my}" r="5" fill="none" stroke="#fff" stroke-width="2"/>`;
  svg += `<circle cx="${mx}" cy="${my}" r="2" fill="#fff"/>`;
  svg += `<text x="${W / 2}" y="${H - 6}" text-anchor="middle" class="ax">x (km E) · eye at centre</text>`;
  svg += `<text x="10" y="${H / 2}" text-anchor="middle" class="ax" transform="rotate(-90 10 ${H / 2})">y (km N)</text>`;
  svg += `</svg>`;
  return svg;
}

// ---- per-dot time series -------------------------------------------------
function timeSeriesSVG(ts) {
  const W = 400, H = 120, mL = 38, mR = 8, mT = 10, mB = 22;
  const wmax = Math.max(...ts.w, 1);
  const x = t => mL + t / 12 * (W - mL - mR);
  const y = v => mT + (1 - v / wmax) * (H - mT - mB);
  let svg = `<svg viewBox="0 0 ${W} ${H}" width="100%">`;
  for (let g = 0; g <= 3; g++) {
    const v = wmax * g / 3;
    svg += `<line x1="${mL}" y1="${y(v)}" x2="${W - mR}" y2="${y(v)}" stroke="#e2e8f0"/>`;
    svg += `<text x="${mL - 4}" y="${y(v) + 3}" text-anchor="end" class="ax">${v.toFixed(0)}</text>`;
  }
  const pts = ts.t.map((t, i) => `${x(t).toFixed(1)},${y(ts.w[i]).toFixed(1)}`).join(" ");
  svg += `<polyline points="${pts}" fill="none" stroke="#2563eb" stroke-width="2"/>`;
  svg += `<circle cx="${x(ts.t[ts.imax])}" cy="${y(ts.w[ts.imax])}" r="3" fill="#ef4444"/>`;
  [0, 3, 6, 9, 12].forEach(t =>
    svg += `<text x="${x(t)}" y="${H - 6}" text-anchor="middle" class="ax">${t}h</text>`);
  svg += `<text x="${(W) / 2}" y="${H - 6}" text-anchor="middle" class="ax"> </text>`;
  svg += `<text x="${mL}" y="${mT}" class="ax">wind (mph) vs time — peak ${ts.w[ts.imax].toFixed(1)}</text>`;
  svg += `</svg>`;
  return svg;
}
