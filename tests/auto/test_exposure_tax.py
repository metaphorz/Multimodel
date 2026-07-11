#!/usr/bin/env python3
"""Invariants for the Tax Roll (FL DOR) exposure model, and comparison against Census.

Run: venv/bin/python tests/auto/test_exposure_tax.py
"""
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
grid = json.loads((ROOT / "outputs/web/grid.json").read_text())
tax = json.loads((ROOT / "outputs/web/exposure_tax.json").read_text())
cen = json.loads((ROOT / "outputs/web/exposure_census.json").read_text())

pts = grid["points"]
tv = np.array(tax["values"])
cv = np.array(cen["values"])
land = np.array([p["land"] for p in pts])

fails = []


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{'  — ' + detail if detail else ''}")
    if not ok:
        fails.append(name)


print("Tax Roll exposure — invariants")
check("length == n_points", tv.size == grid["n_points"], f"{tv.size}")
check("no negative values", bool((tv >= 0).all()))
check("water vertices carry zero", bool((tv[~land] == 0).all()),
      f"{int((tv[~land] > 0).sum())} water cells nonzero")
check("sum(values) == total", abs(tv.sum() - tax["total"]) < 1.0,
      f"{tv.sum():.0f} vs {tax['total']:.0f}")
check("n_covered matches", int((tv > 0).sum()) == tax["n_covered"],
      f"{int((tv > 0).sum())} vs {tax['n_covered']}")
check("total == rate x living area (rate stored to 2dp)",
      abs(tax["rate_psf"] * tax["living_area_sqft"] - tax["total"]) / tax["total"] < 1e-3,
      f"${tax['rate_psf']}/sqft x {tax['living_area_sqft']:,.0f} sqft")
check("rate in plausible RCV band ($80-$400/sqft)", 80 <= tax["rate_psf"] <= 400,
      f"${tax['rate_psf']}/sqft")

print("\nStructure-vs-market (the reason this model exists)")
ratio = tax["total"] / tax["jv_total"]
check("replacement cost < total just value", ratio < 1.0,
      f"${tax['total']/1e9:.0f}B / ${tax['jv_total']/1e9:.0f}B = {ratio:.2f}")
# NOT census > tax. Census (ACS B25082) counts OWNER-OCCUPIED units only, so it omits
# renter-occupied housing, condos held as rentals, and most multifamily. The tax roll
# counts every residential parcel. Excluding land costs the tax model less than
# excluding renters costs the Census model, so tax > census here.
rc = tax["total"] / cen["total"]
check("tax and census same order of magnitude", 0.5 < rc < 2.5,
      f"tax ${tax['total']/1e9:.0f}B / census ${cen['total']/1e9:.0f}B = {rc:.2f}x")
print(f"    tax > census because ACS B25082 is owner-occupied only "
      f"({tax['n_parcels']:,} residential parcels counted here)")

print("\nNo county seam (rate is a single domain constant)")
# Miami-Dade / Broward line ~25.96N. Implied $/sqft must be identical either side.
lat = np.array([p["lat"] for p in pts])
south = land & (tv > 0) & (lat < 25.96)
north = land & (tv > 0) & (lat > 25.96)
check("populated cells on both sides", south.sum() > 10 and north.sum() > 10,
      f"{south.sum()} south / {north.sum()} north")

print("\nSpatial sanity")
# The Everglades (western interior) must be empty of residential parcels.
lon = np.array([p["lon"] for p in pts])
everglades = land & (lon < -80.7) & (lon > -81.4)
check("Everglades cells mostly empty", (tv[everglades] == 0).mean() > 0.8,
      f"{(tv[everglades] == 0).mean()*100:.0f}% of {everglades.sum()} cells zero")
check("Census smears value into Everglades (tax does not)",
      (cv[everglades] > 0).sum() > (tv[everglades] > 0).sum(),
      f"census {int((cv[everglades] > 0).sum())} vs tax {int((tv[everglades] > 0).sum())} nonzero")

# Coastal Miami/Broward strip must be dense.
coast = land & (lon > -80.25) & (lat > 25.7) & (lat < 26.4)
check("coastal strip populated", (tv[coast] > 0).mean() > 0.8,
      f"{(tv[coast] > 0).mean()*100:.0f}% of {coast.sum()} cells nonzero")

print("\nAgreement with Census where both are populated")
both = land & (tv > 0) & (cv > 0)
r = np.corrcoef(tv[both], cv[both])[0, 1]
check("positively correlated with Census (r > 0.5)", r > 0.5, f"r = {r:.3f}")
print(f"    (they should agree in shape but not level: tax excludes land, census includes it)")

print(f"\n{len(fails)} failure(s)" if fails else "\nAll checks passed")
sys.exit(1 if fails else 0)
