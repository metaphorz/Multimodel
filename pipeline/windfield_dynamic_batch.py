#!/usr/bin/env python3
"""Phase 1: batched dynamic-Powell precompute over all 300 input vectors.

Storms are batched into one (B,Nr,Nphi) tensor per category chunk (sorted by VT
so batch windows stay tight) and integrated with the validated dynamic scheme
(physics_terms_dyn: corrected Coriolis + upwind radial advection; grid rmin 4 km
/ Nphi 180 / gamma 2.5; Delta T = 1 min forcing updates; converged spin-up).

Per batch:
  marine spin-up  -> product A (frozen translation of the converged field over
                     the full -12..24 h window; Phase 0 proved dyn == frozen
                     under constant forcing, so A needs no march)
                  -> V0 per storm -> K&D schedules (same as production)
  march B         (K&D through pressure)             from the marine state
  rough spin-up   (z0 drag under the t0 storm position)
  march C         (z0 drag)      march D (both)      from the rough state
Final peaks combine the pre-t0 frozen-marine peak with the dynamic-window peak
(pre-landfall the field IS marine; post-t1 contributions are negligible:
decayed and/or 60+ mi past the west edge -- documented deviation).

Checkpoints: outputs/dynamic/precompute/{cat}_b{i}.json, one per batch; rerun
skips existing checkpoints (resume-safe). Per-storm NaN guard: a bad storm is
recorded in 'failures' and cannot kill the run. Final assembly writes
  outputs/web/powell_dyn.json           (A: marine)
  outputs/web/powell_dyn_kd.json        (B: K&D-through-pressure)
  outputs/web/powell_dyn_rough.json     (C: in-PDE z0 drag)
  outputs/web/powell_dyn_kd_rough.json  (D: both)
with the same schema as powell.json (cat1/cat3/cat5: [100][840] peak mph).

Run:   venv/bin/python -u pipeline/windfield_dynamic_batch.py            # full
       venv/bin/python -u pipeline/windfield_dynamic_batch.py --validate # cat3[0] vs Phase 0
"""
import os, sys, json, time, math, argparse
import torch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "vendor"))   # vendored PDE solver
import hurricane_pde_marine as H  # noqa: E402
from windfield_grid import (  # noqa: E402
    wsp_to_B, cf_effective, intensity_schedule, build_track_land,
    MILE_M, MS_TO_MPH, T_MIN, T_MAX, T_DT, LAT0)
from windfield_dynamic import (  # noqa: E402
    make_z0_fn, DYN_RMIN_KM, DYN_NPHI, DT_FORCING_S, T0_H, SPINUP_ITER)

WEB = os.path.join(ROOT, "outputs", "web")
CKPT = os.path.join(ROOT, "outputs", "dynamic", "precompute")
FRAMES_DIR = os.path.join(ROOT, "outputs", "web", "dyn_frames")
BATCH = 25
T_CHUNK = 120        # timesteps per chunked frozen-sampling / CF-conversion call

# ---- animation frames -----------------------------------------------------
# The march already evaluates the surface field at every vertex and every minute
# (`surf`, below); the peaks path throws it away with max(dim=2). Retaining it on
# the viewer's animation cadence costs nothing extra to compute and yields a real
# time-resolved contour animation for the dynamic model, which was previously
# impossible because only peaks were stored.
#
# One (cat, vector) file = 840 vertices x 73 frames of uint8 mph = 60 KB, fetched
# lazily by the browser for the single storm on screen.
FRAME_DT = 0.5                                        # h, matches ANIM.dt in anim.js
FRAME_HOURS = [T_MIN + i * FRAME_DT
               for i in range(int(round((T_MAX - T_MIN) / FRAME_DT)) + 1)]   # 73
N_FRAMES = len(FRAME_HOURS)
# uint8 stores mph/FRAME_SCALE. Raw mph would NOT fit: the production dynamic peaks
# reach 346 mph (powell_dyn_kd_rough), so a 255 ceiling would silently clip the
# strongest storms. At 2.0 mph/unit the range is 0..510 mph with 2 mph resolution --
# far below the model's own uncertainty and invisible in a contour plot, whose
# narrowest band (39->74 mph) is 35 mph wide.
FRAME_SCALE = 2.0                                     # mph per uint8 unit
FRAME_MAX_MPH = 255 * FRAME_SCALE                     # 510 mph representable
WANT_FRAMES = False                                   # set by --frames
FRAMES_ONLY = False                                   # set by --frames-only


def frame_slots(times):
    """Map a time axis onto animation frames, once, on the CPU.

    Returns (src_idx, dst_idx): time indices that land on the frame cadence, and the
    frames they fill. Computing this up front matters: doing it per timestep inside
    the march meant calling float() on a GPU tensor 2161 times, and each of those
    forces a GPU->CPU sync that stalls the whole pipeline.
    """
    src, dst = [], []
    for k, t in enumerate(times):
        t = float(t)
        f = round((t - T_MIN) / FRAME_DT)
        if 0 <= f < N_FRAMES and abs(t - (T_MIN + f * FRAME_DT)) < 1e-4:
            src.append(k); dst.append(f)
    return src, dst

SHARED = argparse.Namespace(
    lat0=LAT0, h_bl=500.0, beta10=1.0, bearing_deg=270.0,
    rmin_km=DYN_RMIN_KM, rmax_km=250.0, Nr=200, Nphi=DYN_NPHI,
    stretch_gamma=2.5, Kh_inner=100.0, Kh_outer=250.0, cfl=0.5)

PRODUCTS = {
    "A": ("powell_dyn.json", "dynamic Powell, converged marine steady state"),
    "B": ("powell_dyn_kd.json", "dynamic Powell, Kaplan-DeMaria decay through the pressure forcing"),
    "C": ("powell_dyn_rough.json", "dynamic Powell, in-PDE NLCD z0 land drag (storm-scale; local exposure factor still applies)"),
    "D": ("powell_dyn_kd_rough.json", "dynamic Powell, K&D-through-pressure + in-PDE z0 land drag"),
}


def storm_batch(recs):
    return {
        "dp_hpa": [float(r["FFP"]) - float(r["CP"]) for r in recs],
        "B": [wsp_to_B(r["WSP"]) for r in recs],
        "rmax_core_km": [float(r["Rmax"]) * MILE_M / 1000.0 for r in recs],
        "speed_mph": [float(r["VT"]) for r in recs],
    }


def surface_peaks(series_ms, recs, times, ew, ns, device, pre_peak=None, frames=None):
    """Chunked CF conversion -> per-storm/vertex peak surface mph (B,840).

    If `frames` (B,840,N_FRAMES) is given, the surface field is also written into it
    at every time that lands on the animation cadence -- free, since `surf` is
    computed here anyway and otherwise discarded by the max().
    """
    Bn = len(recs)
    vt = torch.tensor([float(r["VT"]) for r in recs], device=device)[:, None, None]
    rmax = torch.tensor([float(r["Rmax"]) for r in recs], device=device)[:, None, None]
    cfb = torch.tensor([float(r["CF"]) for r in recs], device=device)[:, None, None]
    peak = torch.zeros((Bn, ew.numel()), device=device) if pre_peak is None else pre_peak.clone()
    for k0 in range(0, len(times), T_CHUNK):
        tt = torch.tensor(times[k0:k0 + T_CHUNK], device=device)[None, None, :]
        dx = ew[None, :, None] - vt * tt
        r_mi = torch.sqrt(dx * dx + ns[None, :, None] ** 2)
        cf = cf_effective(r_mi, rmax, cfb).clamp(min=0.0)
        surf = series_ms[:, :, k0:k0 + tt.numel()] * cf * MS_TO_MPH
        peak = torch.maximum(peak, surf.max(dim=2).values)
        if frames is not None:
            src, dst = frame_slots(times[k0:k0 + tt.numel()])
            if src:
                frames[:, :, torch.tensor(dst, device=device)] = \
                    surf[:, :, torch.tensor(src, device=device)]
    return peak


def frozen_marine(speed0, S, recs, hours, ew, ns, device, frames=None):
    """Frozen translation of the converged marine field over `hours` (1-min grid).
    Returns (full-window surface peaks (B,840), pre-t0 peaks (B,840), V0 (B,)).

    With `frames`, also lays down the marine field on the animation cadence across
    the WHOLE window. This is the correct base layer: before t0 the storm is still
    offshore and the field genuinely is marine, and after the dynamic window closes
    the storm is 60+ mi past the grid edge, where the marine/decayed distinction is
    negligible (the same approximation the peaks path already documents). The
    dynamic march then overwrites the frames inside [t0, t1].
    """
    Bn = len(recs)
    vt = torch.tensor([float(r["VT"]) for r in recs], device=device)
    rmax = torch.tensor([float(r["Rmax"]) for r in recs], device=device)[:, None]
    cfb = torch.tensor([float(r["CF"]) for r in recs], device=device)[:, None]
    peak = torch.zeros((Bn, ew.numel()), device=device)
    pre = torch.zeros_like(peak)
    V0 = torch.zeros(Bn, device=device)
    for k0 in range(0, hours.numel(), T_CHUNK):
        hh = hours[k0:k0 + T_CHUNK]                       # (T,)
        dx = ew[None, :, None] - (vt[:, None, None] * hh[None, None, :])
        y = ns[None, :, None].expand(Bn, -1, hh.numel())
        r_mi = torch.sqrt(dx * dx + y * y)                # (B,840,T)
        r_m = (r_mi * MILE_M).reshape(Bn, -1)
        phi = (torch.atan2(y, -dx) % (2 * math.pi)).reshape(Bn, -1)
        g = H.bilinear_polar_batch(speed0, S["r"], S["phi_g"], r_m, phi)
        g = torch.where(r_m > S["rmax_out"], torch.zeros_like(g), g)
        g = g.reshape(Bn, ew.numel(), hh.numel())
        cf = cf_effective(r_mi, rmax[..., None], cfb[..., None]).clamp(min=0.0)
        surf = g * cf * MS_TO_MPH
        peak = torch.maximum(peak, surf.max(dim=2).values)
        V0 = torch.maximum(V0, surf.amax(dim=(1, 2)))
        m = hh < T0_H
        if m.any():
            pre = torch.maximum(pre, surf[:, :, m].max(dim=2).values)
        if frames is not None:
            src, dst = frame_slots(hh.tolist())
            if src:
                frames[:, :, torch.tensor(dst, device=device)] = \
                    surf[:, :, torch.tensor(src, device=device)]
    return peak, pre, V0


def run_batch(cat, bi, recs, idxs, grid, z0_fn, is_land, ew, ns, device):
    path = os.path.join(CKPT, f"{cat}_b{bi}.json")
    # In frames-only mode the peaks are NOT recomputed -- they already exist and are
    # committed -- so an existing checkpoint is expected and must not short-circuit us.
    if os.path.exists(path) and not FRAMES_ONLY:
        print(f"[{cat} b{bi}] checkpoint exists, skipping", flush=True)
        return
    if FRAMES_ONLY and all(os.path.exists(os.path.join(FRAMES_DIR, f"{cat}_v{int(r['vector'])}.bin"))
                           for r in recs):
        print(f"[{cat} b{bi}] frames exist, skipping", flush=True)
        return
    t_start = time.time()
    vts = [float(r["VT"]) for r in recs]
    t1_h = min(T_MAX, (117.0 + 60.0) / min(vts))
    S = H.pde_dynamic_setup_batch(SHARED, storm_batch(recs), device=device)
    hours = torch.arange(T_MIN, T_MAX + T_DT / 2, T_DT, dtype=torch.float32, device=device)

    def mkdyn(dp_scale, z0):
        return argparse.Namespace(t0_h=T0_H, t1_h=t1_h, dt_forcing_s=DT_FORCING_S,
                                  dp_scale=dp_scale, z0_fn=z0, sample_ew=ew,
                                  sample_ns=ns, spinup_iter=SPINUP_ITER)

    # frames for product D (roughness + K&D decay) -- the viewer's default config
    framesD = (torch.zeros((len(recs), ew.numel(), N_FRAMES), device=device)
               if WANT_FRAMES else None)

    # marine spin-up -> product A + V0 -> K&D schedules
    dynM = mkdyn(None, None)
    uM, vM = H.pde_dynamic_spinup_batch(S, dynM)
    speed0 = torch.sqrt((uM*S["erx"] + vM*S["etx"] + S["c_x"])**2
                        + (uM*S["ery"] + vM*S["ety"] + S["c_y"])**2)
    # the marine pass lays the base layer across the whole window; march D overwrites [t0,t1]
    peakA, pre_t0, V0 = frozen_marine(speed0, S, recs, hours, ew, ns, device, frames=framesD)
    print(f"[{cat} b{bi}] marine spin-up + A done ({time.time()-t_start:.0f}s, "
          f"t1={t1_h:.1f}h, V0 max={float(V0.max()):.1f} mph)", flush=True)

    s2 = torch.ones((len(recs), hours.numel()), device=device)
    for b, rec in enumerate(recs):
        s = intensity_schedule(float(V0[b]), float(rec["VT"]), hours, is_land)
        s2[b] = torch.tensor(s, device=device) ** 2

    def dp_scale(t_h):
        i = max(0, min(hours.numel() - 1, int(round((t_h - T_MIN) / T_DT))))
        return s2[:, i]

    peaks, fails = {"A": peakA}, []
    def march(tag, dp, z0, u, v, frames=None):
        t0 = time.time()
        series, times, _ = H.pde_dynamic_march_batch(S, mkdyn(dp, z0), u, v)
        bad = series.isnan().flatten(1).any(dim=1)
        if bad.any():
            for b in torch.nonzero(bad).flatten().tolist():
                fails.append({"variant": tag, "vector": int(recs[b]["vector"])})
            series = torch.nan_to_num(series, nan=0.0)
        peaks[tag] = surface_peaks(series, recs, times, ew, ns, device,
                                   pre_peak=pre_t0, frames=frames)
        print(f"[{cat} b{bi}] march {tag}: {time.time()-t0:.0f}s, "
              f"peak={float(peaks[tag].max()):.1f} mph", flush=True)

    # Marches B and C exist only to produce the peaks for products B and C, which are
    # already computed and committed. Frames need product D alone (roughness + K&D,
    # the viewer's default), so frames-only skips them -- and they are the expensive
    # ones: measured 400 s (B) and 2701 s (C) against 142 s for the marine pass.
    if not FRAMES_ONLY:
        march("B", dp_scale, None, uM.clone(), vM.clone())
    dynR = mkdyn(None, z0_fn)
    uR, vR = H.pde_dynamic_spinup_batch(S, dynR)
    if not FRAMES_ONLY:
        march("C", None, z0_fn, uR.clone(), vR.clone())
    march("D", dp_scale, z0_fn, uR, vR, frames=framesD)

    if WANT_FRAMES:
        write_frames(cat, recs, framesD, peaks["D"])
    if FRAMES_ONLY:
        print(f"[{cat} b{bi}] frames done ({time.time()-t_start:.0f}s total; "
              f"peaks left untouched)", flush=True)
        return

    out = {"cat": cat, "batch": bi, "indices": idxs, "t1_h": t1_h,
           "failures": fails,
           "peaks": {k: [[round(float(x), 1) for x in row] for row in v.tolist()]
                     for k, v in peaks.items()}}
    json.dump(out, open(path, "w"))
    print(f"[{cat} b{bi}] wrote checkpoint ({time.time()-t_start:.0f}s total)", flush=True)


def write_frames(cat, recs, framesD, peakD=None):
    """One uint8 file per (cat, vector): 840 vertices x 73 frames, row-major by frame.

    Values are stored as mph/FRAME_SCALE so the strongest storms (up to 510 mph) fit
    in a byte; it keeps a storm at 60 KB so the browser can fetch just the one on
    screen. Clipping would be silent data loss, so it is checked, not assumed.

    Frames are clamped to the product-D peak. A field cannot exceed its own maximum,
    but the frames CAN without this: after the dynamic window closes at t1 they fall
    back to the frozen-marine base, which carries neither the K&D decay nor the in-PDE
    drag, so a slow storm still near the grid at t1 shows a few vertices above the
    decayed peak (measured: 98 cells out of 18.4M, worst 6.9 mph). Clamping keeps the
    animation consistent with the static footprint the map draws from the same peaks.
    """
    os.makedirs(FRAMES_DIR, exist_ok=True)
    if peakD is not None:
        framesD = torch.minimum(framesD, peakD[:, :, None])
    over = int((framesD > FRAME_MAX_MPH).sum())
    if over:
        print(f"  WARNING: {over} frame values exceed {FRAME_MAX_MPH:.0f} mph and "
              f"were clipped -- raise FRAME_SCALE", flush=True)
    q = (framesD / FRAME_SCALE).clamp(0.0, 255.0).round().to(torch.uint8).cpu().numpy()
    for b, rec in enumerate(recs):
        v = int(rec["vector"])
        # (frames, vertices) so the viewer can slice one frame contiguously
        q[b].T.tofile(os.path.join(FRAMES_DIR, f"{cat}_v{v}.bin"))


def assemble(inputs):
    ok = True
    prods = {k: {"unit": "mph", "note": note,
                 "grid_note": f"rmin={DYN_RMIN_KM}km Nphi={DYN_NPHI} dyn window "
                              f"t0={T0_H}h; scheme: corrected Coriolis + upwind-r",
                 "cat1": [None]*100, "cat3": [None]*100, "cat5": [None]*100}
             for k, (fn, note) in PRODUCTS.items()}
    failures = []
    for cat in ("cat1", "cat3", "cat5"):
        for bi in range(100 // BATCH + (1 if 100 % BATCH else 0)):
            path = os.path.join(CKPT, f"{cat}_b{bi}.json")
            if not os.path.exists(path):
                print(f"missing checkpoint {path}; not assembling", flush=True)
                ok = False
                continue
            ck = json.load(open(path))
            failures += ck["failures"]
            for k in PRODUCTS:
                for row, idx in zip(ck["peaks"][k], ck["indices"]):
                    prods[k][cat][idx] = row
    if not ok:
        return
    for k, (fn, _) in PRODUCTS.items():
        path = os.path.join(WEB, fn)
        json.dump(prods[k], open(path, "w"))
        print(f"Wrote {path} ({os.path.getsize(path)/1e6:.2f} MB)", flush=True)
    if failures:
        print(f"WARNING: {len(failures)} storm/variant failures: {failures}", flush=True)
    else:
        print("No NaN failures.", flush=True)


def main():
    global WANT_FRAMES, FRAMES_ONLY
    ap = argparse.ArgumentParser()
    ap.add_argument("--validate", action="store_true",
                    help="single-storm batch (cat3[0]) compared against Phase 0 output")
    ap.add_argument("--frames-only", action="store_true", dest="frames_only",
                    help="dump animation frames WITHOUT recomputing peaks: skips the "
                         "B and C marches entirely (their peaks already exist), so only "
                         "the marine pass, the rough spin-up and march D are run")
    ap.add_argument("--frames", action="store_true",
                    help="also dump per-(cat,vector) uint8 animation frames for "
                         "product D (roughness + K&D) into outputs/web/dyn_frames/")
    ap.add_argument("--only", default=None,
                    help="restrict to one category (cat1|cat3|cat5), for prototyping")
    ap.add_argument("--batches", type=int, default=None,
                    help="stop after N batches per category, for prototyping")
    ap.add_argument("--limit", type=int, default=None,
                    help="cap storms per batch (march cost scales with batch size), "
                         "so the frame output can be validated in minutes")
    args = ap.parse_args()
    FRAMES_ONLY = args.frames_only
    WANT_FRAMES = args.frames or FRAMES_ONLY
    os.makedirs(CKPT, exist_ok=True)
    device = H.device_select()
    grid = json.load(open(os.path.join(WEB, "grid.json")))
    inputs = json.load(open(os.path.join(WEB, "inputs.json")))
    rough = json.load(open(os.path.join(WEB, "roughness.json")))
    pts = grid["points"]
    ew = torch.tensor([p["ew"] for p in pts], dtype=torch.float32, device=device)
    ns = torch.tensor([p["ns"] for p in pts], dtype=torch.float32, device=device)
    z0_fn = make_z0_fn(grid, rough, device)
    is_land = build_track_land(grid)

    if args.validate:
        path = os.path.join(CKPT, "validate_b0.json")
        if os.path.exists(path):
            os.remove(path)
        # reuse run_batch machinery on a batch of one known storm
        recs = [inputs["cat3"][0]]
        run_batch("validate", 0, recs, [0], grid, z0_fn, is_land, ew, ns, device)
        ck = json.load(open(path))
        ph0 = json.load(open(os.path.join(ROOT, "outputs", "dynamic", "dyn_cat3_0.json")))
        print("validation vs Phase 0 (window peaks, mph):", flush=True)
        for k in ("B", "C", "D"):
            b = torch.tensor(ck["peaks"][k][0])
            p = torch.tensor(ph0["variants"][k]["peak_dyn"])
            d = (b - p).abs()
            print(f"  {k}: max|d|={float(d.max()):.2f}  mean|d|={float(d.mean()):.3f}", flush=True)
        return

    t0 = time.time()
    cats = (args.only,) if args.only else ("cat1", "cat3", "cat5")
    partial = bool(args.only or args.batches)
    for cat in cats:
        order = sorted(range(len(inputs[cat])), key=lambda i: -float(inputs[cat][i]["VT"]))
        nb = 0
        for bi in range(0, len(order), BATCH):
            if args.batches and nb >= args.batches:
                break
            idxs = order[bi:bi + BATCH]
            if args.limit:
                idxs = idxs[:args.limit]
            recs = [inputs[cat][i] for i in idxs]
            run_batch(cat, bi // BATCH, recs, idxs, grid, z0_fn, is_land, ew, ns, device)
            nb += 1
            done = time.time() - t0
            print(f"== elapsed {done/3600:.2f} h ==", flush=True)
    if WANT_FRAMES:
        write_frame_manifest()
    if partial:
        print("partial run (--only/--batches): skipping assemble()", flush=True)
        return
    assemble(inputs)


def write_frame_manifest():
    """Tell the viewer the frame geometry and which storms are available."""
    have = sorted(f for f in os.listdir(FRAMES_DIR) if f.endswith(".bin")) \
        if os.path.isdir(FRAMES_DIR) else []
    man = {
        "product": "D (surface roughness + Kaplan-DeMaria decay)",
        "dtype": "uint8", "unit": "mph", "scale": FRAME_SCALE,   # mph = byte * scale
        "n_vertices": 840, "n_frames": N_FRAMES,
        "t_min": T_MIN, "t_max": T_MAX, "dt": FRAME_DT,
        "layout": "frame-major: frame f occupies bytes [f*840, (f+1)*840)",
        "file": "dyn_frames/{cat}_v{vector}.bin",
        "available": have,
        "note": "Outside the dynamic window [t0, t1] the frames carry the frozen "
                "marine translation: before t0 the storm is offshore and the field "
                "genuinely is marine; after t1 it is 60+ mi past the grid edge, where "
                "the marine/decayed distinction is negligible.",
    }
    json.dump(man, open(os.path.join(WEB, "dyn_frames.json"), "w"))
    print(f"wrote dyn_frames.json ({len(have)} storms)", flush=True)


if __name__ == "__main__":
    main()
