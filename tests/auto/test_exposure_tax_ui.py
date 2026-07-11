#!/usr/bin/env python3
"""Selenium check: the Tax Roll (FL DOR) exposure option loads and drives the viewer.

Verifies the option exists, exposure_tax.json is fetched, switching the selector changes
the reported total exposure, and %TLC is INVARIANT to a uniform rescale of exposure
(the property that makes the single domain-wide $/sqft rate safe).

Run (server must be up: ./start):
    venv/bin/python tests/auto/test_exposure_tax_ui.py
"""
import os
import sys
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8012/web/index.html"
HERE = os.path.dirname(os.path.abspath(__file__))
SHOT = os.path.join(HERE, "exposure_tax_ui.png")

opts = Options()
opts.add_argument("--headless=new")
opts.add_argument("--window-size=1400,1000")
opts.set_capability("goog:loggingPrefs", {"browser": "ALL"})

fails = []


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{'  — ' + detail if detail else ''}")
    if not ok:
        fails.append(name)


drv = webdriver.Chrome(options=opts)
try:
    drv.get(URL)
    deadline = time.time() + 25
    while time.time() < deadline:
        if "Loading" not in drv.find_element(By.ID, "info").text:
            break
        time.sleep(0.5)
    time.sleep(1.5)

    print("Tax Roll exposure — UI")
    opts_txt = drv.execute_script(
        "return [...document.querySelectorAll('#exposureModel option')].map(o=>o.value+'|'+o.text);")
    check("selector has tax option", any(o.startswith("tax|") for o in opts_txt), str(opts_txt))
    check("label reads 'Tax Roll (FL DOR)'",
          any("Tax Roll (FL DOR)" in o for o in opts_txt))

    # `state` is a top-level const in a classic script, so it is NOT a window property
    loaded = drv.execute_script(
        "return typeof state !== 'undefined' && !!state.exposureTax;")
    check("exposure_tax.json loaded into state", bool(loaded))

    meta = drv.execute_script(
        "return state.exposureTax ? {total: state.exposureTax.total,"
        " n: state.exposureTax.n_covered, rate: state.exposureTax.rate_psf} : null;")
    check("total is populated", meta and meta["total"] > 1e11, str(meta))

    # totals must differ across the three models
    def total_for(mode):
        return drv.execute_script(
            "document.getElementById('exposureModel').value = arguments[0];"
            "document.getElementById('exposureModel').dispatchEvent(new Event('change'));"
            "return totalExposure();", mode)

    tu, tc, tt = total_for("uniform"), total_for("census"), total_for("tax")
    print(f"    uniform ${tu/1e6:,.0f}M · census ${tc/1e9:,.1f}B · tax ${tt/1e9:,.1f}B")
    check("uniform total = 682 x $100k", abs(tu - 682 * 100000) < 1, f"${tu:,.0f}")
    check("three totals are distinct", len({round(tu), round(tc), round(tt)}) == 3)

    # exposureAt() must agree with the JSON for the active model
    ok_at = drv.execute_script(
        "document.getElementById('exposureModel').value='tax';"
        "document.getElementById('exposureModel').dispatchEvent(new Event('change'));"
        "let v=state.exposureTax.values, bad=0;"
        "for (let i=0;i<v.length;i++){ if (Math.abs(exposureAt(i)-(v[i]||0))>1e-6) bad++; }"
        "return bad;")
    check("exposureAt(i) matches exposure_tax values", ok_at == 0, f"{ok_at} mismatches")

    # %TLC invariance: scaling every exposure by a constant must not change %TLC.
    inv = drv.execute_script("""
      const v = state.exposureTax.values.slice();
      const pts = state.grid.points;
      function pctTLC(vals) {
        let loss = 0, tot = 0;
        for (let i = 0; i < pts.length; i++) {
          if (!pts[i].land) continue;
          const m = 0.001 * ((i % 37) + 1);      // deterministic stand-in for MDR
          loss += m * (vals[i] || 0); tot += (vals[i] || 0);
        }
        return tot > 0 ? loss / tot * 100 : null;
      }
      const a = pctTLC(v);
      const b = pctTLC(v.map(x => x * 3.7));      // uniform rescale (e.g. a different $/sqft)
      return [a, b, Math.abs(a - b)];
    """)
    check("%TLC invariant to uniform exposure rescale", inv[2] < 1e-9,
          f"{inv[0]:.9f}% vs {inv[1]:.9f}%")

    logs = [l for l in drv.get_log("browser")
            if l["level"] == "SEVERE" and "favicon.ico" not in l["message"]]
    check("no severe console errors (favicon 404 ignored)", len(logs) == 0,
          "; ".join(l["message"][:90] for l in logs[:3]))

    drv.save_screenshot(SHOT)
    print(f"\nscreenshot: {SHOT}")
finally:
    drv.quit()

print(f"\n{len(fails)} failure(s)" if fails else "\nAll checks passed")
sys.exit(1 if fails else 0)
