#!/usr/bin/env python3
"""Selenium test: Loss EP / Financial panel (actuarial layer).

Verifies:
  1. The Analysis section's Actuarial group expands to reveal the button.
  2. The panel opens with an EP plot + a metrics table (Annualized default: AAL,
     return-period losses, TVaR).
  3. Conditional <-> Annualized toggle re-renders with the right metric labels.
  4. A per-location deductible reduces the annualized AAL.
  5. No severe console errors.

Run:  source venv/bin/activate && python tests/auto/test_financial.py
"""
import re
import sys
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

URL = "http://localhost:8012/web/index.html"


def money(driver, label_substr):
    # return the first $ value (in dollars) in the metrics row whose label matches;
    # values are adaptively formatted as $X.XXB / $X.XXM / $Xk
    mult = {"B": 1e9, "M": 1e6, "k": 1e3}
    for tr in driver.find_elements(By.CSS_SELECTOR, ".analysis-panel .cmp-tbl tr"):
        tds = tr.find_elements(By.TAG_NAME, "td")
        if len(tds) == 2 and label_substr in tds[0].text:
            m = re.search(r"\$([\d.]+)([BMk])", tds[1].text)
            return float(m.group(1)) * mult[m.group(2)] if m else None
    return None


def main():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1500,1050")
    d = webdriver.Chrome(options=opts)
    failures = []
    try:
        d.get(URL)
        for _ in range(40):
            time.sleep(0.5)
            if d.execute_script("return (typeof state!=='undefined') && !!(state.powell && state.vuln);"):
                break

        # 1. expand Actuarial group, open the panel
        d.execute_script("[...document.querySelectorAll('.analysis-group')]"
                         ".find(g=>g.dataset.grp==='grpAct').click();")
        time.sleep(0.3)
        if not d.find_element(By.ID, "btnFin").is_displayed():
            failures.append("Loss EP / Financial button not revealed by Actuarial group")
        d.execute_script("document.getElementById('btnFin').click();")
        time.sleep(1.5)

        # 2. annualized metrics present
        n_svg = len(d.find_elements(By.CSS_SELECTOR, ".analysis-panel .ap-body svg"))
        if n_svg != 1:
            failures.append(f"expected 1 EP plot svg, got {n_svg}")
        aal = money(d, "AAL")
        rp = money(d, "50 / 100 / 250")
        if aal is None:
            failures.append("AAL metric missing in annualized mode")
        if rp is None:
            failures.append("return-period loss metric missing")
        print(f"annualized: AAL=${aal/1e6:.2f}M  50yr=${rp/1e6:.2f}M  svgs={n_svg}")

        # 3. conditional toggle
        d.execute_script("[...document.querySelectorAll('.fin-tab')]"
                         ".find(b=>b.dataset.mode==='cond').click();")
        time.sleep(1.0)
        labels = [td.text for td in d.find_elements(By.CSS_SELECTOR, ".analysis-panel .cmp-tbl td")]
        if not any("mean" in s for s in labels) or not any("CoV" in s for s in labels):
            failures.append(f"conditional metrics missing mean/CoV: {labels}")
        print(f"conditional labels: {[s for s in labels if s][:3]}")

        # 4. deductible reduces AAL (back in annualized)
        d.execute_script("[...document.querySelectorAll('.fin-tab')]"
                         ".find(b=>b.dataset.mode==='annual').click();")
        time.sleep(0.8)
        aal0 = money(d, "AAL")
        d.execute_script("const i=document.querySelector('[data-fin=ded]');"
                         "i.value=20000; i.dispatchEvent(new Event('change'));")
        time.sleep(1.0)
        aal1 = money(d, "AAL")
        print(f"AAL: ${aal0/1e6:.2f}M -> ${aal1/1e6:.2f}M with $20k deductible")
        if not (aal1 < aal0):
            failures.append(f"deductible did not reduce AAL ({aal0} -> {aal1})")

        errors = [e for e in d.get_log("browser")
                  if e["level"] == "SEVERE" and "favicon.ico" not in e["message"]]
        if errors:
            failures.append(f"console errors: {errors}")
    finally:
        d.quit()

    if failures:
        print("FAIL:\n  - " + "\n  - ".join(failures))
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
