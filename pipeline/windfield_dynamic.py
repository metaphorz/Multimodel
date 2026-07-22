#!/usr/bin/env python3
"""Phase 0 prototype: DYNAMIC (physical-time) Powell windfield vs today's
frozen steady-state translation.

For one input vector this runs the PDE dynamically (pde_dynamic_marine) in the
four viewer checkbox states and compares each against its frozen-field
counterpart built from the SAME coarse steady solve (so differences isolate the
dynamics, not the grid):

  A  dyn marine                 vs frozen marine          <- regression: must match
  B  dyn K&D-through-pressure   vs frozen * s(t) scalar   <- lagged decay
  C  dyn land-z0 drag           vs frozen * static factor <- asymmetric land drag
  D  dyn both                   vs frozen * s(t) * factor

Decay is the SAME Kaplan & DeMaria schedule as windfield_grid.py (imported);
dynamically it scales the pressure forcing (dp ~ V^2) so the wind follows the
K&D target with its physical boundary-layer lag. Land drag samples the
NLCD-derived per-vertex z0 (roughness.json) under the moving storm; off-lattice
positions are treated as marine (the strong-wind core stays on the lattice).

Grid for the dynamic march: rmin 0.5 -> 4 km, Nphi 360 -> 180 (relaxes the CFL
step 0.06 s -> ~1 s); a fine-vs-coarse steady check quantifies the error.

Run:  source venv/bin/activate && python pipeline/windfield_dynamic.py [cat idx]
Outputs: outputs/dynamic/  (JSON + PNG comparisons), log to stdout.
"""
import os, sys, json, time, math, argparse
import torch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "vendor"))   # vendored PDE solver
import hurricane_pde_marine as H  # noqa: E402
from windfield_grid import (  # noqa: E402
    make_args, cf_effective, intensity_schedule, build_track_land,
    MILE_M, MS_TO_MPH, T_MIN, T_MAX, T_DT)

WEB = os.path.join(ROOT, "outputs", "web")
OUTDIR = os.path.join(ROOT, "outputs", "dynamic")

# dynamic-run grid (see module docstring); everything else stays make_args()
DYN_RMIN_KM, DYN_NPHI = 4.0, 180
DT_FORCING_S = 60.0          # forcing update + sampling interval (Delta T = 1 min)
T0_H = -2.0                  # marine steady before this; coast interaction after
SPINUP_ITER = 10000          # safety cap; spin-up exits early on convergence (0.05 m/s
                             # drift). Lowered from 30000: with the MUSCL azimuthal
                             # scheme a well-behaved storm settles in a few thousand
                             # iters, and the march re-equilibrates from any residual.
Z0_MARINE = 2e-4


def coarse_args(rec):
    a = make_args(rec)
    a.rmin_km = DYN_RMIN_KM
    a.Nphi = DYN_NPHI
    return a


def make_z0_fn(grid, rough, device):
    """Nearest-vertex z0 (m) lookup on the 3-mile lattice; marine off-lattice."""
    ews = sorted(grid["ew_values"]); nss = sorted(grid["ns_values"])
    new, nns = len(ews), len(nss)
    step_ew = ews[1] - ews[0]; step_ns = nss[1] - nss[0]
    Z0 = torch.full((nns, new), Z0_MARINE, dtype=torch.float32, device=device)
    iew = {e: i for i, e in enumerate(ews)}; ins = {n: i for i, n in enumerate(nss)}
    for p, z0mm in zip(grid["points"], rough["z0_mm"]):
        Z0[ins[p["ns"]], iew[p["ew"]]] = max(z0mm / 1000.0, Z0_MARINE)

    def fn(ew_pt, ns_pt):
        ie = torch.round((ew_pt - ews[0]) / step_ew).long()
        in_ = torch.round((ns_pt - nss[0]) / step_ns).long()
        inside = (ie >= 0) & (ie < new) & (in_ >= 0) & (in_ < nns)
        z = Z0[in_.clamp(0, nns - 1), ie.clamp(0, new - 1)]
        return torch.where(inside, z, torch.full_like(z, Z0_MARINE))
    return fn


def frozen_series(speed_ms, meta, rec, hours_t, ew, ns):
    """(840, nt) surface mph from the frozen translated field (as solve_all)."""
    r_src, phi_src = meta["r"], meta["phi"]
    rmax_out = float(r_src[-1])
    vt = float(rec["VT"])
    ew_c = vt * hours_t
    dx = ew[:, None] - ew_c[None, :]
    y_north = ns[:, None].expand(-1, hours_t.numel())
    r_miles = torch.sqrt(dx * dx + y_north * y_north)
    r_m = r_miles * MILE_M
    phi = torch.atan2(y_north, -dx) % (2 * math.pi)
    grad = H.bilinear_polar(speed_ms, r_src, phi_src, r_m, phi)
    grad = torch.where(r_m > rmax_out, torch.zeros_like(grad), grad)
    cf = cf_effective(r_miles, float(rec["Rmax"]), float(rec["CF"])).clamp(min=0.0)
    return grad * cf * MS_TO_MPH


def dyn_to_surface(series_ms, rec, times_h, ew, ns):
    """Apply the Form S-6 CF rule to the dynamic gradient-level series -> mph."""
    vt = float(rec["VT"])
    t = torch.tensor(times_h, dtype=torch.float32, device=series_ms.device)
    dx = ew[:, None] - (vt * t)[None, :]
    r_miles = torch.sqrt(dx * dx + (ns[:, None]) ** 2)
    cf = cf_effective(r_miles, float(rec["Rmax"]), float(rec["CF"])).clamp(min=0.0)
    return series_ms * cf * MS_TO_MPH


def main():
    cat = sys.argv[1] if len(sys.argv) > 1 else "cat3"
    idx = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    os.makedirs(OUTDIR, exist_ok=True)
    device = H.device_select()
    grid = json.load(open(os.path.join(WEB, "grid.json")))
    inputs = json.load(open(os.path.join(WEB, "inputs.json")))
    rough = json.load(open(os.path.join(WEB, "roughness.json")))
    rec = inputs[cat][idx]
    vt = float(rec["VT"])
    print(f"storm {cat}[{idx}]: VT={vt} CP={rec['CP']} Rmax={rec['Rmax']} "
          f"WSP={rec['WSP']} CF={rec['CF']}", flush=True)

    pts = grid["points"]
    ew = torch.tensor([p["ew"] for p in pts], dtype=torch.float32, device=device)
    ns = torch.tensor([p["ns"] for p in pts], dtype=torch.float32, device=device)
    factors = torch.tensor(rough["factors"], dtype=torch.float32, device=device)
    land = torch.tensor([1.0 if p["land"] else 0.0 for p in pts], device=device)
    stat_factor = torch.where(land > 0, factors, torch.ones_like(factors))
    is_land = build_track_land(grid)
    z0_fn = make_z0_fn(grid, rough, device)

    # dynamic window: cover the crossing (+60 mi past the west edge), cap at T_MAX
    t1_h = min(T_MAX, (117.0 + 60.0) / vt)
    print(f"dynamic window t = {T0_H} .. {t1_h:.1f} h", flush=True)

    # ---- the four dynamic runs -----------------------------------------------
    def run(tag, dp_scale, z0):
        dyn = argparse.Namespace(
            t0_h=T0_H, t1_h=t1_h, dt_forcing_s=DT_FORCING_S,
            dp_scale=dp_scale, z0_fn=z0, sample_ew=ew, sample_ns=ns,
            spinup_iter=SPINUP_ITER, snap_times_h=None)
        t0 = time.time()
        series, times_h, _, meta = H.pde_dynamic_marine(coarse_args(rec), dyn,
                                                        device=device)
        surf = dyn_to_surface(series, rec, times_h, ew, ns)
        assert not torch.isnan(surf).any(), f"{tag}: NaN in dynamic solution"
        print(f"  {tag}: {time.time()-t0:.0f}s, {len(times_h)} samples, "
              f"peak={surf.max():.1f} mph", flush=True)
        return surf, times_h, meta

    print("dynamic runs:", flush=True)
    dynA, times_h, metaA = run("A dyn marine", None, None)

    # ---- frozen counterparts from the SAME converged marine steady state -----
    # (meta['speed0'] = the spun-up field variant A starts from; using the
    # production 800-iter solve here would conflate convergence drift with
    # dynamics). The production fine-grid field is compared only as context.
    hours_full = torch.arange(T_MIN, T_MAX + T_DT / 2, T_DT, dtype=torch.float32,
                              device=device)
    froz = frozen_series(metaA["speed0"], metaA, rec, hours_full, ew, ns)
    sp_fine, meta_fine = H.pde_steady_marine(make_args(rec), device=device)
    froz_fine = frozen_series(sp_fine, meta_fine, rec, hours_full, ew, ns)
    pk_f, pk_c = froz_fine.max(dim=1).values, froz.max(dim=1).values
    d = (pk_c - pk_f).abs()
    print(f"production(fine,800it) vs converged dyn-marine vertex peaks: "
          f"max|d|={d.max():.2f} mph, mean|d|={d.mean():.2f} mph, "
          f"peaks {pk_f.max():.1f} vs {pk_c.max():.1f} mph", flush=True)

    # ---- K&D schedule (identical to production) on the full 1-min time grid --
    V0 = float(froz.max())
    s_full = intensity_schedule(V0, vt, hours_full, is_land)
    s_t = torch.tensor(s_full, dtype=torch.float32, device=device)

    def s_at(t_h):
        i = int(round((t_h - T_MIN) / T_DT))
        return s_full[max(0, min(len(s_full) - 1, i))]

    dynB, _, _ = run("B dyn K&D(dp)", lambda t: s_at(t) ** 2, None)
    dynC, _, _ = run("C dyn z0-drag", None, z0_fn)
    dynD, _, _ = run("D dyn both", lambda t: s_at(t) ** 2, z0_fn)

    # ---- frozen counterparts on the SAME window / same coarse field ----------
    i0 = int(round((T0_H - T_MIN) / T_DT))
    i1 = int(round((t1_h - T_MIN) / T_DT))
    frozW = froz[:, i0:i1 + 1]                       # marine, dyn window
    sW = s_t[i0:i1 + 1][None, :]
    cmp_pairs = {
        "A": (dynA, frozW),
        "B": (dynB, frozW * sW),
        "C": (dynC, frozW * stat_factor[:, None]),
        "D": (dynD, frozW * sW * stat_factor[:, None]),
    }

    # ---- report + save --------------------------------------------------------
    nsv = sorted(grid["ns_values"]); ewv = sorted(grid["ew_values"])
    idx2rc = {(p["ew"], p["ns"]): k for k, p in enumerate(pts)}
    pois = [(9, 0, "coast (ew=9)"), (45, 0, "mid (ew=45)"), (99, 0, "inland (ew=99)")]
    out = {"cat": cat, "idx": idx, "rec": rec, "t0_h": T0_H, "t1_h": t1_h,
           "grid_note": f"rmin={DYN_RMIN_KM}km Nphi={DYN_NPHI}",
           "production_vs_dynmarine_mph": {"max": float(d.max()), "mean": float(d.mean())},
           "variants": {}}
    for tag, (dyn_s, froz_s) in cmp_pairs.items():
        pk_d, pk_z = dyn_s.max(dim=1).values, froz_s.max(dim=1).values
        diff = pk_d - pk_z
        landm = land > 0
        print(f"variant {tag}: peak diff dyn-frozen (land vertices) "
              f"min={diff[landm].min():.1f} max={diff[landm].max():.1f} "
              f"mean={diff[landm].mean():.2f} mph", flush=True)
        out["variants"][tag] = {
            "peak_dyn": [round(float(v), 1) for v in pk_d.tolist()],
            "peak_frozen": [round(float(v), 1) for v in pk_z.tolist()],
        }
    out["times_h"] = [round(t, 4) for t in times_h]
    for ewp, nsp, label in pois:
        k = idx2rc[(ewp, nsp)]
        out.setdefault("pois", []).append({
            "ew": ewp, "ns": nsp, "label": label,
            **{f"dyn{t}": [round(float(v), 1) for v in cmp_pairs[t][0][k].tolist()]
               for t in cmp_pairs},
            **{f"frozen{t}": [round(float(v), 1) for v in cmp_pairs[t][1][k].tolist()]
               for t in cmp_pairs}})
    jpath = os.path.join(OUTDIR, f"dyn_{cat}_{idx}.json")
    json.dump(out, open(jpath, "w"))
    print(f"Wrote {jpath}", flush=True)

    # ---- figures ---------------------------------------------------------------
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    def as_img(vec):
        img = torch.full((len(nsv), len(ewv)), float("nan"))
        for kk, p in enumerate(pts):
            img[nsv.index(p["ns"]), ewv.index(p["ew"])] = vec[kk]
        return img.numpy()

    fig, axes = plt.subplots(4, 3, figsize=(16, 12), constrained_layout=True)
    for row, tag in enumerate(["A", "B", "C", "D"]):
        dyn_s, froz_s = cmp_pairs[tag]
        pk_d, pk_z = dyn_s.max(dim=1).values.cpu(), froz_s.max(dim=1).values.cpu()
        for col, (vec, ttl) in enumerate([
                (pk_z, f"{tag}: frozen peak (mph)"), (pk_d, f"{tag}: dynamic peak"),
                (pk_d - pk_z, f"{tag}: dyn - frozen")]):
            ax = axes[row, col]
            if col < 2:
                im = ax.imshow(as_img(vec), origin="lower", cmap="viridis",
                               extent=[ewv[0], ewv[-1], nsv[0], nsv[-1]])
            else:
                m = max(1.0, float(vec.abs().max()))
                im = ax.imshow(as_img(vec), origin="lower", cmap="RdBu_r",
                               vmin=-m, vmax=m,
                               extent=[ewv[0], ewv[-1], nsv[0], nsv[-1]])
            fig.colorbar(im, ax=ax, shrink=0.8)
            ax.set_title(ttl, fontsize=10)
            ax.set_xlabel("ew (mi west)"); ax.set_ylabel("ns (mi)")
    fig.suptitle(f"Powell dynamic vs frozen — {cat}[{idx}] "
                 f"VT={vt} Rmax={rec['Rmax']} (window {T0_H}..{t1_h:.1f} h)")
    p1 = os.path.join(OUTDIR, f"dyn_peakmaps_{cat}_{idx}.png")
    fig.savefig(p1, dpi=130); plt.close(fig)

    fig, axes = plt.subplots(len(pois), 1, figsize=(11, 9), sharex=True,
                             constrained_layout=True)
    tt = times_h
    for ax, (ewp, nsp, label) in zip(axes, pois):
        k = idx2rc[(ewp, nsp)]
        for tag, colr in [("B", "tab:red"), ("D", "tab:purple")]:
            ax.plot(tt, cmp_pairs[tag][0][k].cpu(), color=colr, label=f"dyn {tag}")
            ax.plot(tt, cmp_pairs[tag][1][k].cpu(), color=colr, ls="--", alpha=0.6,
                    label=f"frozen {tag}")
        ax.plot(tt, cmp_pairs["A"][0][k].cpu(), color="tab:blue", lw=0.8,
                label="dyn A (marine)")
        ax.set_title(f"{label}  ns={nsp}", fontsize=10)
        ax.set_ylabel("mph"); ax.legend(fontsize=7, ncol=3)
    axes[-1].set_xlabel("t (h)")
    fig.suptitle(f"Wind vs time — dynamic (solid) vs frozen (dashed), {cat}[{idx}]")
    p2 = os.path.join(OUTDIR, f"dyn_timeseries_{cat}_{idx}.png")
    fig.savefig(p2, dpi=130); plt.close(fig)
    print(f"Wrote {p1}\nWrote {p2}", flush=True)


if __name__ == "__main__":
    main()
