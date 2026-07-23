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

---

# Numerical Analysis section in docs (meteorologist request, 2026-07-03)

**Ask:** document how each windfield is actually solved.

Added `\subsection{Numerical methods}` (\label{sec:numerics}) to the Wind-field
models section of docs/FormS6.tex:
- Closed-form Holland/Willoughby: gradient-wind balance is a quadratic in V solved
  by the quadratic formula (analytic root, no iteration/integration); Holland uses
  its radial pressure profile, Willoughby a piecewise power-law blended by a logistic.
- Powell: steady depth-averaged (slab) boundary-layer momentum PDE on a 200×360
  polar grid — advection + Coriolis + PGF + Kh diffusion + wind-dependent drag —
  solved by explicit pseudo-time marching (relaxation) to steady state with a
  CFL-limited Δt (~800 steps). Not root-finding. ~2.3 s/solve → offline-only, which
  is why Powell single-point uses the per-vertex metamodel.
- Frozen-field advection (all three): one storm-relative field, rigidly translated
  west at VT; structure doesn't evolve — only translation, scalar KD decay, and
  per-vertex roughness vary over the passage (the steady-translating-vortex
  assumption the animation visualizes).
PDF rebuilt: 25 pp, equations render, citations/cross-refs resolve.

---

# Duration-accumulated loss: rate-integrated MDR (meteorologist item 2, 2026-07-03)

**Ask (revisited):** literally accumulate damage at a point over the time series, not
just peak. User chose: rate-integrated MDR, rate ∝ instantaneous MDR, self-calibrated.

**Model:** MDR_acc(x) = min( (1/τ)·∫_{V≥40} MDR(V(t)) dt , MDR_max ). τ calibrated
PER POINT (bisection) so the 100-vector mean of MDR_acc = mean of peak-based %LC —
no net bias vs HAZUS, a duration REDISTRIBUTION (slow/large storms up, fast down);
reduces to HAZUS at nominal duration.

### Change (web/analysis.js, web/index.html)
- `accMDRIntegral(ts)` (∫MDR dt above 40 mph), `mdrCeiling()`, `accCalibration()`
  (per-point τ via bisection, cached on model|cat|idx|land), `pointSeriesAt()` helper.
- New Response option `accloss` ("Loss %LC (duration-accumulated)"); `pointResponse`
  dispatch; `isPointOnlyResp` + metricTxt updated. Single-point, live-model only
  (Powell shows a note; footprint gated).

### Verified
- `tests/auto/test_accloss.py`: at off-track (6,33) mean(accloss)=mean(peak %LC)=28.39
  (τ=3.93) — exact calibration; accloss FALLS with VT (35.7→24.9, more dwell) while
  peak %LC RISES (28.8→35.4, translation asymmetry) — opposite trends, same mean;
  Powell accloss unavailable; no console errors. (On-track vertices saturate at the
  MDR ceiling, so both are flat there — duration matters at moderate points.)
  Regression: duration/point-response/powell-singlepoint/profiler-cdf all green.
  Docs: rate-integrated MDR paragraph added to the loss section (PDF 25 pp).

---

# Single-point works for all windfields × roughness/decay (meteorologist, 2026-07-04)

**Ask:** single-point profiler must work for all 3 windfields with or without
roughness and/or decay — consistency.

**Was:** Holland/Willoughby single-point applied roughness but NOT decay, so it was
gated off when decay was on (Powell already worked via its precomputed KD store).

**Fix (web/analysis.js):** `pointSeriesAt` now applies the Kaplan–DeMaria schedule
live when decay is on — `opts.sched = intensitySchedule(V0, VT, pts)` with
`V0 = max(stormRelativeField)` memoized on the intensity/shape params (matches the
precompute `windfield_grid.py: V0 = surf.max()`). Removed the decay gate in
`profilerPredictor`'s Holland/Willoughby branch.

**Verified:** `tests/auto/test_singlepoint_landcombos.py` — all 3 windfields × 4
combos (none/rough/decay/rough+decay) available; Holland/Willoughby live peak matches
the precomputed footprint within |diff| ≤ 0.05 mph in every combo. Decay bites inland
(e.g. (60,0): 93.9→65.6, (90,0): 135→86.8) and single-point tracks the footprint to
~0.02 mph. Regression (point-response, powell-singlepoint, duration, accloss,
profiler-cdf) all green. Docs updated (PDF 25 pp).

---

# Verification sweep + Verification docs section (2026-07-04)

Full sweep of the tests/auto suite (20 Selenium tests). Found + fixed 2 stale tests
(check_interface: landEffect dropdown -> landRoughness/landDecay checkboxes;
test_plot_zoom: expand collapsible groups before button clicks) and added
test_multi_popup (the multiple-windfield-popup feature had no coverage). Result:
20 passed, 0 failed. Added a "Verification and testing" section to FormS6.tex
documenting the in-loop testing approach, the suite coverage, the consistency/
invariant checks (single-point vs footprint ~0.02 mph; anim end == static 682/682;
per-point AAL sums to domain; accloss mean == peak mean; metamodel parity ~1e-9),
and the numerical/physics verification (dt convergence, Powell EPR, track contrast,
node --check). PDF 26 pp.

---

# Logistic damage model with adjustable parameters (meteorologist, 2026-07-05)

**Ask:** add a second damage model (default stays Vickery) called "Logistic:
Parameter Selection" with piecewise logistic D(v)=1/(1+e^{-k(v-v50)}) for v<vmax else
1.0 (v = 3-sec gust). Name implies the parameters are user-selectable -> exposed as
editable inputs (defaults v50=148, k=0.08, vmax=180 for the no-shutter gable house).

**Change:** #damageModel gained the logistic option + a #logisticParams panel (v50, k,
vmax inputs, shown only for logistic). viewer.js: damageModelSel(), logisticParams(),
logisticMDR(), updateDamageUI(); mdrAt() branches on the model. mdrCeiling() -> 1.0
under logistic. buildMetamodel forces the live RSM for the loss response under a
non-Vickery model (the precomputed GPR/NN loss metamodels encode Vickery). accloss
calibration key + both change-listeners include the model + its params.

**Verified:** tests/auto/test_damage_model.py — logistic MDR exact (0.021/0.5/0.723/1/1
at 100/148/160/180/200); ceiling 1.0; switching changes %TLC (16.9%%->0.49%%);
single-point %LC uses the model; editing v50 148->120 moves the median (mdr(120)
0.096->0.5); panel shows only for logistic. Regression: financial/point-ep-aal/
accloss/exposure/gridpoint-csv/profiler-cdf/point-response/powell-singlepoint green.
Docs: logistic paragraph in the loss section (PDF 26 pp).

## Review — Windows-runnable distribution (2026-07-05)
Made the app runnable on a stranger's Windows PC from a zip (no Python, no admin).
- Vendored Leaflet 1.9.4 locally (`web/vendor/leaflet/` js+css+images); dropped the
  unpkg CDN `<link>`/`<script>` from `web/index.html`. App now has no CDN dependency
  (only basemap tiles need the network).
- `serve.ps1`: zero-install PowerShell static server (TcpListener on 127.0.0.1:8012,
  no admin / no URL-ACL) serving the project root; opens the browser once bound.
- `run.bat`: double-click launcher that runs `serve.ps1`.
- `README-Windows.txt`: unzip -> double-click `run.bat`; explains why file:// fails.
- `make-windows-zip.sh`: builds `outputs/FormS6-viewer-windows.zip` (2.1 MB) with only
  runtime files (app + vendored Leaflet + `outputs/web/` data), excluding the 1.2 GB
  venv/pipeline/data/docs.
- Docs: added "Serving and platform" paragraph to the Interface & reproducibility
  section of `docs/FormS6.tex` (rebuilt clean). Records macOS `./start` vs Windows
  `run.bat`/`serve.ps1`, and why a local http server is required.
- Tested on macOS: vendored Leaflet + all data serve 200 over http; no CDN refs
  remain; zip contents verified (43 files). NOT testable here: `serve.ps1`/`run.bat`
  need one real Windows smoke test (a colleague will run it).

# Dynamic time-based Powell simulation (exploration, 2026-07-06)

## Problem
The Powell windfield is NOT actually simulated in time today. `pipeline/windfield_grid.py`
runs `pde_steady_marine` once per input vector: it pseudo-time-marches the boundary-layer
momentum equations (storm-relative polar grid, 200 r x 360 phi) to a STEADY state under
fixed forcing (Holland pressure gradient from dp/B/Rmax, marine Large & Pond drag,
translation added as a constant vector at the end). The Delta T = 1 minute is only the
SAMPLING rate: the frozen field is translated west and sampled; Kaplan-DeMaria decay is a
post-hoc scalar s(t); roughness a static per-vertex multiplier. Holland/Willoughby are
parametric shapes and can never vary structurally in time — but the Powell PDE CAN,
because its steady solver is already a time integrator (Euler steps of the physical
tendency equations); we simply stop it at equilibrium and freeze the answer.

## What a true dynamic simulation adds
Integrate the SAME PDE onward in physical time from the marine steady state while the
forcing evolves as the storm crosses the coast:
1. Lagged decay — drive K&D decay through the PRESSURE forcing (dp(t) = dp0*s(t)^2 so the
   equilibrium gradient wind tracks the K&D Vmax target) instead of scaling the wind
   instantaneously. The boundary layer then spins down with its physical adjustment lag
   (tau ~ h_bl/(Cd*U) ~ 1 h), giving stronger-than-scalar back-side winds.
2. Asymmetric land drag — each step, sample land/roughness (NLCD z0, `Cd_from_z0` already
   exists in the solver) under the storm-relative grid, so drag varies with (r, phi, t).
   Onshore-flow sectors decelerate while offshore sectors stay marine: a genuine
   structural asymmetry at landfall that no post-hoc per-vertex multiplier can produce.
3. The pre-landfall phase is genuinely steady (uniform ocean, constant forcing), so the
   dynamic window only needs to span coast interaction: t = -2 h .. +14 h.

## Cost (measured today: 3.1 ms/iter on MPS, 2.5 s per 800-iter steady solve)
The CFL dt is ~0.06 s (limited by the r=0.5 km inner arc length ~8.7 m), so 16 h of
physical time = ~1M steps = ~50 min/storm — infeasible for 300 storms. Knobs:
- Raise rmin_km (0.5 -> ~4) and/or drop Nphi (360 -> 180) FOR THE DYNAMIC RUN:
  dt ~ 1 s -> ~58k steps ~ 2 min/storm -> ~10 h for all 300 (overnight precompute).
- Phase 0 prototypes on 1-3 storms only, so cost decisions are made with real results.

## Phase 0 — offline prototype (no viewer changes; decision gate)
- [x] `pde_dynamic_marine()` added to storm-anim/hurricane_pde_marine.py (beside the
      steady solver; steady path untouched) + `pipeline/windfield_dynamic.py` driver —
      init from steady spin-up, physical-time march, forcing updated every simulated
      minute: dp(t) from the SAME K&D schedule (imported from windfield_grid), land
      drag Cd(r,phi,t) from roughness.json z0 under the moving storm.
- [x] Validate: with forcing HELD marine-constant, the dynamic run must stay at the
      steady solution — PASSED EXACTLY: dyn-vs-frozen peak diff 0.0 mph at all land
      vertices (cat3[0], 15.1 h window, 907 one-minute samples).

### Stability finding (2026-07-06)
Long integration exposes a latent instability the production steady solve never sees
(it stops at 800 iters ~ 48 s pseudo-time; a 16 h march = 57,600 s does). Diagnosis
trail, each hypothesis tested empirically over 16 simulated hours:
1. NOT the time integrator: SSP-RK3 blows up at t~2364 s, same ~2000 s growth
   timescale as forward Euler on the fine grid.
2. NOT azimuthal: the growing mode is exactly axisymmetric (ring asymmetry 0.000),
   radial, localised near r~28 km (inside the eyewall).
3. NOT the stretched-grid metric inconsistency (radial derivatives use mean dr on a
   stretched grid): a uniform radial grid still blows up at t~1743 s.
4. ROOT CAUSE (hypothesis under test): Coriolis SIGNS in physics_terms are flipped —
   code has rhs_u ~ -f*v and rhs_v ~ +f*u; cylindrical-coordinate momentum equations
   require +f*v (radial) and -f*u (tangential). Linear analysis: the sign-flipped
   system is inertially UNSTABLE for a cyclone (stability product (2v/r - f)(zeta - f)
   goes negative; growth ~10^3 s, matching observation); the correct-sign system is
   unconditionally inertially stable there. Invisible at 48 s pseudo-time (drift
   ~0.3 m/s), fatal over hours. CONFIRMED: fix extends stability 4x (blow-up 1743 s
   -> 7944 s); effect on the production 800-iter peaks <= 0.15 mph (measured cat1/3/5).
5. SECOND ROOT CAUSE: the remaining axisymmetric radial runaway (~8000 s, r~18 km,
   integrator-independent) is the slab-BL INFLOW SHOCK (Smith & Vogl 2008): centered
   differences blow up on the shock. Fix: first-order UPWIND radial advection.
   CONFIRMED STABLE: fixed Coriolis + upwind-r holds the full 16 h window, converging
   to a true steady state (maxU locks at 62.85 m/s, drift 0.000 over 30k+ steps).
Final dynamic scheme: `physics_terms_dyn` (corrected Coriolis + upwind radial
advection + optional z0 drag) in storm-anim, used ONLY by pde_dynamic_marine;
production physics_terms/pde_steady_marine restored byte-identical. Spin-up now runs
to convergence (early-exit on maxU drift < 0.005 m/s per 1000 iters).

- [x] Run storms: cat3[0] (VT=13.5) done; cat5[13] (slowest, VT=10.1) running.
- [x] Diagnostics to outputs/dynamic/: peak maps + diff maps (4 variants x
      frozen/dynamic/diff), wind-vs-time at coast/mid/inland vertices, JSON series.
- [ ] Review with Paul: is the physical difference big enough to justify Phase 1-2?

### Phase 0 results (cat3[0]; VT=13.5, Rmax=28.9 mi, window -2..13.1 h)
- A (marine regression): dynamic == frozen exactly (0.0 mph) — solver validated.
- B (K&D through pressure vs scalar s(t)): land peaks mean +2.6 mph, max +6.5 mph.
  The BL spins down with its physical lag, so the storm carries wind farther inland;
  largest effect far-inland/north (ew 60-110). Direction: today's scalar decay
  UNDERSTATES inland peaks relative to lagged decay.
- C (in-PDE z0 drag): pure storm-scale drag effect (dynC - dynA on land) is a SMOOTH
  regional mean -4.4 mph (to -7.9). NOT comparable to the static exposure factor
  (pointwise -15..-45 mph): in-PDE drag = upstream/storm-scale BL momentum sink; the
  static log-law factor = local 10 m profile conversion. Complementary layers — a
  dynamic viewer product would still apply the per-vertex exposure factor on top
  (double-counting question resolved: they answer different questions).
- D (both): drag effect under decay -5.3 mph mean; effects roughly additive.
- The upwind solution develops the physical slab-BL inflow shock (sharp inner-eyewall
  front) — visible as steps in vertex time series when the shock sweeps past.
- Cost measured: ~3-4.5 min per variant per storm on MPS (convergent spin-up + march
  at dt ~1.1 s, Delta T = 1 min forcing updates).
- Consistency gap (context, NOT dynamics): production 800-iter steady field vs true
  converged steady state = mean 8.5 / max 12.1 mph at vertices (peaks 96.6 vs 103.6).
  The production Powell field is a lightly-relaxed initial guess, not the model's
  equilibrium. Decide in Phase 1 whether production should also run to convergence.

## Phase 1 — full precompute (GO per Paul 2026-07-06; overnight batched run)
- [x] Batched solver in storm-anim (`pde_dynamic_setup/spinup/march_batch`,
      `bilinear_polar_batch`, batch-safe `physics_terms_dyn`): 14x throughput
      (0.13 ms/storm-iter at batch 25 vs 1.82 single). Projection ~2 h for 300 storms.
- [x] `pipeline/windfield_dynamic_batch.py`: batches of 25 sorted by VT, checkpoint
      JSON per batch (resume-safe), per-storm NaN guard, assembles 4 products:
      powell_dyn{,_kd,_rough,_kd_rough}.json (schema = powell.json).
      Variant A needs no march (Phase 0: dyn==frozen exactly) — frozen translation
      of the converged marine spin-up. Baseline decision: products use the TRUE
      converged equilibrium, shipped as a separate "Powell (dynamic)" option;
      today's powell.json untouched.
- [x] Validate batch-of-1 vs Phase 0 single-storm output: EXACT (max|d|=0.00 mph on
      B/C/D window peaks; V0 identical at 103.6 mph).
- [x] Full run launched 2026-07-06 evening (log: tests/auto/logs/dynamic_batch_full.log,
      checkpoints: outputs/dynamic/precompute/). ETA ~2-3 h.
- [x] Full run COMPLETE (3.6 h, 12/12 batches, zero NaN failures). All four
      powell_dyn*.json complete (100 vectors x 840 vertices per cat), ranges sane.
- [ ] Review next session: (a) tail behaviour — extreme vectors (Rmax ~9 mi, CP ~905)
      reach 280 mph dyn-marine vs 212 production (converged-equilibrium amplification
      of input-distribution tails, ~+30%, consistent with the known baseline gap;
      median 138 vs 124); rough variants reach 346 mph via the landfall drag-jet
      transient — decide whether to cap/flag in the viewer; (b) fine-grid accuracy
      cross-check; (c) then Phase 2 viewer integration.
- [ ] Phase 0 slow-storm note for review: cat5[13] (VT=10.1, Rmax=10.5 mi): B lag
      effect is structural, range -18..+5.4 mph (dp-scaling reshapes the vortex, not
      just its amplitude); C shows a transient landfall drag-jet (peak 177 vs 150
      marine) — real slab-BL phenomenon, discuss before Phase 2 exposes C/D.
- [ ] Time-resolved product for popup/animation — decide after peaks land (ground-
      frame series for all 300 too big; likely showcase vectors only).

## Phase 2 — viewer integration (DONE 2026-07-06/07)
- [x] "Powell (dynamic)" model option (index.html); computeWindFor picks the product
      by checkbox state (marine/_kd/_rough/_kd_rough); static exposure factor still
      applied on top of in-PDE drag (documented). Old "Powell (PDE)" kept unchanged
      as the reference (per Paul).
- [x] Graceful degradation: popup + IKE map + animation + analysis panels show
      peaks-only notes for powelldyn (no storm-relative field exists).
- [x] Docs: new subsection "Powell (dynamic): a physical-time simulation"
      (sec:powelldyn) in FormS6.tex — scheme, validation, and FULL tail
      documentation (median 138 vs 124; extremes 280 vs 212 marine, 346 with the
      landfall drag-jet; cause and interpretation). PDF rebuilt (27 pp).
- [x] Selenium smoke test tests/auto/test_powelldyn_option.py: PASS (4 checkbox
      states render, analysis gate note, no severe console errors).
- [DECIDED AGAINST 2026-07-08] time-resolved frames for popup/animation/IKE.
      Storing per-frame fields for all vectors is a few hundred MB, which forces a
      lazy load with a multi-second first-Play stall + large RAM footprint. Paul's
      call: the UI-delay cost outweighs the benefit; Powell (dynamic) stays
      peak-only (footprint + "Color grid by peak wind"). Play shows "unavailable
      for this selection"; IKE returns field-pending; popup shows a peaks-only note.
      If ever revisited, frames would re-enable animation + IKE + popup series
      together (they all draw from the same time-resolved data).
- [ ] Later (optional): fine-grid accuracy cross-check (coarse dynamic grid vs
      fine, corrected physics, one storm to convergence).

## Review — Dynamic Powell (Phases 0-2, 2026-07-06/07)
Dynamic time-based Powell simulation shipped end-to-end in one day: physical-time
slab-BL solver (corrected Coriolis + upwind radial advection, converged spin-up,
K&D through pressure, NLCD z0 drag under the moving storm), validated exactly
against the frozen-field limit; 300-storm batched precompute (3.6 h, 14x batching
speed-up, zero failures) -> 4 products in outputs/web/; "Powell (dynamic)" wired
into the viewer with graceful degradation; tails documented in the docs. Files:
storm-anim/hurricane_pde_marine.py (additive), pipeline/windfield_dynamic{,_batch}.py,
web/{index.html,viewer.js,popup.js,anim.js,analysis.js}, docs/FormS6.tex.

## Decisions (resolved with Paul, 2026-07-06)
1. Decay IS Kaplan & DeMaria — same schedule as today, applied through the pressure
   forcing instead of as an instantaneous wind scalar. Decay and Roughness remain user
   OPTIONS (same checkboxes as today); both-off = pure marine dynamic run.
2. Coarsening is SPATIAL only (solver polar grid: rmin 0.5 -> ~4 km, Nphi 360 -> 180);
   the forcing/sampling Delta T stays 1 minute. The change relaxes the PDE's internal
   CFL stability step 0.06 s -> ~1 s (cost 50 -> ~2 min/storm). Eyewall resolution
   (Rmax ~14-50 km) is untouched; only the calm eye center coarsens. Phase 0 verifies
   coarse-vs-fine agreement on one storm.
3. Integrator lives with the other solvers: `pde_dynamic_marine()` in storm-anim's
   hurricane_pde_marine.py beside pde_steady_marine(); pipeline/windfield_dynamic.py is
   only the thin Form S-6 driver (inputs in, JSON out), mirroring windfield_grid.py.

---

# Tax-Roll Exposure Model (3rd exposure option) — plan (2026-07-09)

## Motivation
`loss = MDR(peak wind) x exposure`, where MDR is a **structural** damage ratio from a
masonry vulnerability curve. Both existing exposure models multiply that ratio by
**land-inclusive** value:

- **Uniform** — $100k/land vertex, a prescribed ROA p.186 constant (not an estimate).
- **Census** — ACS B25082 "aggregate value of owner-occupied housing units". The ACS
  question asks what "this house **and lot**" would sell for, so land cannot be removed.

Measured in downtown Miami (FDOR 2025, residential parcels): **land = 69.8% of just
value, structure = 29.4%**. So the Census model inflates loss ~3.4x there, and worst on
the expensive coast — i.e. the error correlates with the hazard.

The FL DOR tax roll is the only source that separates them: `JV = LND_VAL + building +
SPEC_FEAT_`, so **building value = JV - LND_VAL - SPEC_FEAT_**.

## Data source
- **Values + geometry**: FL Dept. of Revenue 2025 cadastral (NAL roll joined to parcel
  polygons by the county property appraisers). Statewide zip (2.77 GB):
  `https://publicfiles.dep.state.fl.us/otis/gis/data/Cadastral_Statewide.zip`
- Viewer label: **"Tax Roll (FL DOR)"**.
- Rejected: per-cell ArcGIS REST queries (~13 s/query, 2.5 h) and server-side
  `groupBy`/`where` (unindexed `CO_NO` full-scans 10.8M rows -> 55 s timeout, masked as
  HTTP 400). Bulk download is the only workable route.

## Todo
- [x] Download statewide cadastral zip -> `data/` (gitignored, like `nlcd_grid.tif`)
- [x] `pipeline/build_exposure_tax.py` -> `outputs/web/exposure_tax.json`
- [x] Wire "Tax Roll (FL DOR)" into `exposureMode()`/`exposureAt()`/`totalExposure()`
      in `web/viewer.js` + `<select id="exposureModel">` in `web/index.html`
- [x] Test in `tests/auto/` (data invariants + Selenium UI)
- [x] Document in `docs/FormS6.tex` + add `\tableofcontents` (now 29 pages)
- [x] `make-unix-zip.sh` (macOS/Linux counterpart to `make-windows-zip.sh`)

## Decisions
- **Residential only** (`DOR_UC` 001-008), matching the masonry residential
  vulnerability curve and the Census model's owner-occupied basis. Excludes 000 (vacant).
- **Structure value, not just value** — the reason the model exists.
- **Centroid assignment**, not envelope-intersect: the 3-mi cells tile the grid
  (dlat 3.000 mi, dlon 2.989 mi), so a centroid lands in exactly one cell.

## Review (2026-07-09)

### What shipped
1. **Rename** — "Pro Team Model" -> **"Pro Team Multimodel"** in the 5 places it was the
   product name (`index.html` title, `web/index.html` title + `<h1>`, `docs/FormS6.tex`
   `\title` + Purpose). Left alone: "Form S-6" (the regulatory form the software
   implements, not its name) and "Pro Team & Claude Code" (authorship). PDF rebuilt.
2. **Tax Roll (FL DOR) exposure** — third option in the Exposure model selector.
   `pipeline/fetch_cadastral.sh` (2.8 GB statewide zip -> `data/`, gitignored),
   `pipeline/build_exposure_tax.py` -> `outputs/web/exposure_tax.json` (~25 KB).
3. **Docs** — `\tableofcontents` added (there was none; now 29 pages), Exposure section
   rewritten to cover all three models.
4. **`make-unix-zip.sh`** — macOS/Linux counterpart to the Windows zip.

### Results
- $170/sqft x 3.64B sqft living area = **$619B** replacement cost, 1,611,952 residential
  parcels, 266/682 land cells populated.
- Uniform $68.2M · Census $572B · Tax Roll $619B.
- Correlation with Census on cells where both are populated: r = 0.83 (same shape,
  different level -- as expected, since one includes land and the other renters).

### Three findings that changed the design
1. **Both existing models are land-contaminated and cannot be fixed from their own data.**
   ACS B25082 asks what "this house *and lot*" would sell for. Land is 41.3% of
   single-family just value here, 59.1% in Miami-Dade. A structural MDR times
   house-and-lot value overstates loss most on the expensive coast -- i.e. the error
   correlates with the hazard.
2. **"JV - LND_VAL" is NOT structure value.** Appraisers fold a condo's land into the
   unit's just value: `LND_VAL = 0` for 99.9% of condos, 85.8% of co-ops -- 37% of
   parcels, $249B, concentrated in coastal high-rises. The naive subtraction removes
   nothing there. Hence replacement cost (living area x $/sqft) instead.
3. **A per-cell/per-county $/sqft rate encodes assessment policy, not construction cost.**
   Broward and Miami-Dade single-family homes have the same median market value per sqft
   ($276 vs $286), same size (1857 vs 1839 sqft), similar vintage -- yet Broward assigns
   9.1% of just value to land and Miami-Dade 59.1%. Per-county rates would be $247 vs
   $109/sqft: a 2.3x cliff along the county line that crosses this grid at ~25.96N.
   Rejected in favour of one domain-wide rate. Since
   %TLC = SUM(MDR x exposure)/SUM(exposure), a constant rate cancels exactly -- %TLC, SRC
   and EPR are invariant to it (verified in the Selenium test to 9 dp). It sets the
   dollar totals only; the exposure *shape* is residential floor area per cell.

### Notes for later
- **`python` is aliased to `/opt/homebrew/bin/python3`**, which shadows the venv even
  after `source venv/bin/activate`. Always call `venv/bin/python` explicitly.
- `requirements.txt` pinned geopandas/pyproj but they were not installed in the venv;
  `pipeline/build_exposure.py` would not have run either. Installed.
- The FDOR ArcGIS FeatureServer is unusable for this: spatial queries ~13 s each, and any
  `where` on the unindexed `CO_NO` full-scans 10.8M rows and dies at ~55 s (returned as a
  masked HTTP 400). Bulk download is the only workable route.
- `pyogrio.read_dataframe(where="DOR_UC IN (...)")` silently returns **0 rows** if
  `DOR_UC` is not also listed in `columns`. Cost an hour of false debugging.
- Peak disk during a rebuild is ~12.6 GB (`data/`), all gitignored and deletable.

---

# Plan: Sobol' indices as a first-class sensitivity method (2026-07-11)

Source: `pubs/Review and Comparison of SA Techniques.pdf` — Francom & Nachtsheim,
*A Review and Comparison of Different Sensitivity Analysis Techniques in Practice*,
LANL, arXiv:2506.11471 (2025).

## What already exists (surveyed before planning)

Sobol' is **already half-built**, which changes the shape of this task:

- `pipeline/fit_metamodels.py:119` — `sobol_indices()` computes first-order **S1**
  (Saltelli 2010) and total **ST** (Jansen 1999) on the **GPR emulator**, n=2048,
  over the observed input box. Exported to `outputs/web/metamodels.json` under
  `responses[resp][cat].sobol = {S1, ST}` for cat1/3/5 x {wind, tlc}.
- `web/analysis.js:530` — **ST is currently hijacked as an "EPR" variant** when the
  GPR metamodel is selected. **S1 is computed but never used anywhere.**

So the work is mostly *surfacing and correcting*, not implementing from scratch.

## Why the emulator route is the right one (and is already what we do)

The paper is explicit: Sobol' needs either a vast number of model runs or an
emulator. We have **100 runs** per (category, response) — far too few for direct
Sobol', but squarely in the regime the paper recommends for a GP emulator. The
existing GPR is exactly that emulator. No new sampling of the physics model needed.

## Two assumption checks (both run, both pass)

Sobol' requires **independent inputs** and a specified input distribution.

- **Independence**: max |off-diagonal correlation| among the six inputs is 0.040
  (cat1), 0.054 (cat3), 0.290 (cat5, CP~Rmax). cat1/cat3 clean; cat5 is a mild
  correlation, ~2.9 sigma under n=100, plausibly LHS sampling noise. Flag it, do
  not block on it.
- **Marginals**: `sobol_indices()` samples uniform over `[X.min, X.max]`. KS tests
  vs Uniform give p = 0.106 (CP), 0.062 (Rmax), 0.162 (VT), 1.000 (WSP/CF/FFP).
  The inputs *are* uniform, so the existing box-uniform sampling is **valid**. This
  was an unstated assumption in the code; it now has evidence behind it.

## The payoff: what Sobol' buys that SRC cannot

SRC is first-order and linear. Sobol' is variance-based and captures nonlinearity
and interaction. Per the paper: **ST > S1 means the input participates in
interactions**, and `ST - S1` quantifies how much. That gap is precisely the
diagnostic Section 10 (metamodels + interaction profiler) exists to provide, and
it can be stated *without* leaning on the unverifiable "2026 review" citation
(see open item below).

## Todo

### Pipeline
- [ ] Report the **S1/ST gap** per input in the `fit_metamodels.py` console summary.
- [ ] Add a **convergence check**: recompute Sobol' at n=1024 vs 2048 vs 4096 and
      assert indices are stable to ~0.01; record in the build log.
- [ ] Emit the **sum of S1** per (cat, response). Sum(S1) << 1 is itself the
      headline evidence of interaction; export it as `sobol.sum_S1`.
- [ ] Record the **correlation caveat** for cat5 into `metamodels.json`.

### Viewer (the presentation question)
- [ ] Add a **Method** toggle inside the existing **Sensitivity** panel: `SRC` vs
      `Sobol'`. One panel, two methods, same question — so they can be compared
      directly. (Do *not* add a 5th top-level button.)
- [ ] Sobol' view: **grouped bars per input, S1 and ST side by side**, with the
      `ST - S1` gap shaded as "interaction". Y axis = proportion of output variance
      (0..1). Annotate `sum(S1)` as "variance explained by main effects alone".
- [ ] **Un-hijack EPR**: stop overloading EPR with Sobol' ST (`analysis.js:530`).
      EPR is defined as SRC^2 x 100; conflating it with a variance-based total index
      is a category error. Sobol' moves to Sensitivity, where it belongs.
- [ ] Gate the Sobol' view on the default config (Powell + roughness), matching
      where the emulator is actually fit; show a note otherwise (same pattern the
      profiler already uses).

### Docs (`docs/FormS6.tex`)
- [ ] New subsection under Statistics: **Sobol' indices (variance-based SA)** —
      functional ANOVA decomposition, S1, ST, the interaction reading of ST - S1,
      the independence requirement, and the emulator justification for n=100.
- [ ] Add `\bibitem{francom}` (Francom & Nachtsheim 2025) and `\bibitem{sobol}`
      (Sobol' 2001).
- [ ] State the two assumption checks above as *verified*, with numbers.
- [ ] **Resolve the "2026 review" citation** (line 596). Sobol' + Francom gives a
      real, citable basis for the nonlinearity/interaction claim, so the unverifiable
      attribution can simply be replaced.

## Open question for the user
The cat5 CP~Rmax correlation (r = +0.29) mildly violates Sobol's independence
assumption. Options: (a) accept and document, (b) Shapley values, which the paper
recommends *specifically* for dependent inputs, (c) investigate whether the Form S-6
sampler intends CP and Rmax to be correlated. Recommend (a) for now.

## Review — Sobol' shipped (2026-07-11)

### What changed
- **Pipeline** (`pipeline/fit_metamodels.py`): Sobol' S1/ST with Monte-Carlo standard
  errors, pure second-order S_ij, and an emulator fit for **both** land configs.
- **Viewer** (`web/analysis.js`, `web/style.css`): SRC/Sobol' method tabs in the
  Sensitivity panel; stacked bars (main effect + interaction cap + error bar); S_ij
  annotation and red heat tint on the interaction matrix; S1/St labels on the
  profiler; a banner that states whether the indices are on and why.
- **Docs** (`docs/FormS6.tex`): new Section "Sobol' indices (variance-based SA)";
  Francom & Nachtsheim, Sobol' 2001, Saltelli 2010, Jansen 1999 added to References.
- **Test** (`tests/auto/check_sobol.py`): Selenium, drives the real viewer end to end.

### Four things the work turned up
1. **n=2048 was not converged.** The original Sobol' call drifted by ~0.05 between
   sample sizes -- the *same magnitude as the interaction it was meant to measure*.
   Raised to n=262,144 x 3 replicates, and every interaction now carries an error bar;
   one is reported as real only when it clears 2 s.e.
2. **ST was being mislabelled as EPR.** EPR is defined as SRC^2, a regression
   quantity; a variance-based total index is a different thing. Sobol' moved to
   Sensitivity, EPR restored to its definition.
3. **The model is ~97% additive, with exactly one real interaction.** Sum(S1) is
   0.90-0.97 everywhere. The whole interaction signal is Rmax x WSP (S_ij = +0.02 to
   +0.05); every other pair is ~0.000. Storm size and wind-profile shape act jointly.
4. **The K&D restriction was self-inflicted.** The decayed field was already sitting
   in `powell_kd.json` at full (100 x 840) shape -- the pipeline simply never fit an
   emulator to it ("Option A: default config only"). Fitting the second emulator
   removed the restriction entirely; Sobol' now works in the shipped default. Decay
   makes the response *more* interactive (Cat 1 loss: sum(S1) 0.90 -> 0.83).

### Still open
- The unverifiable "2026 review (M. Johnson and colleagues)" citation is **gone** --
  the nonlinearity/interaction claim now rests on the measured Sobol' result plus
  Francom & Nachtsheim, so no invented reference is needed.
- `inputs/` remains untracked and `build_grid.py` / `read_inputs.py` /
  `windfield_ua.py` still read the .xlsx from the repo root, where it no longer is.

## Review — one vector at a time; light default (2026-07-11)

Statistician: "The mean over all the input vectors is never of interest to me. The
total over the grid points for a given input vector *is* one of the outputs I'm
interested in."

### The key realisation
The **analysis was already right**. The SA response Y (`fit_metamodels.metric_columns`)
is `landfields.mean(axis=1)` -- axis 1 is *vertices*, so Y has always been an
aggregate over GRID POINTS, one value per vector. %TLC(i) likewise. SRC, EPR and the
new Sobol' indices therefore needed **no change at all**. Only the *map* was wrong.

### What changed
- **Removed** the across-vector Mean/Max map layers (`computeMeanWind` /
  `computeMaxWind`). Averaging a field over 100 vectors produces a vertex value no
  single storm ever generates, and nothing downstream ever asked for it.
- The **input-vector slider is now always live**; the map always shows one storm.
  (It used to open in mean mode with the slider *disabled*.)
- **Status now reports the per-vector spatial outputs** for every colour-by mode:
  TLC in dollars, %TLC, and the spatial mean/max of peak wind over the 682 land
  points. Verified these change with the slider (v1 -> $8.54M, v50 -> $17.19M,
  v100 -> $4.94M).
- **Light is the default theme**, dark is the option. All 20 doc figures re-captured
  in light; the old `light_theme` figure is repurposed as `dark_theme` to show the
  option.

### Bug caught by running it (not by the syntax check)
Removing `aggLabel()` left a bare `agg` reference in `pointInfoHTML` (viewer.js:630).
`new Function()` cannot catch an undefined *variable*, so the syntax check passed --
the figure capture crashed with "agg is not defined" on the hover popup. Fixed. This
is why the figures get regenerated by driving the real app.

New test: `tests/auto/check_vector_stats.py`.

---

## Plan — Form S-6 §3.2.2: validation, testing and uncertainty (2026-07-13)

Chris's §3.2.2 ("Model validation, testing and uncertainty analysis") asks for three
things. We have the first, are capable of the second, and have never done the third.

| §3.2.2 asks | status |
| --- | --- |
| k-fold validation | **done** — `cv_r2()` (5-fold) is exported per model in `metamodels.json` |
| a separate **30-run** hold-out test design | **missing** |
| catastrophe model re-run on those 30 | **capable** — `windfield_grid.py` drives the vendored PDE solver from any input vector |
| predicted R², prediction SD, prediction intervals | **missing** — we report in-sample R² and CV R², which §3.2.2 explicitly says is not enough |

The gap is the one Chris is pointing at. Paragraph 93 of the draft: training and
validation "are not generally independent", so a final model "should be tested on
hold-out data that has not been employed either in the training or validation
phases." Our 5-fold CV R² is a *validation* number. Nothing in this repo has ever
been scored against data the GPR did not see.

### Cost
Cheap. `build_all.sh` puts 300 PDE solves at ~11 min (~2.2 s/solve), so the 30-run
test design is 30 x 3 categories = **90 solves, ~4 minutes**. The compute is not the
obstacle; the open scope decisions are.

### Todo
- [x] 1. Generate the 30-run test design: maximin Latin hypercube over the same 6-D
      box as the 100 training vectors, per category. §3.1.3 demands the design choice
      be justified from the literature, so write the rationale down as we go.
      Ranges come from `inputs.json` (per-category observed min/max) so the test
      points interpolate rather than extrapolate.
- [x] 2. Parameterise `windfield_grid.py` on its input/output paths (currently the
      `inputs.json` -> `powell.json` paths are module constants) so it can run the
      test design without clobbering the training artifacts.
- [x] 3. Run the forward chain on the 30: Powell PDE -> roughness -> vulnerability ->
      loss. Reuse `metric_columns()` from `fit_metamodels.py` — do not re-implement
      the loss maths.
- [x] 4. Predict the same 30 with the **already-fit** GPR. No refitting; refitting on
      the test points destroys the hold-out.
- [x] 5. Compute the §3.2.2 statistics: predicted R², prediction standard deviation,
      and prediction intervals, for %TLC per category. Run these for **both** the GPR
      and the MLP — see the note below on why that is the point, not a nicety.
- [x] 6. Cross-check the GPR's analytic predictive SD (`return_std`) against the
      empirical hold-out spread. Supplementary, not a deliverable — see below.
- [x] 7. Write it up — a table + short section for `FormS6.tex`, and prose Chris can
      drop into §3.2.2.

### Scope
1. **The five locations are settled** (user, 2026-07-13): they are the app's starred
   Points of Interest, `POI_DEFAULTS` in `web/poi.js:6`. All five are land vertices:

   | §3.1.4 | (ew,ns) | place | regime |
   | --- | --- | --- | --- |
   | Location 1 | (9, 15)   | Dania Beach, Broward       | off-track, near coast |
   | Location 2 | (15, 0)   | Pinewood, Miami-Dade       | **on-track**, near landfall |
   | Location 3 | (60, 0)   | Kendall West, Miami-Dade   | **on-track**, far inland |
   | Location 4 | (12, -12) | Key Biscayne, Miami-Dade   | off-track, south |
   | Location 5 | (6, 45)   | Boynton Beach, Palm Beach  | off-track, 45 mi north |

   This set is a gift for validation. (6,45) is the **same vertex the JMP check used**
   (§sec:jmpcheck), and Dania Beach and Key Biscayne are the two the doc quotes as the
   `Rmax` comparison points (S_i = 0.63 and 0.37). So three of the five already carry
   independently-verified Sobol' numbers — a free correctness check on the per-vertex
   GPRs below. The two on-track points are where the doc says `Rmax` sensitivity
   "collapses to 0.02", so the five span the strong and weak regimes deliberately.
   Fill these into the draft's `XXXXX`/`YYYYY` placeholders (paragraphs 66-70).

2. **%LC needs per-vertex metamodels, which do not exist.** The app's single-point
   sensitivity is a 2nd-order RSM fit live in JS (`analysis.js:342`), *not* a GPR.
   The only GPRs we have are the footprint aggregates. `%TLC` maps cleanly onto our
   `tlc_uniform` response (uniform $100k per land vertex is exactly what §3.1.1
   specifies), so **%TLC can be tested today**. `%LC` at a location needs a new
   per-vertex GPR — cheap (100 points, 6 inputs; 5 locations x 3 cats = 15 fits) but
   it is new code, and it is the same thing the JMP verification did ad hoc.

### Why the hold-out must stay method-agnostic (Chris, 2026-07-13)
Chris's reply to the `return_std` idea: "I fully agree, but that's a GPR capability
that other methods lack." He is right, and it is the reason §3.2.2 is written the way
it is. Form S-6 is a *standard*: §3.2.1 leaves the metamodel choice open (GPR, neural
nets, regression trees, second-order regression). An uncertainty requirement resting
on the GPR's analytic predictive SD could only be met by organizations that happened
to choose GPR. It cannot be the required deliverable.

The 30-run hold-out is exactly the method-agnostic answer: predicted R², prediction
SD and prediction intervals are computed from **residuals on held-out data**, so they
are defined identically for a random forest or a neural net that expose no native
uncertainty at all. That is the deliverable. The GPR's `return_std` is a bonus we
happen to have, and it is *supplementary*, never required.

We are well placed to demonstrate this, because we already fit **both** a GPR and an
MLP. So: run the identical hold-out procedure on both. It yields valid prediction
intervals for the MLP, which has no `return_std` — evidence that the requirement is
satisfiable by any modeling organization — while separately confirming the GPR's
analytic error bars agree with the empirical ones. That is an argument for the form's
design, not merely a result about our model. Worth offering Chris for §3.2.2 itself.

### Recommendation
Nothing is blocked now. The 30 forward runs produce **every** vertex at once, so %TLC
and all five %LC locations come out of the same ~4 minutes of compute; the only extra
work for the locations is 15 per-vertex GPR fits (5 locations x 3 categories), which
are seconds. Do both in one pass.

Order within the pass: %TLC first (it needs no new model code and proves the hold-out
machinery end to end), then the per-vertex GPRs. Check the three per-vertex GPRs at
Boynton Beach, Dania Beach and Key Biscayne against the Sobol' values already in the
doc before trusting any of the five — if those disagree, the per-vertex fit is wrong
and the %LC numbers are worthless.

## Review — Form S-6 §3.2.2 hold-out test shipped (2026-07-13)

All seven todos done. Both scope questions resolved (locations = the app's POIs;
per-vertex GPRs written).

### What was built
- `pipeline/make_test_design.py` — 30-run maximin LHD per category, seed 12345
  (deliberately not the pipeline's SEED=0), on the training min/max box so every test
  point interpolates. Closest test storm sits 0.13-0.21 (scaled) from any training
  storm: new storms, not near-duplicates.
- `pipeline/windfield_grid.py` — now takes `--inputs/--out/--out-kd/--no-field`, so a
  test design runs through the identical solver without touching training artifacts.
- `pipeline/holdout_test.py` — scores the SHIPPED metamodels (never refit) on the 30.
- `pipeline/holdout_points.py` — per-vertex GPRs for %LC at the five §3.1.4 locations.
- `docs/FormS6.tex` §9.2 "Testing on unseen storms" — Tables 2 and 3.

### Result
%TLC predicted R² = 0.9995-0.9998 (GPR), 0.995-0.9996 (NN). s_pred 0.10-0.23 pp.
95% PI coverage 0.90-1.00 vs nominal 0.95. Location-level %LC predicted R² 0.971-1.000.
90 PDE solves, 228 s.

### Three things the work turned up
1. **The test had to be attacked before it could be believed.** R²=0.9998 is exactly
   what a plumbing bug that scored against TRAINING data would produce, and predicted
   R² cannot distinguish the two. Negative controls settle it: permuted test inputs
   give R² = -1.62, the wrong category gives -38.3, nearest test-to-train distance is
   1.81 raw units. Any future change here should re-run those controls.
2. **The per-vertex GPRs were gated on already-known answers.** They had to reproduce
   Sobol' S1(Rmax) at three vertices before any %LC score was trusted — and the
   tightest match (0.827 vs 0.82) is Boynton Beach, the vertex verified against JMP.
   `holdout_points.py` exits non-zero if that check fails.
3. **The emulator is least precise where the loss is smallest.** Both on-track vertices
   score R² >= 0.999; the three off-track ones (Dania Cat 1 = 0.971 the worst) are
   measurably harder. Physical, not numerical: off-track the question is whether the
   wind field *reaches*, a near-threshold regime governed by Rmax — the same effect
   that puts S1(Rmax) at 0.83 off-track and 0.02 on it. A threshold is harder to
   emulate than a plateau. Stated in the doc rather than buried.

### Also corrected
The single-sample Sobol' estimator moved two per-vertex figures the doc quoted in
§8.1: Dania Beach 0.63 -> 0.67, Key Biscayne 0.37 -> 0.42. Boynton (0.82 -> 0.83) and
the on-track collapse (0.02) stand.

### Not done
The 30x6 design matrices (540 numbers) are not in an appendix — they regenerate from
`make_test_design.py` at the recorded seed. Add the listing if a reviewer wants it.

---

## Plan — Fetch-blended directional roughness (2026-07-13)

The meteorologist: "To do this correctly, it needs to be wind direction dependent. In
effect, the roughness is time dependent since the wind direction is time dependent."
He is right, and the Florida *public* model has done it this way since 2005.

### What FPHLM does (Powell et al. 2005, JWEIA 93:651-674, §8 "Land friction influences")
- "For each 10 min segment of storm motion, the open terrain exposure surface wind speed
  AND DIRECTION is determined... The open terrain wind at each zip code centroid is
  corrected to the observed terrain using a **fetch-dependent effective roughness for
  that particular direction and zip code**."
- "The effective roughness... assumes that **internal boundary layer development
  prevents the flow from reaching complete equilibrium** with its surroundings. The flow
  is most influenced by the roughness of the terrain **3 km upstream**... but is still
  influenced by terrain further upstream."
- Implemented with the **Source Area Model** (Schmidt & Oke), not a naive fetch average.
- V8.3 submission: effective roughness for **8 incoming wind directions** on a **~90 m
  grid**; aggregated to ZIP by population weighting.
- And, pointed straight at models like ours: "This approach is an improvement over
  current models that **consider zip code roughness constant for all wind directions**."

### Where the data actually lives (it is NOT in a paper)
**Roughness**: a land-cover raster we ALREADY HOLD. `data/nlcd_grid.tif` — NLCD 2021,
7194x4495 px @ ~30 m, EPSG:4326, bounds (-82,25)-(-80,27), covering the whole S-6 grid.
38 MB on disk, fetched by `pipeline/fetch_nlcd.sh` from MRLC. The z0-per-class table is
published (HAZUS/NIBS; Vickery 2009) and already coded at `build_roughness.py:Z0_BY_CLASS`.
Our 30 m is FINER than FPHLM's ~90 m. Nothing to download. FPHLM's own derived product is
ZIP-aggregated and useless on our 840-vertex 3-mile grid; we borrow the METHOD, not the file.

**Direction**: not data at all — our solver computes it and discards it.
`hurricane_pde_marine.py:371` does `speed_ms = sqrt(Ux_er**2 + Uy_er**2)`, throwing the
direction away; it is `atan2(Uy_er, Ux_er)`. The inflow angle is already modelled
(`inflow_angle_rad`, line 57: 25 deg peak near Rmax, 15 inside, 8 far field) and "er" =
earth-relative means translation is already folded in.

### The structural problem: max does not commute
We compute        `V_peak(x) = [ max_t V_marine(x,t) ] * f(x)`
Correct is        `V_peak(x) = max_t [ V_marine(x,t) * f(x, theta(x,t)) ]`
A time-varying factor cannot be pulled outside the max. The peak SURFACE wind can occur
at a different time than the peak MARINE wind -- typically when the wind swings onshore
onto a low-roughness sea fetch. `solve_all()` keeps `surf_mph` as (840,H) and collapses
it with `.max(dim=1)` on the last line, so the fix goes in just before that collapse.

### Todo
- [x] 1. `pipeline/build_roughness_directional.py`: for each of 840 vertices x N sectors,
      traverse the NLCD raster UPWIND along the sector bearing, sampling z0 every 30 m out
      to ~10 km, and form a fetch-weighted effective z0. Output `roughness_dir.json`
      -> factors[840][N]. Cost ~2.2M raster lookups: seconds.
      ACCEPTANCE TEST: a beachfront vertex must show LOW z0 for onshore sectors and HIGH
      z0 for inland sectors. If it does not, the traverse is backwards -- this is the
      single easiest thing to get wrong and it would silently invert the whole result.
- [x] 2. Return direction from the solver: keep (Ux_er, Uy_er) instead of collapsing to
      speed. In `windfield_grid.solve_all`, sample direction alongside speed.
      NOTE: interpolate the COMPONENTS, never the angle -- angles wrap at 2*pi and
      bilinear-averaging them is silently wrong near the branch cut.
- [x] 3. Apply roughness inside the time loop, BEFORE the max: build f as (840,H) from
      theta(x,t), multiply, then `.max(dim=1)`. Interpolate BETWEEN sectors rather than
      snapping, or the peak wind acquires N discrete jumps as the direction rotates --
      an artifact that would look like physics.
      Output shape stays (840,), so metric_columns / GPR / Sobol' / viewer are untouched.
- [ ] 4. Viewer: the roughness toggle is currently a CLIENT-SIDE multiplier on the marine
      peak (an explicit design choice -- "the toggle is instant and the marine Powell
      precompute is reused as-is"). That cannot survive: a direction-dependent factor
      cannot be applied to an already-peaked wind. Precompute a third field alongside
      `powell` / `powell_kd` and make the toggle a lookup. UI behaviour preserved.
- [ ] 5. Validate: (a) coastal vertices with onshore fetch should gain wind vs the
      isotropic model, deep-inland should barely move; (b) show cases where the surface
      peak shifts OFF the marine peak in time -- that is the physical signature that the
      commutation actually mattered; (c) re-run the S-6 deliverables and report how the
      Sobol' indices move.
- [ ] 6. Docs: new subsection in §Meteorology: Land effect, citing Powell et al. 2005 §8,
      Schmidt & Oke (SAM), ESDU 82026 / Wieringa (1993). Report the sensitivity delta.

### Consequence for Form S-6: this MOVES the deliverable
How much low-roughness onshore fetch a site gets depends on how fast the wind direction
swings, which depends on VT and Rmax. We already saw VT's Sobol' S1 jump 0.006 -> 0.043
the moment Kaplan-DeMaria decay made *time inland* matter. Directional roughness makes
*time in a given fetch sector* matter, so expect VT to rise again and Rmax x VT with it.
This is not cosmetic. It invalidates the current Sobol' table (§8) and the hold-out test
(§9.2) -- both must be re-run (windfield ~15 min; fit_metamodels ~2 h).

### Open decision (do NOT pick silently)
FPHLM uses the **Source Area Model** (Schmidt & Oke 1990) to weight the upwind fetch --
it models the turbulence source area of a downstream sensor, accounting for patchy
roughness. The cheaper alternative is an exponential/IBL distance weighting (ESDU 82026,
Wieringa 1993) peaking ~3 km upwind and decaying to ~10 km. SAM is what the public model
does and is the defensible choice for a Commission submission; the exponential weighting
is a genuine SIMPLIFICATION and must be labelled as one if chosen. ASK THE USER.

### Progress (2026-07-13) — steps 1-3 done, validated

**Decisions taken**: Source Area Model (FPHLM parity), 12 sectors interpolated.

**Built**
- `pipeline/build_roughness_directional.py` -> `roughness_dir.json` (840 x 12, 142 KB)
- `hurricane_pde_marine.py`: returns the earth-relative components (Ux, Uy) in `meta`
  instead of discarding them at the `sqrt(Ux^2+Uy^2)`. Direction is atan2(Uy, Ux); the
  PDE already solves the frictional inflow and carries the translation, so nothing had
  to be modelled.
- `windfield_grid.py`: `dir_roughness_factor()` + `solve_all` now emits `powell_dir.json`
  and `powell_dir_kd.json` with roughness applied INSIDE the time loop.
- `tests/auto/check_directional_roughness.py`

**THREE bugs I made and caught. All three would have shipped silently.**
1. **Wrong height.** I first evaluated the source area at the 10 m anemometer, which
   collapses the footprint to a few hundred metres -- one canal 100 m upwind would have
   dominated. Effective roughness belongs at the BLENDING height (~60 m; Wieringa, Mason).
   The tell: my footprint did not reproduce FPHLM's stated "3 km upstream" scale. At
   zb=60 m it does (50% at 2.1 km, 90% at 10.4 km). That agreement is the calibration.
2. **The acceptance test was wrong and failed a correct map.** I asserted "east-coast
   vertices must be smooth from the east (sea)". False: a MAINLAND vertex west of
   Biscayne Bay meets 5 km of dense Miami on an easterly, so a rough easterly is right.
   "East = sea" holds only on the barrier islands. I had encoded a geography guess AS a
   test. Replaced with two that cannot be fooled: a pure coordinate check on the traverse
   direction, and monotonicity of z0 against the raster's own water fraction.
   I also nearly loosened a Spearman threshold when it read -0.63 instead of -0.8 --
   which is how you talk yourself past a real bug. It turned out -0.63 was EXPECTED
   (purely-land sectors span marsh 30 mm to urban 1000 mm at zero water fetch, injecting
   rank noise that dilutes any global rho by construction). A global correlation was the
   wrong statistic, so it got replaced, not retuned.
3. **Averaged the wrong quantity.** I blended z0 as a footprint-weighted GEOMETRIC mean.
   Wrong: ln(z0) is -8.5 for water vs -0.7 for suburbia, so a log-average is dragged to
   the smoothest surface in the fetch. STRESS is what is conserved, so the drag averages
   (Mason 1988): 1/ln^2(zb/z0_eff) = SUM w_i/ln^2(zb/z0_i). On a 15% land / 85% sea fetch
   the log-average gives 0.6 mm where drag gives 6.2 mm -- 10x too smooth, inflating
   coastal winds. Caught because Boynton Beach gained an implausible +25 mph; after the
   fix it gains +9, and the whole delta distribution went from skewed-high to symmetric.

**Result (Cat 5 vector 1, validated)**
- The factor does NOT commute with the max: **63% of land vertices** have their surface
  peak at a different TIME than their marine peak (median 4 min, max 92 min). That is the
  entire justification for the change, and it is measured, not asserted.
- Peak wind: median -0.7%, p5 -11.6%, p95 +13.0%, range -43 to +41 mph.
- **Coastal gains, inland loses, and both are physical.** Dania Beach +40 mph and Key
  Biscayne +41: on an onshore wind these are effectively ASCE 7 Exposure D (z0 = 1-5 mm
  from the east at Dania, 675 mm from the west -- the isotropic map splits the difference
  at 276 mm and is wrong in BOTH directions). Inland Pinewood LOSES 8.9 mph: its 20 km
  upwind fetch is unbroken city (294-616 mm every sector) whereas the isotropic 500 m
  circle catches parks and canals (222 mm). This is exactly the effect FPHLM claims:
  "flow with an upstream fetch over the sea can be significantly stronger than flow over
  a constant land roughness."

**Next**: fit_metamodels on the directional field (metric_columns must NOT re-apply
roughness.json -- it is already baked in), then re-run Sobol' + hold-out and report the
sensitivity delta.

---

## Order-of-operations: decay out of sequence + gust factor missing (2026-07-14)

Two meteorologists reviewed the wind->loss chain. Verdict: "the decay factor may be out
of sequence and the gust factor missing but everything else is OK." Both confirmed.

### Correct sequence (per meteorologist 2)
    gradient --CF--> 10-min mean MARINE
             --log law--> 10-min mean LAND
             --x1.1 (10min->max 1-min)--> max 1-min sustained
             --K-D decay--> decayed 1-min sustained     <-- decay belongs HERE
             --x1.3 (max 1-min->peak 3s)--> peak 3-s gust
             --> damage curve (3-s gust basis)

### What we have (windfield_grid.py)
    10-min mean MARINE --decay(t) inside loop--> --max over passage--> --x roughness-->
    --x GUST_FACTOR=1.0--> damage curve

### Two defects
1. **Decay out of sequence.** intensity_schedule (line 156) starts from V0 = peak
   MARINE 10-min-mean surface wind and decays toward Vb = KD_VB_MPH = 30.7 mph. But
   Kaplan-DeMaria was calibrated for the max 1-MINUTE SUSTAINED wind: Vb=30.7 (26.7 kt)
   is a 1-min background, V0 should be the 1-min sustained LAND wind. We apply
   1-min-calibrated constants to a 10-min marine field.
   **This is NOT fixable by a trailing factor** because the decay is AFFINE (additive
   Vb), not multiplicative, so it does not commute with the gust factor:
       correct  decay(1.1 V) = 30.7 + (1.1 V0 - 30.7) e^-at   -> asymptote 30.7
       current  1.1 decay(V) = 33.8 + (1.1 V0 - 33.8) e^-at   -> asymptote 33.8
   ~3 mph (~10%) disagreement deep inland, growing with time inland, ~0 near landfall.
   The decay must be applied to the 1-min sustained land wind directly.
2. **Gust factor missing.** GUST_FACTOR=1.0. Need 10min->1min ~1.1 and 1min->3s ~1.3,
   BOTH varying with roughness/turbulence/speed ("gust factors over water are much less
   than over land") -> derive from the directional effective z0 already computed in
   build_roughness_directional.py, not a constant. Marine fetch small G, urban fetch large.

### Everything else confirmed correct
CF gradient->surface, Vickery log-law marine->land, the slot positions -- all OK per both
reviewers. Only the decay position and the two gust factors are wrong/absent.

### Sequencing
A design change (new HURDAT2/M1 ranges) forces a full regeneration anyway. Fold BOTH
fixes into that pass rather than re-running the windfields twice. Open input still needed:
the averaging-period convention and confirmation the gust factors derive from z0 (the
directional-roughness gust-factor decision). Not started; holding for go-ahead.

---

## Physics-constrained design (HURDAT2 dependence) — 2026-07-15

Replaces the uniform strawman with the dependence HURDAT2 actually shows. Built and
validated; NOT yet fed to the pipeline (holds with the other regeneration items).

### Dependence measured from HURDAT2 (2021-2024 RMW era, 18-32 N)
Full CP/RMW/VT correlation checked. Only ONE physical pair is real:
- **CP-RMW: r = +0.47** (~13 sigma). Real, strongest in the lumped C1-C5 range.
- CP-VT = -0.14, RMW-VT = +0.03 -> both <2% variance -> VT INDEPENDENT.
- CF independent (modeling knob; user noted it should be low-variance around a mean --
  open question whether to narrow it from U[0.8,0.95]).
- FFP independent; deficit FFP-CP>0 holds (max CP 990 < min FFP 1006).
- 37% of the raw CP x RMW box has ZERO observations (intense + huge eye).

### Two couplings encoded (user approved both)
1. **RMW | CP**: bounded by a CP-dependent envelope (empirical p90, monotone). Filled
   LOG-uniformly (RMW|CP is right-skewed) -> induced r = +0.46, matches observed +0.47,
   and 0 impossible-corner runs. Uniform fill first gave +0.64 (over-correlated); the
   log fill fixed it.
2. **B | RMW**: Powell (2005) Eq.7  B = 1.881 - 0.010917*Lat - 0.005567*RMW + eps,
   eps~N(0,0.286), censored [0.8,2.2]. At 25.86 N -> B = 1.599 - 0.005567*RMW. This
   couples B to RMW (r=-0.20) AND recalibrates the range from ad-hoc [1.0,2.5] to
   Powell's physical [0.96,2.00].

### Deliverables
- pipeline/make_constrained_design.py  -> outputs/design_hurricane_constrained.xlsx
- outputs/design_hurricane_constrained.png  (validation vs HURDAT2)
- pipeline/make_lhs_design.py (earlier: plain uniform designs, incl. the abstract test)

### DOWNSTREAM CHANGE required before this design runs
The design now outputs **Holland B directly** (column B_Holland), replacing the WSP
quantile. The model's wsp_to_B(quantile) in windfield_grid.py must be bypassed -- B is
consumed as-is. Small change, but required. Fold into the regeneration pass alongside the
decay-reorder + gust-factor fixes.

### Still open (all converge on one regeneration pass)
- 5 design-check points (RMW nm vs statute, C1-C5 lumping, ...) -- with meteorologist.
- Gust-factor value + averaging convention -- with meteorologist.
- Whether to narrow CF from uniform.
- Sensitivity on correlated inputs: standard Sobol' assumes independence; with CP-RMW
  coupled, the analysis needs Shapley effects (or Kucherenko) -- not standard Saltelli.

---

## Order of operations: decay reorder + gust factor IMPLEMENTED (2026-07-15)

User: "operate on the assumption the right-most flowchart is correct" (outputs/
order_of_operations.png). Implemented the corrected sequence. Two minimal changes,
because G1/G2 are constants and roughness is a per-vertex scalar -> both commute with
the max-over-passage; only the decay's affine background does not.

### Changes
1. windfield_grid.py: gust constants G_10MIN_1MIN=1.1, G_1MIN_3S=1.3; and the decay
   schedule is now keyed to the 1-MIN intensity: V0 = G1 * surf.max() (was surf.max()).
   This is the affine correction -- K-D decays toward a 1-min background (Vb=30.7), so
   its ratio must be taken relative to the 1-min peak.
2. web/viewer.js: GUST_FACTOR 1.0 -> 1.43 (= G1*G2). Finishes 10-min mean -> 3-s gust,
   the basis the damage curve expects.

### Validated (tests/auto/check_order_of_ops.py, Cat5 v1, no full regen)
- Decay asymptote: deep-inland decayed wind 45.5 -> 45.0 mph (small, correct direction;
  affine effect only bites near full decay).
- Gust: five S-6 locations scale x1.43 near landfall (Dania 90.7 -> 129.7 mph).
- **Domain %LC 12.5 -> 33.8 (2.7x)**. The mean wind was going into a 3-s-gust curve with
  no conversion; it now gets an actual 3-s gust. Correct, but shifts EVERY loss number.

### Consequences
- Wind-response Sobol' indices UNCHANGED (x1.43 is a constant scale; variance
  decomposition is scale-invariant). LOSS-response Sobol'/hold-out/EP/AAL all shift
  (MDR is nonlinear, so the gust conversion moves where on the curve we sit).
- STALE now: powell_kd.json (kd uses the new V0), metamodels.json, all loss figures/
  tables in the doc. Marine powell.json is unchanged (no V0 dependence). Regenerate in
  the unified pass with the new design.

### Provisional / open
- Constant 1.1 and 1.3 are the flowchart's LAND values; the meteorologist noted gust
  factors vary with roughness/speed and are much smaller over water. z0-dependent gust
  (keyed to the directional effective z0 already computed) is the planned refinement.
- Mark not yet confirmed on the flowchart; operating on the assumption per user.

---

## Plan — Run the constrained n=200 design (lumped C1–C5) — 2026-07-20

User asked to "run the latest design with n=200", expecting an overnight job.

### Finding 1: the compute is ~7 minutes, not overnight
Measured 2.11 s/solve (tests/auto/windfield_grid_36h.log). The new design is 200
solves in ONE lumped set (not 3x100 = 300), so the base run is ~7 min; training +
hold-out + the KD/directional variants still land well under an hour. There is no
reason to batch this to a nightly window. **The cost here is the migration, not the CPU.**

### Finding 2: the design shape does not match the pipeline
outputs/design_hurricane_constrained.xlsx is 200 rows, columns
`Run, CP, RMW, VT, B_Holland, CF, FFP` — lumped C1–C5, no category, B direct.
Everything downstream is hardwired to 3 categories x 100 with `Rmax`/`WSP`+`Quantile`:

- pipeline/read_inputs.py — reads the legacy FormS6Input.xlsx; **no converter exists**
  from the new .xlsx to inputs.json
- pipeline/windfield_grid.py:330,346,348 — cat1/cat3/cat5 output keys + solve loop
  (line 101 ALREADY consumes B directly — the projectplan downstream change is done)
- pipeline/fit_metamodels.py:31 — CATS list; VARS still names Rmax/WSP
- web/analysis.js:15 — SA_CATS, plus ~20 per-cat call sites
- web/viewer.js:217-243 — computePointAAL; ~20 more per-cat sites
- docs/FormS6.tex — ~20 sites incl. per-category tables (L455, L1476, L1522) and the
  category-weighted EP/AAL (L1316-17)

User decision (2026-07-20): **go lumped, full migration**, and **regenerate the
hold-out to match** (current inputs_test.json is 30/cat on the OLD uniform box with
WSP — it cannot validate the new metamodels).

### TWO BLOCKERS needing a user call before the run (see "Open" below)
1. **AAL loses its category weighting.** Today AAL = sum_c lambda_c * mean_i[loss | cat c],
   with editable per-category rates (viewer.js:225, 834). A lumped design has no
   category bins, so this needs a single landfall rate lambda for the CP band the
   design covers (CP 912.6–986.9 mb), giving AAL = lambda * mean over the 200 runs.
   That is the correct formulation for a population sample, but lambda is a NEW number
   the user must supply or approve — it is not derivable from anything in the repo.
2. **Standard Sobol' becomes invalid.** fit_metamodels.py uses Saltelli A/B sampling,
   which assumes independent inputs. The constrained design deliberately couples
   CP–RMW (r=+0.46) and B–RMW (r=-0.20). The 2026-07-15 entry already flagged this:
   correlated inputs need Shapley effects or Kucherenko, not standard Saltelli.
   Running Saltelli on this design would produce indices that silently misattribute
   the shared CP/RMW variance.

### Blocker 2 RESOLVED (2026-07-20): Shapley effects
User chose Shapley effects for the correlated design. Chosen estimator: the
**given-data nearest-neighbour** Shapley estimator (Broto, Bachoc & Depecker 2020),
NOT the conditional-permutation algorithm (Song, Nelson & Staum 2016).
Rationale: the constrained joint is defined implicitly (empirical CP-dependent RMW
envelope + log fill; Powell Eq.7 for B|RMW), so we can sample the joint forward but
cannot cheaply invert arbitrary conditionals E[Y|X_J] that Song's method needs. The
given-data estimator needs only an (X,Y) sample and kNN conditioning — no conditional
sampler. We already have a generative joint (make_constrained_design.build) + a GPR
emulator, so draw a large synthetic (X,Y) set from them and estimate Shapley on it.
Properties: Shapley effects sum EXACTLY to 1 even under dependence (unlike Sobol',
where S1 and ST bracket but don't partition), so the doc's "sum of indices" framing
(FormS6.tex L899) changes from "lands near 1" to "partitions 1 by construction".
Keep the independent-input Saltelli path available for the marginal/reference reading.

### Todo
- [ ] Decide AAL rate lambda (blocker 1) — PHASE 2
- [x] Shapley estimator built + validated (tests/auto/check_shapley.py, ALL PASS):
      indep-additive matches Sobol' S1 (err 0.012); interaction splits 0.49/0.49;
      correlated pair r=0.95 shares 0.545/0.452 and sums to 1. Default k=40 (the kNN
      smoothing bias goes as ~1/k; k=40 -> ~0.01 floor, matching the Sobol' MC error).
- [ ] fit_metamodels.py: add given-data Shapley estimator; keep Saltelli as reference
- [x] make_constrained_design.py: emits inputs_constrained.json (lumped, single "all"
      group; names CP/Rmax/VT/B/CF/FFP; Rmax = RMW(nm)*1.150779 -> statute mi for the
      solver). --holdout emits inputs_test_constrained.json (60 unseen, seed 97).
- [x] windfield_grid.py: group loop now derives groups from inputs keys (any list-of-
      dicts) instead of hardcoding cat1/cat3/cat5 — handles both legacy and lumped.
- [x] Run: 200 training solves (8 min, marine peak 212 mph, KD 211.8) ->
      powell_constrained{,_kd,_dir,_dir_kd,_field}.json ; 60 holdout solves (2.5 min)
      -> powell_test_constrained*.json. All new files, old viewer data untouched.
- [ ] fit_metamodels.py: lumped; VARS -> B; Shapley (given-data kNN) primary SA,
      Saltelli kept as an independent-input reference -> metamodels_constrained.json
- [ ] web/analysis.js + viewer.js: drop the category selector and per-cat plumbing (P2)
- [ ] docs/FormS6.tex: per-category tables and EP/AAL section rewritten to lumped (P2)
- [ ] Review section: what moved vs the 3x100 baseline

### Sanity vs baseline (2026-07-20)
Lumped n=200 domain-max peak wind 59-212 mph spans the union of the old 3 categories
(65-219); domain-mean-per-storm 48-158 mph vs old 58-171. Mean sits between old cat3
and cat5 (uniform CP sampling over 890-990, not 3 centroids). Coherent, no anomalies.

## REVIEW — Phase 1 (core run) COMPLETE, 2026-07-20

The n=200 constrained lumped design is run end-to-end and validated. Phase 2 (viewer,
docs, dynamic Powell) is deliberately deferred; the repo is left WORKING (all new data
in *_constrained files; legacy 3x100 viewer data untouched).

### What was built / run
- make_constrained_design.py: emits inputs_constrained.json (lumped "all", 200 runs;
  Rmax = RMW(nm)*1.150779 -> statute mi; B direct). transform()/sample_joint() share the
  coupling logic; --holdout -> inputs_test_constrained.json (60 unseen, seed 97).
- windfield_grid.py: group loop derives groups from inputs keys (legacy 3-cat OR lumped).
- 200 training solves (8 min, marine peak 212 / KD 211.8 mph) + 60 holdout solves
  (2.5 min) -> powell_constrained*.json, powell_test_constrained*.json.
- Shapley estimator (given-data kNN, Broto 2020) added to fit_metamodels.py, VALIDATED
  standalone (tests/auto/check_shapley.py: matches Sobol' S1 for independent inputs,
  splits interactions, shares correlated credit, sums to 1). Default k=40.
- fit_metamodels.py --constrained -> metamodels_constrained.json (187 KB): GPR/MLP
  predictors + Shapley (primary, both land configs) + Saltelli (independent-uniform
  REFERENCE, lighter budget n=16384/reps=4). JS parity exact (GPR 2e-9, MLP 0).
- holdout_test.py --constrained -> holdout_test_constrained.json.

### Findings (the "did conclusions change" answer: YES, and defensibly)
1. **CP is the top driver under lumping.** Shapley (roughness): CP 0.55-0.66, Rmax
   0.13-0.24, B 0.07-0.14 across all 5 responses. The OLD per-category framing put Rmax
   on top only because CP barely varies within a category; across C1-C5 CP's 100-mb
   swing dominates the variance. ARD still reads Rmax top (local sensitivity) -- Shapley
   reads CP top (variance contribution). Both are "right"; Shapley answers the SA question.
2. **With K-D decay on, VT gains influence** (wind: 0.04 -> 0.12): time-over-land ~ 1/VT.
3. **Shapley vs Sobol divergence is real and expected.** Sobol reference sum(S1)=0.93-0.99
   with NEGATIVE higher-order (-0.006..-0.027) -- the independence assumption breaking on
   the CP-RMW coupling (r=0.43). Shapley sums to EXACTLY 1. Top interaction CPxRmax
   (the coupled pair) in every response. This is exactly why Shapley was chosen.
4. **Emulator quality is excellent.** GPR/MLP R²=1.000, CV R² 0.990-0.999. Hold-out on
   60 unseen storms: predicted R² >= 0.998 all responses (headline %TLC 0.9995 both
   models), 95% PI coverage 0.88-0.97. Form S-6 §3.2.2 satisfied on the new design.

### Phase 2 (deferred, explicit user go-ahead needed)
- web/analysis.js + viewer.js: drop category selector, read *_constrained + shapley,
  reformulate AAL to a single landfall rate lambda (blocker 1 -- user input needed).
- docs/FormS6.tex: per-category tables + EP/AAL section rewritten to lumped; Sobol'->
  Shapley narrative (sum-to-1 becomes exact, not approximate).
- Dynamic Powell A/B/C/D + dyn_frames.json: needs the rec["B"] bypass at
  windfield_dynamic_batch.py:110 first, then rerun on inputs_constrained.json (~30-40 min).

### Dynamic products ALSO need regeneration (user flagged 2026-07-20) — PHASE 2
The dynamic A/B/C/D land products are the dynamic twin of powell.json and are built on
the SAME old foundation being replaced, so they are stale on two counts (old storms +
old B mapping):
- powell_dyn.json, powell_dyn_kd.json, powell_dyn_rough.json, powell_dyn_kd_rough.json
- dyn_frames.json (animation frames), outputs/dynamic/precompute/, precompute_live.py out
- ALL read the cat-based inputs.json; windfield_dynamic_batch.py:110 derives B via
  wsp_to_B(r["WSP"]) and has NO rec["B"] bypass yet -> needs the same one-line B-direct
  edit the static solver already has, BEFORE it can consume the constrained design.
- Viewer-map deliverables -> PHASE 2 (with analysis.js/viewer.js/docs), not the core run.
  Compute ~ another 30-40 min (200 storms x 4 products).

### Open
- lambda (annual landfall rate) for the design's CP band — user input needed (PHASE 2)
- Whether the viewer keeps a category selector at all, or becomes single-population
- SETTLED: Shapley effects (given-data kNN estimator) for the correlated design

---

## Dynamic-solver instability + PyClaw research direction (2026-07-20)

Phase-2 dynamic-Powell regeneration (windfield_dynamic_batch.py --constrained, refactored
to lumped groups + rec["B"] bypass) was launched, but HALTED at batch 2/8: product D
(K&D-through-pressure + in-PDE roughness) produced unphysical peaks on the constrained
design's tiny-eye storms -- batch 0 D max 5017 mph, batch 1 vec188 1162 mph. Products
A/B/C stayed <=370. Checkpoints all_b0/b1 preserved; static pipeline untouched.

### Essence of the instability (tests/auto/diagnose_dyn_instability.py)
- NOT fixed-vs-adaptive timestepping: the march ALREADY adapts dt to advective CFL
  (_dyn_step_batch: dt = cfl*dx_min/max_U, recomputed each step).
- Root cause: the dynamic grid is deliberately COARSE (rmin 0.5->4 km, Nphi 360->180,
  per the solver docstring, to relax CFL for ~40 km eyes). The constrained design's new
  ~3-8 km eyes (Rmax down to ~2 mi) are resolved by ~1 radial cell, so the near-singular
  Holland forcing (dpdr ~ r^(-B-1), B up to ~2) is under-resolved, and the CENTERED
  azimuthal advection rings at the grid scale; the nonlinear advection amplifies it.
- Empirics (vec64, CP896/Rmax4.4mi/B1.84): baseline isolated 319 mph (field 504 mph,
  already unphysical); cfl 0.5->0.1 -> field 219 mph but ~11x slower (3639s/storm --
  not viable at n=200). CFL is a lever but resolution is the real gap; both are coupled
  (explicit scheme: dx down forces dt down). => per-storm hand-tuning, which recurs.

### Decision: research-first, replace the marcher (user, 2026-07-20)
User: "we have been here (instability) before ... research on open source libs rather
than one-off tests and fixes." The durable cure for a conditionally-stable explicit
centered scheme that keeps breaking is a battle-tested FV/Godunov solver with TVD
limiting (monotone by construction) and/or IMEX time integration (unconditionally
stable for the stiff drag/diffusion). Candidates surveyed: PyClaw/Clawpack (FV wave-
propagation, lead), PETSc TS + SUNDIALS ARKODE (IMEX time integrators), Firedrake/
FEniCSx over PETSc (FEM/DG, heavier), Dedalus (spectral, rings on land-water z0 jumps).

### PyClaw side test -- PASSED (tests/auto/pyclaw_radial_dambreak.py)
clawpack 5.14.0 installed (macOS gfortran/SDK fix recorded in memory). 2D shallow-water
radial dam break = a sharp azimuthally-symmetric front on a coarse grid (our eyewall
analog). MC-limited FV (Roe + entropy fix): on 30x30/40x40/80x80 the depth stays
POSITIVE (h_min ~0.64), bounded (h_max ~1.23), NaN-free, mass conserved to 1e-12. The
UNLIMITED run (centered-like Lax-Wendroff -- what our solver's azimuthal advection is)
undershoots to h_min=0.40: the ringing signature. => FV+TVD removes our failure mode by
construction; PyClaw toolchain proven on Python 3.14.

### Next options (not yet done)
- Rotating shallow-water vortex with a tight core (add Coriolis + drag source) -- closer
  to the hurricane BL, to test PyClaw on our actual regime before committing to a port.
- Scope a PyClaw (or PETSc-TS/ARKODE IMEX) reimplementation of the slab BL momentum
  system; decide spatial discretization (FV structured vs FEM/DG) and time integrator.
- Static pipeline (peaks/metamodels/Shapley/hold-out) remains DONE + validated; dynamic
  products stay parked until the solver direction is chosen.

### PyClaw side test #2 -- rotating vortex + Coriolis + stiff drag -- PASSED
tests/auto/pyclaw_rotating_vortex.py. Adds what test #1 lacked: the SOURCE terms our BL
equations have (Coriolis + quadratic drag), via PyClaw operator splitting (step_source;
Coriolis = exact rotation, drag = semi-implicit u/(1+dt*r), source_split=2 Strang). 2D
shallow-water cyclonic vortex, 60x60 coarse grid, f at 25.86N.
- broad core (~8 cells): stable, KE->10% (drag spin-down), h>0, no NaN.
- TIGHT core (~1.6 cells, tighter than our failing regime): STABLE, bounded (vmax 10.8),
  h_min 981, no NaN, KE->11%. The tiny-eye regime that blows up the vendored marcher is
  handled cleanly.
- tight + 4x drag (stiff-source stress): stable, KE->2% -> semi-implicit drag is
  unconditionally stable; likely NO full IMEX needed, FV + split semi-implicit source
  suffices. Simplifies a port.
Both halves of the real problem now demonstrated stable in PyClaw: sharp-front advection
(test #1) + Coriolis/stiff-drag source (test #2), on the tight-core regime.
Caveats: shallow-water ANALOGS -- validate the method's STABILITY on our stress regime,
not a Powell physics port or field FIDELITY. Next: scope recasting the slab BL momentum
system (conservation form + source terms) into PyClaw.

---

## PyClaw port scoping -- the key decision (2026-07-20)

### The equations to solve (physics_terms_dyn, vendored solver)
Slab boundary-layer MOMENTUM on a storm-centered polar (r,phi) grid; NO height/
continuity equation (single fixed BL depth h_bl); pressure gradient dpdr(r,t) is
PRESCRIBED (Holland, time-scaled by the K&D schedule s(t)^2):
  u_t + [u u_r + (v/r)u_phi - v^2/r] = +f v - dpdr/rho + Kh*lap(u) - (Cd|U|/h_bl) u
  v_t + [u v_r + (v/r)v_phi + u v/r] =  -f u            + Kh*lap(v) - (Cd|U|/h_bl) v
Current discretization: radial grid STRETCHED (q^gamma, concentrates cells at the eye);
radial advection FIRST-ORDER UPWIND (ddr_upwind -- monotone, fine); AZIMUTHAL advection
CENTERED (ddphi_b -- NON-monotone). Adaptive-CFL explicit Euler in time.

### Root cause is localized
The blow-up is the centered AZIMUTHAL advection ringing on the under-resolved tiny-eye
front (radial is already upwind/monotone). The PyClaw side test's unlimited-vs-MC
contrast is exactly this operator: centered -> undershoot/ring; TVD-limited -> monotone.

### THE decision: how far to go
- **Option B (surgical, days):** replace the centered azimuthal advection in
  physics_terms_dyn with a MUSCL/minmod TVD-limited flux (optionally upgrade radial to
  MUSCL too). Keep the stretched polar grid, the source terms, diffusion, and all
  validated physics. Directly kills the root cause; the side tests already validated the
  mechanism (TVD limiting). Stays in torch, batched, fast. Keeps the bespoke solver.
- **Option A (full PyClaw port, weeks):** reformulate the BL momentum system in
  conservation form and solve it in Clawpack/PyClaw. Because the physics is a polar,
  eye-refined vortex, this means a MAPPED (curvilinear) grid + a CUSTOM Riemann solver
  for this non-standard system (no off-the-shelf SW/Euler solver fits -- there is no
  height equation; pressure is prescribed). Battle-tested framework and the cleanest
  long-term footing, but a real build: custom Riemann solver, mapped-grid metrics, inner
  (r=rmin) + outer sponge BCs, and re-validation. A uniform Cartesian PyClaw grid is NOT
  viable (loses the cheap eye refinement; a 2-mi eye over a 500 km domain needs AMR/
  ForestClaw, more infra again).

### Recommendation
Option B first: it targets the exact root cause, the side tests already prove TVD
limiting is the cure, it preserves the validated static/steady physics, and it unblocks
the dynamic n=200 run in days rather than weeks. Treat the full PyClaw/mapped-grid port
as the longer-term direction if the bespoke solver keeps causing pain -- the side tests
show PyClaw CAN carry our regime, so that option stays open, not lost. (Note: this is a
genuine principled fix -- TVD limiting is the textbook cure for the ringing -- not a
one-off CFL/grid tweak of the kind we agreed to stop doing.)

### DECISIONS (user, 2026-07-20)
- Solver: **Option B, surgical TVD fix**. Implemented: ddphi_muscl (MUSCL + minmod, TVD)
  replaces the centered ddphi_b in physics_terms_dyn's azimuthal advection ONLY. Radial
  stays first-order upwind; source terms, diffusion, stretched polar grid, and the STEADY
  solver (physics_terms -> static products, already validated) all UNCHANGED. Batch-safe
  (roll on last dim). tests/auto/verify_muscl_fix.py checks vec64/vec188.
- AAL lambda: **editable placeholder** in the viewer (single resettable landfall rate),
  wired during phase-2; nothing depends on the value except the AAL dollar display.

### MUSCL fix VERIFIED end-to-end (2026-07-20)
- Isolated (verify_muscl_fix.py): vec64 field 504->199 mph (was 5017 in batch),
  vec188 ->192. Physical, at the SAME coarse grid/cfl=0.5 -- the limiter achieves what
  cfl=0.1 did (11x slower) pre-fix.
- Batch context (3-storm timing test): product D peak 5017 -> **204.4 mph**. All marches
  physical (A/B/C/D: V0 192, 192.4, 209.5, 204.4). The blow-up is gone in the real
  pipeline, not just isolation.
- Perf note: minmod made branchless (0.5*(sign+sign)*min|.| -- MPS-native, no nested
  where) to avoid GPU stalls. MPS runs async so the internal timers under-report; the
  run completes (~13 min / 3 storms -> full 200 ~3-4 hr). Spin-up may run more iters
  (MUSCL kinks vs the 0.005 drift threshold); acceptable. Full 200-storm run LAUNCHED.
- Still open if perf matters later: smooth z0 land-water over 2-3 cells (ChatGPT rec) to
  aid spin-up convergence; not required for correctness.

---

## Phase 2 — VIEWER migration scope (constrained lumped design) — 2026-07-21

Surveyed web/viewer.js, web/analysis.js, web/index.html. Goal: make the interactive app
render the lumped constrained n=200 design (Shapley, single population, B, constrained
data) instead of the legacy 3-category/Sobol'/WSP world. Concrete change list:

### A. Data loading (viewer.js ~L935-985) — repoint to _constrained files
- L936 inputs.json -> inputs_constrained.json
- L975 powell.json -> powell_constrained.json ; L943 powell_kd.json -> _constrained_kd
- L945 powell_field.json -> powell_constrained_field.json
- L958 metamodels.json -> metamodels_constrained.json
- dynamic loop (~L979): powell_dyn* -> powell_dyn_constrained*
- L962 dyn_frames.json -> dyn_frames_constrained.json (ONLY if a --frames-all run is done;
  the current run is PEAKS-ONLY, so no frames yet)
- L947 powell_ua.json: no constrained UA generated -> the EPR/UA panel degrades or is hidden
- DECISION: Holland/Willoughby (L980-982) were NOT solved on the constrained design (Powell
  only). Either restrict the model selector to Powell, or run Holland/Willoughby too.

### B. Remove/repurpose the category selector
- index.html:73-74 <select id="category"> (Cat 1/3/5). Lumped design has no categories.
  Remove it; the vector slider (1..200) is the only storm selector.
- Every `"cat" + document.getElementById("category").value` site becomes the single
  group "all": viewer.js L359,393; analysis.js L729,747,892,1083,1149,1199,1336,1628.
- analysis.js:15 SA_CATS = ["cat1","cat3","cat5"] -> ["all"]; per-cat loops L208,261.

### C. Sensitivity panel: Sobol' -> Shapley (analysis.js)
- Tab set L642 "SRC | Sobol'" -> "SRC | Shapley" (saMethod 'sobol' -> 'shapley').
- drawSobol()/sobolBlock() (L659,661) -> drawShapley()/shapleyBlock() reading the
  "shapley"/"shapley_kd" blocks in metamodels_constrained.json (mm.responses[r].all.shapley).
  Shapley is a single share per input (sums to 1) + SE -> simpler stacked/plain bar than
  Sobol's S1+interaction split. Keep Sobol' block available as a labeled reference if wanted.
- Update banner/notes L664,734 ("assumes independent inputs" -> "Shapley, valid under the
  design's CP-RMW/B-RMW dependence").

### D. Per-category visualizations that no longer apply (analysis.js)
- L541 SVG "x = category {1,3,5}" line chart -> single-population bar (or drop).
- L1336-1497 EP/severity "per category" over 100 vectors -> single population of 200.
- L1270 rates {1:0.20,3:0.05,5:0.01} -> single lambda.

### E. AAL -> single lambda placeholder (viewer.js)
- computePointAAL L217: AAL(x) = Sum_c lambda_c*mean_i[...] -> lambda * mean over the 200.
- finState.rates {1,3,5} -> single editable lambda (placeholder default, resettable in UI).
- Display L834-835 "λ 0.20/0.05/0.01 per yr" -> single "λ per yr".

### Scope DECISIONS to confirm before building
1. Model selector: Powell-only, or also run Holland/Willoughby on the constrained design?
2. Animation frames: generate (a --frames-all dynamic run, ~hours) or ship static-map only?
3. AAL lambda placeholder default value (single annual landfall rate).
4. Category selector: remove entirely (recommended) vs keep a vestigial intensity filter.
5. UA/EPR panel: hide (no constrained UA) or generate powell_ua_constrained?

### Effort
Moderate, well-scoped: ~2 focused files, ~20 coupling sites each, + browser testing.
Not blocking: docs/FormS6.tex (separate deliverable). Blocking-ish: the product-D spike
decision (D-vs-A scan) and whether frames are in scope.

### DECISION (user, 2026-07-21): new primary + old as comparison (option 3)
- INTERACTIVE tool = constrained/Shapley design (the chosen final method). Viewer
  migration proceeds as scoped above.
- KEEP rectangular/Sobol' as a COMPARISON, not live dual-mode: a side-by-side figure
  (Sobol' Rmax-dominant vs Shapley CP-dominant; the ~9% corner-loss agreement) in the
  docs, and optionally a static reference panel in the app. Reinforces defensibility
  ("we did both and they agree") and answers the rectangular-vs-irregular debate.
- DO NOT build full dual-mode (3-cat/WSP/Sobol AND lumped/B/Shapley live toggle) --
  structural mismatch too costly for weak-correlation results that are close.
- Old data files (powell.json, metamodels.json w/ Sobol', 3-cat inputs) are RETAINED
  (not deleted) so the comparison figure/panel can be regenerated.

---

## Phase 2 IN PROGRESS — viewer migration (2026-07-21)

User decisions: (1) run Holland+Willoughby on the constrained design too, (2) all frames,
(3) lambda placeholder editable (set 0.35/yr), (4) generate UA. Goal: FULL parity, new design.

### DONE (both JS parse clean — node --check)
- Filenames normalized: static constrained now `{base}_constrained.json` (was
  powell_constrained_kd -> powell_kd_constrained etc.), matching the dynamic naming.
- viewer.js: DF() design-suffix helper; ALL design-dependent fetches -> _constrained
  (inputs, powell{,_kd,_field}, powell_ua, powell_dyn*, metamodels, dyn_frames, holland/
  willoughby); shared files (grid/roughness/exposure/vulnerability) untouched.
- viewer.js: GROUP="all" (single population) in currentSelection + computePointAAL;
  recB() B-adapter (rec.B, fallback quantileToB(rec.WSP)); AAL -> single lambda;
  removed "category" from wireControls listeners.
- analysis.js: finState.rates{1,3,5} -> finState.lambda=0.35; SA_CATS=[GROUP];
  mechanical `"cat"+category.value` -> GROUP done.

### REMAINING (viewer not yet functional)
- analysis.js STRUCTURAL: per-category visualizations still reference cats -- the
  category line chart (L580-581,623 data["cat"+c]), "Cat X" display (L729,892,1150,1199,
  1340), per-cat loss series (L1385-86), category-change listener (L1632). Rework to
  single population. AND Sobol' -> Shapley panel (drawSobol->drawShapley reading the
  shapley/shapley_kd block; tab label; banner).
- index.html: remove category <select>; vector slider max 100 -> 200; WSP section/column
  labels -> B.
- DATA-GEN runs: Holland/Willoughby (precompute_live.py migrated + run, needs the migrated
  viewer serving); UA (windfield_ua.py migrated -> powell_ua_constrained); frames
  (--frames-all --constrained, LONG ~2hr, -> Colab/A100).
- Browser testing (needs server + the above data).

### State: structural direction set + data layer done; ~half the analysis.js rework, all
### of index.html, and the 3 data-gen runs + testing remain. Old design untouched (files
### retained for the comparison figure).

---

## ============ PHASE 2 RESTART GUIDE (2026-07-21 evening) ============

Full-parity viewer migration to the constrained lumped n=200 design. Session paused
mid-migration. Both web/viewer.js and web/analysis.js PARSE CLEAN (node --check), but the
viewer is NOT yet runnable -- a few analysis.js sites still read the removed category
selector (will throw null.value) and 3 data-gen products don't exist yet. Old design +
files fully retained (untouched) for the comparison figure.

### DONE (this session)
- All constrained data generated + physically corrected: static powell + metamodels
  (Shapley) + holdout (R2>=0.998) + dynamic A/B/C/D (roughness spike fixed via C<=A,D<=B,
  baked into assemble()). Files normalized to {base}_constrained.json in outputs/web/.
  A100 dynamic checkpoints saved: outputs/dynamic/a100_checkpoints/ (all 200).
- viewer.js: DF() suffix helper -> all design files _constrained; GROUP="all" single
  population; recB() B-adapter; AAL -> single finState.lambda; category listener removed.
- analysis.js: SA_VARS WSP->B; VAR_COLORS/UA_KEY WSP->B; SA_CATS=[GROUP];
  finState.lambda=0.35 (editable placeholder); drawChart reworked to single-population
  BAR chart; drawSobol -> drawShapley (reads shapley/shapley_kd, sums to 1, given-data kNN
  note) + shapleyBlock() added; methodTabs "Sobol'"->"Shapley"; saMethod dispatch updated;
  profiler cache key + one quantileToB(rec.WSP) -> recB(rec).
- index.html: category <select> removed; vector slider max 100->200; B section header
  updated (B is a design input now).

### REMAINING CODE (analysis.js) -- MUST FIX before the viewer runs
1. `document.getElementById("category").value` still read at ~L861, L1129, L1178, L1319
   (var catN / selCat) -- the element is GONE, so these throw null.value. These are the
   EP/severity/financial + GPR-note panels. Replace the "Cat X" display with a single-
   population descriptor (no category), and drop catN.
2. Per-category loss loops at ~L1364-65: `pointLossSeries(model,"cat"+c,...)` /
   `tlcSeries(model,"cat"+c,...)` iterate cats [1,3,5] -> rework to the single GROUP
   population (one severity/EP curve over 200 storms, not 3 category curves).
3. Category change listener at ~L1611: `getElementById("category").addEventListener` ->
   remove (element gone).
4. CSV export ~L506-516: `const cols=[...,"WSP",...]` and the row builder use WSP ->
   change to B (rec.B). Check faithfulEPR() and any other data["cat"+c] / rec.WSP.
5. Verify: grep -nE 'getElementById\("category"\)|"cat" \+|rec\.WSP|"WSP"' web/analysis.js
   should return NOTHING when done.

### REMAINING DATA-GEN RUNS
A. UA -> powell_ua_constrained.json: migrate pipeline/windfield_ua.py (reads FormS6Input
   .xlsx + cats; change to inputs_constrained.json + GROUP "all" + B not WSP; output key
   "all"; UA_KEY expects lower-case b). Fast solve. Enables the EPR panel.
B. Holland/Willoughby -> holland_constrained.json, willoughby_constrained.json (+_kd):
   migrate pipeline/precompute_live.py (MODELS ok; CATS=(cat1,cat3,cat5)->("all",);
   output names -> _constrained; it DRIVES the migrated viewer via Selenium, so do this
   AFTER the code fixes + with the server running). Needs the viewer computeLiveWind to
   use rec.B (recB) -- already handled in viewer.js.
C. Frames -> dyn_frames_constrained.json + frames dir: run
   `windfield_dynamic_batch.py --constrained --frames-all` (LONG ~2hr; --frames-all reruns
   B/C marches). Best on Colab/A100 (reuse outputs/dynamic_a100_colab.ipynb; the code
   already writes dyn_frames_constrained + FRAMES_DIR _constrained). NON-blocking for the
   rest -- the static map + 4 land products work without frames (animation just disabled).

### SA note: Holland/Willoughby use LIVE SRC in the viewer (fitRSM); the precomputed
### Shapley/Sobol is Powell-only (shapleyBlock returns null otherwise -> "select Powell"
### note), same as the old design. So NO SA extension needed for Holland/Willoughby.

### HOW TO TEST (after code fixes + at least UA; Holland/Willoughby/frames optional)
- Start server (check for ./start or serve.ps1/serve script), open web/index.html.
- Expect: vector slider 1..200, no category selector, Shapley tab in Sensitivity,
  single-population SRC/EPR bars, AAL with one editable lambda. Powell + roughness shows
  Shapley (CP dominant ~0.55-0.66); Holland/Willoughby show live SRC.
- Debug per CLAUDE.md: open in browser, screencapture the window, read the screenshot.

### Decisions already locked: Powell+Holland+Willoughby all on constrained; all frames;
### lambda=0.35 editable; generate UA. Keep-old-as-comparison (option 3), NOT dual-mode.

---

## Phase 2 RESUMED — code fixes DONE, viewer runs on Powell (2026-07-22)

Picked up the paused migration. The remaining analysis.js/viewer.js code fixes are
complete and the migrated viewer now RUNS (Powell) with no unexpected console errors.

### Code fixes (all DONE, both files node --check clean)
- analysis.js: removed all 6 leftover `getElementById("category")` reads (buildProfilerDOM
  head -> "n=200"; drawCDF/drawCompare notes drop "Cat X"; drawFinancial reworked).
- analysis.js FINANCIAL rework (was the structural blocker): `finState.rates{1,3,5}` ->
  single `finState.lambda`; controls show one "Landfall rate /yr" input (data-lambda);
  severity is ONE 200-storm population (no per-cat loop); conditional uses `sev` directly;
  annualized OEP uses λ·P(L>x) with per-storm weight λ/N, AAL=λ·mean(sev); wireFinControls
  data-rate -> data-lambda; category-change listener removed.
- viewer.js: VEC_FIELDS WSP+Quantile -> B (rec.B, the constrained input); right-click CSV
  cols WSP -> B; hover popup "WSP … (B=…)" -> "B <recB>".
- **GROUP load-order bug FIXED**: analysis.js (loads before viewer.js) referenced GROUP at
  top level (`SA_CATS=[GROUP]`) but the `const GROUP="all"` lived in viewer.js -> fatal
  top-level ReferenceError aborted ALL of analysis.js (Shapley tab + panels missing). Moved
  the `const GROUP="all"` declaration to analysis.js; viewer.js now just references it.
- index.html already migrated (no category select; vector max 200; no WSP) — verified.

### Verified (tests/auto/test_constrained_migration.py — headless, PASSED)
No category selector; vector slider 1..200; Powell field renders; Shapley tab present in
Sensitivity; single-lambda financial panel renders; no console errors EXCEPT the expected
404s for the not-yet-generated files (powell_ua/holland/willoughby/dyn_frames — try/catch'd).

### Data-gen: Holland/Willoughby — DONE + VERIFIED (2026-07-22)
- `pipeline/precompute_live.py` migrated with a `--constrained` flag: CATS=("all",), B via
  the viewer's `recB(rec)` (rec.B), outputs `{model}{_kd}_constrained.json`. ANALYTIC
  models — ran locally in-browser via Selenium in ~3 min total; **no Colab/A100 needed**
  (Colab was only for the dynamic Powell PDE frames).
- Generated: holland_constrained.json, holland_kd_constrained.json, willoughby_constrained
  .json, willoughby_kd_constrained.json (each ~1 MB, 200 vectors). Marine peak 188.7 mph
  (both — set by the single most-intense storm; gradient-balance profiles converge at the
  eyewall for the same CP/Rmax/B).
- VERIFIED rendering in the migrated viewer (tests/auto/test_constrained_models.py, PASSED):
  both switch in via the model selector, load state.holland/willoughby(+Kd), computeWindFor
  (model,"all",0) returns a real 840-length field, land-mean ~55 mph / peak ~151 mph for
  vector 0, no unexpected console errors.

### Peak-wind sanity across constrained products (2026-07-22)
analytic 189 < Powell static 212 < Powell DYNAMIC 253 (marine) / 275 (+K&D). The ordering
is expected physics: the dynamic slab PDE resolves supergradient BL overshoot + transient
march accumulation the gradient-balance analytic models can't. The dynamic +K&D > marine
gap is the KNOWN "dynamic-march-vs-frozen-marine" effect (A frozen, B/C/D dynamic marches),
already documented + deliberately left in the dynamic-solver-instability note — NOT a decay
bug. The 250-275 mph tail magnitudes are the documented under-resolved-tight-eyewall
limitation (constrained design samples deep-CP/tiny-eye storms heavily). No action needed;
the C<=A/D<=B invariant already corrects the roughness spike.

### UA / EPR — RESOLVED via Option 2 (user, 2026-07-22): EPR = SRC² by design
The constrained lumped n=200 design has NO one-at-a-time (OAT) worksheets — it's a single
dependence-sampled (CP-RMW / B-RMW) population — so the ROA's faithful variance-share EPR
(Var(Y | only X_i varies) / Var(Y_SA), 1800 OAT solves) isn't defined. Chose **Option 2**:
skip faithful EPR; the EPR panel uses its built-in SRC² fallback, and **Shapley** is the
dependence-aware SA method. Rationale: OAT EPR would re-introduce the independence
assumption the constrained/Shapley design exists to avoid. Implemented:
- viewer.js: gate the powell_ua fetch on `DESIGN_SUFFIX === ""` (legacy only); constrained
  sets `state.powellUa = null` -> no spurious 404 on load.
- analysis.js: EPR footnote now explains "no OAT sheets, faithful EPR not defined, see the
  Shapley tab" instead of a bare "≈ SRC²". faithfulEPR() stays migrated (B/"all") for the
  legacy design.
- Verified: test_constrained_migration.py asserts the EPR panel shows the SRC² note;
  console now has ONLY the dyn_frames 404 (powell_ua 404 gone).

### Animation frames — IN PROGRESS on A100/Colab (2026-07-22)
- Colab notebook `outputs/dynamic_frames_a100_colab.ipynb` (Drive-backed, resumable):
  runs `windfield_dynamic_batch.py --constrained --frames-all --select all --batch-size 25`.
  Reuses the peaks run's Drive dir `mm_dynamic` (products + checkpoints already there); no
  bundle re-upload. Cell 5 zips dyn_frames_constrained/ + manifest to Drive.
- `colab_bundle.zip` rebuilt to include the 4 powell_dyn*_constrained.json products (frames
  are clamped to these via stored_peaks; fallback for a fresh Drive dir).
- **Google Drive MCP** connected: I can see the user's Drive (list/search/download) to
  monitor progress + pull results; the user runs Colab. Frames land at
  Drive `.../mm_dynamic/outputs/web/dyn_frames_constrained/` as `all_v{N}_{A|B|C|D}.bin`.
- **Batch 0 VALIDATED (2026-07-22):** 25 storms x 4 products = 100 .bin on Drive, each
  exactly 61320 bytes (840 vertices x 73 frames), stored_peaks clamp OK, no crash. ~175
  storms remain. Retrieve zip -> outputs/web/dyn_frames_constrained/, regen manifest locally.

### Viewer fixes needed for constrained frames + live models (2026-07-22)
- **anim.js**: frame fetch path was hardcoded `dyn_frames/` -> now `dyn_frames${DESIGN_SUFFIX}/`
  so the animation finds the constrained frames. Only `powelldyn` uses precomputed frames;
  Holland/Willoughby animate LIVE (fieldFnFor), Powell-steady samples powell_field.json.
- **anim.js + popup.js**: leftover `quantileToB(rec.WSP)` -> `recB(rec)` (constrained records
  have no WSP; this drove live Holland/Willoughby animation B and the left-click popup field).
  My earlier WSP grep only covered viewer.js/analysis.js — anim.js/popup.js were missed.
- windfield_dynamic_batch.py: manifest `file` field now uses basename(FRAMES_DIR) (cosmetic;
  anim.js builds the path itself). All JS node --check clean.

### Comprehensive doc (docs/FormS6.tex) — constrained-primary reframe IN PROGRESS (2026-07-22)
Framing decision (user): constrained/Shapley primary, Sobol'/3-category kept as the
independent-inputs baseline + cross-check ("we did both, they agree on CP"). User likes
the irregular/Shapley vs regular/Sobol' cross-verification framing. DONE this pass
(compiles clean, 47pp, no undefined refs):
- Goal + inputs: constrained lumped n=200 primary; B a DIRECT input (not WSP quantile);
  HURDAT2 CP-RMW (r=0.43) + B-RMW (Powell Eq.7) dependence; legacy retained as baseline.
- SA section: SRC over 200 storms; EPR=SRC² (Option 2, faithful EPR undefined w/o OAT);
  headline = CP dominates once lumped (Shapley 0.555 wind / 0.572 loss), Rmax second.
- Sobol' section retitled "Shapley effects and the Sobol' cross-check": Shapley primary
  (given-data kNN, Broto/Bachoc/Depecker; k=40); new comparison TABLE (Shapley vs Sobol'
  S1/ST on constrained — both CP-dominant, B's larger Sobol' share = the correlation
  Shapley corrects); constrained additivity (sum S1=0.985, top pairs Rmax×B 0.020,
  CP×Rmax 0.014); legacy Cat5 WSP table relabeled as baseline illustration.
- Dynamic-worth-cost: framed as legacy 3-category solver-comparison (design-immaterial).
- Interface: app UI corrected — no category selector, 200-vector slider, B direct,
  9-col CSV (…,B,…), activity diagram loop 200, grid-sensitivity generic.
- Metamodel + JMP verification: framed as legacy-baseline illustrations of the machinery.
- Bib: added Owen 2014, Song/Nelson/Staum 2016, Broto/Bachoc/Depecker 2020.

FIGURES — regenerated (2026-07-22, capture_figures.py vs migrated viewer on :8012):
- analysis_src, analysis_epr, analysis_tlc_cdf, grid_sensitivity re-captured for the
  constrained single population; NEW analysis_shapley (Shapley tab). capture_figures.py
  entries de-categorized (removed the now-gone `category` control) + added analysis_shapley.
- KEPT legacy (labeled baseline): analysis_sobol, analysis_profiler, analysis_matrix,
  analysis_compare, and the powell_cat5_* windfield figures (illustrative example storms).
- **Config decision: SA headline uses the SHIPPED DEFAULT (roughness + K&D decay)**, matching
  the figure + the legacy Sobol table, NOT the roughness-only "marine" numbers (defensibility:
  report what ships). tab:shapley + prose + additivity now K&D: CP 0.45 (wind Shapley), Rmax
  0.29, VT 0.117 (decay/dwell), sum S1 0.956, top pairs CP×Rmax 0.025, Rmax×B 0.020. Methods
  agree on leaders (CP, Rmax); diverge on VT (decay) + B (B~Rmax) — reported honestly, not as
  clean agreement. Loss headline CP ≈0.51. Doc compiles 48pp, fig:shapley resolves.

REMAINING on the doc:
- Detailed legacy tables in metamodel/profiler/matrix kept with baseline framing (acceptable;
  full recompute optional). Holdout + directional-roughness + time-step-conv per-category
  numbers left as legacy-baseline analyses (valid; re-run not feasible now).

### "In a Nutshell" condensed doc — DONE (2026-07-22)
- New docs/FormS6-Nutshell.tex (+ .pdf): 6-page condensed technical summary across all 5
  disciplines, same preamble/figures as the long doc. Reuses powell_cat5_contour +
  analysis_shapley. Constrained-primary: CP-dominant headline; Shapley(irregular) ↔
  Sobol'(regular) cross-check framing; abstract + 5-line essentials.
- Added inline citations throughout + a 21-entry references section (verbatim from the
  comprehensive doc's bibliography for consistency): fchlpm, iman, holland, willoughby,
  powell, vickery, smithvogl, kaplan, nlcd, esdu, aersurface, hazus, vickery2006,
  araoir2024, francom, sobol, saltelli2010, jansen1999, owen2014, song2016, broto2020.
  Compiles clean, no undefined cites, 8pp.

### Unified one-run dynamic pipeline — `--full` (2026-07-22)
The peaks run and the frames run repeat the SAME B/C/D marches — the peak is just the
running max over the frames. Added a `--full` mode to windfield_dynamic_batch.py so ONE
march writes BOTH the peak checkpoints AND all four products' frames (no second run):
- New global FULL + `--full` arg; WANT_FRAMES/prods/base include FULL (all-4 frame buffers).
- FULL does NOT return early -> writes the peak checkpoint; applies C<=A/D<=B to the batch
  peaks, then clamps frames to those just-computed peaks (write_frames gained peak_rows=;
  frames-only/-all still clamp via stored_peaks committed products). assemble() re-applies
  the invariant (idempotent). Resume is checkpoint-based (checkpoint written AFTER frames,
  so checkpoint-exists => frames done). py_compile OK; --full in --help.
- Unified notebook `outputs/dynamic_full_a100_colab.ipynb` runs
  `--constrained --full --select all --batch-size 25`; Cell 5 assembles products + rewrites
  manifest + zips products+frames+manifest -> dynamic_full_constrained.zip. **Use this for
  any NEW design — one notebook, one run.** (The current 2-run split stands because peaks
  were already banked before frames were scoped this session.)

### Frames COMPLETE + integrated + committed (2026-07-22)
- All 8 batches finished (200/200 storms). Retrieved dynamic_frames_constrained.zip from the
  user's Downloads (macOS TCC blocked ~/Downloads; user moved it into the project), unzipped
  800 .bin (200 x A/B/C/D, each 61320 B) + manifest into outputs/web/dyn_frames_constrained/.
  Regenerated the manifest locally with the fixed script (file field -> dyn_frames_constrained/).
- VERIFIED animation plays: headless test switched to Powell (dynamic), fetched a frame via the
  design-suffixed path (61320 B), animPrecompute() built 73 frames, mid-passage ~112 mph, zero
  console errors. The anim.js DESIGN_SUFFIX fix validated on real data.

### COMMITTED + PUSHED (2026-07-22): 86b0063 on origin/sobol-indices
Curated deliverable (858 files, 6527 insertions): viewer JS + index.html, pipeline (grid/
dynamic/dynamic_batch --full/precompute_live --constrained/fit_metamodels/vendor), all
constrained data JSONs, 800 frames + manifest, docs (FormS6 reframe + figures + Nutshell),
3 Colab notebooks + bundle, holdout + design workbook, pubs/, projectplan. EXCLUDED per user:
tests/, chris/, outputs/dynamic/ checkpoints, Windows helper files. Frames committed (deployed
animation needs them). Pushed 10 commits (this + 9 prior-session) to GitHub.

### Server note: dev server left running on :8012 (tests/auto/server.pid). ./stop to halt.
### New tests this session: tests/auto/test_constrained_migration.py (viewer smoke) +
### tests/auto/test_constrained_models.py (Holland/Willoughby render). Both PASS.
