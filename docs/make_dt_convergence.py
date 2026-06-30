#!/usr/bin/env python3
"""Time-step convergence figure for docs/FormS6.tex.

Recomputes per-grid-point peak wind -> loss (HAZUS/ARA vulnerability curve) at a
range of integration steps (hourly down to 1-min) and plots the loss
underestimate vs the 1-min reference. Demonstrates that the 1-min step used by
the model is converged and that hourly stepping (as some modelers use) misses a
material fraction of loss, especially at individual grid points.

The expensive boundary-layer PDE is solved ONCE per storm vector; only the cheap
field re-sampling is repeated per step size.

Run: ./venv/bin/python docs/make_dt_convergence.py
Author: Paul Fishwick and Claude Code
"""
import os, sys, json
import numpy as np
import torch
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pipeline"))
import windfield_grid as G

N_VEC = 10                      # vectors averaged per category
DTS = [60, 30, 15, 6, 2, 1]     # minutes
OUT = os.path.join(ROOT, "docs", "figures", "dt_convergence.png")


def marine_peak(speed_ms, meta, rec, ew, ns, dt_min, dev):
    """Per-vertex marine peak wind (mph) sampling one PDE solve at step dt_min."""
    dt = dt_min / 60.0
    ht = torch.arange(G.T_MIN, G.T_MAX + dt / 2, dt, dtype=torch.float32, device=dev)
    r_src, phi_src = meta["r"], meta["phi"]
    rmax_out = float(r_src[-1])
    vt = float(rec["VT"]); rmax_miles = float(rec["Rmax"]); cf_base = float(rec["CF"])
    ew_c = vt * ht
    dx = ew[:, None] - ew_c[None, :]
    y = ns[:, None].expand(-1, ht.numel())
    r_miles = torch.sqrt(dx * dx + y * y); r_m = r_miles * G.MILE_M
    phi = torch.atan2(y, -dx) % (2 * np.pi)
    grad = G.H.bilinear_polar(speed_ms, r_src, phi_src, r_m, phi)
    grad = torch.where(r_m > rmax_out, torch.zeros_like(grad), grad)
    cf = G.cf_effective(r_miles, rmax_miles, cf_base).clamp(min=0.0)
    surf = grad * cf * G.MS_TO_MPH
    return surf.max(dim=1).values.cpu().numpy()


def main():
    dev = G.H.device_select()
    grid = json.load(open(G.GRID)); inputs = json.load(open(G.INPUTS))
    vul = json.load(open(os.path.join(ROOT, "outputs", "web", "vulnerability.json")))
    xs, mdr = np.array(vul["xs"]), np.array(vul["mdr"])
    pts = grid["points"]
    ew = torch.tensor([p["ew"] for p in pts], dtype=torch.float32, device=dev)
    ns = torch.tensor([p["ns"] for p in pts], dtype=torch.float32, device=dev)
    land = np.array([p["land"] for p in pts])
    to_mdr = lambda w: np.interp(w, xs, mdr)

    cats = ["cat1", "cat3", "cat5"]
    total_miss = {c: [] for c in cats}     # % aggregate loss missed vs 1-min
    worst_miss = {c: [] for c in cats}     # mean of per-vector worst grid-point miss %

    for c in cats:
        # accumulate per-dt loss across vectors; worst-point per vector
        agg_loss = {d: 0.0 for d in DTS}
        worst_acc = {d: [] for d in DTS}
        for vi in range(N_VEC):
            rec = inputs[c][vi]
            args = G.make_args(rec)
            speed_ms, meta = G.H.pde_steady_marine(args, device=dev)
            peaks = {d: marine_peak(speed_ms, meta, rec, ew, ns, d, dev) for d in DTS}
            refL = to_mdr(peaks[1])[land]
            for d in DTS:
                L = to_mdr(peaks[d])[land]
                agg_loss[d] += L.sum()
                with np.errstate(divide="ignore", invalid="ignore"):
                    pm = np.where(refL > 1e-6, 100 * (refL - L) / refL, 0.0)
                worst_acc[d].append(float(pm.max()))
            print(f"  {c} v{vi+1}/{N_VEC}", flush=True)
        ref = agg_loss[1]
        for d in DTS:
            total_miss[c].append(100 * (ref - agg_loss[d]) / ref)
            worst_miss[c].append(float(np.mean(worst_acc[d])))

    # ---- plot ----
    colors = {"cat1": "#2563eb", "cat3": "#d97706", "cat5": "#dc2626"}
    fig, ax = plt.subplots(1, 2, figsize=(10, 4.2))
    for c in cats:
        lbl = "Cat " + c[-1]
        ax[0].plot(DTS, total_miss[c], "o-", color=colors[c], label=lbl)
        ax[1].plot(DTS, worst_miss[c], "o-", color=colors[c], label=lbl)
    for a, title in zip(ax, ["Total loss underestimate", "Worst grid-point loss underestimate"]):
        a.set_xscale("log"); a.set_xticks(DTS); a.set_xticklabels([str(d) for d in DTS])
        a.invert_xaxis()
        a.set_xlabel("integration step (minutes)")
        a.set_ylabel("% missed vs 1-min reference")
        a.set_title(title)
        a.grid(True, alpha=0.3)
        a.axvline(60, color="#888", ls=":", lw=1)
        a.legend()
    fig.suptitle(f"Grid-point loss sensitivity to time step "
                 f"(mean of {N_VEC} vectors/category, −12..+24 h window)")
    fig.tight_layout()
    fig.savefig(OUT, dpi=300)
    print(f"\nWrote {OUT}")
    print("hourly (60-min) loss missed vs 1-min:")
    for c in cats:
        print(f"  Cat {c[-1]}: total {total_miss[c][0]:.1f}% | worst point {worst_miss[c][0]:.1f}%")


if __name__ == "__main__":
    main()
