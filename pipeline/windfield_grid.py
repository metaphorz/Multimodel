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
import argparse
import os, sys, json, time, math
import torch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
# the Powell slab PDE solver is vendored in-repo (pipeline/vendor/) so a fresh
# clone can rebuild every windfield without files from the author's machine
STORM_ANIM = os.path.join(HERE, "vendor")
sys.path.insert(0, STORM_ANIM)
import hurricane_pde_marine as H  # noqa: E402

WEB = os.path.join(ROOT, "outputs", "web")
GRID = os.path.join(WEB, "grid.json")
INPUTS = os.path.join(WEB, "inputs.json")
OUT = os.path.join(WEB, "powell.json")
OUT_KD = os.path.join(WEB, "powell_kd.json")
OUT_FIELD = os.path.join(WEB, "powell_field.json")
# direction-dependent roughness: the map, and the two fields it produces
ROUGH_DIR = os.path.join(WEB, "roughness_dir.json")
OUT_DIR = os.path.join(WEB, "powell_dir.json")
OUT_DIR_KD = os.path.join(WEB, "powell_dir_kd.json")

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
# animation; +24 h gives the slowest storm room to clear). For the MARINE peak field
# this changes nothing: the eye starts at the east edge and moves WEST, so every land
# vertex is closest to the eye at t>=0 and its peak lies inside [0,12] h (verified:
# peak over [-12,+24] == peak over [0,12] at all 682 vertices; roughness, a time-
# independent multiplier, can't shift the peak time either). WITH Kaplan-DeMaria
# decay it does matter at the immediate-coastal landfall column (ew=9): the front-side
# eyewall passes at t<0 while the storm is still offshore and UNDECAYED (s=1), which
# beats the decayed back-side passage -- raising ~2 vertices by up to ~9 mph over the
# strict [0,12] window (a deliberate, physically faithful deviation).
T_MIN, T_MAX, T_DT = -12.0, 24.0, 1.0 / 60.0

# default WSP-quantile -> Holland B (Uniform[1.0, 2.5])
B_MIN, B_MAX = 1.0, 2.5

# Kaplan & DeMaria (1995) inland decay + gentle Gulf recovery
KD_ALPHA = 0.095        # land decay rate (1/hr)
KD_R = 0.90             # one-time coastal reduction at first landfall
KD_VB_MPH = 30.7        # background wind (26.7 kt) the storm decays toward
KD_ALPHA_REC = 0.05     # gulf recovery rate toward pre-landfall Vmax (1/hr)

# Gust factors for the corrected order of operations (meteorologist review, 2026-07-15;
# see outputs/order_of_operations.png, right-most flowchart). The wind chain is
#   10-min mean --G1--> max 1-min sustained --K-D decay--> --G2--> peak 3-s gust --> damage
# Kaplan-DeMaria was calibrated on the max 1-MIN SUSTAINED wind (Vb=30.7 is a 1-min
# background), so the decay must act AFTER the 10min->1min factor. Because G1 and G2 are
# constants and the surface roughness is a per-vertex scalar, both commute with the
# max-over-passage and with each other -- so the whole correction reduces to TWO changes:
#   (1) feed the decay schedule the 1-min intensity  V0 = G1 * surf.max()  (below), which
#       is the only non-commuting piece (the decay is affine in Vb);
#   (2) apply G1*G2 = 1.43 at the damage curve  (viewer GUST_FACTOR).
# Values are the flowchart's land constants; z0-dependent gust factors (water << land,
# per the same review) are the planned refinement -- see projectplan.
G_10MIN_1MIN = 1.1      # 10-min mean -> max 1-min sustained
G_1MIN_3S = 1.3        # max 1-min sustained -> peak 3-s gust  (G1*G2 = 1.43 total)


def wsp_to_B(p):
    return B_MIN + float(p) * (B_MAX - B_MIN)


def make_args(rec):
    """Build the solver args namespace from a Form S-6 input record."""
    import argparse
    dp = float(rec["FFP"]) - float(rec["CP"])          # pressure deficit (mb)
    rmax_km = float(rec["Rmax"]) * MILE_M / 1000.0      # statute miles -> km
    # Holland B: the constrained design supplies it directly (Powell Eq.7, physical
    # range ~[0.9,2.0]); the legacy path maps a WSP quantile in [0,1] via wsp_to_B.
    B = float(rec["B"]) if "B" in rec else wsp_to_B(rec["WSP"])
    return argparse.Namespace(
        lat0=LAT0, lon0=-80.0, B=B,
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


def dir_roughness_factor(ux, uy, F):
    """Per-(vertex, timestep) roughness multiplier from the LOCAL wind direction.

    F is (840, S): the fetch-blended effective-roughness factor of each vertex for each
    upwind sector (build_roughness_directional.py). The wind vector (ux, uy) is
    earth-relative east/north, so the compass bearing it blows TOWARD is atan2(ux, uy)
    and the bearing it comes FROM -- which is the one that selects the upwind fetch --
    is that plus 180 deg.

    The two sectors bracketing the bearing are blended linearly and CIRCULARLY. Snapping
    to the nearest sector instead would make the peak wind jump S times as the wind
    swings round, and those steps would look like physics rather than the discretisation
    artefact they are.
    """
    S = F.shape[1]
    width = 360.0 / S
    bearing_from = (torch.rad2deg(torch.atan2(ux, uy)) + 180.0) % 360.0   # (840, nt)
    pos = bearing_from / width
    i0 = torch.floor(pos).long() % S
    i1 = (i0 + 1) % S
    w = (pos - torch.floor(pos)).to(F.dtype)
    f0 = torch.gather(F, 1, i0)            # (840,12) gathered by (840,nt) -> (840,nt)
    f1 = torch.gather(F, 1, i1)
    return (1.0 - w) * f0 + w * f1


def solve_all(rec, ew, ns, hours_t, device, is_land, F_dir=None):
    """One PDE solve -> (marine, kd, dir, dir_kd, field), each a (840,) peak.

    `marine` and `kd` keep the ISOTROPIC convention: a peak marine wind, to which the
    viewer applies a single direction-independent roughness factor client-side.

    `dir` and `dir_kd` are the direction-dependent ones, and the difference is not a
    change of data but a change of ORDER:

        isotropic     peak = [ max_t V(t) ] * f          (f constant, pulled outside)
        directional   peak = max_t [ V(t) * f(theta(t)) ]

    A time-varying factor cannot be pulled out of a max. The peak SURFACE wind can occur
    at a different time than the peak MARINE wind -- typically when the wind swings onto
    a low-roughness sea fetch after the marine wind has already passed its maximum. That
    is why the multiply has to happen HERE, inside the time loop, and not in the browser.
    """
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
    # Feed the decay schedule the 1-MINUTE-sustained intensity, not the 10-min mean:
    # Kaplan-DeMaria decays toward a 1-min background (Vb=30.7), so its ratio s(t) must be
    # taken relative to the 1-min peak. This is the affine correction from the order-of-
    # operations review; the remaining G1*G2=1.43 is applied at the damage curve (viewer).
    V0 = G_10MIN_1MIN * float(surf.max())
    s = torch.tensor(intensity_schedule(V0, vt, hours_t, is_land),
                     dtype=torch.float32, device=device)
    kd = (surf * s[None, :]).max(dim=1).values

    dirp = dir_kd = None
    if F_dir is not None:
        # Interpolate the COMPONENTS, never the angle: bearings wrap at 360 and a
        # bilinear average of two angles either side of the branch cut is silently wrong.
        ux = H.bilinear_polar(meta["Ux"], r_src, phi_src, r_m, phi)
        uy = H.bilinear_polar(meta["Uy"], r_src, phi_src, r_m, phi)
        f = dir_roughness_factor(ux, uy, F_dir)        # (840, nt)
        dirp = (surf * f).max(dim=1).values
        dir_kd = (surf * s[None, :] * f).max(dim=1).values

    field = storm_field(speed_ms, meta, rmax_miles, cf_base, device)
    return marine, kd, dirp, dir_kd, field


def main():
    # Paths are overridable so a hold-out TEST design can be pushed through the very
    # same solver without overwriting the training artifacts. --no-field skips the
    # storm-relative field dump, which the viewer needs but a hold-out test does not.
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--inputs", default=INPUTS)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--out-kd", default=OUT_KD)
    ap.add_argument("--out-field", default=OUT_FIELD)
    ap.add_argument("--no-field", action="store_true")
    ap.add_argument("--out-dir-rough", default=OUT_DIR)
    ap.add_argument("--out-dir-rough-kd", default=OUT_DIR_KD)
    ap.add_argument("--no-directional", action="store_true",
                    help="skip the direction-dependent roughness fields")
    a = ap.parse_args()

    device = H.device_select()
    grid = json.load(open(GRID))
    inputs = json.load(open(a.inputs))
    pts = grid["points"]
    ew = torch.tensor([p["ew"] for p in pts], dtype=torch.float32, device=device)
    ns = torch.tensor([p["ns"] for p in pts], dtype=torch.float32, device=device)
    hours_t = torch.arange(T_MIN, T_MAX + T_DT / 2, T_DT, dtype=torch.float32, device=device)

    is_land = build_track_land(grid)

    # Direction-dependent roughness: (840, S) factors, one per upwind sector per vertex.
    F_dir = None
    if not a.no_directional:
        if not os.path.exists(ROUGH_DIR):
            raise SystemExit(f"{ROUGH_DIR} not found -- run "
                             f"pipeline/build_roughness_directional.py first "
                             f"(or pass --no-directional)")
        rd = json.load(open(ROUGH_DIR))
        F_dir = torch.tensor(rd["factors"], dtype=torch.float32, device=device)
        print(f"Directional roughness: {F_dir.shape[0]} vertices x {F_dir.shape[1]} "
              f"sectors, fetch {rd['method']['fetch_m']/1000:.0f} km", flush=True)

    # Group keys are read from the inputs file, not hardcoded: the legacy Form S-6 file
    # has three (cat1/cat3/cat5); the constrained lumped design has one ("all"). Any
    # top-level list value is a group of input records.
    groups = [k for k, v in inputs.items()
              if isinstance(v, list) and v and isinstance(v[0], dict)]
    base = {"unit": "mph", **{g: [] for g in groups}}
    import copy
    out = {**copy.deepcopy(base), "t_min": T_MIN, "t_max": T_MAX, "t_dt": T_DT,
           "n_steps": int(hours_t.numel()),
           "wsp_to_B": {"dist": "uniform", "min": B_MIN, "max": B_MAX}}
    out_kd = {**copy.deepcopy(base), "note": "Kaplan-DeMaria inland decay + Gulf recovery"}
    out_fld = {**copy.deepcopy(base), "n": FIELD_N, "halfKm": FIELD_HALF_KM,
               "note": "storm-relative marine surface wind (mph)"}
    out_dir = {**copy.deepcopy(base), "note": (
        "Direction-dependent fetch-blended roughness APPLIED IN THE TIME LOOP: "
        "peak = max_t[V(t)*f(theta(t))], not [max_t V(t)]*f. Roughness is already "
        "baked in -- do NOT multiply by roughness.json factors again.")}
    out_dir_kd = {**copy.deepcopy(base), "note": (
        "As above, plus Kaplan-DeMaria inland decay, both applied inside the time loop.")}

    t_start = time.time()
    total = sum(len(inputs[c]) for c in groups)
    done = 0
    for cat in groups:
        for rec in inputs[cat]:
            marine, kd, dirp, dir_kd, field = solve_all(
                rec, ew, ns, hours_t, device, is_land, F_dir)
            out[cat].append([round(float(v), 1) for v in marine.tolist()])
            out_kd[cat].append([round(float(v), 1) for v in kd.tolist()])
            if F_dir is not None:
                out_dir[cat].append([round(float(v), 1) for v in dirp.tolist()])
                out_dir_kd[cat].append([round(float(v), 1) for v in dir_kd.tolist()])
            if not a.no_field:
                out_fld[cat].append(field)
            done += 1
            if done % 20 == 0 or done == total:
                el = time.time() - t_start
                print(f"  {done}/{total}  ({el:.1f}s, {el/done:.2f}s/solve, "
                      f"ETA {el/done*(total-done):.0f}s)", flush=True)
        print(f"{cat}: done, marine peak={max(max(v) for v in out[cat]):.1f} "
              f"kd peak={max(max(v) for v in out_kd[cat]):.1f} mph", flush=True)

    writes = [(a.out, out), (a.out_kd, out_kd)]
    if F_dir is not None:
        writes += [(a.out_dir_rough, out_dir), (a.out_dir_rough_kd, out_dir_kd)]
    if not a.no_field:
        writes.append((a.out_field, out_fld))
    for path, obj in writes:
        json.dump(obj, open(path, "w"))
        print(f"Wrote {path} ({os.path.getsize(path)/1e6:.2f} MB)", flush=True)
    print(f"Total {time.time()-t_start:.1f}s", flush=True)


if __name__ == "__main__":
    main()
