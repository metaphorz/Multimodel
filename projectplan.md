# FORM S-6 — Interactive Grid + Windfields

## Goal
An interactive Leaflet web app over southern Florida that draws the official Form S-6
21×40 grid and runs selectable hurricane windfield models over it: **Powell** (PDE/slab),
**Holland**, and **Willoughby**. (An ERA5 4th option was considered and retired —
see Phase 4.) Supports the sensitivity/uncertainty analysis context of
Standard S-3 / Form S-6 in the ROA.

## Confirmed decisions (2026-06-18)
- **Scope:** Map + windfields first (phased). Loss-cost + SA/UA pipeline = later Phase 5.
- **Compute:** Hybrid. Powell (PDE) precomputed in Python → JSON. Holland +
  Willoughby computed live in the browser (analytic, instant interactivity).
- **ERA5:** Retired (2026-06-19). Originally planned as a 4th option; see Phase 4.

## Key facts (from ROA pp. 167–168, 182–191, 336–341 + Excel inputs)
- **Grid:** 21×40 = 840 vertices, ~3 statute-mile spacing.
  - E-W = 0,3,…,117 (40 columns); N-S = −15,−12,…,45 (21 rows).
  - Lat/lon for all 840 points are in the `Land-Water ID` sheet.
  - **682 land** (ID=1), 158 water (ID=0).
- **Track:** origin (0,0) = storm center at t=0, **9 mi east of landfall (25.8611 N,
  80.1196 W)**. Storm moves **due west** (0,0)→(117,0) over **12 hours**.
- **Inputs (`FormS6Input.xlsx`, 9 sheets):** CP, Rmax (st mi), VT (mph), WSP (shape),
  CF (conversion factor), FFP (mb), Quantile — 100 vectors × categories {1,3,5}.
  `FormS6InputQuantiles.xlsx` mirrors these as 0–1 quantiles. Sheet 1 = SA (all vars);
  sheets 2–8 = UA per variable; sheet 9 = Land-Water ID.
- **Pressure deficit:** dp = FFP − CP.
- **CF 3-zone radial rule (pp. 184–185):**
  - r < Rmax: CF·(r/Rmax)
  - Rmax < r < 3·Rmax: CF − [(r−Rmax)/(3Rmax−Rmax)]·0.1
  - r > 3·Rmax: CF − 0.1 (held constant)

## Reusable code
- `../storm-anim/hurricane_pde_marine.py` — implements all three models as functions:
  `pde_steady_marine` (Powell), `holland_asym_marine`, `willoughby_asym_marine`, with
  translation asymmetry + marine settings. Adapt to evaluate at the 840 grid points.
- `../era5tracks/web/` — Leaflet + canvas patterns, `start`/`stop` server scripts.

## Plan / Todo

### Phase 0 — Scaffold  ✅
- [x] Project venv + requirements (openpyxl, numpy)
- [x] Folder structure: `pipeline/`, `web/`, `outputs/web/`, `tests/auto/`
- [x] `start` / `stop` server scripts (port 8012)

### Phase 1 — Grid + map foundation  ✅
- [x] `pipeline/build_grid.py`: read `Land-Water ID` → `outputs/web/grid.json`
      (verified 840 pts, 682 land / 158 water, 40×21)
- [x] `web/index.html` + Leaflet: grid (land vs water), track (0,0)→(117,0),
      landfall marker, layer toggles, B-distribution UI control (default Uniform [1.0,2.5])

### Phase 2 — Windfield engine over the grid  (≈done)
- [x] `pipeline/read_inputs.py` → inputs.json (100×3 vectors, all variables)
- [x] `pipeline/windfield_grid.py`: Powell PDE → per-vertex 12-hr **peak** surface wind.
      Field computed once/vector (translation-invariant), sampled hourly t=0..12, CF
      3-zone conversion, dp=FFP−CP. ~2.4s/solve on MPS.
- [~] Precompute Powell all 100×3 → powell.json (running; cat1 done, peak 121 mph)
- [x] `web/windfield.js`: live Holland + Willoughby in JS — **validated** vs Python
      (Holland land-mean 76.3 mph == Powell marine 76.3 mph for cat1 v1).
- [x] **Surface roughness (rigorous)**: NLCD 2021 land cover (MRLC WCS, properly
      georeferenced) → modal class → published z0 table → **Vickery/ESDU gradient-tied
      log-law** marine→terrain ratio (`fetch_nlcd.sh` + `build_roughness.py`). Land-mean
      factor ≈0.67 (urban ~0.50, wetland ~0.70, water 1.0). Replaced the earlier
      heuristic+JPEG (whose georeferencing was wrong — sampling was spatially scrambled).
- [ ] **K&D land-effect selector**: replace roughness checkbox with 3-way
      None / Surface roughness / Kaplan–DeMaria decay (+Gulf recovery). (in progress)

### Phase 3 — Interactive viewer  ✅ (hour-animation deferred)
- [x] Controls: model, category {1,3,5}, input vector (1–100), B-distribution.
- [x] **"Color by" dropdown** (Max wind speed default; Land/Water option) + legend.
- [x] Hover vertex → tooltip: peak wind, (E-W,N-S) mi, lat/lon, land/water, **place name**
      (nearest area/county/state), input params.
- [x] **Display toggle: Points (default) / Filled contour** — banded filled contours on
      the 21×40 lattice (vendored d3-contour), matching ROA Figs 6–8.
- [x] **Light/Dark theme** toggle (dark default) — swaps basemap + sidebar.
- [x] **Place names**: `pipeline/add_place_names.py` (offline reverse_geocoder) → grid.json.
- [x] **Time-sampling fix**: peak envelope uses dt=0.1h (hourly aliased the fast westward
      storm → comb artifact in contours; fine sampling gives the true smooth peak).
- [x] `pipeline/build_all.sh` — one-command reproducible rebuild (dependency order).
- [ ] Hour slider / animate (t=0..12) — deferred (peak-wind is the agreed metric).

### Phase 4 — ERA5 4th option  ❌ RETIRED (2026-06-19)
- [x] Removed the inert "ERA5 (coming soon)" dropdown entry.
- **Rationale:** ERA5 is a *reanalysis* (fixed, already-observed field), not a
  *parametric* model. It cannot be a peer to Powell/Holland/Willoughby:
  - **Paradigm mismatch:** the 3 models are generated from tunable inputs
    (CP, Rmax, VT, WSP, CF, FFP); ERA5 has no such knobs, so category {1,3,5},
    input-vector 1–100, and the B-distribution controls would all be inert.
  - **SA/UA undefined:** SRC/EPR require perturbing the 6 inputs; a single fixed
    reanalysis field has nothing to vary.
  - **Resolution:** ERA5 ~0.25° (~28 km) under-resolves the TC inner core and
    systematically damps peak winds — the exact quantity (peak wind → loss) the
    app reports — and yields only ~8–12 cells over the southern-FL domain vs. 840
    grid vertices.
- **If revisited later**, the only coherent roles are (A) a clearly-labeled,
  per-historical-storm *validation overlay* (storm/date picker; coarse), or
  (B) using ERA5's well-resolved large scales (CP, track, translation speed) to
  *estimate* the input vector fed into the existing parametric models — a
  separate feature, not a dropdown entry.

### Phase 4.5 — Sensitivity & Uncertainty Analysis  ✅ (v1)
- [x] **SA → SRC**: standardized regression of output on the 6 inputs (CP, Rmax, VT,
      WSP, CF, FFP) over the 100 "SA all Variables" vectors, per category. (`analysis.js`)
- [x] **UA → EPR**: EPR_i = SRC_i²·100% (Option 2 variance-share approximation; valid for
      ~independent inputs). Uniform for all 3 models in v1.
- [x] Output metric: **mean peak wind over 682 land vertices** (wind proxy; → loss in P5).
- [x] UI: **Analysis** buttons (Sensitivity / Uncertainty) → SVG line chart x=cat {1,3,5},
      one line per variable + legend + R² (lightweight SVG, no new dep). ROA Figs 9/10 analog.
- [x] SRC + EPR open as **independent floating windows** — both viewable at once,
      **draggable** (title bar) and **resizable** (corner handle); bring-to-front on click.
- [x] **Validated**: SRC signs/ranking match ROA Fig 9 (WSP dominant cat1, CP negative,
      CF/FFP/Rmax positive).
- [ ] Option 1 (faithful Powell EPR via 1800-solve UA-sheet precompute) — optional, later.
- [ ] ASCII/PDF export of SA/UA (XXX25SA, XXX25UA) — Phase 5.

### Phase 5 — Full Form S-6 analysis (later)
- [ ] Loss costs at 682 land points; 300-row + 2,046-row ASCII/PDF outputs;
      CDF (Fig 5), contour (Figs 6–8), SRC (Fig 9), EPR (Fig 10).

## Resolved (2026-06-18)
- **Output metric:** per-vertex **peak (12-hr max) surface wind speed** is sufficient for
  now. Loss costs deferred to Phase 5.
- **Powell precompute:** all 100 vectors × 3 categories (300 PDE solves).
- **WSP → B (shape parameter):** WSP is a quantile p∈[0,1]; convert via inverse-CDF of a
  user-chosen distribution for Holland's B (shared shape knob across all 3 models).
  **UI control** lets the user pick the family + parameters:
    - Uniform: [Bmin, Bmax]  — **default Uniform [1.0, 2.5]**  → B = Bmin + p·(Bmax−Bmin)
    - Triangular: [Bmin, Bmode, Bmax]
    - Normal: [mean, std] (clamped to a sane range)
  Powell precompute uses the default; Holland/Willoughby recompute live in JS when the
  user changes the distribution.

## Conventions
- **Selenium** is used to (a) analyze/verify the web interface during development and
  (b) generate all figures when `docs/` is built later (as in other ~/code projects).

## Open questions for later
- (ERA5 4th-option resolved — retired; see Phase 4.)

## Mean / CSV buttons (2026-06-23) — DONE
Two buttons below the Input-vector slider.

Decisions confirmed with user:
- **CSV scope:** all 3 categories (300 rows + header). `Category` column is the bare
  number (1/3/5), per follow-up request.
- **Mean mode:** persistent toggle that overrides the slider (slider greyed while active);
  mean is over all 100 input vectors.

Done:
- [x] `index.html`: `<div class="vec-actions">` with `#btnMean` + `#btnCsv` under the slider.
- [x] `style.css`: `.vec-actions` / `.vec-btn` (+ `.active` green) styles; greyed disabled slider.
- [x] `viewer.js`: `state.meanMode`; `computeMeanWind(model,cat)` averages per-point wind
      over all 100 vectors (respects current model/category/land-effect/B); `computeWind()`
      returns the mean field when `meanMode`.
- [x] `viewer.js`: wired `#btnMean` (toggle, disable slider, deferred "Computing…" status)
      and `#btnCsv` (`downloadInputsCsv()` builds CP,Rmax,VT,WSP,CF,FFP for all 3 cats).
- [x] `viewer.js`: `pointInfoHTML` + info `tag` read "mean (100 vectors)" in mean mode.
- [x] Selenium test `tests/auto/test_mean_csv_buttons.py` — Powell + Holland mean, toggle,
      slider-disable, and 300-row CSV download. **ALL CHECKS PASSED.**

## Metamodel + Interaction Profiler — Phase A (2026-06-23) — DONE
From the chris/ deck (Mark Johnson / "Other Chris", 6/5/26): upgrade SA from a
first-order linear regression to an interactive **metamodel** with an **interaction
profiler**, and allow the response Y to be either mean peak wind or loss cost %TLC.
Phase A is pure JS (no new deps / no Python precompute); GPR + neural-net
metamodels are Phase B.

Confirmed with user:
- Start with **Phase A** (pure JS).
- Response Y is **user-toggleable**: mean peak wind (current) OR loss cost %TLC(i).

Definitions:
- %TLC(i) = TLC(i)/total_exposure, TLC(i)=Σ_land LC(i,x,y), LC=MDR(wind)·$100k,
  total_exposure = 682·$100k = $68.2M  ⇒  %TLC(i) = 100·mean(MDR over 682 land pts).
  (Needs state.vuln — loaded. Equivalent to the ROA definition.)
- Second-order response-surface metamodel (lets the profiler bend / show interactions):
  Ŷ = b0 + Σ bᵢxᵢ + Σ bᵢᵢxᵢ² + Σ_{i<j} bᵢⱼxᵢxⱼ   (standardized inputs; 28 terms, n=100).
  Fit by least squares via the existing solve() on the normal equations XᵀX β = Xᵀy.

Done:
- [x] `index.html`: Response selector (Mean peak wind / Loss cost %TLC) + buttons
      **Interaction Profiler** and **%TLC CDF** in the Analysis section.
- [x] `analysis.js`: `responseVar()`, `pctTLC()`, `outputMetric()`; computeSRC routed
      through outputMetric; getData cache key includes `resp`; faithful-EPR guarded to
      wind response; SRC/EPR footnote is response-aware.
- [x] `analysis.js`: `rsmFeatures` + `fitRSM` (standardized 2nd-order RSM, 28 terms,
      ridge-stabilized normal equations via existing `solve`) + `rsmPredict`.
- [x] `analysis.js`: **Interaction Profiler** panel (`drawProfiler`/`buildProfilerDOM`/
      `updateProfilerPlots`) — 6 colour-coded partial-dependence subplots + 6 sliders;
      slider drag redraws only the curves (sliders stay live); category-aware.
- [x] `analysis.js`: **%TLC empirical CDF** panel (`drawCDF`) — sorted step plot, 100 vectors.
- [x] `analysis.js`: panel dispatch (`renderPanel`) + `redrawOpenPanels`; category change
      redraws prof/cdf only.
- [x] `style.css`: `.prof-grid` / `.prof-cell` / `.prof-sliders` styles.
- [x] Selenium test `tests/auto/test_profiler_cdf.py` — 6 subplots+sliders, moving CP
      changed 5/6 curves (interactions visible), %TLC re-render, CDF renders. **PASSED.**
      No regression: `test_mean_csv_buttons.py` still passes; SRC works for wind + %TLC.

## Metamodels Phase B (machine-learning metamodels) — DONE (2026-06-23)
Built and Selenium-tested. GPR + NN fit offline (scikit-learn) → metamodels.json;
browser evaluates only. Defaults unchanged (Metamodel=Linear (RSM), Color-by=wind,
Response=wind) — verified by the Phase A regression test still passing.

Implemented:
- `pipeline/fit_metamodels.py`: fits GPR (ARD) + MLP (tanh, 5-fold CV) per
  category × response for the DEFAULT config (Powell+roughness, Option A); exports
  θ/weights/scalers/R²/CV + Sobol S1/ST. In-process parity checks vs sklearn
  predict: GPR max|Δ|=9e-10, MLP max|Δ|=0. Needs scikit-learn (added to venv).
- `web/analysis.js`: `gprPredictRaw`/`mlpPredictRaw` evaluators; `buildMetamodel`
  dispatcher; profiler now metamodel-driven; `Compare metamodels` panel (overlaid
  Linear/GPR/NN + R²/CV table); EPR shows Sobol total-effect when GPR selected;
  `computeGridSensitivity` (per-vertex dominant input).
- `web/viewer.js`: loads metamodels.json; `Sensitivity (dominant input)` colour mode.
- `index.html`/`style.css`: +1 Metamodel dropdown, +1 Compare button, +1 colour-by
  option, compare-table styles.
- Tests: `tests/auto/test_metamodels.py` (GPR/NN switch, compare 3-series+table,
  Sobol EPR, grid sensitivity colours+legend) — PASSED. Phase A test still PASSES.
- Figures added to `docs/capture_figures.py`: analysis_compare, grid_sensitivity.

Note: all metamodels hit R²≈1 (smooth deterministic simulator) as predicted — the
value is diagnostic (ARD ranking, Sobol indices, 3-way agreement), not accuracy.
Sobol ST top variable: WSP (Cat 1/3) → Rmax (Cat 5), matching the ROA finding.

### (historical) original Phase B plan
Upgrade the metamodel backend from the second-order response surface to
machine-learning metamodels, per the 6/5/26 deck. Preserves the app's existing
hybrid pattern: **train offline in Python → export JSON → the browser only
evaluates** (identical to how Powell already works). The deployed site stays a
zero-backend static page (GitHub Pages unaffected).

### Training / execution model (decided 2026-06-23)
Training runs **offline, in the dev/precompute step** (like Powell) — NOT on a
button press. The UI only *evaluates* pre-fit models loaded from JSON (kernel
dot-product for GPR, forward pass for the NN); instant, and the deployed site
stays a zero-backend static page. A button that trained would need in-browser
training (awkward) or a live server (breaks GitHub Pages) — rejected.

**Config scope = Option A (chosen).** GPR/NN are precomputed for ONE canonical
configuration per (category × response) — default model + land effect
(Powell + roughness). Changing land-effect or B-distribution leaves GPR/NN
fixed to that default (gray out / "default config" note); **Linear/RSM stays
live for every config** as the always-available baseline. Option B (precompute
the full model × land × response × category grid so GPR/NN track every toggle)
is a later expansion if needed.

### Reality check (decide before building)
With Powell the simulator is smooth + deterministic, so the linear/RSM metamodel
already gives R²≈1.0. GPR/NN will NOT predict better — their value here is
**diagnostic**: ARD length-scales as a sensitivity ranking, variance-based
(Sobol) total/interaction indices, and confirming the interaction structure three
independent ways (Linear vs GPR vs NN). Build Phase B for the diagnostics, not
for accuracy.

### Pieces
1. **GPR metamodel** — scikit-learn `GaussianProcessRegressor`, ARD kernel, per
   category, per response (wind / %TLC). Export length-scales θ (= sensitivity),
   kernel hyperparameters, training points + α, and R²/CV.
2. **Neural-net metamodel** — `MLPRegressor` (~2 layers × 6 nodes, 5-fold CV).
   Export weights/biases + activation + input scaling + R²/CV.
3. **Comparison** — Linear vs GPR vs NN: R²/CV table + overlaid profiler curves.
4. **Variance-based indices** — Sobol total + two-factor interaction indices from
   GPR (slide 26); feeds the existing EPR panel when Metamodel = GPR.
5. **Grid-point-level SA map** — sensitivity computed at every vertex (not the
   land-mean), surfaced as a map colour mode (dominant input per vertex, or a
   chosen variable's importance).

### UI footprint (small — mostly reuse)
- `index.html` Analysis section: **+1 dropdown** `Metamodel: Linear (RSM) / GPR /
  Neural net` (drives the existing Interaction Profiler + SRC), **+1 button**
  `Compare metamodels` (new overlay panel).
- Existing **EPR panel** gains Sobol total/interaction indices when GPR selected
  (no new button; maybe a tiny main/total toggle).
- Existing **Color grid by** dropdown: **+1 option** `Sensitivity (dominant input)`
  (+ optional per-variable picker) for the grid-point SA map.
- Interaction Profiler, %TLC CDF, Response toggle: unchanged, reused as-is.

### Build outline
- [ ] `pipeline/`: Python script fits GPR + MLP per (category × response) for the
      default config only (Powell + roughness, Option A) over the 100 LHC vectors;
      writes `outputs/web/metamodels.json` (θ, weights, R²/CV, Sobol indices).
      Mirrors the Powell precompute step; runnable via a shell script.
      (Linear/RSM is NOT precomputed — it stays live in the browser.)
- [ ] `web/analysis.js`: JS predictors `gprPredict()` (kernel eval vs training pts)
      and `mlpPredict()` (forward pass); route `fitRSM`→ a metamodel dispatcher
      keyed by the new dropdown.
- [ ] `web/analysis.js`: `Compare metamodels` panel (R²/CV + overlaid profiles);
      EPR panel reads Sobol indices for GPR.
- [ ] `web/viewer.js` + `analysis.js`: grid-point SA colour mode in `updateField`.
- [ ] `index.html` / `style.css`: the +1 dropdown, +1 button, +1 colour-by option.
- [ ] Selenium tests in `tests/auto/`: metamodel switch re-renders profiler;
      compare panel shows 3 series; grid-point SA colours the map.
- [ ] Docs: extend §5 with GPR/NN, ARD, Sobol indices; new Selenium figures.

### Open question for later
- Separate interactive *training/DOE bench* (live refit, CNNDOE designs) — if
  wanted, that is a distinct Python/notebook companion, NOT this static viewer.
  This app only ever *evaluates* pre-fit models.

## Powell wind-vs-time cliff fix (2026-06-23)
The Powell popup's "wind vs time" curve dropped abruptly to 0 (unlike Holland/
Willoughby's smooth decay). Cause: the stored storm-relative field spanned only
+/-90 km, where Powell winds are still ~66-69 mph; the popup sampler returns a
hard 0 outside that box, so the curve cliffs once the storm-relative track exits
+/-90 km. The PDE itself and the peak-wind map are correct (the map samples the
PDE directly out to 250 km).

Fix (Option 1): widen the stored field to +/-250 km (the PDE solver's rmax_km),
keeping N=81. Winds reach 0 at the 250 km solver boundary, so the curve now
decays smoothly. No JS change needed (popup reads halfKm from the JSON).

- [x] windfield_grid.py: FIELD_HALF_KM 90 -> 250 (N=81, step 2.25 -> 6.25 km)
- [x] Re-run windfield_grid.py to regenerate powell_field.json (779s, 300 solves)
- [x] Verify the field edge now decays toward 0

Result: cat3 center-row now reads 0 (eye) -> ~56 mph at +/-125 km -> ~32 mph at
the +/-250 km solver boundary -> 0 in the corners. The old +/-90 km edge was
~66-69 mph clipped straight to 0; now the curve decays smoothly across the full
storm extent. powell.json / powell_kd.json peak winds unchanged (those already
sampled the PDE to 250 km). No JS change needed.

## Per-grid-point loss-cost CSV (2026-06-24) — DONE
Replaced the global **CSV** button (exported all 100 input vectors x 3 categories)
with a **right-click any grid vertex** action that exports a per-point loss-cost
CSV, per the statistician's spec.

- [x] `index.html`: removed `#btnCsv` from the vec-actions div (only `#btnMean` remains).
- [x] `viewer.js`: removed `downloadInputsCsv()` + its listener; added
      `downloadGridPointCsv(idx)` and a `contextmenu` handler on the nearest dot.
- [x] CSV: 100 rows (one per input vector i) x 8 columns for the current
      model/category/land-effect:
      `CP, Rmax, VT, WSP, CF, FFP, %LC, %TLC` where
      `%LC(i,x,y) = LC(i,x,y)/$100,000` (loss cost at that vertex; 0 on water) and
      `%TLC(i) = TLC(i)/(total exposure)`, `TLC(i) = sum_x sum_y LC(i,x,y)` over all
      land vertices. Total exposure = `n_land * $100,000` (= $68.2M), not hardcoded.
      Filename: `formS6_losscost_<cat>_x<ew>_y<ns>.csv`.
- [x] Selenium test `tests/auto/test_gridpoint_csv.py` — button gone, 8x100 CSV
      captured from the right-click handler, no console errors. **PASSED.**
- [x] Docs: updated `docs/FormS6.tex` interface paragraph; regenerated
      `docs/figures/grid_sensitivity.png` (the only figure showing the old CSV
      button) via the canonical settings in `docs/capture_figures.py`; rebuilt
      `docs/FormS6.pdf`.

## Contour-mode info readout fix (2026-06-24) — DONE
The bottom-left status line showed `Peak wind 0.0 mph · land mean – mph` (and
`$0.00M` loss) whenever Display = Filled contour. Cause: peak/land-mean/loss were
tallied inside the per-vertex marker-styling loop, which early-`return`s on hidden
markers — and in contour mode every dot is hidden. The contour overlay itself was
always correct (drawn from a separate copy of the wind array); only the text was wrong.

- [x] `web/viewer.js`: in `updateField`, accumulate the summary stats from the
      `wind` field before the visibility gate, so the readout is correct in both
      points and contour modes. Marker styling unchanged.
- [x] Verified via Selenium: Powell CAT5 v1 now reads Peak 148.7 / land mean 78.2
      (wind) and $19.28M = 28.27% (loss) identically in points and contour modes;
      `test_gridpoint_csv.py` still passes; no console errors.

## Points of Interest panel + printable detail page (2026-06-24) — DONE
Built and Selenium-tested. Lower-right map panel lists user grid points by
`(ew,ns)`; insert/delete with validation; gold-star markers on the map; click a
point for a draggable detail panel combining the hover summary + windfield
isotach/time-series, with a **Print / Save PDF** button (opens a clean print
window → browser print dialog → printer or PDF). POIs persist in `localStorage`
(reset link restores the 5 defaults). Approved options: localStorage + markers.
- `web/popup.js`: extracted `windfieldBodyHTML(idx)` (shared by left-click popup
  and POI detail — left-click popup verified still rendering 2 SVGs).
- `web/poi.js` (new): POI state/UI/markers/detail/print.
- `index.html`: `#poiPanel` map overlay + `poi.js`; `style.css`: panel/marker/print.
- `web/viewer.js`: `setupPoi()` in `init()`.
- Test `tests/auto/test_poi.py`: defaults=5, add→6, bad coord errors, detail has
  hover text + 2 SVGs + Print btn, delete→5, no console errors. **PASSED.**
- Docs: `docs/FormS6.tex` Points-of-Interest paragraph + figure
  `points_of_interest.png` (added to `capture_figures.py` with a new `_js` hook);
  rebuilt `docs/FormS6.pdf`.

### Original plan (for reference)

### Goal
A **Points of Interest** area in the lower-right of the map where the user can
insert/delete grid points by `(ew,ns)` coordinate. Clicking a POI opens an in-app
detail panel that combines BOTH the hover details and the left-click windfield
image for that point, and the panel is **savable / printable to printer or PDF**.

### Coordinate system (confirmed from grid.json)
Points are addressed by `(ew, ns)` in miles on a 3-mile grid:
`ew ∈ {0,3,…,117}` (40 values), `ns ∈ {-15,-12,…,45}` (21 values), 840 vertices.
Insert validation = both multiples of 3 in range AND an existing grid vertex.

### Initial POIs (5, per your answer)
`(9,15) (15,0) (60,0) (12,-12) (6,45)` — all land points (Dania Beach, Pinewood,
Kendall West, Key Biscayne, Boynton Beach).

### Design decisions (from your answers)
- Detail view = **in-app draggable panel** (like the existing windfield/analysis
  panels), stacking hover details over the isotach + wind-vs-time plots.
- **Print / Save PDF**: a button in the detail panel opens a clean print view and
  calls `window.print()`, so the OS dialog can print or "Save as PDF".

### To do
- [ ] `web/popup.js`: extract the windfield body builder into a reusable
      `windfieldBodyHTML(idx)` returning `{title, html}` (or `null` if no field).
      `openWindfieldPopup` calls it; the POI detail panel reuses it (DRY, no
      behavior change to the existing left-click popup).
- [ ] `web/poi.js` (new): POI state + UI.
      - defaults + `localStorage` persistence (so inserts/deletes survive reload;
        a small "reset" link restores the 5 defaults).
      - `gridIdx(ew,ns)` lookup; `addPoi`/`removePoi` with validation + a clear
        inline error for bad/duplicate/off-grid coordinates.
      - `renderPoiPanel()`: list of `(ew,ns) — place`, each row with a **view**
        action and a **×** delete; an `ew,ns` input + **Add** button on top.
      - `openPoiDetail(idx)`: combined panel = `pointInfoHTML(idx)` (hover details)
        + `windfieldBodyHTML(idx)` (isotach + time series) + a **Print / Save PDF**
        button; draggable/closable like the windfield panel.
      - `printPoiDetail(idx)`: opens a minimal print window with the same content
        and the app title/coords as a header, then `window.print()`.
      - small POI markers on the map (so points are findable); toggle with the
        existing Layers section is optional — default on.
- [ ] `index.html`: `#poiPanel` map overlay (lower-right) + load `web/poi.js`.
- [ ] `web/style.css`: styles for `#poiPanel`, the detail panel, the POI markers,
      and an `@media print` block so the print view is clean (no map/sidebar).
- [ ] `web/viewer.js`: call `setupPoi()` from `init()` after the map is built.
- [ ] Selenium test `tests/auto/test_poi.py`: 5 defaults present; add (30,-6) →
      6 rows; delete one → 5; open a detail → has hover text + 2 SVG plots +
      Print button; bad coord shows error; no console errors.
- [ ] Docs: add a short Points-of-Interest paragraph to `docs/FormS6.tex`; capture
      one figure of the panel + open detail page; rebuild the PDF.

### Open question
- Persist POIs in `localStorage` (survive reload) vs session-only? Plan assumes
  **localStorage + a reset link**. Say the word if you'd rather they reset each load.

## Max button + MaxWind CSV column (2026-06-25) — DONE
Added a **Max** aggregation toggle to the right of **Mean**, and a 9th column to
the right-click per-point CSV.
- `web/viewer.js`: `state.maxMode`; `computeMaxWind()` (per-vertex max — worst-case
  envelope over the 100 vectors, mirroring `computeMeanWind`); `aggLabel()` helper;
  `computeWind()`/`pointInfoHTML`/info-tag now mean/max-aware; `setAggMode()` makes
  Mean and Max mutually exclusive (either disables the vector slider; clearing both
  re-enables it). Default unchanged (Mean on).
- `index.html`: `#btnMax` next to `#btnMean`.
- Right-click CSV now 9 cols: `…,FFP,MaxWind_mph,%LC,%TLC` — `MaxWind_mph` = peak
  wind at the clicked vertex for input vector i (the driver of `%LC`; distinct from
  the VT input, which is the storm's forward speed).
- Tests: new `tests/auto/test_mean_max_buttons.py` (Mean default, Max exclusive,
  envelope peak ≥ mean peak, slider re-enable, Holland live max) replaces the
  obsolete `test_mean_csv_buttons.py`; `test_gridpoint_csv.py` updated to 9 cols.
  Both **PASS**, no console errors.
- Docs: `docs/FormS6.tex` interface paragraph (Mean/Max + 9-col CSV); rebuilt PDF.

## Axis titles on all analysis plots (2026-06-25) — DONE
The analysis charts had tick numbers but no axis titles. Added them:
- `web/analysis.js` `drawChart` (SRC/EPR): rotated y-title ("SRC (standardized
  regression coeff.)" / "EPR (% of output variance)") + x-title "Hurricane
  category" (margins widened to fit).
- `drawCDF`: y-title "cumulative probability F(x)" + x-title "%TLC (loss cost,
  % of $68.2M exposure)".
- `drawProfiler`/`drawCompare` small-multiples: a shared `.prof-axis` caption
  ("Each panel — y: <metric> · x: the named input over its range"); each cell keeps
  its variable name as the x label. `web/style.css`: `.prof-axis`.
- Windfield popup plots already carried axis labels (isotach: km E / km N;
  time series: "wind (mph) vs time" + hour ticks) — left as-is.
- `docs/capture_figures.py`: added an optional name filter so a change can refresh
  just its figures. Regenerated analysis_src/epr/profiler/tlc_cdf/compare; rebuilt PDF.
- Follow-up: the taller labeled charts overflowed the default SRC/EPR/CDF panel
  (470x360), cutting off the legend/note. `openPanel` now sizes those panels to
  480x480 (prof/cmp 580x580); verified zero body overflow on all five.

## Loss EP / Financial panel — actuarial layer (2026-06-25) — DONE
Built and Selenium-tested (`tests/auto/test_financial.py`). Analysis tools regrouped
into collapsible **Statistics** (SRC/EPR/profiler/compare) and **Actuarial** (%TLC
CDF + new Loss EP / Financial) menus. The financial panel: Conditional/Annualized
toggle, per-category event rates, per-location deductible/limit, an EP/OEP curve
(loss vs return period, 50/100/250-yr markers), and AAL / RP-loss / TVaR metrics —
all scoped to the panel (map + CSV stay ground-up). Docs: new `\paragraph` + figure
`analysis_financial.png` (capture uses a taller 700px box); PDF rebuilt. Full
Selenium suite (financial/mean-max/gridpoint-csv/poi) green.

### Original plan (for reference)
Adds the third cat-model leg (financial/actuarial) on top of the existing
hazard (windfield) + vulnerability (MDR) chain. Ships as a **sixth Analysis tool**:
one new sidebar button + one floating panel, displayed/managed exactly like the
SRC/EPR/CDF panels (inputs-in-panel precedent = the Interaction Profiler sliders).
Scoped: the map's Loss colouring and the right-click CSV stay ground-up (untouched).

### Design
- Panel body = controls (top) + EP plot + metrics table, re-rendered in place on
  any input change (like the profiler).
- Controls: **mode** toggle (Conditional | Annualized); per-category **event rate**
  (events/yr, editable assumptions, greyed in Conditional); per-location
  **deductible** and **limit** ($).
- Severity per vector = net TLC$ = Σ_land clamp(MDR·$100k − deductible, 0, limit).
- **Conditional** (selected category): severity exceedance P(L>x) vs loss; metrics
  mean / SD / CoV / 50–90–99th-pct event loss.
- **Annualized** (OEP across cats): exceedance frequency λ(x)=Σ_c rate_c·P(L_c>x);
  plot loss vs return period (1/λ, log x); metrics AAL=Σ_c rate_c·mean(L_c),
  50/100/250-yr loss (PML), TVaR at the 100-yr threshold.

### To do
- [ ] `web/analysis.js`: `finState`; `tlcSeries(model,cat,ded,lim)` (net TLC$ per
      vector); `drawFinancial()` (controls + EP plot + metrics); register `"fin"`
      in the panel dispatch; rate/ded/limit/mode inputs wired like profiler sliders.
- [ ] `index.html`: `#btnFin` ("Loss EP / Financial") in the Analysis section.
- [ ] `web/analysis.js` `setupAnalysis`: wire `btnFin`→`openPanel("fin")`; add
      `"fin"` to the model/landEffect and category redraw triggers; size the panel.
- [ ] `web/style.css`: a few rules for the financial controls row.
- [ ] Selenium `tests/auto/test_financial.py`: panel opens, EP svg + metrics
      render, Conditional↔Annualized toggle, deductible change re-renders, no errors.
- [ ] Docs: Financial/EP paragraph + figure (`analysis_financial.png` via the `_js`
      hook) in `docs/FormS6.tex`; rebuild PDF.

## Exposure module: swappable Uniform / Census exposure (2026-06-25) — DONE
Built and Selenium-tested. The 4th cat-model leg is now swappable via an **Exposure
model** selector (Uniform $100k/vertex | Census ACS home value), parallel to the
Windfield and Damage selectors. Decisions per user: Option A (aggregate),
ratio-form %LC, deductible/limit kept-and-labelled.
- `pipeline/build_exposure.py` (new): ACS B25082 (FL tracts) → join TIGER polygons
  by GEOID → areal value-density apportionment to the 682 grid cells →
  `outputs/web/exposure_census.json` (9 KB). Raw GIS stays outside the repo
  (`EXPOSURE_SHP` env override). Result: $572.2B total, max/median ≈ 1572×.
- `web/viewer.js`: `exposureAt(i)` / `totalExposure()` / adaptive `fmtMoney`;
  loads `exposure_census.json`; replaced all scalar `EXPOSURE_VALUE` loss sites;
  Census option self-disables if the JSON is absent.
- `web/analysis.js`: `pctTLC` now value-weighted; `tlcSeries` uses `exposureAt`
  + null-limit = no-cap; financial notes reflect the active exposure model.
- `index.html`: Exposure model selector; both files wire its change to
  `updateField` + panel redraw.
- Reconciliation verified: Uniform → $68.2M / %TLC 28.27% (unchanged); Census →
  $572.2B / %TLC 24.74%; AAL millions → billions. Map MDR colouring is
  exposure-independent (by design).
- Tests: `tests/auto/test_exposure.py` (new) + `test_financial.py` (adaptive $
  parsing); full suite green (exposure/financial/gridpoint-csv/mean-max/poi).
- `requirements.txt` added (geopandas/shapely/pyproj/pandas/…). Docs: Exposure-module
  paragraph in §loss; PDF rebuilt.

### Original plan (for reference)

### Goal
Add the catastrophe model's 4th leg — **Exposure** — as a swappable input, parallel
to the Windfield and Damage selectors. Today exposure is the scalar
`EXPOSURE_VALUE = $100k` at every land vertex; add a **Census (ACS)** set giving the
*actual* aggregate home value at each grid point. Same architecture as everything
else: heavy GIS work offline → small per-vertex JSON → browser selects. Pages is
unaffected (the client only ever sees a 682-number array, a few KB).

### Data
- ACS 5-yr via the Census API, Florida tracts:
  `…/2022/acs/acs5?get=NAME,B25082_001E,B25001_001E&for=tract:*&in=state:12`
  (`B25082_001E` = aggregate owner-occupied home value $; `B25001_001E` = units).
- TIGER FL tract polygons already on disk: `~/code/weather/GIS/census_tl_2021_12_tract/
  tl_2021_12_tract.shp` (join to ACS by `GEOID`). **Raw GIS stays outside the repo**
  (gitignored); only the derived JSON is committed.

### To do
- [ ] `pipeline/build_exposure.py`: fetch ACS → join to tract polygons by GEOID →
      compute tract value density ($/land-km²) → **areal-apportion** to each grid
      cell (3-mi box ∩ tracts, land area only) → write
      `outputs/web/exposure_census.json` = `{ values:[…682], total, meta }`.
      Prereqs: geopandas / shapely / requests (verify in venv).
- [ ] `web/viewer.js`: load `exposure_census.json` (try/catch like roughness);
      add `exposureAt(i)` (uniform $100k vs census[i]) and `totalExposure()`
      (n_land·$100k vs Σ census); replace the 13 `EXPOSURE_VALUE` call sites in
      viewer.js + analysis.js with these.
- [ ] `index.html`: **Exposure model** selector under Damage model
      (`Uniform ($100k / vertex)` | `Census (ACS home value)`); wire change →
      `updateField()` + redraw analysis/financial panels.
- [ ] `web/style.css`: none expected (reuses section styling).
- [ ] Test `tests/auto/test_exposure.py`: Uniform total reconciles to $68.2M;
      Census total is a sane statewide-footprint figure and is non-uniform
      (coast ≫ inland); loss map + financial panel re-render on switch; no errors.
- [ ] Docs: Exposure-module paragraph + a before/after loss-map figure; rebuild PDF.

### Decisions to confirm (flagged — these change semantics)
1. **%LC / %TLC normalization.** Cleanest under non-uniform exposure:
   `%LC = LC_j / exposure_j` (= MDR, exposure-agnostic) and
   `%TLC = ΣLC / Σexposure`. The statistician's original %LC used a fixed
   `/$100,000`; under Census that would re-scale by `exposure_j/100k`. Propose the
   ratio form; flag for the statistician.
2. **Deductible / limit under Census.** A census cell is an *aggregate* of many
   homes, not one policy, so per-location deductible/limit are less meaningful.
   Propose: keep them but note they act on the aggregate cell (or grey them in
   Census mode). Confirm preference.
3. **Apportionment method.** Areal value-density apportionment (accurate) vs simple
   point-in-polygon (coarser). Plan uses areal; say if you'd rather start simple.
4. **Renter/commercial.** `B25082` is owner-occupied only. v1 ships that as a
   labeled proxy; renter/commercial scaling is a later refinement.

## Interaction matrix view (2026-06-26) — DONE
Second, all-at-once view of pairwise interactions, as a **Profiler ↔ Interaction
matrix** toggle inside the existing Interaction Profiler panel (per user: toggle,
not a separate menu item; low/high = min/max). Replicates an emailed JMP-style
matrix.
- `web/analysis.js`: `profilerState.view`; `buildProfilerDOM` branches on view +
  renders the toggle; `wireProfTabs`; `drawInteractionMatrix` — N×N grid, diagonal
  = variable + min→max range, off-diagonal (r,c) = effect of c with r at min (red)
  / max (blue), others at mean. Reuses `mm.predict` — no refit.
- `web/style.css`: `.prof-toggle/.prof-tab/.prof-matrix/.prof-diag`.
- Tests: `test_profiler_cdf.py` fixed for the Statistics/Actuarial groups (open
  group before clicking btnProf/btnCDF) + a matrix-toggle check (36 cells, 6
  diagonal, 60 polylines). Passes.
- Docs: Interaction-matrix paragraph + figure `analysis_matrix.png`
  (capture `_js` toggles to the matrix tab; +1s settle before `_js`); PDF rebuilt.

## Single-point vs footprint response for profiler / matrix (2026-06-26) — DONE
Built and Selenium-tested. A **Footprint mean ↔ Single point** toggle in the
profiler/matrix; single-point picks a vertex by **map click** and recomputes via
**direct wind-field simulation** (live models, None/Roughness), preserving the true
S-shape a quadratic metamodel rounds away. Confirmed: footprint Rmax = 0 inflections
(concave); single-point (6,33) = 1 inflection (S); Powell/KD show a note.
- `web/analysis.js`: `profilerState` {scale,pt,picking,marker,pred}; `pointResponse`,
  `profilerPredictor`, `profilerPickPoint`; predictor parametrized so profiler/matrix
  use metamodel (footprint) or direct sim (point); scale toggle + "Pick on map".
- `web/viewer.js`: map-click routes to `profilerPickPoint` when picking.
- `web/style.css`: scale toggle, pick button, crosshair pin.
- Test `tests/auto/test_point_response.py` (S vs concave, matrix in point mode, Powell
  note). Docs: single-point §+ figure `analysis_matrix_point.png`; PDF rebuilt.

### Original plan (for reference)

### Goal
A response-scale toggle so the Interaction Profiler and the Interaction matrix can
show either the **footprint-mean** response (concave) or a **single grid point's**
response (S-shaped), demonstrating that spatial resolution changes the shape of the
loss response and the apparent interactions. Confirmed empirically: a single vertex's
MDR-vs-Rmax is a clean S (517/682 land points are S-shaped; e.g. Boca Raton (6,33):
0.001 → steep → 0.305), while the 682-point mean is concave (staggered sigmoids
average out).

### Key design decision (why DIRECT simulation, not a metamodel)
The single-point loss curve is S-shaped, but the second-order RSM **cannot represent
an inflection** — fitting an RSM per point would round the S away, and GPR/NN are
only precomputed for the aggregate config. So **single-point mode plots the response
computed directly from the wind-field model** (sweep one input, others at their means,
peak wind at the chosen vertex → MDR), exactly the computation validated in the
spatial-scale test. **Footprint mode keeps the existing metamodel** (unchanged).

### Constraints
- Direct sweeps need a model that recomputes for arbitrary inputs → **Holland /
  Willoughby only**. Powell is precomputed per-vector and can't sweep, so single-point
  mode shows a short note for Powell (switch to a live model).
- Cheap: evaluate peak wind at the **one** chosen vertex (reuse `pointTimeSeries`),
  not the full 840-point field. Matrix worst case ≈ 30 cells × 24 steps × 2 ≈ 1440
  single-point evals — sub-second.

### To do
- [ ] `web/analysis.js`: `profilerState.scale` ("footprint" | "point") and
      `profilerState.pt` (ew,ns); a **Footprint mean | Single point** toggle plus an
      (ew,ns) box shown in point mode. A direct evaluator
      `pointResponse(model, rec)` = peak wind (or its MDR) at the chosen vertex.
      In point mode, the profiler curves and the matrix cells sweep inputs through
      `pointResponse` instead of `mm.predict`; y-range from the direct response;
      header labels it "direct simulation (metamodel N/A)". Powell → note.
- [ ] `index.html` / `style.css`: the scale toggle + coord box (reuse existing styles).
- [ ] Test `tests/auto/test_point_response.py`: single-point profiler/matrix shows
      ≥1 inflection (S) for Rmax at a transitional vertex while footprint is concave;
      Powell shows the note; no console errors.
- [ ] Docs: extend the interaction-matrix section with the single-point vs footprint
      comparison (the S vs concave) + a figure; rebuild PDF.

### Open questions (flagged)
1. **Point selection:** a coordinate (ew,ns) box in the panel (default e.g. 6,33),
   a dropdown of the current **Points of Interest**, or map-click. Plan assumes a
   coord box (self-contained); say if you'd rather tie it to POIs.
2. **Metric in point mode:** reuse the existing Response selector — `wind` → peak
   wind at the point; `loss` → MDR at the point (per-point %LC, the S-shaped one).
3. **Metamodel selector** is hidden/ignored in point mode (it's direct simulation);
   restored in footprint mode.

## Review
_(to be filled in as work proceeds)_

---

# Optional storm animation east→west (meteorologist request, 2026-07-03)

**Ask:** watch the storm traverse — a semi-transparent moving windfield contour
(like the left-click isotachs) sweeping east→west from t=−12 h, with Play + a scrub
slider below the map. **Optional, NOT the default** (static peak footprint stays
default). Phase 1 = **wind only**; **all 3 models** incl. Powell.

**Feasible:** `fieldFnFor` gives wind at any (x,y) with the eye at `ewc=VT·t`; a
full-grid snapshot at t is 840 evals. Powell samples its stored storm-relative
field (`powell_field.json`, 100 vectors/cat, 81², ±250 km) like the popup does.
Snapshot ≠ the static peak footprint — animation temporarily overrides coloring
with the instantaneous field and reverts on stop.

### Plan (revised: option B — extended offshore domain + auto-zoom)
- [x] `web/anim.js`: per-frame instantaneous wind field over an EXTENDED domain
      (grid + offshore ew<0 columns sized to `12·VT + 90 mi`), all models; roughness
      (land only) + KD decay s(t) per frame; cached on storm key.
- [x] Render: semi-transparent moving contour (reused `buildContourLayer`, coarser
      upsample=4 for the bigger domain) + moving eye marker; Play/Pause timer +
      scrub slider + `t` readout.
- [x] `contour.js` refactored: `buildLatticeFrom` + optional `{lattice, upsample}`
      so animation uses its own extended lattice without clobbering the static map.
- [x] Auto-zoom: `fitBounds` to the extended domain on enter; **restore the saved
      view (default grid zoom) on Reset/exit** (per user).
- [x] `#simBar` overlay at map bottom + CSS; modal — any sidebar change calls
      `animExit()` (guarded in `updateField`) → static footprint at default zoom.
- [x] Optional/not-default: nothing animates until Play/scrub.

### Review
- New `web/anim.js` (+`anim.js` script tag, `#simBar` HTML/CSS). Off by default;
  the static peak footprint is untouched until the user presses Play or scrubs.
- **Extended domain + zoom (option B):** at t=−12 the eye is offshore, so the render
  domain extends east into the Atlantic (per-storm, sized to VT) and the map zooms
  out to show the approach; Reset restores the exact pre-animation view + footprint.
- **All 3 models** animate the selected vector (Holland/Willoughby live; Powell
  samples `powell_field.json`); roughness + Kaplan–DeMaria decay honored per frame.
- **Verified** (`tests/auto/test_anim.py`): default off; Play → extended domain
  (2835 pts), 73 frames, contour+eye, zoom 9→7; t=−12 eye offshore (lon −76.9);
  t=+24 last frame; Reset restores zoom 9 + markers; all 3 models precompute; a
  sidebar change drops out of sim mode; no console errors. Screenshot-verified the
  t=−12 (offshore near the Bahamas) and t=0 (eyewall at Miami) frames.
- Contour refactor is backward-compatible — `test_mean_max_buttons` and the AAL/IKE
  map tests still pass. (Pre-existing unrelated failure: `check_interface.py` refs a
  removed `landEffect` dropdown — the UI uses landRoughness/landDecay checkboxes.)
- Known cosmetic: the extended render domain is a rectangle, so the lowest (≈40 mph)
  band clips to a straight offshore edge. Fine for Phase 1.
- **UX follow-ups:** the bar dragged the map instead of the slider — fixed with
  `L.DomEvent.disableClickPropagation/disableScrollPropagation` on `#simBar`; lifted
  the bar up (bottom 74 px) so it's easy to grab; added a **separate speed slider**
  (1..10 → 500..50 ms/frame via `animFrameMs()`, live-applied while playing). Docs
  figures regenerated to match. Test covers the speed mapping.
- **Narrow/Wide + opacity (round 2):** added **Narrow** (default — grid only at the
  default zoom, storm enters from the east edge, no offshore render) and **Wide**
  (extended offshore domain + zoom-out) view buttons; `ANIM.mode` in the cache key,
  `animBuildExtGrid(rec, extraCols)` (0 for narrow), `animApplyZoom()`/`animSetMode()`.
  Windfield is now translucent with the **grid points visible through it** — added an
  **opacity slider** (`buildContourLayer` gained `opts.fillOpacity`, default 0.78 for
  the static map; animation defaults 0.5) and stopped hiding the markers during
  animation. `test_anim.py` rewritten to cover Narrow(840)/Wide(2835), zoom behavior,
  and the opacity slider; docs paragraph + figures updated (Wide approach / Narrow
  landfall). Static-contour tests unchanged (opacity default preserved).
- **Dynamic dots (round 3):** during animation each grid marker recolours to the
  INSTANTANEOUS wind at its vertex per frame (`animRenderFrame` maps `ext.gridIdx` →
  `state.markers`), so the lattice updates live and the calm eye reads as a dark hole.
  `updateField` restores the static coloring on exit. Verified: (6,0) goes base →
  39–74 mph band as the eye arrives. At t=−12 the grid is uniformly calm (max 13–28
  mph, 0 dots ≥ 39 mph across all models) — the storm's outer circulation is nonzero
  but below TS force, so all dots show the single base band (possible follow-up: a
  finer sub-40 ramp to reveal the approaching outer winds).
- **Dots-not-changing fix (round 4):** the recolour was working (Selenium: the SVG
  `path` fill and the on-screen pixel both update, `(54,68,82)`→`(63,176,190)`), but
  the translucent field was drawn ON TOP of the dots and used the same colour scale,
  so the dots were masked/indistinguishable from the moving field. Fixed by drawing
  the animation contour on a dedicated lower pane (`animField`, z-index 350, below the
  overlayPane markers at 400) via a new `buildContourLayer` `opts.pane` — the dynamic
  dots now sit on top of the field and their live recolouring is clearly visible (the
  calm eye reads as a dark hole in the lattice).
- **Running-max dots (round 5):** the meteorologist clarified the dots should build up
  the PEAK footprint, not show instantaneous wind (which goes calm behind the storm).
  First cut used the running max of the live single storm.
- **End == static footprint, any mode (round 6):** the running max of the *live single*
  storm didn't equal the static map, because (a) the default map is the MEAN of 100
  vectors (not one storm — single-vs-mean diff ~4.9 mph, 110 band mismatches) and (b)
  the animation's 0.5 h steps undersample the peak vs the pipeline's 1-min sim (~1.3
  mph, 23 mismatches). The pipeline already simulated each storm east→west
  (`ew_c=vt·t`, per-vertex max); it stores only the peak, so the animation re-derives
  only the *timing*. Fix: `dot = target(x)·φ(x,t)`, where `φ` = live running-max /
  live full-peak (temporal fraction) and `target = computeWindCached()` (mean/max/
  single-aware). At `t=+24` φ=1, so dots equal the static footprint EXACTLY in every
  mode. `animStormKey` now includes the agg mode. Verified (Selenium): end-of-anim
  dots match the static footprint 682/682 in mean, max, and single; build-up real
  (`#3b4a5a`→`#fd8d3c`); mean-mode end screenshot is pixel-identical to the static
  mean map. Contour stays instantaneous (still watch the storm cross).

---

# Integrated Kinetic Energy (IKE) at a grid cell (meteorologist point 3, 2026-07-02)

**Ask:** IKE (TJ) as an integrated per-cell quantity — accumulate ½ρV² every
timestep the wind is above TS force (~40 mph); ties to location-level loss.

**Physics note:** textbook IKE (Powell & Reinhold 2007) is a *spatial* integral of
½ρV² over the area where V≥34 kt, in a 1-m surface layer (TJ, whole-storm snapshot).
The meteorologist's version is a *temporal* integral at one cell → different units:
time-integrated is **TJ·h**, peak instantaneous is **TJ**. Per 3-mi cell ≈ 0.01–0.1
TJ·h / 0.01–0.05 TJ (a cell is a tiny slice of the 10–200 TJ whole storm). It's the
physical sibling of the `dosage` metric (full ½ρV² vs excess wind), reusing
`pointTimeSeries`. Cell = 3 mi × 3 mi (A≈2.331e7 m²), ρ=1.15, h=1 m, V0=40 mph.

**Scope chosen:** BOTH metrics (integrated TJ·h + peak TJ) as single-point Response
options, AND a single-storm IKE map colour mode.

### Plan
- [x] `ikeMetrics(ts)` → {integ TJ·h, peak TJ}; constants (A_cell, ρ, h, V0).
- [x] Response options `ike` / `ikepeak`; `pointResponse` dispatch; renamed
      `isDurationResp`→`isPointOnlyResp` (+ ike/ikepeak) so footprint gating +
      RSM scaffold + labels cover them.
- [x] IKE map: `colorBy=ike` (single-storm integrated TJ·h); `computePointIKE(model)`
      (682 live time series for the current vector; live models, decay off); viridis
      `ikeColor` + dynamic legend + info + contour; cache by model|cat|vIdx|rough.
- [x] Selenium test (`test_ike.py`); docs paragraph + figure + Powell–Reinhold cite.

### Review
- **Response metrics** (`web/analysis.js`): `ikeMetrics(ts)` returns integrated
  (TJ·h) + peak (TJ) IKE from the same `pointTimeSeries` as dwell/dosage; two new
  Response options wired through `pointResponse` + labels. `isDurationResp` renamed
  `isPointOnlyResp` (now dwell/dosage/ike/ikepeak) so all four share the footprint
  gating + RSM scaffold.
- **IKE map** (`web/viewer.js`): `colorBy=ike` → `computePointIKE()` builds the
  682-cell integrated-IKE field for the slider's single storm via live time series
  (Holland/Willoughby, decay off; Powell/decay → "live-only" note), cached by
  model|cat|vIdx|rough; viridis ramp, dynamic energy-unit legend, info, contour.
- **Verified** (`tests/auto/test_ike.py`): integrated IKE >0, falls with VT
  (0.116→0.074 TJ·h) and with CP (deeper storm = more energy); peak IKE tracks
  intensity; map computes in ~140 ms (no freeze), Powell→live-only; no console
  errors. Figure screenshot-checked (`docs/figures/ike_map.png`).
- **Units honesty:** integrated = TJ·h (energy×time), peak = TJ; documented that
  this is a temporal per-cell adaptation of the spatial Powell–Reinhold IKE, cited.
- **Docs** (`docs/FormS6.tex`, +figure +bib entry, rebuilt 22 pp clean).
- Regression: duration, point-response, point-ep-aal, financial, profiler-cdf green.

---

# Per-point EP curve + AAL heat-map (meteorologist point 2, 2026-07-02)

**Ask:** the Loss EP panel is a domain aggregate; also show it for an individual
grid point. "Do we have enough storms/sampling?"

**Answer given:** yes — the per-point severity sample is the *same* 100 LHS vectors
per category the aggregate already uses (aggregate sums over 682 pts, it isn't more
storms). Per-point loss = MDR(peak wind at pt)·exposure is already computed for the
right-click CSV. EP math is unchanged. Caveats: (a) tail (250-yr) rests on the top
1–2 samples, same as the aggregate; (b) **fixed track** — every storm runs the same
due-west 25.86°N line, only the 6 params vary, so per-point EP is *scenario-
conditional* (parameter uncertainty only, no landfall/heading jitter): per-point AAL
is biased and variability understated. Frame as "given these 100 storms on this
track". Works for all models incl. Powell (uses precomputed peak field, not live sim).

**Scope chosen:** EP+AAL at a picked point AND an AAL heat-map over the grid;
point = reuse `profilerState.pt` (profiler map-pick).

### Plan
- [x] `pointLossSeries(model, cat, idx, ded, lim)` — 100 per-point net-loss samples.
- [x] Financial panel: Domain ↔ Single point toggle (finState.scale); single-point
      feeds pointLossSeries into the existing Conditional/Annualized/AAL/TVaR code;
      fixed-track caveat note; "pick a point" prompt when none.
- [x] AAL heat-map: `colorBy=aal` option; `computePointAAL(model)` (Σ_c λ_c·mean
      per-pt loss); continuous `aalColor` + dynamic $ legend; info readout; contour
      support; refresh on rate/term change.
- [x] Selenium test (`test_point_ep_aal.py`); docs paragraph + figure + rebuild.

### Review
- **Per-point EP** (`web/analysis.js`): `pointLossSeries()` mirrors `tlcSeries()`
  minus the spatial sum; a Domain↔Single point toggle (`finState.scale`) feeds it
  into the unchanged EP/AAL/TVaR math. Point = the profiler's map-pick
  (`profilerState.pt`); `profilerPickPoint` now also refreshes the fin panel. Panel
  carries the fixed-track/scenario-conditional caveat and a pick prompt when none.
- **AAL heat-map** (`web/viewer.js`): `colorBy=aal` → `computePointAAL()` (Σ_c λ_c·
  mean per-pt loss over the 100 vectors), sequential `aalColor` ramp, dynamic $
  legend, info readout, contour support; rate/term edits refresh the map.
- **Sampling answer stands:** per-point uses the same 100 vectors/cat as the
  aggregate. Verified (`tests/auto/test_point_ep_aal.py`): 100 samples/cat; domain
  AAL ≈ \$873k/yr, point (6,33) ≈ \$4.5k/yr; per-point AAL over land sums back to
  the domain total; raising λ₅ 0.01→0.05 raises domain AAL 873k→1334k; no console
  errors. Screenshot-verified both panels render.
- **Docs** (`docs/FormS6.tex`, +`figures/aal_map.png`, rebuilt 21 pp clean): new
  "Per-point EP and the AAL map" paragraph + AAL-map figure.
- Regression: `test_financial`, `test_profiler_cdf`, `test_duration_metric`,
  `test_point_response` all green.

---

# Duration-aware location loss metric (meteorologist request, 2026-07-02)

**Why:** loss accumulates while wind stays above the ~40 mph damage threshold, so
peak wind alone hides the VT (forward-speed) and size sensitivity you'd most want
at the location level. Peak→HAZUS-MDR is faithful to the *published* curve (duration
baked in at one nominal value) but can't express dwell. Option chosen: **#1 — add a
duration diagnostic alongside the existing loss**, scoped to the single-point (5-point)
workflow, since footprint stores hold precomputed peak wind only (no time series).

### Plan
- [x] `#response` selector: add `dwell` (hours V≥40 mph) and `dosage` (∫(V−40)⁺ dt, mph·h).
- [x] `DAMAGE_THRESHOLD_MPH = 40` constant + `durationMetrics(ts)` helper (integrate ts.w).
- [x] `pointResponse`: return dwell/dosage from the live per-point time series.
- [x] Force RSM scaffold + gate footprint scale (SRC panel, footprint profiler, compare)
      for duration — they'd otherwise mislabel peak-wind as duration.
- [x] Metric labels/units in the profiler + interaction-matrix headers.

### Review
All changes confined to `web/analysis.js` (metric + gating), `web/index.html` (two
selector options). No windfield/physics change — dwell/dosage read the existing
per-point time series `pointTimeSeries()` already used by the left-click popup.

- Duration metrics work in the **Interaction Profiler / Interaction Matrix at
  Single-point scale** (the 5-point workflow). Footprint scale, SRC/EPR, and
  Compare show a "location-level only" note, because the footprint stores hold
  precomputed **peak** wind per vertex with no time series to integrate.
- `durationMetrics()` integrates the surface-wind series: dwell = hours ≥ 40 mph,
  dosage = ∫(V−40)⁺ dt (mph·h).
- Verified end-to-end (Selenium, `tests/auto/test_duration_metric.py`): at point
  (6,33), **dwell falls 11.6 h → 6.8 h and dosage 230 → 150 mph·h as VT rises** —
  the forward-speed sensitivity a peak-only metric collapses. Footprint gated;
  SRC note shown; no console errors.
- Fixed a **pre-existing** break in `tests/auto/test_point_response.py` (it never
  unticked Kaplan–DeMaria decay, which single-point live sim requires — `landDecay`
  now defaults on). Both tests green.
- **Docs** (`docs/FormS6.tex`, rebuilt to PDF, 20 pp, clean): loss section now
  states the peak-driven basis + forward-refs the diagnostic; Response-variable
  paragraph lists the two new responses; a new "Duration of exposure" paragraph
  defines dwell/dosage (with $V_0=40$ mph) and cites the $(6,33)$ VT numbers.

---

# Powell single-point sensitivity via per-vertex RSM (meteorologist request, 2026-07-03)

**Ask:** enable single-point (grid-vertex) sensitivity analysis for the Powell wind
field, which was previously Holland/Willoughby-only.

**Why blocked:** single-point mode does a live re-simulation at the vertex
(`pointResponse`→`pointTimeSeries`→`fieldFnFor`), analytic H/W only. Powell is an
offline PDE — the browser has only the per-vertex peak for the 100 sampled vectors.

**Scope chosen:** peak wind + %LC; 2nd-order RSM (live). Duration metrics
(dwell/dosage/IKE) stay live-model only (need a time series).

### Change (all in web/analysis.js)
- Refactored `fitRSM`→ core `fitRSMFromY(cat, y)` (fit given an explicit per-vector y).
- `pointRSMForPoint(model, cat, idx)` — y = each vector's peak at the vertex via
  `computeWindFor` (respects marine/KD + roughness); fits the 2nd-order RSM.
- `profilerPredictor`: Powell point-scale now returns a metamodel predictor
  (`peak = rsmPredict(fit, raw)`, %LC via `mdrAt`), `direct:false`; H/W still direct
  (decay-off). dwell/dosage/IKE → unavailable note for Powell. Labels show
  "per-vertex RSM (Powell)" vs "direct simulation".

### Verified
- `tests/auto/test_powell_singlepoint.py`: Powell single-point wind available &
  non-direct; `predict(means)`≈mean-of-100 peaks (158.3 vs 158.0); CP sweep moves the
  curve; %LC≈60%; IKE unavailable; matrix 36 cells; Holland still direct; no console
  errors. Updated the stale assertion in `test_point_response.py` (Powell single-point
  is now available) and fixed a pre-existing group-expand break in `test_metamodels.py`.
  Regression: profiler/cdf/duration/metamodels all green. Docs updated.
