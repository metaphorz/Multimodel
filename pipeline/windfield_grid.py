#!/usr/bin/env python3
"""Powell (PDE) windfield precompute over the Form S-6 grid.

For each (category, input-vector) this:
  1. Solves the Powell PDE windfield once (storm-relative; the field shape is
     translation-invariant because the storm tracks due west at constant lat).
  2. Steps the storm center hourly t=0..12 along (0,0)->(117,0).
  3. Samples the gradient-level wind at all 840 grid vertices each hour.
  4. Applies the Form S-6 CF gradient->surface conversion (3-zone radial rule).
  5. Keeps the per-vertex peak (12-hr max) surface wind.

Inputs : outputs/web/grid.json, outputs/web/inputs.json
Output : outputs/web/powell.json   { unit, hours, cat1/cat3/cat5: [[840]*100] }

Modeling notes:
  - beta10 = 1.0 so the model returns gradient-level wind; the CF variable then
    performs the gradient->surface conversion exactly as Form S-6 specifies.
  - WSP (a quantile in [0,1]) -> Holland B via the default Uniform[1.0,2.5] map.
    (Holland/Willoughby recompute live in JS; Powell uses this default.)
"""
import os, sys, json, time, math
import torch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STORM_ANIM = os.path.expanduser("~/code/catmodel/wind/storm-anim")
sys.path.insert(0, STORM_ANIM)
import hurricane_pde_marine as H  # noqa: E402

WEB = os.path.join(ROOT, "outputs", "web")
GRID = os.path.join(WEB, "grid.json")
INPUTS = os.path.join(WEB, "inputs.json")
OUT = os.path.join(WEB, "powell.json")
OUT_KD = os.path.join(WEB, "powell_kd.json")
OUT_FIELD = os.path.join(WEB, "powell_field.json")

# constants
MILE_M = 1609.344
MS_TO_MPH = 2.2369362920544
LAT0 = 25.8611          # landfall latitude; storm tracks due west at constant lat
BEARING = 270.0         # due west
# fine time sampling for the peak envelope (hourly is too coarse vs the storm's
# fast westward motion -> aliasing; dt=1/60h (1-min) removes residual aliasing at
# negligible cost -- the PDE solve is per-storm, not per-timestep).
# Window: t=0 is the storm center at ew=0 (east edge). Form S-6 specifies 12 h from
# t=0; we integrate a wider t=-12..+24 h superset (drives the viewer's approach
# animation; +24 h gives the slowest storm room to clear). This changes NO result:
# the eye starts at the east edge and moves WEST, so every land vertex is closest to
# the eye at t>=0 and its peak is always attained inside [0,12] h -- the extra window
# only adds weaker (farther-eye) winds (verified: peak over [-12,+24] == peak over
# [0,12] at all 682 land vertices, max difference 0).
T_MIN, T_MAX, T_DT = -12.0, 24.0, 1.0 / 60.0

# default WSP-quantile -> Holland B (Uniform[1.0, 2.5])
B_MIN, B_MAX = 1.0, 2.5

# Kaplan & DeMaria (1995) inland decay + gentle Gulf recovery
KD_ALPHA = 0.095        # land decay rate (1/hr)
KD_R = 0.90             # one-time coastal reduction at first landfall
KD_VB_MPH = 30.7        # background wind (26.7 kt) the storm decays toward
KD_ALPHA_REC = 0.05     # gulf recovery rate toward pre-landfall Vmax (1/hr)


def wsp_to_B(p):
    return B_MIN + float(p) * (B_MAX - B_MIN)


def make_args(rec):
    """Build the solver args namespace from a Form S-6 input record."""
    import argparse
    dp = float(rec["FFP"]) - float(rec["CP"])          # pressure deficit (mb)
    rmax_km = float(rec["Rmax"]) * MILE_M / 1000.0      # statute miles -> km
    return argparse.Namespace(
        lat0=LAT0, lon0=-80.0, B=wsp_to_B(rec["WSP"]),
        rmax_core_km=rmax_km, dp_hpa=dp,
        beta10=1.0, h_bl=500.0,
        speed_mph=float(rec["VT"]), bearing_deg=BEARING,
        rmin_km=0.5, rmax_km=250.0, Nr=200, Nphi=360, stretch_gamma=2.5,
        Kh_inner=100.0, Kh_outer=250.0, iter=800, cfl=0.5,
        z0_img=None, z0_blur=0.0, z0_gain=1.0,
    )


def cf_effective(r_miles, rmax_miles, cf_base):
    """Form S-6 conversion factor 3-zone radial rule (ROA pp.184-185)."""
    rr = r_miles / rmax_miles
    inner = cf_base * rr
    mid = cf_base - (rr - 1.0) / 2.0 * 0.1     # (r-Rmax)/(2Rmax)*0.1
    outer = cf_base - 0.1
    return torch.where(rr < 1.0, inner, torch.where(rr < 3.0, mid, outer))


def peak_winds(rec, ew, ns, hours_t, device):
    """Return (840,) peak surface wind (mph) for one input vector."""
    args = make_args(rec)
    speed_ms, meta = H.pde_steady_marine(args, device=device)
    r_src, phi_src = meta["r"], meta["phi"]
    rmax_out = float(r_src[-1])
    rmax_miles = float(rec["Rmax"])
    cf_base = float(rec["CF"])
    vt = float(rec["VT"])

    # storm center E-W position each hour (miles); N-S = 0
    ew_c = vt * hours_t                              # (H,)
    dx = ew[:, None] - ew_c[None, :]                # (840,H) miles, +west of storm
    y_north = ns[:, None].expand(-1, hours_t.numel())
    x_east = -dx
    r_miles = torch.sqrt(dx * dx + y_north * y_north)
    r_m = r_miles * MILE_M
    phi = torch.atan2(y_north, x_east) % (2 * math.pi)

    grad = H.bilinear_polar(speed_ms, r_src, phi_src, r_m, phi)   # (840,H) m/s
    grad = torch.where(r_m > rmax_out, torch.zeros_like(grad), grad)

    cf = cf_effective(r_miles, rmax_miles, cf_base).clamp(min=0.0)
    surf_mph = grad * cf * MS_TO_MPH
    return surf_mph.max(dim=1).values            # (840,)


# ---- Kaplan-DeMaria intensity schedule + storm-relative field (post-UA run) --
# Field spans +/-250 km (the PDE solver's rmax_km) so the popup wind-vs-time plot
# decays smoothly to 0 instead of clipping at a still-strong ~66-69 mph edge.
FIELD_HALF_KM, FIELD_N = 250.0, 81


def build_track_land(grid):
    """Return is_land(ewc_miles) using the N-S=0 grid row (nearest column)."""
    row = sorted([(p["ew"], p["land"]) for p in grid["points"] if p["ns"] == 0])
    ews = [e for e, _ in row]
    lands = [bool(l) for _, l in row]
    lo, hi = ews[0], ews[-1]

    def is_land(ewc):
        if ewc < lo or ewc > hi:
            return False
        best, bd = 0, 1e9
        for i, e in enumerate(ews):
            d = abs(e - ewc)
            if d < bd:
                bd = d; best = i
        return lands[best]
    return is_land


def intensity_schedule(V0, vt, hours_t, is_land):
    """s(t)=V(t)/V0: K&D inland decay + gentle Gulf recovery. Returns (nt,) list."""
    s, V, made = [], V0, False
    for t in hours_t.tolist():
        if is_land(vt * t):
            if not made:
                V *= KD_R; made = True
            V = KD_VB_MPH + (V - KD_VB_MPH) * math.exp(-KD_ALPHA * T_DT)
        elif made:
            V = V0 - (V0 - V) * math.exp(-KD_ALPHA_REC * T_DT)
        s.append(V / V0)
    return s


def storm_field(speed_ms, meta, rmax_miles, cf_base, device):
    """Cartesian storm-relative surface field (mph), FIELD_N x FIELD_N over +/-halfKm.
    Flattened row-major (row->y north, col->x east) to match web/popup.js."""
    r_src, phi_src = meta["r"], meta["phi"]
    rmax_out = float(r_src[-1])
    step = 2 * FIELD_HALF_KM / (FIELD_N - 1)
    coords = torch.arange(FIELD_N, device=device, dtype=torch.float32) * step - FIELD_HALF_KM
    X = coords[None, :].expand(FIELD_N, FIELD_N)   # col -> x east (km)
    Y = coords[:, None].expand(FIELD_N, FIELD_N)   # row -> y north (km)
    r_km = torch.sqrt(X * X + Y * Y)
    r_m = r_km * 1000.0
    phi = torch.atan2(Y, X) % (2 * math.pi)
    grad = H.bilinear_polar(speed_ms, r_src, phi_src, r_m, phi)
    grad = torch.where(r_m > rmax_out, torch.zeros_like(grad), grad)
    r_miles = r_km * 1000.0 / MILE_M
    cf = cf_effective(r_miles, rmax_miles, cf_base).clamp(min=0.0)
    field = (grad * cf * MS_TO_MPH).reshape(-1)
    return [int(round(float(v))) for v in field.tolist()]


def solve_all(rec, ew, ns, hours_t, device, is_land):
    """One PDE solve -> (marine_peak[840], kd_peak[840], field[FIELD_N^2])."""
    args = make_args(rec)
    speed_ms, meta = H.pde_steady_marine(args, device=device)
    r_src, phi_src = meta["r"], meta["phi"]
    rmax_out = float(r_src[-1])
    rmax_miles = float(rec["Rmax"]); cf_base = float(rec["CF"]); vt = float(rec["VT"])

    ew_c = vt * hours_t
    dx = ew[:, None] - ew_c[None, :]
    y_north = ns[:, None].expand(-1, hours_t.numel())
    r_miles = torch.sqrt(dx * dx + y_north * y_north)
    r_m = r_miles * MILE_M
    phi = torch.atan2(y_north, -dx) % (2 * math.pi)
    grad = H.bilinear_polar(speed_ms, r_src, phi_src, r_m, phi)
    grad = torch.where(r_m > rmax_out, torch.zeros_like(grad), grad)
    cf = cf_effective(r_miles, rmax_miles, cf_base).clamp(min=0.0)
    surf = grad * cf * MS_TO_MPH                       # (840, nt) marine

    marine = surf.max(dim=1).values
    V0 = float(surf.max())
    s = torch.tensor(intensity_schedule(V0, vt, hours_t, is_land),
                     dtype=torch.float32, device=device)
    kd = (surf * s[None, :]).max(dim=1).values
    field = storm_field(speed_ms, meta, rmax_miles, cf_base, device)
    return marine, kd, field


def main():
    device = H.device_select()
    grid = json.load(open(GRID))
    inputs = json.load(open(INPUTS))
    pts = grid["points"]
    ew = torch.tensor([p["ew"] for p in pts], dtype=torch.float32, device=device)
    ns = torch.tensor([p["ns"] for p in pts], dtype=torch.float32, device=device)
    hours_t = torch.arange(T_MIN, T_MAX + T_DT / 2, T_DT, dtype=torch.float32, device=device)

    is_land = build_track_land(grid)
    base = {"unit": "mph", "cat1": [], "cat3": [], "cat5": []}
    import copy
    out = {**copy.deepcopy(base), "t_min": T_MIN, "t_max": T_MAX, "t_dt": T_DT,
           "n_steps": int(hours_t.numel()),
           "wsp_to_B": {"dist": "uniform", "min": B_MIN, "max": B_MAX}}
    out_kd = {**copy.deepcopy(base), "note": "Kaplan-DeMaria inland decay + Gulf recovery"}
    out_fld = {**copy.deepcopy(base), "n": FIELD_N, "halfKm": FIELD_HALF_KM,
               "note": "storm-relative marine surface wind (mph)"}

    t_start = time.time()
    total = sum(len(inputs[c]) for c in ("cat1", "cat3", "cat5"))
    done = 0
    for cat in ("cat1", "cat3", "cat5"):
        for rec in inputs[cat]:
            marine, kd, field = solve_all(rec, ew, ns, hours_t, device, is_land)
            out[cat].append([round(float(v), 1) for v in marine.tolist()])
            out_kd[cat].append([round(float(v), 1) for v in kd.tolist()])
            out_fld[cat].append(field)
            done += 1
            if done % 20 == 0 or done == total:
                el = time.time() - t_start
                print(f"  {done}/{total}  ({el:.1f}s, {el/done:.2f}s/solve, "
                      f"ETA {el/done*(total-done):.0f}s)", flush=True)
        print(f"{cat}: done, marine peak={max(max(v) for v in out[cat]):.1f} "
              f"kd peak={max(max(v) for v in out_kd[cat]):.1f} mph", flush=True)

    for path, obj in ((OUT, out), (OUT_KD, out_kd), (OUT_FIELD, out_fld)):
        json.dump(obj, open(path, "w"))
        print(f"Wrote {path} ({os.path.getsize(path)/1e6:.2f} MB)", flush=True)
    print(f"Total {time.time()-t_start:.1f}s", flush=True)


if __name__ == "__main__":
    main()
