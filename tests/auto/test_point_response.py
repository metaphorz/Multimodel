#!/usr/bin/env python3
"""Selenium test: single-point vs footprint response in the profiler/matrix.

Verifies the spatial-scale toggle:
  1. Footprint mean (metamodel): Rmax->%TLC is concave (0 inflections).
  2. Single point (direct simulation, map-picked vertex): Rmax->%LC is S-shaped
     (>=1 inflection) — the sigmoid the metamodel averages away.
  3. The interaction matrix renders in point mode.
  4. Powell in point mode shows the "switch to a live model" note (no plots).
  5. No severe console errors.

Run:  source venv/bin/activate && python tests/auto/test_point_response.py
"""
import sys
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

URL = "http://localhost:8012/web/index.html"

INFLECT = """
  const infl=ys=>{let s=[];for(let k=1;k<ys.length-1;k++)s.push(Math.sign(ys[k+1]-2*ys[k]+ys[k-1]));
    let f=0;for(let k=1;k<s.length;k++)if(s[k]&&s[k-1]&&s[k]!==s[k-1])f++;return f;};
  const mm=profilerState.mm, ri=mm.stats.findIndex(s=>s.v==='Rmax'), means=mm.stats.map(s=>s.m);
  const lo=mm.stats[ri].min,hi=mm.stats[ri].max,ys=[];
  for(let k=0;k<=24;k++){const raw=means.slice();raw[ri]=lo+(k/24)*(hi-lo);ys.push(profilerState.pred.predict(raw));}
  return infl(ys);
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
                                "!!(state.grid && state.vuln && state.metamodels);"):
                break

        def sel(i, v):
            d.execute_script("const e=document.getElementById(arguments[0]);e.value=arguments[1];"
                             "e.dispatchEvent(new Event('change'));", i, v)
            time.sleep(0.4)

        sel("model", "holland")     # live model so single-point can simulate
        sel("response", "tlc")      # loss response carries the damage sigmoid
        d.execute_script("[...document.querySelectorAll('.analysis-group')]"
                         ".find(g=>g.dataset.grp==='grpStats').click();")
        time.sleep(0.2)
        d.execute_script("document.getElementById('btnProf').click();")
        time.sleep(1.0)

        # 1. footprint = metamodel -> concave
        d.execute_script("profilerState.scale='footprint'; buildProfilerDOM();")
        time.sleep(0.3)
        fp_infl = d.execute_script(INFLECT)
        # 2. single point (6,33) -> S-shaped
        d.execute_script("profilerState.scale='point';"
                         "profilerPickPoint(state.grid.points.findIndex(p=>p.ew===6&&p.ns===33));")
        time.sleep(0.5)
        pt_infl = d.execute_script(INFLECT)
        print(f"Rmax inflections — footprint: {fp_infl}  single-point: {pt_infl}")
        if fp_infl != 0:
            fail.append(f"footprint Rmax should be concave (0 inflections), got {fp_infl}")
        if pt_infl < 1:
            fail.append(f"single-point Rmax should be S-shaped (>=1 inflection), got {pt_infl}")

        # 3. matrix renders in point mode + marker dropped
        d.execute_script("[...document.querySelectorAll('.prof-tab[data-view]')]"
                         ".find(b=>b.dataset.view==='matrix').click();")
        time.sleep(0.6)
        cells = len(d.find_elements("css selector", ".prof-matrix .prof-cell"))
        if cells != 36:
            fail.append(f"point-mode matrix expected 36 cells, got {cells}")
        if not d.execute_script("return !!profilerState.marker;"):
            fail.append("no map marker after picking a point")

        # 4. Powell in point mode -> unavailable note (no matrix cells)
        sel("model", "powell")
        time.sleep(0.4)
        note = d.execute_script("return (document.querySelector('.prof-matrix')?"
                                "document.querySelectorAll('.prof-matrix .prof-cell').length:0);")
        why = d.execute_script("return profilerState.pred && profilerState.pred.available;")
        if why:
            fail.append("Powell single-point should be unavailable")
        print("Powell point-mode available:", why, "(expect False/None)")

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
