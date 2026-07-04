#!/usr/bin/env python3
"""Selenium test: duration-accumulated loss (rate-integrated MDR) at a grid point.

  D(x) = min( (1/tau) integral_{V>=40} MDR(V(t)) dt , MDR_max ), with tau calibrated
  per point so the 100-storm mean of D equals the mean of the peak-based %LC.

  1. Response 'accloss' available at single point (Holland), NOT direct-only... it's a
     direct-simulation response; predictor available.
  2. CALIBRATION: over the 100 vectors, mean(accloss) ~= mean(peak %LC) at the vertex
     (rate-integrated MDR is a redistribution, no net bias vs HAZUS).
  3. DURATION SENSITIVITY: accloss DECREASES with VT (slower storm dwells longer ->
     more accumulated damage), whereas peak %LC is ~flat in VT.
  4. Powell single-point accloss is unavailable (needs a live time series).
  5. No severe console errors.

Run:  source venv/bin/activate && python tests/auto/test_accloss.py
"""
import sys
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

URL = "http://localhost:8012/web/index.html"

# sweep VT across its range at the picked point, return the y curve
SWEEP_VT = """
  const mm=profilerState.mm, means=mm.stats.map(s=>s.m);
  const vi=mm.stats.findIndex(s=>s.v==='VT');
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
            if d.execute_script("return typeof state!=='undefined' && "
                                "!!(state.grid && state.inputs && state.vuln && state.holland);"):
                break

        def sel(i, v):
            d.execute_script("const e=document.getElementById(arguments[0]);e.value=arguments[1];"
                             "e.dispatchEvent(new Event('change'));", i, v)
            time.sleep(0.4)

        sel("model", "holland")
        sel("category", "5")
        d.execute_script("const c=document.getElementById('landDecay');"
                         "if(c.checked){c.checked=false;c.dispatchEvent(new Event('change'));}")
        time.sleep(0.3)
        d.execute_script("[...document.querySelectorAll('.analysis-group')]"
                         ".find(g=>g.dataset.grp==='grpStats').click();")
        time.sleep(0.2)
        d.execute_script("document.getElementById('btnProf').click();")
        time.sleep(1.0)

        # off-track vertex where MDR is moderate (not saturated), so duration can matter
        idx = d.execute_script("return state.grid.points.findIndex(p=>p.ew===6&&p.ns===33);")
        d.execute_script("profilerState.scale='point'; profilerPickPoint(arguments[0]);", idx)
        time.sleep(0.5)

        # 1. accloss available
        sel("response", "accloss")
        av = d.execute_script("return profilerState.pred && profilerState.pred.available;")
        if not av:
            fail.append("accloss single-point should be available for Holland")

        # 2. calibration: mean over 100 vectors of accloss ~= mean peak %LC
        cal = d.execute_script("""
          const model='holland', cat='cat5', pt=profilerState.pt;
          const recs=state.inputs[cat];
          let accSum=0, peakSum=0;
          for(const rec of recs){
            const ts=pointSeriesAt(model,rec,pt);
            const A=accMDRIntegral(ts);
            const c=accCalibration(model,cat,pt);
            accSum += Math.min(A/c.tau, c.mdrMax)*100;
            let pk=0; for(const w of ts.w) if(w>pk)pk=w;
            peakSum += (mdrAt(pk)||0)*100;
          }
          return {accMean:accSum/recs.length, peakMean:peakSum/recs.length, tau:accCalibration(model,cat,pt).tau};
        """)
        print(f"calibration: mean(accloss)={cal['accMean']:.3f}  mean(peak %LC)={cal['peakMean']:.3f}  tau={cal['tau']:.3f}")
        if abs(cal["accMean"] - cal["peakMean"]) > 0.5:
            fail.append(f"accloss mean should match peak %LC mean (calibration); "
                        f"{cal['accMean']:.2f} vs {cal['peakMean']:.2f}")

        # 3. duration sensitivity: accloss decreases with VT; peak %LC ~flat in VT
        acc_vt = d.execute_script(SWEEP_VT)
        sel("response", "tlc")
        tlc_vt = d.execute_script(SWEEP_VT)
        print(f"accloss vs VT: {[round(v,2) for v in acc_vt]}")
        print(f"peak %LC vs VT: {[round(v,2) for v in tlc_vt]}")
        acc_drop = acc_vt[0] - acc_vt[-1]
        tlc_drop = abs(tlc_vt[0] - tlc_vt[-1])
        if acc_drop <= 0.2:
            fail.append(f"accloss should DECREASE with VT (duration), drop={acc_drop:.2f}")
        if acc_drop <= tlc_drop:
            fail.append(f"accloss should be more VT-sensitive than peak %LC: "
                        f"acc drop={acc_drop:.2f} vs peak drop={tlc_drop:.2f}")

        # 4. Powell single-point accloss unavailable
        sel("response", "accloss")
        sel("model", "powell")
        time.sleep(0.4)
        pav = d.execute_script("return profilerState.pred && profilerState.pred.available;")
        print("Powell accloss available:", pav, "(expect False)")
        if pav:
            fail.append("Powell single-point accloss should be unavailable (needs time series)")

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
