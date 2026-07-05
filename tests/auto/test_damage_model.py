#!/usr/bin/env python3
"""Selenium test: the Logistic damage model alongside the default Vickery/HAZUS curve.

  1. Default damage model is 'vickery'; both options are in the selector.
  2. The logistic MDR matches the piecewise formula exactly:
       D(v) = 1/(1+e^{-0.08(v-148)}) for v<180, else 1.0   (v = 3-sec gust mph)
     -> D(100)=0.021, D(148)=0.500, D(160)=0.723, D(180)=1.000, D(200)=1.000.
  3. Its accumulation ceiling (mdrCeiling) is 1.0 (vs Vickery's ~0.6 plateau).
  4. Switching the damage model re-renders the loss map and changes %TLC.
  5. Single-point %LC uses the selected model (mdrAt), and the profiler loss response
     stays consistent (falls back to the live RSM under the non-Vickery model).
  6. No severe console errors.

Run:  source venv/bin/activate && python tests/auto/test_damage_model.py
"""
import sys
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

URL = "http://localhost:8012/web/index.html"


def main():
    o = Options(); o.add_argument("--headless=new"); o.add_argument("--window-size=1500,1050")
    d = webdriver.Chrome(options=o); fail = []
    try:
        d.get(URL)
        for _ in range(40):
            time.sleep(0.5)
            if d.execute_script("return typeof state!=='undefined' && !!(state.grid && state.vuln && state.inputs);"):
                break

        def sel(i, v):
            d.execute_script("const e=document.getElementById(arguments[0]);e.value=arguments[1];"
                             "e.dispatchEvent(new Event('change'));", i, v); time.sleep(0.3)

        # 1. default + options
        if d.execute_script("return damageModelSel();") != "vickery":
            fail.append("default damage model should be vickery")
        opts = d.execute_script("return [...document.getElementById('damageModel').options].map(o=>o.value);")
        if "logistic" not in opts:
            fail.append(f"logistic option missing; got {opts}")

        # 2. logistic values
        sel("damageModel", "logistic")
        vals = d.execute_script("return [mdrAt(100),mdrAt(148),mdrAt(160),mdrAt(180),mdrAt(200)];")
        expect = [0.0210, 0.5000, 0.7231, 1.0, 1.0]
        print("logistic MDR @100/148/160/180/200:", [round(x, 4) for x in vals])
        for got, exp in zip(vals, expect):
            if abs(got - exp) > 0.002:
                fail.append(f"logistic MDR off: got {got:.4f}, expected {exp}")
        # 3. ceiling
        if abs(d.execute_script("return mdrCeiling();") - 1.0) > 1e-9:
            fail.append("logistic mdrCeiling should be 1.0")

        # 3b. parameters are user-selectable: the panel is VISIBLE (computed display,
        # not just the hidden attribute) only for logistic, and editing changes the curve.
        def panel_visible():
            return d.execute_script("const e=document.getElementById('logisticParams');"
                                    "return getComputedStyle(e).display!=='none' && e.offsetParent!==null;")
        if not panel_visible():
            fail.append("logistic parameter panel should be visible when logistic is selected")
        # lower the median v50 148 -> 120: D(120) should jump from ~0.021-ish toward 0.5
        before = d.execute_script("return mdrAt(120);")
        d.execute_script("const e=document.getElementById('logV50');e.value=120;"
                         "e.dispatchEvent(new Event('input'));"); time.sleep(0.2)
        after = d.execute_script("return mdrAt(120);")
        print(f"tweak v50 148->120: mdr(120) {before:.4f} -> {after:.4f} (expect ~0.500)")
        if abs(after - 0.5) > 0.01:
            fail.append(f"after v50=120, mdr(120) should be ~0.5, got {after:.4f}")
        d.execute_script("const e=document.getElementById('logV50');e.value=148;"
                         "e.dispatchEvent(new Event('input'));"); time.sleep(0.2)
        # panel visually HIDDEN under vickery (computed display none / no layout box)
        sel("damageModel", "vickery")
        if panel_visible():
            fail.append("logistic parameter panel should be HIDDEN under Vickery")
        sel("damageModel", "logistic")

        # 4. switching changes the loss map %TLC
        sel("colorBy", "loss")
        sel("damageModel", "vickery"); time.sleep(0.3)
        pctV = d.execute_script("return pctTLC(computeWindCached());")
        sel("damageModel", "logistic"); time.sleep(0.3)
        pctL = d.execute_script("return pctTLC(computeWindCached());")
        print(f"%TLC vickery={pctV:.2f}  logistic={pctL:.2f}")
        if pctV is None or pctL is None or abs(pctV - pctL) < 0.1:
            fail.append(f"switching damage model should change %TLC ({pctV} vs {pctL})")

        # 5. single-point %LC uses the model; profiler loss stays available (live RSM)
        d.execute_script("const e=document.getElementById('model');e.value='holland';"
                         "e.dispatchEvent(new Event('change'));"); time.sleep(0.4)
        d.execute_script("[...document.querySelectorAll('.analysis-group')]"
                         ".find(g=>g.dataset.grp==='grpStats').click();"); time.sleep(0.2)
        d.execute_script("document.getElementById('btnProf').click();"); time.sleep(0.8)
        d.execute_script("profilerState.scale='point'; profilerPickPoint("
                         "state.grid.points.findIndex(p=>p.ew===6&&p.ns===0));"); time.sleep(0.4)
        sel("response", "tlc")
        av = d.execute_script("return profilerState.pred && profilerState.pred.available;")
        # single-point %LC at the mean storm should equal 100*mdrAt(peak) under logistic
        chk = d.execute_script("""
          const pt=profilerState.pt, rec=state.inputs['cat5'][0];
          const ts=pointSeriesAt('holland',rec,pt); let pk=0; for(const w of ts.w) if(w>pk)pk=w;
          const lc=pointResponse('holland',rec,pt);  // response=tlc -> %LC
          return {lc:+lc.toFixed(3), expect:+(mdrAt(pk)*100).toFixed(3)};
        """)
        print(f"single-point %LC (logistic) = {chk['lc']}  vs 100*mdrAt(peak) = {chk['expect']}")
        if not av:
            fail.append("profiler loss response should be available under logistic model")
        if abs(chk["lc"] - chk["expect"]) > 0.05:
            fail.append(f"single-point %LC should use logistic mdrAt: {chk['lc']} vs {chk['expect']}")

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
