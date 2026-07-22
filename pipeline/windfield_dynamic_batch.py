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
# Input/manifest paths and the per-checkpoint/frame dirs are module globals so the
# --constrained switch (in main) can point the whole run at the lumped design's own
# file set, leaving the legacy cat1/cat3/cat5 dynamic products in place during migration.
INPUTS_JSON = os.path.join(WEB, "inputs.json")
MANIFEST_JSON = os.path.join(WEB, "dyn_frames.json")
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
FRAMES_ALL  = False                                   # set by --frames-all
FULL        = False                                   # set by --full (peaks + all frames, one march)


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
        # Holland B direct from the constrained design (Powell Eq.7); legacy path maps a
        # WSP quantile. Same bypass as windfield_grid.make_args.
        "B": [float(r["B"]) if "B" in r else wsp_to_B(r["WSP"]) for r in recs],
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
    # Checkpoint is named by the batch's FIRST vector, not the batch index, so that
    # independent subset runs (e.g. the 155 cheap storms now, the expensive tail one at a
    # time later) never collide -- each storm belongs to exactly one batch and vectors are
    # unique. assemble() globs all of them and merges by the stored per-storm indices.
    path = os.path.join(CKPT, f"{cat}_v{int(recs[0]['vector'])}.json")
    # In frames-only mode the peaks are NOT recomputed -- they already exist and are
    # committed -- so an existing checkpoint is expected and must not short-circuit us.
    if os.path.exists(path) and not FRAMES_ONLY:
        print(f"[{cat} b{bi}] checkpoint exists, skipping", flush=True)
        return
    want = ("A", "B", "C", "D") if FRAMES_ALL else ("D",)
    if FRAMES_ONLY and all(
            os.path.exists(os.path.join(FRAMES_DIR, f"{cat}_v{int(r['vector'])}_{p}.bin"))
            for r in recs for p in want):
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

    # Frame buffers. The marine pass lays the pre-t0 base into framesA; each march
    # then overwrites its own copy inside [t0, t1]. One buffer per product, because
    # the viewer's four land-checkbox states map onto four different fields:
    #   A neither   B decay only   C roughness only   D both (the default)
    prods = ("A", "B", "C", "D") if (FRAMES_ALL or FULL) else ("D",)
    frames = ({p: torch.zeros((len(recs), ew.numel(), N_FRAMES), device=device)
               for p in prods} if WANT_FRAMES else {})
    framesD = frames.get("D")

    # marine spin-up -> product A + V0 -> K&D schedules
    dynM = mkdyn(None, None)
    uM, vM = H.pde_dynamic_spinup_batch(S, dynM)
    speed0 = torch.sqrt((uM*S["erx"] + vM*S["etx"] + S["c_x"])**2
                        + (uM*S["ery"] + vM*S["ety"] + S["c_y"])**2)
    # The marine pass lays the base layer across the WHOLE window. For product A that
    # base IS the answer (no march: the marine field is translation-invariant, so the
    # dynamic solution equals the frozen one under constant forcing). For B/C/D it is
    # the pre-t0 lead-in, which each march then overwrites inside [t0, t1].
    base = frames.get("A") if (FRAMES_ALL or FULL) else framesD
    peakA, pre_t0, V0 = frozen_marine(speed0, S, recs, hours, ew, ns, device, frames=base)
    if (FRAMES_ALL or FULL) and frames:
        for p in ("B", "C", "D"):
            frames[p].copy_(frames["A"])
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

    # Marches B and C produce the peaks for products B and C, which already exist and
    # are committed -- so plain --frames-only skips them (they are the expensive ones:
    # 400 s and 2701 s against 142 s for the marine pass). --frames-all runs them
    # anyway, because their FRAMES do not exist and the viewer needs one field per
    # land-checkbox state.
    need_BC = FRAMES_ALL or not FRAMES_ONLY
    if need_BC:
        march("B", dp_scale, None, uM.clone(), vM.clone(), frames=frames.get("B"))
    dynR = mkdyn(None, z0_fn)
    uR, vR = H.pde_dynamic_spinup_batch(S, dynR)
    if need_BC:
        march("C", None, z0_fn, uR.clone(), vR.clone(), frames=frames.get("C"))
    march("D", dp_scale, z0_fn, uR, vR, frames=frames.get("D"))

    if FULL:
        # Dissipative constraint (same invariant assemble() enforces): the roughness
        # products cannot exceed their no-roughness bounds, C<=A and D<=B per vertex.
        # Apply it to the batch peaks BEFORE clamping the frames to them, so the
        # animation settles onto exactly the constrained static footprint.
        for rk, bk in (("C", "A"), ("D", "B")):
            if rk in peaks and bk in peaks:
                peaks[rk] = torch.minimum(peaks[rk], peaks[bk])
    if WANT_FRAMES:
        for p, F in frames.items():
            # FULL clamps to the peaks computed in THIS batch (no committed products yet);
            # frames-only/-all clamp to the already-committed products via stored_peaks().
            write_frames(cat, recs, F, p, peak_rows=(peaks[p] if FULL else None))
    if (FRAMES_ONLY or FRAMES_ALL) and not FULL:
        print(f"[{cat} b{bi}] frames done ({time.time()-t_start:.0f}s total; "
              f"peaks left untouched)", flush=True)
        return

    out = {"cat": cat, "batch": bi, "indices": idxs, "t1_h": t1_h,
           "failures": fails,
           "peaks": {k: [[round(float(x), 1) for x in row] for row in v.tolist()]
                     for k, v in peaks.items()}}
    json.dump(out, open(path, "w"))
    print(f"[{cat} b{bi}] wrote checkpoint ({time.time()-t_start:.0f}s total)", flush=True)


_STORED_PEAKS = {}


def stored_peaks(prod, cat):
    """The peak field the MAP draws, straight from the committed powell_dyn*.json.

    Frames are clamped to this, not to a freshly recomputed peak: the static footprint
    on screen comes from these files, and an animation that momentarily exceeded the
    footprint it settles on would be visibly wrong.
    """
    if prod not in _STORED_PEAKS:
        fn = PRODUCTS[prod][0]
        _STORED_PEAKS[prod] = json.load(open(os.path.join(WEB, fn)))
    return _STORED_PEAKS[prod][cat]


def write_frames(cat, recs, F, prod, peak_rows=None):
    """One uint8 file per (product, cat, vector): 840 vertices x 73 frames.

    Values are stored as mph/FRAME_SCALE so the strongest storms (up to 510 mph) fit
    in a byte; it keeps a storm at 60 KB so the browser can fetch just the one on
    screen. Clipping would be silent data loss, so it is checked, not assumed.

    Frames are clamped to the product's peak. A field cannot exceed its own maximum,
    but the frames CAN without this: after the dynamic window closes at t1 they fall
    back to the frozen-marine base, which carries neither the K&D decay nor the in-PDE
    drag, so a slow storm still near the grid at t1 shows a few vertices above the
    decayed peak (measured on product D: 98 cells of 18.4M, worst 6.9 mph). The clamp
    target is `peak_rows` (the peaks just computed in this batch, --full mode) when
    given, else the committed product read by stored_peaks() (frames-only/-all).
    """
    os.makedirs(FRAMES_DIR, exist_ok=True)
    if peak_rows is not None:
        pk = peak_rows.to(dtype=F.dtype, device=F.device)          # (n_storms, 840)
    else:
        pk = torch.tensor([stored_peaks(prod, cat)[int(r["vector"]) - 1] for r in recs],
                          dtype=F.dtype, device=F.device)
    F = torch.minimum(F, pk[:, :, None])
    over = int((F > FRAME_MAX_MPH).sum())
    if over:
        print(f"  WARNING: {over} frame values exceed {FRAME_MAX_MPH:.0f} mph and "
              f"were clipped -- raise FRAME_SCALE", flush=True)
    q = (F / FRAME_SCALE).clamp(0.0, 255.0).round().to(torch.uint8).cpu().numpy()
    for b, rec in enumerate(recs):
        v = int(rec["vector"])
        # (frames, vertices) so the viewer can slice one frame contiguously
        q[b].T.tofile(os.path.join(FRAMES_DIR, f"{cat}_v{v}_{prod}.bin"))


def assemble(inputs):
    """Merge ALL checkpoints in CKPT into the product files, by per-storm index.

    Glob-based and incremental: it reads whatever `{cat}_v*.json` checkpoints exist and
    fills each storm's slot, so a partial run (e.g. only the 155 cheap storms) writes
    valid products with the not-yet-run storms left as null, and a later run of the
    remaining storms simply re-assembles to fill them in. Reports coverage per group.
    """
    import glob
    groups = [k for k, v in inputs.items()
              if isinstance(v, list) and v and isinstance(v[0], dict)]
    prods = {k: {"unit": "mph", "note": note,
                 "grid_note": f"rmin={DYN_RMIN_KM}km Nphi={DYN_NPHI} dyn window "
                              f"t0={T0_H}h; scheme: corrected Coriolis + upwind-r",
                 **{g: [None] * len(inputs[g]) for g in groups}}
             for k, (fn, note) in PRODUCTS.items()}
    failures = []
    filled = {g: 0 for g in groups}
    for path in sorted(glob.glob(os.path.join(CKPT, "*_v*.json"))):
        ck = json.load(open(path))
        cat = ck["cat"]
        if cat not in prods[next(iter(PRODUCTS))]:
            continue
        failures += ck.get("failures", [])
        for k in PRODUCTS:
            for row, idx in zip(ck["peaks"][k], ck["indices"]):
                if prods[k][cat][idx] is None:
                    filled[cat] += 1 if k == next(iter(PRODUCTS)) else 0
                prods[k][cat][idx] = row

    # PHYSICAL CONSTRAINT: surface roughness (drag) is DISSIPATIVE, so the roughness
    # products cannot exceed their no-roughness counterparts at any vertex -- C ("rough")
    # <= A ("marine") and D ("kd_rough") <= B ("kd"). Enforce it here to remove residual
    # numerical overshoots at the under-resolved tight eyewall and domain boundaries (a
    # milder remnant of the instability the MUSCL azimuthal scheme fixed; the extreme
    # tight-eye storms otherwise show a spurious in-PDE-roughness spike deep inland / at
    # the far edge where drag physically can only slow the wind). This does NOT touch the
    # marine/K-D products (A, B) nor the dynamic-vs-frozen difference (B may exceed A).
    _CONSTRAIN = [("C", "A"), ("D", "B")]   # (roughness product, its no-roughness bound)
    for rough_k, base_k in _CONSTRAIN:
        if rough_k in prods and base_k in prods:
            for g in groups:
                for i, (rr, bb) in enumerate(zip(prods[rough_k][g], prods[base_k][g])):
                    if rr is not None and bb is not None:
                        prods[rough_k][g][i] = [min(a, b) for a, b in zip(rr, bb)]

    for k, (fn, _) in PRODUCTS.items():
        path = os.path.join(WEB, fn)
        json.dump(prods[k], open(path, "w"))
    for g in groups:
        n = len(inputs[g])
        missing = [i + 1 for i in range(n) if prods[next(iter(PRODUCTS))][g][i] is None]
        tag = "COMPLETE" if not missing else f"PARTIAL: {len(missing)} of {n} still null"
        print(f"Assembled {g}: {filled[g]}/{n} filled -- {tag}", flush=True)
        if missing and len(missing) <= 60:
            print(f"  missing vectors: {missing}", flush=True)
    for k, (fn, _) in PRODUCTS.items():
        print(f"Wrote {os.path.join(WEB, fn)} "
              f"({os.path.getsize(os.path.join(WEB, fn))/1e6:.2f} MB)", flush=True)
    print(f"NaN failures: {len(failures)}" + (f" {failures}" if failures else ""), flush=True)


def main():
    global WANT_FRAMES, FRAMES_ONLY, FRAMES_ALL, FULL
    ap = argparse.ArgumentParser()
    ap.add_argument("--validate", action="store_true",
                    help="single-storm batch (cat3[0]) compared against Phase 0 output")
    ap.add_argument("--frames-all", action="store_true", dest="frames_all",
                    help="dump animation frames for ALL FOUR land products (A/B/C/D) so "
                         "every land-checkbox state can be animated. Runs the B and C "
                         "marches, which --frames-only skips, so it is the long one. "
                         "Peaks are still left untouched.")
    ap.add_argument("--frames-only", action="store_true", dest="frames_only",
                    help="dump animation frames WITHOUT recomputing peaks: skips the "
                         "B and C marches entirely (their peaks already exist), so only "
                         "the marine pass, the rough spin-up and march D are run")
    ap.add_argument("--frames", action="store_true",
                    help="also dump per-(cat,vector) uint8 animation frames for "
                         "product D (roughness + K&D) into outputs/web/dyn_frames/")
    ap.add_argument("--full", action="store_true",
                    help="ONE march produces BOTH the peak checkpoints AND all four "
                         "products' animation frames -- the peaks and frames come from the "
                         "same marches, so this replaces the peaks-then-frames two-run "
                         "workflow. Frames are clamped to the peaks computed in the same "
                         "batch (with the dissipative C<=A, D<=B constraint applied), so "
                         "the animation settles exactly onto the static footprint. Use this "
                         "for any new design: one notebook, one run.")
    ap.add_argument("--only", default=None,
                    help="restrict to one group (cat1|cat3|cat5, or 'all'), for prototyping")
    ap.add_argument("--constrained", action="store_true",
                    help="run the lumped constrained n=200 design: reads "
                         "inputs_constrained.json, writes powell_dyn_constrained*.json "
                         "and dyn_frames_constrained/ (legacy products left untouched)")
    ap.add_argument("--batches", type=int, default=None,
                    help="stop after N batches per category, for prototyping")
    ap.add_argument("--limit", type=int, default=None,
                    help="cap storms per batch (march cost scales with batch size), "
                         "so the frame output can be validated in minutes")
    ap.add_argument("--select", default=None,
                    help="subset of storms to run: 'bulk' (VT>=8 AND CP>=900, the cheap "
                         "155), 'tail' (the expensive 45), or 'vec:1,2,3' (explicit "
                         "vectors). Checkpoints accumulate; re-run assemble to merge.")
    ap.add_argument("--batch-size", type=int, default=BATCH, dest="batch_size",
                    help=f"storms marched together per batch (default {BATCH}). The march "
                         "is latency-bound (many tiny sequential steps), so on a big-memory "
                         "GPU (A100) a much larger batch amortizes the per-step overhead "
                         "over more storms for near-free throughput. Cost-sorting keeps a "
                         "batch's timestep homogeneous, so large batches don't over-shrink "
                         "dt for the mild storms. Try 100-155 on an A100.")
    ap.add_argument("--assemble-only", action="store_true", dest="assemble_only",
                    help="skip solving; just merge existing checkpoints into the products")
    args = ap.parse_args()
    FULL = args.full
    FRAMES_ALL = args.frames_all
    FRAMES_ONLY = args.frames_only or FRAMES_ALL
    WANT_FRAMES = args.frames or FRAMES_ONLY or FULL
    if args.constrained:
        global CKPT, FRAMES_DIR, PRODUCTS, INPUTS_JSON, MANIFEST_JSON
        INPUTS_JSON = os.path.join(WEB, "inputs_constrained.json")
        CKPT = os.path.join(ROOT, "outputs", "dynamic", "precompute_constrained")
        FRAMES_DIR = os.path.join(WEB, "dyn_frames_constrained")
        MANIFEST_JSON = os.path.join(WEB, "dyn_frames_constrained.json")
        PRODUCTS = {k: (fn.replace(".json", "_constrained.json"), note)
                    for k, (fn, note) in PRODUCTS.items()}
    os.makedirs(CKPT, exist_ok=True)
    device = H.device_select()
    grid = json.load(open(os.path.join(WEB, "grid.json")))
    inputs = json.load(open(INPUTS_JSON))
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

    groups = [k for k, v in inputs.items()
              if isinstance(v, list) and v and isinstance(v[0], dict)]

    if args.assemble_only:
        assemble(inputs)
        return

    # Storm selection (for piecewise runs). The march cost of a storm ~ (long window from
    # low VT) x (tiny timestep from high intensity), two INDEPENDENT penalties; 155 of the
    # 200 pay neither. 'bulk' runs those 155; 'tail' the 45 that pay at least one; 'vec:'
    # names explicit storms so the expensive ones can be studied one at a time.
    def selected(rec):
        if not args.select or args.select == "all":
            return True
        vt, cp = float(rec["VT"]), float(rec["CP"])
        if args.select == "bulk":
            return vt >= 8.0 and cp >= 900.0
        if args.select == "tail":
            return not (vt >= 8.0 and cp >= 900.0)
        if args.select.startswith("vec:"):
            return int(rec["vector"]) in {int(x) for x in args.select[4:].split(",") if x}
        raise SystemExit(f"bad --select {args.select!r}")

    t0 = time.time()
    cats = (args.only,) if args.only else tuple(groups)
    partial = bool(args.only or args.batches or args.select)
    for cat in cats:
        # Sort ascending by a march-cost proxy so the CHEAP storms run first (early
        # progress + a real projection) and the expensive intense/slow ones cluster at the
        # end. cost ~ sqrt(deficit) / VT: high intensity (small dt) and low VT (long window)
        # both raise it. Batching by cost also keeps a single expensive storm from dragging
        # a whole batch of cheap ones into tiny shared timesteps.
        def cost(i):
            r = inputs[cat][i]
            return math.sqrt(max(float(r["FFP"]) - float(r["CP"]), 1e-6)) / float(r["VT"])
        order = [i for i in sorted(range(len(inputs[cat])), key=cost)
                 if selected(inputs[cat][i])]
        print(f"[{cat}] {len(order)} storms selected"
              f"{' (' + args.select + ')' if args.select else ''}", flush=True)
        bsz = args.batch_size
        nb = 0
        for bi in range(0, len(order), bsz):
            if args.batches and nb >= args.batches:
                break
            idxs = order[bi:bi + bsz]
            if args.limit:
                idxs = idxs[:args.limit]
            recs = [inputs[cat][i] for i in idxs]
            run_batch(cat, bi // bsz, recs, idxs, grid, z0_fn, is_land, ew, ns, device)
            nb += 1
            done = time.time() - t0
            print(f"== elapsed {done/3600:.2f} h ==", flush=True)
    if WANT_FRAMES:
        write_frame_manifest()
    # assemble() is glob-based and incremental, so it is safe to run after a subset: it
    # writes valid products with the not-yet-run storms left null. Skip only for --only
    # (legacy per-category prototyping) and --batches (mid-run prototyping).
    if args.only or args.batches:
        print("prototyping run (--only/--batches): skipping assemble()", flush=True)
        return
    assemble(inputs)


def write_frame_manifest():
    """Tell the viewer the frame geometry and which (product, storm) files exist.

    Products map onto the viewer's two land checkboxes:
        A  neither            (marine)
        B  Kaplan-DeMaria decay only
        C  surface roughness only
        D  both  <- the viewer default
    """
    have = sorted(f for f in os.listdir(FRAMES_DIR) if f.endswith(".bin")) \
        if os.path.isdir(FRAMES_DIR) else []
    byprod = {}
    for f in have:
        byprod.setdefault(f.rsplit("_", 1)[-1][:-4], []).append(f)
    man = {
        "products": {k: PRODUCTS[k][1] for k in ("A", "B", "C", "D")},
        "land_map": {"neither": "A", "decay": "B", "roughness": "C", "both": "D"},
        "dtype": "uint8", "unit": "mph", "scale": FRAME_SCALE,   # mph = byte * scale
        "n_vertices": 840, "n_frames": N_FRAMES,
        "t_min": T_MIN, "t_max": T_MAX, "dt": FRAME_DT,
        "layout": "frame-major: frame f occupies bytes [f*840, (f+1)*840)",
        "file": os.path.basename(FRAMES_DIR) + "/{cat}_v{vector}_{product}.bin",
        "counts": {k: len(v) for k, v in sorted(byprod.items())},
        "note": "Frames are clamped to the product's stored peak. Outside the dynamic "
                "window [t0, t1] they carry the frozen marine translation: before t0 the "
                "storm is offshore and the field genuinely IS marine; after t1 it is 60+ "
                "mi past the grid edge, where the marine/decayed distinction is negligible.",
    }
    json.dump(man, open(MANIFEST_JSON, "w"))
    print(f"wrote {os.path.basename(MANIFEST_JSON)} ({man['counts']})", flush=True)


if __name__ == "__main__":
    main()
