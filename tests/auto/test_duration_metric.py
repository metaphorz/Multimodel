#!/usr/bin/env python3
"""Selenium test: duration-aware location loss metrics (dwell / dosage).

Meteorologist request (#1): loss accumulates while wind stays above ~40 mph, so
a peak-only metric hides the forward-speed (VT) and size sensitivity you want at
the location level. This verifies the two new single-point response metrics:

  1. Single point (map-picked vertex), response=dwell: predict returns a positive
     number of hours, and dwell DECREASES as VT (forward speed) increases — a
     faster storm dwells less over the point. This is the VT sensitivity a
     peak-only metric collapses.
  2. response=dosage behaves the same way (positive, VT-decreasing).
  3. Footprint scale is gated for duration (pred.available is False) — no
     time series exists in the precomputed peak-wind stores.
  4. The SRC panel shows the "location-level" note for duration (no footprint chart).
  5. No severe console errors.

Run:  source venv/bin/activate && python tests/auto/test_duration_metric.py
"""
import sys
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

URL = "http://localhost:8012/web/index.html"

# sweep one input variable over its range at the picked point, return the y curve
SWEEP = """
  const mm=profilerState.mm, means=mm.stats.map(s=>s.m);
  const vi=mm.stats.findIndex(s=>s.v===arguments[0]);
  const lo=mm.stats[vi].min, hi=mm.stats[vi].max, ys=[];
  for(let k=0;k<=12;k++){const raw=means.slice();raw[vi]=lo+(k/12)*(hi-lo);
    ys.push(profilerState.pred.predict(raw));}
  return ys;
"""


def main():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1500,1100")
    d = webdriver.Chrome(options=opts)
    fail = []
    try:
        d.get(URL)
        for _ in range(40):
            time.sleep(0.5)
            if d.execute_script("return (typeof state!=='undefined') && "
                                "!!(state.grid && state.metamodels);"):
                break

        def sel(i, v):
            d.execute_script("const e=document.getElementById(arguments[0]);e.value=arguments[1];"
                             "e.dispatchEvent(new Event('change'));", i, v)
            time.sleep(0.4)

        sel("model", "holland")     # live model so single-point can simulate
        # single-point runs a live windfield; Kaplan–DeMaria decay must be off
        d.execute_script("const c=document.getElementById('landDecay');"
                         "if(c.checked){c.checked=false;c.dispatchEvent(new Event('change'));}")
        time.sleep(0.3)
        d.execute_script("[...document.querySelectorAll('.analysis-group')]"
                         ".find(g=>g.dataset.grp==='grpStats').click();")
        time.sleep(0.2)
        d.execute_script("document.getElementById('btnProf').click();")
        time.sleep(1.0)

        def point_curve(resp, var):
            sel("response", resp)
            d.execute_script("profilerState.scale='point';"
                             "profilerPickPoint(state.grid.points.findIndex(p=>p.ew===6&&p.ns===33));")
            time.sleep(0.5)
            return d.execute_script(SWEEP, var)

        # 1. dwell: positive at the point, and decreasing in VT (faster storm dwells less)
        dwell_vt = point_curve("dwell", "VT")
        print(f"dwell vs VT (h): {[round(v,2) for v in dwell_vt]}")
        if not all(v >= 0 for v in dwell_vt):
            fail.append(f"dwell should be >=0 hours, got {dwell_vt}")
        if max(dwell_vt) <= 0:
            fail.append("dwell is zero everywhere — expected >0 hours over the point")
        if dwell_vt[-1] >= dwell_vt[0]:
            fail.append(f"dwell should DECREASE with VT (faster=less dwell): "
                        f"lo={dwell_vt[0]:.2f} hi={dwell_vt[-1]:.2f}")

        # 2. dosage: same qualitative VT behaviour, positive
        dose_vt = point_curve("dosage", "VT")
        print(f"dosage vs VT (mph.h): {[round(v,1) for v in dose_vt]}")
        if max(dose_vt) <= 0:
            fail.append("dosage is zero everywhere — expected >0 mph.h over the point")
        if dose_vt[-1] >= dose_vt[0]:
            fail.append(f"dosage should DECREASE with VT: lo={dose_vt[0]:.1f} hi={dose_vt[-1]:.1f}")

        # 3. footprint scale gated for duration
        d.execute_script("profilerState.scale='footprint'; buildProfilerDOM();")
        time.sleep(0.4)
        avail = d.execute_script("return profilerState.pred && profilerState.pred.available;")
        print("footprint dwell available:", avail, "(expect False)")
        if avail:
            fail.append("footprint scale should be unavailable for duration metrics")

        # 4. SRC panel shows the location-level note for duration
        d.execute_script("document.getElementById('btnSRC').click();")
        time.sleep(0.6)
        body = d.execute_script("return panels['src'].body.textContent;")
        if "location-level" not in body:
            fail.append(f"SRC panel should show location-level note for duration; got: {body[:120]}")

        errs = [e for e in d.get_log("browser")
                if e["level"] == "SEVERE" and "favicon.ico" not in e["message"]]
        if errs:
            fail.append(f"console errors: {errs}")
    finally:
        d.quit()

    if fail:
        print("FAIL:\n  - " + "\n  - ".join(fail))
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
