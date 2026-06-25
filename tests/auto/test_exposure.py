#!/usr/bin/env python3
"""Selenium test: Exposure model selector (Uniform vs Census ACS).

Verifies:
  1. The selector offers Uniform (default) + Census.
  2. Uniform total exposure reconciles to n_land x $100k (= $68.2M).
  3. Census total matches exposure_census.json and is non-uniform (coast >> inland).
  4. Switching exposure re-renders the loss map and changes %TLC (value-weighting).
  5. The right-click CSV's %TLC reflects the active exposure model.
  6. No severe console errors.

Run:  source venv/bin/activate && python tests/auto/test_exposure.py
"""
import sys
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

URL = "http://localhost:8012/web/index.html"


def main():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1500,1050")
    d = webdriver.Chrome(options=opts)
    fail = []
    try:
        d.get(URL)
        for _ in range(40):
            time.sleep(0.5)
            if d.execute_script("return (typeof state!=='undefined') && "
                                "!!(state.powell && state.vuln && state.exposure);"):
                break

        def setsel(i, v):
            d.execute_script("const e=document.getElementById(arguments[0]);e.value=arguments[1];"
                             "e.dispatchEvent(new Event('change'));", i, v)
            time.sleep(0.7)

        # single vector + loss colouring for determinism
        d.execute_script("if(state.meanMode)document.getElementById('btnMean').click();")
        time.sleep(0.3)
        setsel("model", "powell"); setsel("category", "5"); setsel("colorBy", "loss")

        # 1. options present
        opts_present = d.execute_script(
            "return [...document.querySelectorAll('#exposureModel option')].map(o=>o.value);")
        if opts_present != ["uniform", "census"]:
            fail.append(f"exposure options unexpected: {opts_present}")

        # 2. uniform total = n_land * 100k
        setsel("exposureModel", "uniform")
        tu = d.execute_script("return totalExposure();")
        nland = d.execute_script("return state.grid.n_land;")
        if abs(tu - nland * 100000) > 1:
            fail.append(f"uniform total {tu} != n_land*100k {nland*100000}")
        u_pct = d.execute_script("return pctTLC(state.wind);")
        print(f"uniform: total ${tu/1e6:.1f}M  %TLC {u_pct:.3f}%")

        # 3. census total matches JSON; non-uniform
        setsel("exposureModel", "census")
        tc = d.execute_script("return totalExposure();")
        tj = d.execute_script("return state.exposure.total;")
        if abs(tc - tj) > 1:
            fail.append(f"census total {tc} != json total {tj}")
        vals = d.execute_script("return state.exposure.values.filter(v=>v>0);")
        nonuniform = max(vals) > 50 * (sorted(vals)[len(vals)//2])   # max >> median
        if not nonuniform:
            fail.append("census exposure not strongly non-uniform")
        c_pct = d.execute_script("return pctTLC(state.wind);")
        print(f"census : total ${tc/1e9:.1f}B  %TLC {c_pct:.3f}%  "
              f"(max/median value ratio {max(vals)/sorted(vals)[len(vals)//2]:.0f}x)")

        # 4. %TLC differs between modes (value-weighting)
        if abs(u_pct - c_pct) < 0.01:
            fail.append(f"%TLC did not change with exposure model ({u_pct} vs {c_pct})")

        # 5. CSV %TLC reflects census
        csv = d.execute_script("""
          let cap=null;const oc=URL.createObjectURL;
          URL.createObjectURL=b=>{b.text().then(t=>cap=t);return 'blob:x';};
          HTMLAnchorElement.prototype.click=function(){};
          downloadGridPointCsv(state.grid.points.findIndex(p=>p.land));
          return new Promise(r=>{let n=0;const iv=setInterval(()=>{if(cap!==null||n++>40)
            {clearInterval(iv);URL.createObjectURL=oc;r(cap);}},50);});
        """)
        row1 = csv.split("\n")[1].split(",")
        tlc_csv = float(row1[8])      # %TLC column, as fraction
        if abs(tlc_csv * 100 - c_pct) > 1.0:
            fail.append(f"CSV %TLC {tlc_csv*100:.2f}% != census pctTLC {c_pct:.2f}%")
        print(f"CSV %TLC (census) = {tlc_csv*100:.2f}%")

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
