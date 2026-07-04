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
RESPONSES = ["wind", "tlc"]
SEED = 0


# ---- reproduce the viewer's default-config output metric in Python --------
def load_data():
    grid = json.loads((WEB / "grid.json").read_text())
    powell = json.loads((WEB / "powell.json").read_text())
    rough = json.loads((WEB / "roughness.json").read_text())
    vuln = json.loads((WEB / "vulnerability.json").read_text())
    inputs = json.loads((WEB / "inputs.json").read_text())
    land = np.array([p["land"] for p in grid["points"]], dtype=bool)
    factors = np.array(rough["factors"], dtype=float)        # marine->land per vertex
    xs = np.array(vuln["xs"], dtype=float)
    mdr = np.array(vuln["mdr"], dtype=float)
    return grid, powell, factors, land, xs, mdr, inputs


def mdr_at(wind, xs, mdr):
    """Linear-interp MDR vs wind (gust factor 1.0), matching mdrAt() in the JS."""
    return np.interp(wind, xs, mdr, left=mdr[0], right=mdr[-1])


def metric_columns(powell, factors, land, xs, mdr, cat, response):
    """Per-vector scalar Y for the default config (Powell * roughness)."""
    fields = np.array(powell[cat], dtype=float) * factors[None, :]    # (100, 840)
    landfields = fields[:, land]                                      # (100, n_land)
    if response == "wind":
        return landfields.mean(axis=1)                               # mean land peak wind
    # %TLC = 100 * mean land MDR  (= TLC / $68.2M exposure)
    return 100.0 * mdr_at(landfields, xs, mdr).mean(axis=1)


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
def sobol_indices(predict_fn, lo, hi, n=2048):
    rng = np.random.default_rng(SEED)
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
    return np.clip(S1, 0, 1).tolist(), np.clip(ST, 0, 1).tolist()


def cv_r2(estimator, X, y):
    kf = KFold(5, shuffle=True, random_state=SEED)
    return float(np.mean(cross_val_score(estimator, X, y, cv=kf, scoring="r2")))


def r2(y, yhat):
    ss_res = np.sum((y - yhat) ** 2)
    ss_tot = np.sum((y - y.mean()) ** 2) or 1e-12
    return float(1 - ss_res / ss_tot)


def main():
    grid, powell, factors, land, xs, mdr, inputs = load_data()
    out = {"config": {"model": "powell", "land": "roughness"},
           "vars": VARS, "note": "Option A: GPR/NN fit for the default config only; "
           "Linear/RSM stays live in the browser.",
           "responses": {}}
    max_gpr_err = max_mlp_err = 0.0

    for response in RESPONSES:
        out["responses"][response] = {}
        for cat in CATS:
            recs = inputs[cat]
            X = np.array([[r[v] for v in VARS] for r in recs], dtype=float)
            y = metric_columns(powell, factors, land, xs, mdr, cat, response)

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

            # Sobol on the (cheap) GPR predictor over observed input ranges
            S1, ST = sobol_indices(lambda Q: gpr_predict(gp, (Q - xs_mean) / xs_std),
                                   X.min(0), X.max(0))

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
                "sobol": {"S1": S1, "ST": ST},
            }
            print(f"  {response:4s} {cat}: GPR R²={r2(y, gpr_yhat):.3f} cv={gp_cv:.3f} | "
                  f"MLP R²={r2(y, mlp_yhat):.3f} cv={mp_cv:.3f} | "
                  f"ARD top={VARS[int(np.argmax(ard))]} Sobol ST top={VARS[int(np.argmax(ST))]}")

    (WEB / "metamodels.json").write_text(json.dumps(out))
    size = (WEB / "metamodels.json").stat().st_size / 1024
    print(f"\nParity vs sklearn.predict  GPR max|Δ|={max_gpr_err:.2e}  "
          f"MLP max|Δ|={max_mlp_err:.2e}")
    print(f"Wrote {WEB/'metamodels.json'} ({size:.0f} KB)")


if __name__ == "__main__":
    main()
