#!/usr/bin/env python3
"""Selenium test: Integrated Kinetic Energy (IKE) metrics + map (meteorologist pt 3).

  1. Single-point Response 'ike' (integrated, TJ·h): positive at the point, and
     DECREASES with VT (faster storm dwells less -> less accumulated energy),
     mirroring the dosage metric.
  2. IKE increases with intensity: it DECREASES as CP (central pressure) rises,
     i.e. a deeper (lower-CP) storm deposits more kinetic energy.
  3. Response 'ikepeak' (TJ): positive; peak-cell IKE integrated >= 0.
  4. IKE map (colorBy=ike): ikeMax>0 for Holland; legend shows energy units; and
     the 682-cell field computes in reasonable time.
  5. IKE map is live-only: Powell / decay-on -> "live-only" (ikeMax 0).
  6. No severe console errors.

Run:  source venv/bin/activate && python tests/auto/test_ike.py
"""
import sys
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

URL = "http://localhost:8012/web/index.html"

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
            if d.execute_script("return typeof state!=='undefined' && "
                                "!!(state.grid && state.inputs && state.holland);"):
                break

        def sel(i, v):
            d.execute_script("const e=document.getElementById(arguments[0]);e.value=arguments[1];"
                             "e.dispatchEvent(new Event('change'));", i, v)
            time.sleep(0.3)

        sel("model", "holland")
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

        # 1. integrated IKE vs VT (faster storm -> less accumulated energy)
        ike_vt = point_curve("ike", "VT")
        # 2. integrated IKE vs CP (deeper/lower-CP storm -> more energy => decreasing in CP)
        ike_cp = point_curve("ike", "CP")
        print(f"IKE(TJ·h) vs VT: {[round(v,4) for v in ike_vt]}")
        print(f"IKE(TJ·h) vs CP: {[round(v,4) for v in ike_cp]}")
        if max(ike_vt) <= 0:
            fail.append("integrated IKE should be > 0 over the point")
        if ike_vt[-1] >= ike_vt[0]:
            fail.append(f"integrated IKE should DECREASE with VT: lo={ike_vt[0]:.4f} hi={ike_vt[-1]:.4f}")
        if ike_cp[-1] >= ike_cp[0]:
            fail.append(f"integrated IKE should DECREASE with CP (intensity): "
                        f"lo={ike_cp[0]:.4f} hi={ike_cp[-1]:.4f}")

        # 3. peak IKE positive
        peak = point_curve("ikepeak", "CP")
        print(f"peak IKE(TJ) vs CP: {[round(v,4) for v in peak]}")
        if max(peak) <= 0:
            fail.append("peak IKE should be > 0 over the point")

        # 4. IKE map (Holland) — field computes, has a positive max, timed
        sel("colorBy", "ike")
        time.sleep(0.4)
        timing = d.execute_script(
            "state.ikeCache=null;const t=performance.now();const f=computePointIKE('holland');"
            "let mx=0;for(const v of f)if(v>mx)mx=v;return [performance.now()-t, mx];")
        print(f"IKE map — compute {timing[0]:.0f} ms, max {timing[1]:.4f} TJ·h")
        amax = d.execute_script("return state.ikeMax;")
        legend = d.execute_script("return document.getElementById('legend').textContent;")
        info = d.execute_script("return document.getElementById('info').textContent;")
        if not (amax and amax > 0):
            fail.append(f"IKE map ikeMax should be > 0, got {amax}")
        if not any(u in legend for u in ("TJ", "GJ", "MJ")):
            fail.append(f"IKE legend missing energy units: {legend[:80]}")
        if "IKE" not in info:
            fail.append(f"IKE map info missing 'IKE': {info[:80]}")
        if timing[0] > 6000:
            fail.append(f"IKE map compute too slow: {timing[0]:.0f} ms")

        # 5. Powell IKE map now works (samples the stored storm-relative field),
        # for both marine and with Kaplan-DeMaria decay.
        sel("model", "powell")
        time.sleep(0.5)
        pmax = d.execute_script("return state.ikeMax;")
        pinfo = d.execute_script("return document.getElementById('info').textContent;")
        print(f"Powell IKE map — ikeMax {pmax}  info {pinfo[:60]!r}")
        if not (pmax and pmax > 0):
            fail.append(f"Powell IKE map should now compute (ikeMax > 0), got {pmax}")
        if "IKE" not in pinfo:
            fail.append(f"Powell IKE map info should mention IKE; got {pinfo[:80]}")
        # decay-on Powell also computes
        d.execute_script("const c=document.getElementById('landDecay');"
                         "if(!c.checked){c.checked=true;c.dispatchEvent(new Event('change'));}")
        time.sleep(0.5)
        pmaxD = d.execute_script("return state.ikeMax;")
        print(f"Powell IKE map (decay on) — ikeMax {pmaxD}")
        if not (pmaxD and pmaxD > 0):
            fail.append(f"Powell IKE map with decay should compute (ikeMax > 0), got {pmaxD}")

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
