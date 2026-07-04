#!/usr/bin/env python3
"""Selenium test: Powell single-point sensitivity via a per-vertex 2nd-order RSM.

Powell has no live field to re-simulate, so single-point mode fits a per-vertex RSM
from the 100 precomputed peaks at the picked vertex (peak wind / %LC only).

  1. Powell + Single point + response=wind: predictor available, NOT direct (metamodel);
     predict(means) ~= the mean of the 100 peaks at that vertex (RSM intercept), and a
     swept input (CP) actually moves the curve.
  2. response=tlc gives %LC = 100*MDR(predicted peak) (0..~60).
  3. response=ike (duration) is unavailable for Powell single-point (needs a live model).
  4. The interaction matrix renders (36 cells) for Powell single point.
  5. Holland single-point is still 'direct' (unchanged).
  6. No severe console errors.

Run:  source venv/bin/activate && python tests/auto/test_powell_singlepoint.py
"""
import sys
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

URL = "http://localhost:8012/web/index.html"


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
            if d.execute_script("return typeof state!=='undefined' && "
                                "!!(state.grid && state.inputs && state.powell && state.metamodels);"):
                break

        def sel(i, v):
            d.execute_script("const e=document.getElementById(arguments[0]);e.value=arguments[1];"
                             "e.dispatchEvent(new Event('change'));", i, v)
            time.sleep(0.4)

        sel("model", "powell")
        sel("category", "5")
        # ensure decay off so we use the marine peaks (KD may be pending)
        d.execute_script("const c=document.getElementById('landDecay');"
                         "if(c.checked){c.checked=false;c.dispatchEvent(new Event('change'));}")
        time.sleep(0.3)
        d.execute_script("[...document.querySelectorAll('.analysis-group')]"
                         ".find(g=>g.dataset.grp==='grpStats').click();")
        time.sleep(0.2)
        d.execute_script("document.getElementById('btnProf').click();")
        time.sleep(1.0)

        # go to single point and pick a land vertex the storm strongly affects
        d.execute_script("profilerState.scale='point';"
                         "profilerPickPoint(state.grid.points.findIndex(p=>p.ew===6&&p.ns===0));")
        time.sleep(0.6)
        sel("response", "wind")

        info = d.execute_script("""
          const idx = state.grid.points.findIndex(p=>p.ew===6&&p.ns===0);
          const pred = profilerState.pred, mm = profilerState.mm;
          const means = mm.stats.map(s=>s.m);
          const atMeans = pred.available ? pred.predict(means) : null;
          // sweep CP across its range -> does the curve move?
          const ci = mm.stats.findIndex(s=>s.v==='CP'); const ys=[];
          if (pred.available) for(let k=0;k<=12;k++){const raw=means.slice();
            raw[ci]=mm.stats[ci].min+(k/12)*(mm.stats[ci].max-mm.stats[ci].min); ys.push(pred.predict(raw));}
          const meanPeak = computeMeanWind('powell','cat5')[idx];   // mean of 100 peaks at vertex
          return {available:pred.available, direct:pred.direct, atMeans, meanPeak,
                  ySpread:+(Math.max(...ys)-Math.min(...ys)).toFixed(2)};
        """)
        print("Powell wind:", info)
        if not info["available"]:
            fail.append("Powell single-point (wind) should be available")
        if info["direct"] is not False:
            fail.append(f"Powell single-point should be a metamodel (direct=false), got {info['direct']}")
        if info["atMeans"] is None or abs(info["atMeans"] - info["meanPeak"]) > 3.0:
            fail.append(f"predict(means) ~= mean peak expected; got {info['atMeans']} vs {info['meanPeak']}")
        if info["ySpread"] < 1.0:
            fail.append(f"sweeping CP should move the curve, spread={info['ySpread']}")

        # 2. %LC response
        sel("response", "tlc")
        lc = d.execute_script("const mm=profilerState.mm;return profilerState.pred.available?"
                              "profilerState.pred.predict(mm.stats.map(s=>s.m)):null;")
        print("Powell %LC at means:", lc)
        if lc is None or not (0 <= lc <= 65):
            fail.append(f"Powell single-point %LC should be a 0..~60 percent, got {lc}")

        # 3. duration metric unavailable for Powell single-point
        sel("response", "ike")
        av = d.execute_script("return profilerState.pred && profilerState.pred.available;")
        print("Powell IKE single-point available:", av, "(expect False)")
        if av:
            fail.append("Powell single-point should be unavailable for IKE (needs live model)")

        # 4. matrix renders for Powell single point
        sel("response", "wind")
        d.execute_script("[...document.querySelectorAll('.prof-tab[data-view]')]"
                         ".find(b=>b.dataset.view==='matrix').click();")
        time.sleep(0.6)
        cells = len(d.find_elements("css selector", ".prof-matrix .prof-cell"))
        if cells != 36:
            fail.append(f"Powell single-point matrix expected 36 cells, got {cells}")

        # 5. Holland single-point is still 'direct'
        d.execute_script("[...document.querySelectorAll('.prof-tab[data-view]')]"
                         ".find(b=>b.dataset.view==='profiler').click();")
        sel("model", "holland")
        time.sleep(0.5)
        hdirect = d.execute_script("return profilerState.pred && profilerState.pred.direct;")
        print("Holland single-point direct:", hdirect, "(expect True)")
        if hdirect is not True:
            fail.append(f"Holland single-point should remain direct simulation, got {hdirect}")

        errs = [e["message"][:160] for e in d.get_log("browser")
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
