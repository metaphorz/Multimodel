#!/usr/bin/env python3
"""Why THIS grid? Test the Form S-6 lattice against the HURDAT2 best-track archive.

The 21x40 grid and its landfall point are SPECIFIED by Form S-6, not chosen by us.
This script asks what that specification buys, and reproduces every HURDAT2 number
quoted in the Goal section of docs/FormS6.tex.

The answer is not the obvious one. The grid does sit in a hurricane-favoured corridor
(~5x the track density of an equal-area box), but it is NOT the point of maximum
hurricane frequency: the landfall maximum lies ~60 mi south, over the Keys, and
catches nearly three times as many. The placement is best read as maximising loss
(hazard x exposure) rather than hazard alone.

Data: inputs/hurdat2-atlantic.txt -- NOAA AOML Atlantic best-track (HURDAT2),
public domain, vendored so the analysis runs from a fresh clone.

Run:  ./venv/bin/python pipeline/analyze_hurdat2_grid.py
Author: Pro Team & Claude Code
"""
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
HURDAT = ROOT / "inputs" / "hurdat2-atlantic.txt"
GRID = ROOT / "outputs" / "web" / "grid.json"

FLA = (24.4, 31.1, -87.7, -79.9)     # Florida, generous bounding box
EAST_COAST_LON = -81.0               # Atlantic side of the peninsula
HURRICANE_KT = 64


def parse_hurdat2(path):
    """-> [{id, name, pts: [(lat, lon, wind_kt, status, record_id)]}]"""
    storms, cur = [], None
    for line in open(path):
        f = [x.strip() for x in line.split(",")]
        if f[0].startswith("AL") and len(f) >= 3 and f[2].isdigit():
            cur = {"id": f[0], "name": f[1], "pts": []}
            storms.append(cur)
        elif cur is not None and len(f) > 6 and f[0].isdigit():
            lat = float(f[4][:-1]) * (1 if f[4][-1] == "N" else -1)
            lon = float(f[5][:-1]) * (-1 if f[5][-1] == "W" else 1)
            try:
                w = int(f[6])
            except ValueError:
                w = -999
            cur["pts"].append((lat, lon, w, f[3], f[2]))
    return storms


def track_crosses(pts, box, hurricane_only=True, n=40):
    """Does the track POLYLINE cross the box?

    Fixes are 6-hourly, and the grid is only 60x117 mi -- a storm can pass clean
    through it between two fixes, so the segments must be tested, not the points.
    """
    lat0, lat1, lon0, lon1 = box
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        if hurricane_only and max(a[2], b[2]) < HURRICANE_KT:
            continue
        for t in np.linspace(0, 1, n):
            y = a[0] + t * (b[0] - a[0])
            x = a[1] + t * (b[1] - a[1])
            if lat0 <= y <= lat1 and lon0 <= x <= lon1:
                return True
    return False


def box_area_sqmi(b):
    lat0, lat1, lon0, lon1 = b
    return (lat1 - lat0) * 69 * (lon1 - lon0) * 69 * np.cos(np.radians((lat0 + lat1) / 2))


def main():
    g = json.loads(GRID.read_text())
    lats = [p["lat"] for p in g["points"]]
    lons = [p["lon"] for p in g["points"]]
    GRID_BOX = (min(lats), max(lats), min(lons), max(lons))
    H = GRID_BOX[1] - GRID_BOX[0]

    storms = parse_hurdat2(HURDAT)
    years = sorted({int(s["id"][4:]) for s in storms})
    hurr = [s for s in storms if any(p[2] >= HURRICANE_KT for p in s["pts"])]
    print(f"HURDAT2 {years[0]}-{years[-1]}: {len(storms):,} storms, "
          f"{len(hurr)} reached hurricane strength\n")

    print(f"Grid box: lat {GRID_BOX[0]:.3f}..{GRID_BOX[1]:.3f}, "
          f"lon {GRID_BOX[2]:.3f}..{GRID_BOX[3]:.3f}  "
          f"({H*69:.0f} x {(GRID_BOX[3]-GRID_BOX[2])*69*0.9:.0f} mi)\n")

    # --- 1. tracks through the grid -----------------------------------------
    fl = [s for s in hurr if track_crosses(s["pts"], FLA)]
    gr = [s for s in hurr if track_crosses(s["pts"], GRID_BOX)]
    a_grid, a_fl = box_area_sqmi(GRID_BOX), box_area_sqmi(FLA)
    pct, area_pct = 100 * len(gr) / len(fl), 100 * a_grid / a_fl
    print("[1] TRACKS through the grid")
    print(f"    hurricanes crossing Florida        : {len(fl)}")
    print(f"    of those, crossing the grid        : {len(gr)}  ({pct:.1f}%)")
    print(f"    grid is {area_pct:.1f}% of Florida's area -> "
          f"concentration {pct/area_pct:.1f}x\n")

    # --- 2. landfalls (HURDAT2 flags them: record id 'L', status 'HU') -------
    land = [(p[0], p[1]) for s in storms for p in s["pts"]
            if p[4] == "L" and p[3] == "HU"]
    fl_land = [p for p in land if FLA[0] <= p[0] <= FLA[1] and FLA[2] <= p[1] <= FLA[3]]
    east = [p for p in fl_land if p[1] > EAST_COAST_LON]
    band = [p for p in east if GRID_BOX[0] <= p[0] <= GRID_BOX[1]]
    print("[2] LANDFALLS (HURDAT2 record id 'L', status HU)")
    print(f"    Florida hurricane landfalls        : {len(fl_land)}")
    print(f"    on the Atlantic (east) coast       : {len(east)}")
    print(f"    inside the grid's latitude band    : {len(band)}\n")

    # --- 3. is the band well placed? slide it down the coast -----------------
    wins = [(sum(1 for p in east if y <= p[0] <= y + H), y)
            for y in np.arange(FLA[0], FLA[1] - H, 0.05)]
    wins.sort(reverse=True)
    rank = next(i + 1 for i, (_, y) in enumerate(wins) if abs(y - GRID_BOX[0]) < 0.06)
    print(f"[3] Sliding a {H*69:.0f}-mi window down the east coast "
          f"({len(wins)} placements)")
    for n, y in wins[:3]:
        print(f"    {n:2d} landfalls  lat {y:5.2f}-{y+H:5.2f}")
    print(f"    ...")
    print(f"    {len(band):2d} landfalls  lat {GRID_BOX[0]:5.2f}-{GRID_BOX[1]:5.2f}"
          f"  <-- the ROA band, rank {rank} of {len(wins)}\n")
    print(f"    => the landfall maximum is ~{(GRID_BOX[0]-wins[0][1])*69:.0f} mi SOUTH "
          f"of the ROA band, and catches {wins[0][0]} vs {len(band)}.")
    print("    => the grid is NOT placed at the hurricane-frequency maximum; it is")
    print("       best read as maximising hazard x EXPOSURE, not hazard alone.")


if __name__ == "__main__":
    main()
