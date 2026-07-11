/* Live Holland & Willoughby windfields over the Form S-6 grid (client-side).
   Mirrors pipeline/windfield_grid.py + hurricane_pde_marine.py physics:
   gradient wind -> inflow rotation -> translation asymmetry -> CF conversion.
   Powell (steady) is precomputed in Python; these two are cheap enough to run live. */

const PHYS = {
  MILE_M: 1609.344,
  MS_TO_MPH: 2.2369362920544,
  RHO: 1.15,
  OMEGA: 7.2921159e-5,
  LAT0: 25.8611,          // constant-latitude due-west track
  BEARING: 270.0,         // due west
  // t=0 is the storm at ew=0 (east edge); t<0 is the off-grid Atlantic approach
  // (coastal front side), +24h lets even the slowest storm fully clear the grid's
  // west edge. 1-min sampling avoids aliasing of the peak.
  T_MIN: -12.0, T_MAX: 24.0, T_DT: 1.0 / 60.0,
  BETA10: 1.0,            // gradient level; CF does surface conversion
};

function coriolis(latDeg) {
  return 2 * PHYS.OMEGA * Math.sin(latDeg * Math.PI / 180);
}

// Kaplan & DeMaria (1995) inland decay + gentle Gulf recovery
// (constants MUST match pipeline/windfield_grid.py)
const KD = { ALPHA: 0.095, R: 0.90, VB: 30.7, ALPHA_REC: 0.05 };

// storm-track land mask: is the centre over land at E-W position ewc (miles)?
// built once from the N-S=0 grid row (nearest column).
let _trackRow = null;
function trackIsLand(ewc, pts) {
  if (!_trackRow) {
    _trackRow = pts.filter(p => p.ns === 0).sort((a, b) => a.ew - b.ew);
  }
  if (ewc < _trackRow[0].ew || ewc > _trackRow[_trackRow.length - 1].ew) return false;
  let best = _trackRow[0], bd = Infinity;
  for (const p of _trackRow) {
    const d = Math.abs(p.ew - ewc);
    if (d < bd) { bd = d; best = p; }
  }
  return best.land;
}

// per-time intensity ratio s(t)=V(t)/V0 (decay over land, recover over Gulf)
function intensitySchedule(V0, vt, pts) {
  const nT = Math.round((PHYS.T_MAX - PHYS.T_MIN) / PHYS.T_DT);
  const s = new Float64Array(nT + 1);
  let V = V0, made = false;
  for (let i = 0; i <= nT; i++) {
    const land = trackIsLand(vt * (PHYS.T_MIN + i * PHYS.T_DT), pts);
    if (land) {
      if (!made) { V *= KD.R; made = true; }          // one-time coastal drop
      V = KD.VB + (V - KD.VB) * Math.exp(-KD.ALPHA * PHYS.T_DT);
    } else if (made) {
      V = V0 - (V0 - V) * Math.exp(-KD.ALPHA_REC * PHYS.T_DT);  // Gulf recovery
    }
    s[i] = V / V0;
  }
  return s;
}

// inflow angle (radians) — matches inflow_angle_rad() in the Python model
function inflowAngle(rm, RmaxM) {
  const s = rm / RmaxM;
  const bump = 25.0 * Math.exp(-((s - 1) ** 2) / 0.4);
  const outward = 8.0 * (1 - Math.exp(-(Math.max(s - 1, 0) ** 2) / 1.2));
  const inward = 15.0 * (1 - Math.exp(-(Math.max(1 - s, 0) ** 2) / 0.2));
  return (bump + outward + inward) * Math.PI / 180;
}

// Holland gradient wind (m/s) at radius rm (m)
function hollandVg(rm, dpPa, B, RmaxM, f) {
  const ratio = Math.pow(RmaxM / rm, B);
  const expTerm = Math.exp(-ratio);
  const fr2 = f * rm / 2;
  return Math.sqrt((dpPa * B / PHYS.RHO) * ratio * expTerm + fr2 * fr2) - fr2;
}

// Willoughby axisymmetric wind (m/s); Vmax anchored to Holland gradient at Rmax
function willoughbyV(rm, dpPa, B, RmaxM, f, n = 0.6, m = 0.5) {
  const dpdrR = dpPa * Math.exp(-1) * (B / RmaxM);
  const frR = f * RmaxM;
  const VmaxR = 0.5 * (-frR + Math.sqrt(frR * frR + 4 * RmaxM * dpdrR / PHYS.RHO));
  const Vmax = Math.max(VmaxR, 0);
  const s = Math.max(rm / RmaxM, 1e-6);
  const Vin = Vmax * Math.pow(s, n);
  const Vout = Vmax * Math.pow(s, -m);
  const blend = 1 / (1 + Math.exp(-(rm - RmaxM) / (0.12 * RmaxM + 1)));
  return (1 - blend) * Vin + blend * Vout;
}

// Form S-6 CF 3-zone radial rule (ROA pp.184-185)
function cfEffective(rMiles, RmaxMiles, cfBase) {
  const rr = rMiles / RmaxMiles;
  let cf;
  if (rr < 1) cf = cfBase * rr;
  else if (rr < 3) cf = cfBase - (rr - 1) / 2 * 0.1;
  else cf = cfBase - 0.1;
  return Math.max(cf, 0);
}

/* Compute per-vertex peak (max over the t=T_MIN..T_MAX passage) surface wind (mph) for one input vector.
   model: "holland" | "willoughby"
   rec:   { CP, Rmax(mi), VT(mph), CF, FFP, ... }
   B:     Holland shape parameter (from WSP quantile)
   pts:   grid points array (ordered like grid.json)            */
// factory: returns surf(xEast_m, yNorth_m) -> marine surface wind (mph) for one
// storm, in the storm-relative frame (x=East, y=North). Shared by all callers.
function fieldFnFor(model, rec, B) {
  const dpPa = (rec.FFP - rec.CP) * 100;
  const RmaxMiles = rec.Rmax;
  const RmaxM = RmaxMiles * PHYS.MILE_M;
  const f = coriolis(PHYS.LAT0);
  const cMs = rec.VT * 0.44704;
  const th = PHYS.BEARING * Math.PI / 180;
  const cx = cMs * Math.sin(th), cy = cMs * Math.cos(th);  // due west: (-c, 0)
  return function (xEast_m, yNorth_m) {
    const rm = Math.max(Math.hypot(xEast_m, yNorth_m), 1);
    const rMiles = rm / PHYS.MILE_M;
    const phi = Math.atan2(yNorth_m, xEast_m);
    const Vg = model === "willoughby"
      ? willoughbyV(rm, dpPa, B, RmaxM, f)
      : hollandVg(rm, dpPa, B, RmaxM, f);
    const V10 = PHYS.BETA10 * Vg;
    const tin = inflowAngle(rm, RmaxM);
    const uRad = -V10 * Math.sin(tin), vTan = V10 * Math.cos(tin);
    const cp = Math.cos(phi), sp = Math.sin(phi);
    const Ux = uRad * cp + vTan * (-sp) + cx;
    const Uy = uRad * sp + vTan * cp + cy;
    return Math.hypot(Ux, Uy) * cfEffective(rMiles, RmaxMiles, rec.CF) * PHYS.MS_TO_MPH;
  };
}

function computeLiveWind(model, rec, B, pts, sched) {
  const fn = fieldFnFor(model, rec, B);
  const out = new Float32Array(pts.length);
  const nT = Math.round((PHYS.T_MAX - PHYS.T_MIN) / PHYS.T_DT);
  for (let i = 0; i < pts.length; i++) {
    const ew = pts[i].ew, ns = pts[i].ns;
    let peak = 0;
    for (let s = 0; s <= nT; s++) {
      const ewc = rec.VT * (PHYS.T_MIN + s * PHYS.T_DT);
      let surf = fn(-(ew - ewc) * PHYS.MILE_M, ns * PHYS.MILE_M);
      if (sched) surf *= sched[s];          // K&D intensity ratio at this time
      if (surf > peak) peak = surf;
    }
    out[i] = peak;
  }
  return out;
}

// storm-relative marine surface-wind field on an n x n grid over +/- halfKm.
// Z[row*n + col]; col -> x_east, row -> y_north (both -half..+half km).
function stormRelativeField(model, rec, B, halfKm = 90, n = 81) {
  const fn = fieldFnFor(model, rec, B);
  const Z = new Float32Array(n * n);
  const step = (2 * halfKm) / (n - 1);
  for (let r = 0; r < n; r++) {
    const y_km = -halfKm + r * step;
    for (let c = 0; c < n; c++) {
      const x_km = -halfKm + c * step;
      Z[r * n + c] = fn(x_km * 1000, y_km * 1000);
    }
  }
  return { Z, n, halfKm };
}

// wind at one grid vertex over the full passage (t=T_MIN..T_MAX), with its storm-relative
// position each step. opts: { sched (K&D s(t)), factor (roughness multiplier) }.
function pointTimeSeries(model, rec, B, ew, ns, opts = {}) {
  const fn = fieldFnFor(model, rec, B);
  const nT = Math.round((PHYS.T_MAX - PHYS.T_MIN) / PHYS.T_DT);
  const t = [], w = [], rx = [], ry = [];
  let imax = 0;
  for (let s = 0; s <= nT; s++) {
    const tt = PHYS.T_MIN + s * PHYS.T_DT, ewc = rec.VT * tt;
    const xE_m = -(ew - ewc) * PHYS.MILE_M, yN_m = ns * PHYS.MILE_M;
    let surf = fn(xE_m, yN_m);
    if (opts.sched) surf *= opts.sched[s];
    if (opts.factor) surf *= opts.factor;
    t.push(tt); w.push(surf); rx.push(xE_m / 1000); ry.push(yN_m / 1000);
    if (surf > w[imax]) imax = s;
  }
  return { t, w, rx, ry, imax };
}

// Holland/Willoughby with Kaplan-DeMaria decay: marine pass -> V0 -> decayed pass
function computeLiveWindKD(model, rec, B, pts) {
  const marine = computeLiveWind(model, rec, B, pts);
  let V0 = 0;
  for (let i = 0; i < marine.length; i++) if (marine[i] > V0) V0 = marine[i];
  const sched = intensitySchedule(V0, rec.VT, pts);
  return computeLiveWind(model, rec, B, pts, sched);
}
