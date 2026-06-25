#!/usr/bin/env python3
"""Build per-grid-vertex exposure from ACS census home values (Option A: aggregate).

For each land grid vertex, exposure = the total owner-occupied home value of all
homes inside that vertex's 3-mile grid cell, computed by areal apportionment of
ACS tract aggregate value (table B25082) onto the cells via tract value-density.

This is the offline pipeline step: heavy GIS stays here; it writes a small
per-vertex JSON (outputs/web/exposure_census.json) that the browser loads and the
Exposure-model selector switches to. The raw TIGER shapefile is NOT committed.

Requires a free Census API key (env CENSUS_API_KEY, or a CENSUS_API_KEY=... line in
~/.env). Get one at https://api.census.gov/data/key_signup.html.

Run:  source venv/bin/activate && python pipeline/build_exposure.py
"""
import json
import math
import os
import re
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests
from shapely.geometry import box

ROOT = Path(__file__).resolve().parents[1]
GRID = ROOT / "outputs" / "web" / "grid.json"
OUT = ROOT / "outputs" / "web" / "exposure_census.json"
# TIGER FL tract shapefile (kept outside the repo); override with EXPOSURE_SHP
SHP = Path(os.environ.get(
    "EXPOSURE_SHP",
    Path.home() / "code" / "weather" / "GIS" / "census_tl_2021_12_tract" / "tl_2021_12_tract.shp"))

ACS_YEAR = 2022
STATE_FIPS = "12"            # Florida
VALUE_VAR = "B25082_001E"    # aggregate value ($) of owner-occupied housing units
UNITS_VAR = "B25001_001E"    # total housing units
EQUAL_AREA = "EPSG:5070"     # NAD83 / Conus Albers (meters) for areal weighting
CELL_HALF_MI = 1.5           # half a 3-mile grid cell


def census_key():
    key = os.environ.get("CENSUS_API_KEY")
    if key:
        return key.strip()
    env = Path.home() / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            m = re.match(r'\s*(?:export\s+)?CENSUS_API_KEY\s*=\s*["\']?([^"\']+)', line)
            if m:
                return m.group(1).strip()
    raise SystemExit("No CENSUS_API_KEY in env or ~/.env — get one at "
                     "https://api.census.gov/data/key_signup.html")


def fetch_acs(key):
    url = (f"https://api.census.gov/data/{ACS_YEAR}/acs/acs5"
           f"?get=NAME,{VALUE_VAR},{UNITS_VAR}&for=tract:*&in=state:{STATE_FIPS}&key={key}")
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    rows = r.json()
    df = pd.DataFrame(rows[1:], columns=rows[0])
    df["GEOID"] = df["state"] + df["county"] + df["tract"]
    df["value"] = pd.to_numeric(df[VALUE_VAR], errors="coerce").fillna(0).clip(lower=0)
    df["units"] = pd.to_numeric(df[UNITS_VAR], errors="coerce").fillna(0).clip(lower=0)
    return df[["GEOID", "value", "units"]]


def build_cells(points):
    """3-mile lat/lon box around each land vertex (grid is axis-aligned to lat/lon)."""
    idx, geoms = [], []
    for i, p in enumerate(points):
        if not p["land"]:
            continue
        hlat = CELL_HALF_MI / 69.17
        hlon = CELL_HALF_MI / (69.17 * math.cos(math.radians(p["lat"])))
        idx.append(i)
        geoms.append(box(p["lon"] - hlon, p["lat"] - hlat, p["lon"] + hlon, p["lat"] + hlat))
    return gpd.GeoDataFrame({"gi": idx}, geometry=geoms, crs="EPSG:4326")


def main():
    grid = json.loads(GRID.read_text())
    pts = grid["points"]
    n = grid["n_points"]

    print(f"ACS {ACS_YEAR} 5-yr · FL tracts …")
    acs = fetch_acs(census_key())
    print(f"  {len(acs)} tracts · statewide owner-occ value ${acs['value'].sum()/1e9:.1f}B")

    tr = gpd.read_file(SHP)[["GEOID", "geometry"]].merge(acs, on="GEOID", how="left")
    tr["value"] = tr["value"].fillna(0)
    tr = tr.to_crs(EQUAL_AREA)
    tr["tract_area"] = tr.geometry.area
    tr["density"] = tr["value"] / tr["tract_area"].where(tr["tract_area"] > 0, other=1)

    cells = build_cells(pts).to_crs(EQUAL_AREA)
    print(f"  {len(cells)} land cells · areal apportionment …")

    inter = gpd.overlay(cells[["gi", "geometry"]], tr[["density", "geometry"]],
                        how="intersection", keep_geom_type=True)
    inter["exposure"] = inter["density"] * inter.geometry.area
    by_cell = inter.groupby("gi")["exposure"].sum()

    values = [0.0] * n
    for gi, v in by_cell.items():
        values[int(gi)] = float(v)
    total = sum(values)
    covered = sum(1 for v in values if v > 0)

    OUT.write_text(json.dumps({
        "values": [round(v, 2) for v in values],
        "total": round(total, 2),
        "n_land": grid["n_land"],
        "n_covered": covered,
        "meta": {
            "source": f"ACS {ACS_YEAR} 5-yr {VALUE_VAR} (aggregate owner-occupied home value)",
            "basis": "Option A (aggregate): per cell = total home value within its 3-mi footprint",
            "apportionment": "areal value-density over tract polygons (EPSG:5070)",
            "tracts_shapefile": str(SHP),
            "caveats": "owner-occupied market value only (no renter/commercial; "
                       "tract polygons include some water area)",
        },
    }))
    print(f"  wrote {OUT.name}: total ${total/1e9:.1f}B over {covered}/{grid['n_land']} land cells")
    nz = sorted(v for v in values if v > 0)
    if nz:
        print(f"  per-cell $: min ${nz[0]/1e6:.1f}M · median ${nz[len(nz)//2]/1e6:.1f}M · "
              f"max ${nz[-1]/1e6:.1f}M")


if __name__ == "__main__":
    main()
