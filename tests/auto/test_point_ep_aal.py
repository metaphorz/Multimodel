#!/usr/bin/env python3
"""Selenium test: per-point EP curve + AAL heat-map (meteorologist point 2).

  1. Financial panel, Single-point scale with NO vertex picked -> "pick a grid
     vertex" prompt (no EP curve).
  2. After picking vertex (6,33): the per-point EP renders, shows the
     scenario-conditional (fixed-track) caveat, and the per-point AAL is positive
     and far below the domain aggregate AAL (one $100k home vs 682-point sum).
  3. pointLossSeries returns 100 samples/category (same sampling as the aggregate).
  4. AAL heat-map (colorBy=aal): aalMax>0, info reads "Domain AAL", legend shows $
     thresholds; raising a category rate raises the domain AAL total.
  5. No severe console errors.

Run:  source venv/bin/activate && python tests/auto/test_point_ep_aal.py
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
                                "!!(state.grid && state.inputs && state.vuln && state.holland);"):
                break

        def sel(i, v):
            d.execute_script("const e=document.getElementById(arguments[0]);e.value=arguments[1];"
                             "e.dispatchEvent(new Event('change'));", i, v)
            time.sleep(0.3)

        sel("model", "holland")
        d.execute_script("document.getElementById('btnFin').click();")
        time.sleep(0.8)

        # 1. single-point scale, no point picked -> prompt
        d.execute_script("finState.scale='point'; profilerState.pt=null; drawFinancial();")
        time.sleep(0.4)
        body = d.execute_script("return panels['fin'].body.textContent;")
        if "pick a grid vertex" not in body.lower():
            fail.append(f"expected pick-a-point prompt; got: {body[:140]}")

        # 2. pick (6,33) and render per-point EP
        idx = d.execute_script("return state.grid.points.findIndex(p=>p.ew===6&&p.ns===33);")
        if idx < 0:
            fail.append("vertex (6,33) not found")
        d.execute_script("const q=state.grid.points[arguments[0]];"
                         "profilerState.pt={ew:q.ew,ns:q.ns,idx:arguments[0]};"
                         "finState.scale='point'; finState.mode='annual'; drawFinancial();", idx)
        time.sleep(0.5)
        body = d.execute_script("return panels['fin'].body.textContent;")
        if "Scenario-conditional" not in body:
            fail.append("per-point EP missing the fixed-track caveat")
        if "AAL" not in body:
            fail.append("per-point EP missing the AAL metric")

        # numeric sanity: point AAL > 0 and << domain AAL; 100 samples/category
        nums = d.execute_script("""
          const rates={1:0.20,3:0.05,5:0.01};
          const dom=[1,3,5].reduce((a,c)=>{const s=tlcSeries('holland','cat'+c,0,null);
              return a+rates[c]*s.reduce((x,y)=>x+y,0)/s.length;},0);
          const s1=pointLossSeries('holland','cat1',arguments[0],0,null);
          const pt=[1,3,5].reduce((a,c)=>{const s=pointLossSeries('holland','cat'+c,arguments[0],0,null);
              return a+rates[c]*s.reduce((x,y)=>x+y,0)/s.length;},0);
          return {dom:dom, pt:pt, n:s1.length};
        """, idx)
        print(f"AAL — domain: {nums['dom']:.0f}  point(6,33): {nums['pt']:.2f}  "
              f"samples/cat: {nums['n']}")
        if nums["n"] != 100:
            fail.append(f"expected 100 samples/category, got {nums['n']}")
        if not (nums["pt"] > 0):
            fail.append(f"per-point AAL should be > 0, got {nums['pt']}")
        if not (nums["pt"] < nums["dom"]):
            fail.append(f"per-point AAL ({nums['pt']:.2f}) should be << domain ({nums['dom']:.0f})")

        # 4. AAL heat-map
        sel("colorBy", "aal")
        time.sleep(0.6)
        amax = d.execute_script("return state.aalMax;")
        info = d.execute_script("return document.getElementById('info').textContent;")
        legend = d.execute_script("return document.getElementById('legend').textContent;")
        print(f"AAL map — aalMax: {amax:.2f}  info: {info[:60]!r}")
        if not (amax and amax > 0):
            fail.append(f"AAL map aalMax should be > 0, got {amax}")
        if "AAL" not in info:
            fail.append(f"AAL map info readout missing 'AAL': {info[:80]}")
        if "$" not in legend:
            fail.append(f"AAL legend missing $ thresholds: {legend[:80]}")

        # raising the Cat-5 rate raises the domain AAL
        base_total = d.execute_script(
            "let t=0;for(let i=0;i<state.grid.points.length;i++)"
            "if(state.grid.points[i].land)t+=computePointAAL('holland')[i];return t;")
        d.execute_script("finState.rates[5]=0.05; updateField();")
        time.sleep(0.4)
        hi_total = d.execute_script(
            "let t=0;const a=computePointAAL('holland');"
            "for(let i=0;i<state.grid.points.length;i++)if(state.grid.points[i].land)t+=a[i];return t;")
        print(f"domain AAL total — rate5=0.01: {base_total:.0f}  rate5=0.05: {hi_total:.0f}")
        if not (hi_total > base_total):
            fail.append("raising Cat-5 rate should raise domain AAL total")

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
