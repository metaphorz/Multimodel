#!/usr/bin/env python3
"""Phase B: fit machine-learning metamodels (GPR + neural net) offline.

For the DEFAULT viewer config (Powell + surface roughness, "Option A"), fit a
Gaussian-process regression and a small neural network to the 100 Latin-hypercube
input vectors, per hurricane category and per response (mean peak wind / loss-cost
%TLC). Export everything the browser needs to *evaluate* the models (no training
in JS) to outputs/web/metamodels.json, plus ARD sensitivities and Sobol indices.

The exported parameters are verified in-process to reproduce scikit-learn's
predict() within tight tolerance, so the JS evaluators match exactly.

Run:  ./venv/bin/python pipeline/fit_metamodels.py
Author: Pro Team & Claude Code
"""
import json
from pathlib import Path

import numpy as np
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import ConstantKernel, RBF, WhiteKernel
from sklearn.neural_network import MLPRegressor
from sklearn.preprocessing import StandardScaler
from sklearn.compose import TransformedTargetRegressor
from sklearn.pipeline import make_pipeline
from sklearn.model_selection import cross_val_score, KFold

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "outputs" / "web"
VARS = ["CP", "Rmax", "VT", "WSP", "CF", "FFP"]
CATS = ["cat1", "cat3", "cat5"]
# The wind responses do not depend on exposure; the loss response does, so it is fit
# once per exposure model. The viewer maps (response=tlc, exposure=census) -> "tlc_census".
RESPONSES = ["wind", "windmax", "tlc_uniform", "tlc_census", "tlc_tax"]
SEED = 0
# Sobol' Monte-Carlo budget. n=2048 (the original) left the indices drifting by
# ~0.05 -- as large as the interaction effect itself. Converged to <0.01 here.
#
# S1, ST and S2 are all taken from ONE A/B sample per replicate (see sobol_decompose).
# They used to come from two separate samplings at different budgets, which meant the
# S_i subtracted inside S_ij was a DIFFERENT estimate from the S_i reported alongside
# it: their sampling errors added instead of cancelling, and the decomposition summed
# to 1.014 +/- 0.005 -- overshooting 1, so the higher-order residual came out
# negative. One sample makes the decomposition self-consistent by construction.
# 10 replicates (was 3 and 2) resolve the small pairwise cells, which at 2 replicates
# were pure noise: CP x Rmax read 0.00017 there against 0.00080 +/- 0.00023 here.
N_SOBOL = 131072
REPS = 10


# ---- reproduce the viewer's default-config output metric in Python --------
def load_data(constrained=False):
    # The constrained n=200 run keeps its own file set so the legacy 3x100 viewer data
    # stays intact during the migration; --constrained switches the whole input group.
    tag = "_constrained" if constrained else ""
    grid = json.loads((WEB / "grid.json").read_text())
    powell = json.loads((WEB / f"powell{tag}.json").read_text())
    # Kaplan-DeMaria-decayed Powell footprint, same (100 vectors x 840 vertices) shape.
    # Fitting an emulator on this too is what lets the Sobol' indices follow the decay
    # toggle instead of being pinned to the no-decay configuration.
    powell_kd = json.loads((WEB / f"powell{tag}_kd.json").read_text())
    rough = json.loads((WEB / "roughness.json").read_text())
    vuln = json.loads((WEB / "vulnerability.json").read_text())
    inputs = json.loads((WEB / f"inputs{tag}.json").read_text())
    land = np.array([p["land"] for p in grid["points"]], dtype=bool)
    factors = np.array(rough["factors"], dtype=float)        # marine->land per vertex
    xs = np.array(vuln["xs"], dtype=float)
    mdr = np.array(vuln["mdr"], dtype=float)

    # Per-vertex insured value, one array per exposure model. The loss response is
    # value-WEIGHTED (as pctTLC() in analysis.js already is), so an emulator must be
    # fit per exposure model: value is brutally concentrated (Census Gini 0.84, top
    # 10% of vertices hold 65% of it), so WHERE the wind lands changes the total loss.
    # Fitting only Uniform -- as this pipeline used to -- makes every SRC/EPR/Sobol'
    # number for loss cost silently answer the Uniform question no matter what the
    # sidebar's Exposure selector says.
    n_land = int(land.sum())
    exposures = {"uniform": np.full(n_land, 100_000.0)}
    for key, fn in (("census", "exposure_census.json"), ("tax", "exposure_tax.json")):
        p = WEB / fn
        if p.exists():
            d = json.loads(p.read_text())
            vals = d.get("values") or next(v for v in d.values() if isinstance(v, list))
            exposures[key] = np.array(vals, dtype=float)[land]
    return grid, powell, powell_kd, factors, land, xs, mdr, inputs, exposures


def mdr_at(wind, xs, mdr):
    """Linear-interp MDR vs wind (gust factor 1.0), matching mdrAt() in the JS."""
    return np.interp(wind, xs, mdr, left=mdr[0], right=mdr[-1])


def metric_columns(powell, factors, land, xs, mdr, cat, response, exposure=None):
    """Per-vector scalar Y: an aggregate over the land GRID POINTS of one vector.

    Every response reduces axis 1 (the 682 land vertices), leaving one value per
    input vector -- never an average across vectors. `wind` is the domain-average
    storm, `windmax` the worst-hit vertex, `tlc` the domain total loss.

    A `tlc*` response is value-weighted by `exposure` ($ per land vertex):
        %TLC(i) = 100 * sum_x MDR(V(i,x)) * exposure(x) / sum_x exposure(x)
    which reduces to the old 100 * mean(MDR) only when exposure is uniform.
    """
    fields = np.array(powell[cat], dtype=float) * factors[None, :]    # (100, 840)
    landfields = fields[:, land]                                      # (100, n_land)
    if response.startswith("tlc"):
        m = mdr_at(landfields, xs, mdr)                               # (100, n_land)
        e = exposure if exposure is not None else np.ones(m.shape[1])
        return 100.0 * (m * e[None, :]).sum(axis=1) / e.sum()
    if response == "wind":
        return landfields.mean(axis=1)                               # mean land peak wind
    if response == "windmax":
        return landfields.max(axis=1)                                # worst-hit vertex
    raise ValueError(f"unknown response {response!r}")


# ---- GPR: fit + extract exact-prediction parameters -----------------------
def fit_gpr(Xz, y):
    kernel = (ConstantKernel(1.0, (1e-3, 1e3))
              * RBF([1.0] * Xz.shape[1], (1e-2, 1e2))
              + WhiteKernel(1e-3, (1e-8, 1e1)))
    gpr = GaussianProcessRegressor(kernel=kernel, normalize_y=True,
                                   n_restarts_optimizer=4, random_state=SEED)
    gpr.fit(Xz, y)
    k = gpr.kernel_
    const = float(k.k1.k1.constant_value)
    length_scale = np.atleast_1d(k.k1.k2.length_scale).astype(float)
    params = {
        "const": const,
        "length_scale": length_scale.tolist(),
        "x_train": gpr.X_train_.tolist(),
        "alpha": gpr.alpha_.ravel().tolist(),
        "y_mean": float(np.ravel(gpr._y_train_mean)[0]),
        "y_std": float(np.ravel(gpr._y_train_std)[0]),
    }
    return gpr, params


def gpr_predict(params, Xz):
    """Replicate gpr.predict() from exported params -- this is what JS will do."""
    Xt = np.asarray(params["x_train"])
    ls = np.asarray(params["length_scale"])
    d2 = ((Xz[:, None, :] - Xt[None, :, :]) / ls[None, None, :]) ** 2
    Ktrans = params["const"] * np.exp(-0.5 * d2.sum(axis=2))          # (q, n)
    return Ktrans @ np.asarray(params["alpha"]) * params["y_std"] + params["y_mean"]


# ---- MLP: fit + extract weights for a JS forward pass ---------------------
def fit_mlp(Xz, yz):
    mlp = MLPRegressor(hidden_layer_sizes=(6, 6), activation="tanh",
                       solver="lbfgs", alpha=1e-3, max_iter=4000, random_state=SEED)
    mlp.fit(Xz, yz)
    params = {
        "activation": "tanh",
        "weights": [w.tolist() for w in mlp.coefs_],     # (in,out) per layer
        "biases": [b.tolist() for b in mlp.intercepts_],
    }
    return mlp, params


def mlp_predict_z(params, Xz):
    """Replicate the MLP forward pass (standardized in/out) from exported params."""
    a = np.asarray(Xz)
    W, B = params["weights"], params["biases"]
    for i in range(len(W) - 1):
        a = np.tanh(a @ np.asarray(W[i]) + np.asarray(B[i]))
    return a @ np.asarray(W[-1]) + np.asarray(B[-1])[None, :]          # identity output


# ---- Sobol first-order + total indices (Saltelli/Jansen) on the GPR -------
# Variance-based SA per Sobol' (2001); estimators and the emulator-based approach
# follow Francom & Nachtsheim (2025), arXiv:2506.11471. Sobol' requires
# INDEPENDENT inputs and an explicit input distribution. Both hold here: the six
# Form S-6 inputs are near-uncorrelated (see input_correlation below) and their
# marginals are uniform over the sampled range (KS test, see check_uniform), so
# drawing A/B uniformly on the observed box is the correct reference measure.
def sobol_decompose(predict_fn, lo, hi, n=N_SOBOL, seed=SEED):
    """S1, ST and the pairwise S_ij for ONE A/B sample. d + C(d,2) + 2 = 23 evals.

    Everything comes off the same sample on purpose. S_ij = S^closed_ij - S_i - S_j,
    and the S_i it subtracts is the very estimate returned in the S1 vector, so the
    error in S_i cancels out of S_ij instead of being an independent second draw.
    Indices are NOT clipped to [0,1] here: clipping a replicate biases a near-zero
    index upward, and the replicate spread is what the standard errors are made of.
    """
    rng = np.random.default_rng(seed)
    d = len(lo)
    A = lo + (hi - lo) * rng.random((n, d))
    B = lo + (hi - lo) * rng.random((n, d))
    fA, fB = predict_fn(A), predict_fn(B)
    var = np.var(np.concatenate([fA, fB])) or 1e-12

    S1, ST = np.zeros(d), np.zeros(d)
    for i in range(d):
        ABi = A.copy(); ABi[:, i] = B[:, i]
        fABi = predict_fn(ABi)
        S1[i] = np.mean(fB * (fABi - fA)) / var                       # Saltelli 2010
        ST[i] = 0.5 * np.mean((fA - fABi) ** 2) / var                 # Jansen 1999

    S2 = np.zeros((d, d))
    for i in range(d):
        for j in range(i + 1, d):
            ABij = A.copy(); ABij[:, [i, j]] = B[:, [i, j]]
            closed = np.mean(fB * (predict_fn(ABij) - fA)) / var
            S2[i, j] = S2[j, i] = closed - S1[i] - S1[j]
    return S1, ST, S2


def sobol_estimate(predict_fn, lo, hi, n=N_SOBOL, reps=REPS):
    """Average the decomposition over independent replicates; report the MC error.

    The Saltelli S1 estimator is high-variance here (one input carries ~70% of the
    output variance), and at the original n=2048 its Monte-Carlo noise (~0.05) was
    the SAME size as the ST-S1 interaction signal it was meant to reveal. Replicates
    give an honest standard error, so an effect can be called real only when it
    clears its own error bar. Returns (S1, ST, S2, se_S1, se_ST, se_S2).
    """
    runs = [sobol_decompose(predict_fn, lo, hi, n=n, seed=SEED + k) for k in range(reps)]
    mean = [np.mean([r[k] for r in runs], axis=0) for k in range(3)]
    se = [np.std([r[k] for r in runs], axis=0, ddof=1) / np.sqrt(reps) for k in range(3)]
    return (*mean, *se)


def input_correlation(X):
    """Max |off-diagonal| Pearson r among inputs. Sobol' assumes independence."""
    C = np.corrcoef(X.T)
    off = C[~np.eye(C.shape[0], dtype=bool)]
    k = int(np.argmax(np.abs(off)))
    iu = np.triu_indices(C.shape[0], 1)
    pair = max(zip(*iu), key=lambda ij: abs(C[ij]))
    return float(np.abs(off).max()), f"{VARS[pair[0]]}~{VARS[pair[1]]}"


def cv_r2(estimator, X, y):
    kf = KFold(5, shuffle=True, random_state=SEED)
    return float(np.mean(cross_val_score(estimator, X, y, cv=kf, scoring="r2")))


def r2(y, yhat):
    ss_res = np.sum((y - yhat) ** 2)
    ss_tot = np.sum((y - y.mean()) ** 2) or 1e-12
    return float(1 - ss_res / ss_tot)


def sobol_block(field, factors, land, xs, mdr, inputs, cat, response, exposure=None,
                n=N_SOBOL, reps=REPS):
    """Fit a GPR to one land configuration and return its Sobol' block.

    Called once per land configuration so the indices track the viewer's Kaplan-
    DeMaria toggle. The GPR here is a throwaway emulator used only for the variance
    decomposition -- the exported predictor is still the roughness-only one.
    (n, reps) default to the full budget; the constrained run passes a smaller budget
    because there Sobol' is only an independent-uniform REFERENCE -- Shapley is primary.
    """
    X = np.array([[r[v] for v in VARS] for r in inputs[cat]], dtype=float)
    y = metric_columns(field, factors, land, xs, mdr, cat, response, exposure)
    m, s = X.mean(0), X.std(0)
    s[s == 0] = 1.0
    _, gp = fit_gpr((X - m) / s, y)
    emu = lambda Q: gpr_predict(gp, (Q - m) / s)                      # noqa: E731
    lo, hi = X.min(0), X.max(0)
    S1, ST, S2, se_S1, se_ST, se_S2 = sobol_estimate(emu, lo, hi, n=n, reps=reps)
    inter = ST - S1
    se_int = np.sqrt(se_S1 ** 2 + se_ST ** 2)
    max_r, max_r_pair = input_correlation(X)
    iu = np.triu_indices(len(VARS), 1)
    return {
        "S1": S1.tolist(), "ST": ST.tolist(),
        "se_S1": se_S1.tolist(), "se_ST": se_ST.tolist(),
        "interaction": inter.tolist(),
        "resolved": (inter > 2 * se_int).tolist(),
        "sum_S1": float(np.sum(S1)),
        "S2": S2.tolist(), "se_S2": se_S2.tolist(),
        # a pair is real only if it clears twice its own MC error; without this the
        # near-zero cells read as spurious 1e-4 "interactions" that are pure noise
        "resolved_S2": (np.abs(S2) > 2 * se_S2).tolist(),
        "sum_S2": float(np.sum(S2[iu])),
        "n": N_SOBOL, "reps": REPS,
        "max_input_corr": max_r, "max_input_corr_pair": max_r_pair,
    }


# ---- Shapley effects (given-data kNN) for the CORRELATED constrained design ----
# Sobol' above assumes independent inputs; the constrained n=200 design deliberately
# couples CP-RMW (r~+0.43) and B-RMW (r~-0.31), so Saltelli's A/B mixing samples storms
# that violate the envelope and misattributes the shared CP/RMW variance. Shapley effects
# (Owen 2014; Song, Nelson & Staum 2016) split the variance so the shares sum to EXACTLY 1
# even under dependence. The estimator is the given-data nearest-neighbour form (Broto,
# Bachoc & Depecker 2020) -- validated in tests/auto/check_shapley.py -- run on a large
# IID sample from the constrained joint (make_constrained_design.sample_joint) scored by
# the same GPR emulator, i.e. the emulator-based SA with the correct reference measure.
from itertools import combinations                                     # noqa: E402
from math import comb                                                  # noqa: E402
from scipy.spatial import cKDTree                                      # noqa: E402

SHAPLEY_N = 20000       # IID joint draws per replicate
SHAPLEY_K = 40          # kNN neighbours; smoothing bias ~ 1/k, k=40 -> ~0.01 floor
SHAPLEY_REPS = 5        # replicates (fresh joint samples) for an honest standard error


def shapley_knn(X, Y, k=SHAPLEY_K):
    """Given-data Shapley effects from a joint sample (X, Y). Returns (raw, normalized);
    raw sum to Var(Y), normalized sum to 1. See tests/auto/check_shapley.py for the
    known-answer validation (matches Sobol' S1 when inputs are independent)."""
    X = np.asarray(X, float); Y = np.asarray(Y, float)
    N, d = X.shape
    Xz = (X - X.mean(0)) / (X.std(0) + 1e-12)
    varY = Y.var()
    cache = {}

    def V(cols):
        cols = tuple(sorted(cols))
        if cols in cache:
            return cache[cols]
        if len(cols) == 0:
            v = 0.0
        elif len(cols) == d:
            v = varY                       # deterministic emulator: E[Y|X]=Y -> Var(Y)
        else:
            _, idx = cKDTree(Xz[:, cols]).query(Xz[:, cols], k=k)
            v = Y[idx].mean(1).var()
        cache[cols] = v
        return v

    Sh = np.zeros(d)
    for i in range(d):
        rest = [c for c in range(d) if c != i]
        for m in range(d):
            w = 1.0 / (d * comb(d - 1, m))
            for u in combinations(rest, m):
                Sh[i] += w * (V(u + (i,)) - V(u))
    return Sh, Sh / Sh.sum()


def shapley_block(field, factors, land, xs, mdr, inputs, cat, response, exposure,
                  joint_sampler):
    """Shapley-effect block for one land configuration, mirroring sobol_block's role.

    Fits the throwaway GPR emulator on the training runs, then estimates Shapley effects
    on SHAPLEY_REPS fresh IID draws from the constrained joint. Reports the mean share
    per input and its across-replicate standard error.
    """
    X = np.array([[r[v] for v in VARS] for r in inputs[cat]], dtype=float)
    y = metric_columns(field, factors, land, xs, mdr, cat, response, exposure)
    m, s = X.mean(0), X.std(0)
    s[s == 0] = 1.0
    _, gp = fit_gpr((X - m) / s, y)
    emu = lambda Q: gpr_predict(gp, (Q - m) / s)                      # noqa: E731

    shares = []
    for rep in range(SHAPLEY_REPS):
        Xj = joint_sampler(SHAPLEY_N, SEED + rep)
        _, sh = shapley_knn(Xj, emu(Xj))
        shares.append(sh)
    shares = np.array(shares)
    sh_mean = shares.mean(0)
    sh_se = shares.std(0, ddof=1) / np.sqrt(SHAPLEY_REPS)
    max_r, max_r_pair = input_correlation(X)
    return {
        "shapley": sh_mean.tolist(),
        "se": sh_se.tolist(),
        "sum": float(sh_mean.sum()),          # 1 by construction (partition of variance)
        "n_joint": SHAPLEY_N, "k": SHAPLEY_K, "reps": SHAPLEY_REPS,
        "max_input_corr": max_r, "max_input_corr_pair": max_r_pair,
    }


def main(constrained=False):
    global VARS, CATS
    joint_sampler = None
    if constrained:
        # Lumped C1-C5 design: one group ("all"), physical inputs incl. Holland B direct,
        # and the CORRELATED joint means Shapley effects are the primary SA (Saltelli
        # stays as an independent-uniform reference). See the header note below.
        VARS = ["CP", "Rmax", "VT", "B", "CF", "FFP"]
        CATS = ["all"]
        import make_constrained_design as mcd
        joint_sampler = mcd.sample_joint

    grid, powell, powell_kd, factors, land, xs, mdr, inputs, exposures = load_data(constrained)
    note = ("GPR/NN predictors are fit for the default config (Powell + roughness); "
            "Linear/RSM stays live in the browser. ")
    note += ("Constrained lumped C1-C5 design (n=200). Inputs are CORRELATED (CP-RMW, "
             "B-RMW), so the PRIMARY sensitivity is Shapley effects (shapley/shapley_kd, "
             "given-data kNN on the constrained joint, sum to 1 by construction); the "
             "Saltelli Sobol' block (sobol/sobol_kd) is kept as an independent-uniform "
             "REFERENCE only and does not respect the couplings."
             if constrained else
             "Sobol' indices are fit for BOTH land configs (sobol = roughness, sobol_kd = "
             "roughness + Kaplan-DeMaria decay) so they follow the viewer's decay toggle.")
    out = {"config": {"model": "powell", "land": "roughness",
                      "design": "constrained_n200_lumped" if constrained else "legacy_3x100"},
           "vars": VARS, "note": note, "responses": {}}
    max_gpr_err = max_mlp_err = 0.0

    for response in RESPONSES:
        out["responses"][response] = {}
        for cat in CATS:
            recs = inputs[cat]
            X = np.array([[r[v] for v in VARS] for r in recs], dtype=float)
            # a tlc_* response names its exposure model; wind responses ignore it
            expo = exposures.get(response.split("_", 1)[-1]) if response.startswith("tlc") else None
            y = metric_columns(powell, factors, land, xs, mdr, cat, response, expo)

            xs_mean, xs_std = X.mean(0), X.std(0)
            xs_std[xs_std == 0] = 1.0
            Xz = (X - xs_mean) / xs_std
            y_mean, y_std = float(y.mean()), float(y.std() or 1.0)
            yz = (y - y_mean) / y_std

            # GPR
            gpr, gp = fit_gpr(Xz, y)
            gpr_yhat = gpr_predict(gp, Xz)
            max_gpr_err = max(max_gpr_err, np.max(np.abs(gpr_yhat - gpr.predict(Xz))))
            gp_cv = cv_r2(GaussianProcessRegressor(
                kernel=(ConstantKernel(1.0) * RBF([1.0] * len(VARS)) + WhiteKernel(1e-3)),
                normalize_y=True, random_state=SEED), Xz, y)
            # ARD sensitivity: shorter length-scale = more influential -> 1/l, normalized
            inv = 1.0 / np.asarray(gp["length_scale"])
            ard = (inv / inv.sum()).tolist()

            # Sobol' for BOTH land configurations, so the viewer's Kaplan-DeMaria
            # toggle selects the matching set instead of hiding the indices entirely.
            # In constrained mode Sobol' is a reference only -> lighter budget.
            sob_n, sob_reps = (16384, 4) if constrained else (N_SOBOL, REPS)
            sob = sobol_block(powell, factors, land, xs, mdr, inputs, cat, response, expo,
                              n=sob_n, reps=sob_reps)
            sob_kd = sobol_block(powell_kd, factors, land, xs, mdr, inputs, cat, response, expo,
                                 n=sob_n, reps=sob_reps)
            S1 = np.array(sob["S1"]); ST = np.array(sob["ST"])
            interaction = np.array(sob["interaction"])
            resolved = np.array(sob["resolved"])
            sum_S1 = sob["sum_S1"]
            se_int = np.sqrt(np.array(sob["se_S1"]) ** 2 + np.array(sob["se_ST"]) ** 2)
            S2 = np.array(sob["S2"])
            max_r, max_r_pair = sob["max_input_corr"], sob["max_input_corr_pair"]

            # Shapley effects (constrained/correlated design only): the PRIMARY SA, on
            # the constrained joint, for both land configs. Saltelli above is kept as an
            # independent-uniform reference.
            shap = shap_kd = None
            if constrained:
                shap = shapley_block(powell, factors, land, xs, mdr, inputs, cat,
                                     response, expo, joint_sampler)
                shap_kd = shapley_block(powell_kd, factors, land, xs, mdr, inputs, cat,
                                        response, expo, joint_sampler)

            # MLP
            mlp, mp = fit_mlp(Xz, yz)
            mlp_yhat = mlp_predict_z(mp, Xz).ravel() * y_std + y_mean
            max_mlp_err = max(max_mlp_err,
                              np.max(np.abs(mlp_predict_z(mp, Xz).ravel() - mlp.predict(Xz))))
            mlp_pipe = TransformedTargetRegressor(
                regressor=make_pipeline(StandardScaler(), MLPRegressor(
                    hidden_layer_sizes=(6, 6), activation="tanh", solver="lbfgs",
                    alpha=1e-3, max_iter=4000, random_state=SEED)),
                transformer=StandardScaler())
            mp_cv = cv_r2(mlp_pipe, X, y)

            out["responses"][response][cat] = {
                "scaler": {"mean": xs_mean.tolist(), "std": xs_std.tolist()},
                "y_mean": y_mean, "y_std": y_std,
                "y_range": [float(y.min()), float(y.max())],
                "gpr": {**gp, "r2": r2(y, gpr_yhat), "cv_r2": gp_cv, "ard": ard},
                "mlp": {**mp, "y_mean": y_mean, "y_std": y_std,
                        "r2": r2(y, mlp_yhat), "cv_r2": mp_cv},
                "sobol": sob,          # Powell + surface roughness
                "sobol_kd": sob_kd,    # Powell + surface roughness + K-D decay
            }
            if constrained:
                out["responses"][response][cat]["shapley"] = shap
                out["responses"][response][cat]["shapley_kd"] = shap_kd
            hits = [VARS[i] for i in range(len(VARS)) if resolved[i]]
            print(f"  {response:4s} {cat}: GPR R²={r2(y, gpr_yhat):.3f} cv={gp_cv:.3f} | "
                  f"MLP R²={r2(y, mlp_yhat):.3f} cv={mp_cv:.3f} | "
                  f"ARD top={VARS[int(np.argmax(ard))]}")
            print(f"        Sobol: ST top={VARS[int(np.argmax(ST))]} "
                  f"sum(S1)={sum_S1:.3f} -> interaction={1 - sum_S1:+.3f} | "
                  f"max(ST-S1)={interaction.max():.3f}±{se_int.max():.3f} "
                  f"@ {VARS[int(np.argmax(interaction))]} | "
                  f"resolved interactions: {', '.join(hits) if hits else 'none'} | "
                  f"max|r|={max_r:.3f} ({max_r_pair})")
            iu = np.triu_indices(len(VARS), 1)
            for tag, b in (("roughness", sob), ("rough+K-D ", sob_kd)):
                M = np.array(b["S2"])
                k = int(np.argmax(np.abs(M[iu])))
                bi, bj = iu[0][k], iu[1][k]
                resid = 1.0 - b["sum_S1"] - b["sum_S2"]
                print(f"        Sobol[{tag}]: sum(S1)={b['sum_S1']:.3f} "
                      f"sum(S2)={b['sum_S2']:+.4f} higher-order={resid:+.4f} | "
                      f"top pair {VARS[bi]}x{VARS[bj]} S_ij={M[bi, bj]:+.4f}")
            if constrained:
                for tag, b in (("roughness", shap), ("rough+K-D ", shap_kd)):
                    sh = np.array(b["shapley"]); se = np.array(b["se"])
                    order = np.argsort(sh)[::-1]
                    top = " ".join(f"{VARS[i]}={sh[i]:.2f}±{se[i]:.2f}" for i in order[:3])
                    print(f"        Shapley[{tag}]: sum={b['sum']:.3f} | {top} | "
                          f"max|r|={b['max_input_corr']:.2f} ({b['max_input_corr_pair']})")

    fname = "metamodels_constrained.json" if constrained else "metamodels.json"
    (WEB / fname).write_text(json.dumps(out))
    size = (WEB / fname).stat().st_size / 1024
    print(f"\nParity vs sklearn.predict  GPR max|Δ|={max_gpr_err:.2e}  "
          f"MLP max|Δ|={max_mlp_err:.2e}")
    print(f"Wrote {WEB/fname} ({size:.0f} KB)")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--constrained", action="store_true",
                    help="fit the lumped constrained n=200 design with Shapley SA "
                         "-> metamodels_constrained.json")
    a = ap.parse_args()
    main(constrained=a.constrained)
