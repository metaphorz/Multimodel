#!/usr/bin/env python3
"""Build per-grid-vertex exposure from the FL DOR tax roll (replacement cost).

Motivation
----------
Loss is MDR(peak wind) x exposure, where MDR is a *structural* damage ratio from the
masonry vulnerability curve. The other two exposure models carry LAND value:

  * Uniform -- $100k/land vertex, a prescribed ROA p.186 constant (not an estimate).
  * Census  -- ACS B25082 asks what "this house AND LOT" would sell for. Land cannot be
    separated out of it.

Wind damages the structure, not the land, so multiplying a structural MDR by house+lot
market value inflates loss -- worst on the expensive coast, where the windfield is also
strongest. The error correlates with the hazard.

Why replacement cost, not "just value minus land"
-------------------------------------------------
The FL DOR roll records land separately (s.193.114 F.S.: JV = LND_VAL + building +
SPEC_FEAT_), so building value LOOKS like a simple subtraction. It is not: the county
appraisers fold a condominium's land into the unit's just value and report LND_VAL = 0
for 99.9% of condo parcels (and 85.8% of co-ops). In this domain that is 37% of all
residential parcels and $249B of just value, concentrated in exactly the coastal
high-rises where the wind is strongest. Subtracting LND_VAL there removes nothing.

Instead we use the basis a catastrophe model actually wants -- insured value, i.e.
REPLACEMENT COST of the structure:

    exposure(cell) = rate(cell) x SUM(TOT_LVG_AR) over residential parcels in the cell

where rate ($/sqft of living area) is calibrated from the clean subset in which the
appraiser DOES separate land -- single-family parcels (DOR_UC 001):

    rate = median( (JV - LND_VAL - SPEC_FEAT_) / TOT_LVG_AR )   over DOR_UC 001

Land is absent by construction, condos and houses are treated on one basis, and the
$/sqft rate is derived from the roll itself rather than assumed.

The rate is a SINGLE domain-wide constant, deliberately. Calibrating it per cell or per
county would import the appraisers' land-allocation policy, which varies wildly and is
not construction cost: Broward and Miami-Dade single-family homes have the same market
value per square foot (median $276 vs $286/sqft), the same median size (1857 vs 1839
sqft) and similar vintage, yet Broward allocates 9.1% of just value to land where
Miami-Dade allocates 59.1%. A per-county rate would therefore be $247 vs $109/sqft --
a 2.3x cliff along the county line, which runs straight through this grid at ~25.96N.

Because %TLC = SUM(MDR x exposure) / SUM(exposure), a constant rate cancels exactly:
%TLC, SRC and EPR are invariant to its absolute level. The rate sets the dollar totals
only; the exposure *shape* is residential floor area per cell.

Geometry
--------
Each land vertex owns the 3-mile cell centred on it. The grid steps exactly 3 mi in ew
and ns, so the cells TILE the domain: every parcel is assigned to exactly ONE cell by
its centroid. No double-counting of boundary parcels, and no areal smearing of value
onto water/Everglades the way tract-density apportionment does.

Inputs : outputs/web/grid.json, data/cadastral/ (see pipeline/fetch_cadastral.sh)
Output : outputs/web/exposure_tax.json

Run:  venv/bin/python pipeline/build_exposure_tax.py
"""
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pyogrio
from pyproj import Transformer

ROOT = Path(__file__).resolve().parents[1]
GRID = ROOT / "outputs" / "web" / "grid.json"
OUT = ROOT / "outputs" / "web" / "exposure_tax.json"
CAD = Path(os.environ.get("CADASTRAL_DIR", ROOT / "data" / "cadastral"))

# DOR use codes 001-008 = residential. 000 (vacant residential) has no structure and is
# excluded. Matches the masonry residential vulnerability curve and the Census model's
# owner-occupied basis.
RESIDENTIAL_UC = ("001", "002", "003", "004", "005", "006", "007", "008")
SINGLE_FAMILY = "001"          # the subset where LND_VAL is meaningfully populated
COLUMNS = ["CO_NO", "DOR_UC", "JV", "LND_VAL", "SPEC_FEAT_", "TOT_LVG_AR"]
CHUNK = 250_000
MARGIN_DEG = 0.05              # bbox pad so edge half-cells are fully covered


def find_source():
    for pat in ("*.gdb", "*.shp"):
        hits = sorted(CAD.rglob(pat))
        if hits:
            return hits[0]
    raise SystemExit(f"No .gdb/.shp under {CAD} — run pipeline/fetch_cadastral.sh first")


def cell_edges(centers):
    """Bin edges midway between adjacent vertex coords, extended half a step at each end.
    Digitizing a centroid against these gives its nearest-vertex cell (a true partition)."""
    c = np.asarray(centers, dtype=float)
    mid = (c[:-1] + c[1:]) / 2.0
    return np.concatenate(([c[0] - (c[1] - c[0]) / 2.0], mid, [c[-1] + (c[-1] - c[-2]) / 2.0]))


def main():
    grid = json.loads(GRID.read_text())
    pts = grid["points"]
    n = grid["n_points"]

    lon_by_ew, lat_by_ns = {}, {}
    for p in pts:
        lon_by_ew.setdefault(p["ew"], []).append(p["lon"])
        lat_by_ns.setdefault(p["ns"], []).append(p["lat"])
    ew_vals, ns_vals = sorted(lon_by_ew), sorted(lat_by_ns)
    lons = np.array([np.mean(lon_by_ew[e]) for e in ew_vals])
    lats = np.array([np.mean(lat_by_ns[s]) for s in ns_vals])
    order = np.argsort(lons)
    lons_asc, ew_asc = lons[order], np.array(ew_vals)[order]
    lon_edges, lat_edges = cell_edges(lons_asc), cell_edges(lats)
    ns_arr = np.array(ns_vals)

    idx_of = {(p["ew"], p["ns"]): i for i, p in enumerate(pts)}
    is_land = np.array([p["land"] for p in pts])

    src = find_source()
    info = pyogrio.read_info(src)
    crs = info["crs"]
    print(f"source : {src}")
    print(f"layer  : {info['features']:,} features · crs={crs}")

    fwd = Transformer.from_crs(4326, crs, always_xy=True)
    back = Transformer.from_crs(crs, 4326, always_xy=True)
    corners = [fwd.transform(x, y)
               for x in (lons.min() - MARGIN_DEG, lons.max() + MARGIN_DEG)
               for y in (lats.min() - MARGIN_DEG, lats.max() + MARGIN_DEG)]
    xs, ys = zip(*corners)
    bbox = (min(xs), min(ys), max(xs), max(ys))

    where = "DOR_UC IN (%s)" % ",".join(f"'{c}'" for c in RESIDENTIAL_UC)
    print(f"filter : {where}\n         bbox={tuple(round(b) for b in bbox)}")

    frames, skip, oob = [], 0, 0
    while True:
        gdf = pyogrio.read_dataframe(src, columns=COLUMNS, bbox=bbox, where=where,
                                     skip_features=skip, max_features=CHUNK)
        if len(gdf) == 0:
            break
        cen = gdf.geometry.centroid
        lon, lat = (np.asarray(v) for v in back.transform(cen.x.values, cen.y.values))

        i = np.digitize(lon, lon_edges) - 1
        j = np.digitize(lat, lat_edges) - 1
        ok = (i >= 0) & (i < lons_asc.size) & (j >= 0) & (j < lats.size)
        oob += int((~ok).sum())

        gi = np.full(len(gdf), -1, dtype=np.int32)
        ew_hit, ns_hit = ew_asc[i[ok]], ns_arr[j[ok]]
        gi[ok] = [idx_of.get((int(a), int(b)), -1) for a, b in zip(ew_hit, ns_hit)]

        df = pd.DataFrame({
            "gi": gi,
            "co": gdf["CO_NO"].to_numpy(float),
            "uc": gdf["DOR_UC"].to_numpy(str),
            "jv": np.nan_to_num(gdf["JV"].to_numpy(float)),
            "lnd": np.nan_to_num(gdf["LND_VAL"].to_numpy(float)),
            "spec": np.nan_to_num(gdf["SPEC_FEAT_"].to_numpy(float)),
            "lvg": np.nan_to_num(gdf["TOT_LVG_AR"].to_numpy(float)),
        })
        frames.append(df[df.gi >= 0])
        skip += CHUNK
        print(f"  read {skip if len(gdf)==CHUNK else skip-CHUNK+len(gdf):,}", flush=True)

    df = pd.concat(frames, ignore_index=True)
    on_water = int((~is_land[df.gi.to_numpy()]).sum())
    df = df[is_land[df.gi.to_numpy()]]
    print(f"\nparcels: {len(df):,} on land · {on_water:,} on water vertices · {oob:,} outside grid")

    # ---- calibrate one replacement-cost rate ($/sqft) on single-family parcels ----
    sf = df[(df.uc == SINGLE_FAMILY) & (df.lvg > 0)].copy()
    sf["struct"] = np.clip(sf.jv - sf.lnd - sf.spec, 0, None)
    sf = sf[sf.struct > 0]
    sf["rate"] = sf.struct / sf.lvg
    rate = float(sf["rate"].median())

    print(f"rate   : ${rate:.0f}/sqft (median over {len(sf):,} single-family parcels)")
    print("         per-county rates REJECTED — they encode land-allocation policy, "
          "not cost:")
    diag = sf.groupby("co").agg(n=("rate", "size"), rate=("rate", "median"),
                                land=("lnd", "sum"), jv=("jv", "sum"))
    for c, r in diag.iterrows():
        share = r.land / r.jv * 100 if r.jv else 0.0
        print(f"           county {int(c):>2}: ${r.rate:6.0f}/sqft  land {share:4.1f}% of JV  "
              f"({int(r.n):,} SF parcels)")

    # ---- exposure = rate x total living area, per cell ----
    agg = df.groupby("gi").agg(lvg=("lvg", "sum"), jv=("jv", "sum"),
                               lnd=("lnd", "sum"), n=("jv", "size"))
    values = np.zeros(n)
    jv_tot = np.zeros(n)
    counts = np.zeros(n, dtype=int)
    lvg_tot = np.zeros(n)
    for gi, r in agg.iterrows():
        values[gi] = rate * r.lvg
        jv_tot[gi] = r.jv
        counts[gi] = int(r.n)
        lvg_tot[gi] = r.lvg

    covered = int((values > 0).sum())
    total = float(values.sum())

    OUT.write_text(json.dumps({
        "values": [round(v, 2) for v in values.tolist()],
        "total": round(total, 2),
        "n_land": grid["n_land"],
        "n_covered": covered,
        "n_parcels": int(counts.sum()),
        "jv_total": round(float(jv_tot.sum()), 2),
        "living_area_sqft": round(float(lvg_tot.sum()), 0),
        "rate_psf": round(rate, 2),
        "meta": {
            "source": "FL Dept. of Revenue 2025 cadastral (NAL roll joined to county "
                      "property-appraiser parcel polygons)",
            "basis": "replacement cost = rate($/sqft) x TOT_LVG_AR, summed over "
                     "residential parcels whose centroid falls in the vertex's 3-mi cell",
            "rate": f"${rate:.2f}/sqft — single domain-wide median of "
                    f"(JV - LND_VAL - SPEC_FEAT_)/TOT_LVG_AR over DOR_UC "
                    f"{SINGLE_FAMILY} (single family), {len(sf):,} parcels. Per-cell and "
                    f"per-county rates were rejected: they encode appraiser land-allocation "
                    f"policy (Broward 9.1% of JV to land vs Miami-Dade 59.1%, at equal "
                    f"market $/sqft), which would impose a 2.3x artificial seam at the "
                    f"county line crossing the grid. %TLC/SRC/EPR are invariant to this "
                    f"rate; it sets dollar totals only.",
            "use_codes": list(RESIDENTIAL_UC),
            "assignment": "centroid-in-cell (cells tile: 3-mi ew and ns steps)",
            "caveats": "Condo/co-op parcels report LND_VAL=0 (land folded into unit just "
                       "value), so 'JV - land' is NOT structure value for them; that is why "
                       "exposure is built from living area x a single-family-calibrated "
                       "$/sqft rate instead. Assessed/taxable value is NOT used (Save Our "
                       "Homes caps assessed growth at 3%/yr, diverging from market). "
                       "Excludes commercial, vacant land, and renter-occupied is included "
                       "(unlike the ACS owner-occupied Census model). The rate is calibrated "
                       "on low-rise masonry single-family construction and applied to "
                       "high-rise condo living area, which likely understates high-rise cost.",
        },
    }))

    print(f"\nwrote {OUT.name}")
    print(f"  parcels          : {int(counts.sum()):,}")
    print(f"  living area      : {lvg_tot.sum()/1e6:.1f}M sqft")
    print(f"  replacement cost : ${total/1e9:.2f}B over {covered}/{grid['n_land']} land cells")
    print(f"  (total just value: ${jv_tot.sum()/1e9:.2f}B — for reference only)")
    nz = np.sort(values[values > 0])
    if nz.size:
        print(f"  per-cell $: min ${nz[0]/1e6:.1f}M · median ${nz[nz.size//2]/1e6:.1f}M · "
              f"max ${nz[-1]/1e6:.1f}M")


if __name__ == "__main__":
    main()
