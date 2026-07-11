# Vendored upstream components

These are the two pieces the pipeline depends on that were previously imported
from paths outside this repository (`~/code/catmodel/...`). They are vendored here
so the repo is self-contained: a fresh clone can rebuild every windfield and the
vulnerability curve without any files from the author's machine.

- `hurricane_pde_marine.py` — the Powell slab boundary-layer PDE solver
  (`pde_steady_marine`, `pde_dynamic_setup_batch`, `pde_dynamic_spinup_batch`,
  `pde_dynamic_march_batch`, `bilinear_polar*`, `device_select`). Used by
  `windfield_grid.py`, `windfield_dynamic.py`, `windfield_dynamic_batch.py`.
- `wind_vulnerability_tool_v5.html` — the HAZUS/ARA wind-vulnerability tool driven
  headlessly to produce the Mean-Damage-Ratio curve. Used by
  `build_vulnerability.py`.
