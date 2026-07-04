#!/usr/bin/env python3
"""Single-point profiler works for all 3 windfields x every roughness/decay combo,
and Holland/Willoughby decay results stay consistent with the precomputed footprint.

  1. Holland/Willoughby single-point is AVAILABLE in all 4 combos (none, roughness,
     decay, roughness+decay) -- no longer gated off by decay.
  2. CONSISTENCY: for a sampled vector, the single-point peak (response=wind) equals
     the precomputed footprint peak at that vertex (computeWindFor) in every combo,
     within a small tolerance -- i.e. live decay/roughness matches the footprint.
  3. Powell single-point (peak wind) available in all 4 combos too.
  4. No severe console errors.

Run:  source venv/bin/activate && python tests/auto/test_singlepoint_landcombos.py
"""
import sys, time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

URL = "http://localhost:8012/web/index.html"
COMBOS = [(False, False), (True, False), (False, True), (True, True)]  # (rough, decay)


def main():
    o = Options(); o.add_argument("--headless=new"); o.add_argument("--window-size=1500,1100")
    d = webdriver.Chrome(options=o); fail = []
    try:
        d.get(URL)
        for _ in range(40):
            time.sleep(0.5)
            if d.execute_script("return typeof state!=='undefined' && !!(state.grid && state.inputs "
                                "&& state.holland && state.hollandKd && state.powell && state.metamodels);"):
                break

        def setcombo(rough, decay):
            d.execute_script("""
              const r=document.getElementById('landRoughness'), k=document.getElementById('landDecay');
              if(r.checked!==arguments[0]){r.checked=arguments[0];r.dispatchEvent(new Event('change'));}
              if(k.checked!==arguments[1]){k.checked=arguments[1];k.dispatchEvent(new Event('change'));}
            """, rough, decay); time.sleep(0.3)

        def sel(i, v):
            d.execute_script("const e=document.getElementById(arguments[0]);e.value=arguments[1];"
                             "e.dispatchEvent(new Event('change'));", i, v); time.sleep(0.3)

        sel("category", "5"); sel("response", "wind")
        d.execute_script("[...document.querySelectorAll('.analysis-group')]"
                         ".find(g=>g.dataset.grp==='grpStats').click();"); time.sleep(0.2)
        d.execute_script("document.getElementById('btnProf').click();"); time.sleep(0.8)
        idx = d.execute_script("return state.grid.points.findIndex(p=>p.ew===6&&p.ns===33);")

        for model in ("holland", "willoughby"):
            sel("model", model)
            d.execute_script("profilerState.scale='point'; profilerPickPoint(arguments[0]);", idx)
            time.sleep(0.3)
            for rough, decay in COMBOS:
                setcombo(rough, decay)
                d.execute_script("buildProfilerDOM();"); time.sleep(0.2)
                av = d.execute_script("return profilerState.pred && profilerState.pred.available;")
                # single-point peak vs precomputed footprint peak for vector 0
                cmp = d.execute_script("""
                  const cat='cat5', i=0, rec=state.inputs[cat][i], pt=profilerState.pt;
                  const sp = pointResponse(arguments[0], rec, pt);      // live single-point peak
                  const fp = computeWindFor(arguments[0], cat, i)[pt.idx]; // precomputed footprint peak
                  return {sp:+sp.toFixed(2), fp:+fp.toFixed(2), diff:+Math.abs(sp-fp).toFixed(2)};
                """, model)
                tag = f"{model} rough={rough} decay={decay}"
                print(f"{tag}: available={av}  single-pt={cmp['sp']}  footprint={cmp['fp']}  |diff|={cmp['diff']}")
                if not av:
                    fail.append(f"{tag}: single-point should be AVAILABLE")
                if cmp["diff"] > 2.0:
                    fail.append(f"{tag}: single-point peak should match footprint (|diff|={cmp['diff']})")

        # Powell single-point available in all combos (peak wind)
        sel("model", "powell")
        d.execute_script("profilerState.scale='point'; profilerPickPoint(arguments[0]);", idx); time.sleep(0.3)
        for rough, decay in COMBOS:
            setcombo(rough, decay); d.execute_script("buildProfilerDOM();"); time.sleep(0.2)
            av = d.execute_script("return profilerState.pred && profilerState.pred.available;")
            print(f"powell rough={rough} decay={decay}: available={av}")
            if not av:
                fail.append(f"powell rough={rough} decay={decay}: single-point should be available")

        errs = [e["message"][:160] for e in d.get_log("browser")
                if e["level"] == "SEVERE" and "favicon.ico" not in e["message"]]
        if errs:
            fail.append(f"console errors: {errs}")
    finally:
        d.quit()
    if fail:
        print("FAIL:\n  - " + "\n  - ".join(fail)); sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
