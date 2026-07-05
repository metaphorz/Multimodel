#!/usr/bin/env python3
"""Selenium test: multiple windfield popups can be open at once (left-click a grid dot).

Regression for the change that made each left-click spawn an INDEPENDENT windfield
panel (cascaded, removed from the DOM on close) instead of reusing one shared panel.

  1. Opening the popup on two different vertices yields TWO .wf-panel elements.
  2. Their titles differ (each names its own vertex) and they are cascaded (offset).
  3. Closing one removes it from the DOM (one remains) -- not merely hidden.
  4. A third popup makes three; each has an isotach + time-series SVG body.
  5. No severe console errors.

Run:  source venv/bin/activate && python tests/auto/test_multi_popup.py
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
            if d.execute_script("return typeof state!=='undefined' && "
                                "!!(state.grid && state.inputs && state.holland);"):
                break
        # live model so the popup body renders without depending on the Powell field
        d.execute_script("const e=document.getElementById('model');e.value='holland';"
                         "e.dispatchEvent(new Event('change'));"); time.sleep(0.4)

        # 1. open two popups on different vertices
        info = d.execute_script("""
          const a=state.grid.points.findIndex(p=>p.land&&p.ew===9&&p.ns===0);
          const b=state.grid.points.findIndex(p=>p.land&&p.ew===30&&p.ns===0);
          openWindfieldPopup(a); openWindfieldPopup(b);
          const panels=[...document.querySelectorAll('.wf-panel')];
          return {n:panels.length,
                  titles:panels.map(p=>p.querySelector('.ap-title').textContent),
                  lefts:panels.map(p=>p.style.left), tops:panels.map(p=>p.style.top),
                  svgs:panels.map(p=>p.querySelectorAll('.ap-body svg').length)};
        """)
        print("two popups:", info)
        if info["n"] != 2:
            fail.append(f"two left-clicks should give 2 independent panels, got {info['n']}")
        if info["titles"][0] == info["titles"][1]:
            fail.append(f"panels should name their own vertex; titles equal: {info['titles']}")
        if info["lefts"][0] == info["lefts"][1] and info["tops"][0] == info["tops"][1]:
            fail.append("second panel should be cascaded (offset), not stacked exactly")
        if any(s < 2 for s in info["svgs"]):
            fail.append(f"each popup should have isotach + time-series SVGs, got {info['svgs']}")

        # 3. closing one removes it from the DOM (not hidden)
        d.execute_script("document.querySelector('.wf-panel .ap-close').click();")
        time.sleep(0.2)
        n2 = d.execute_script("return document.querySelectorAll('.wf-panel').length;")
        print("after closing one:", n2)
        if n2 != 1:
            fail.append(f"closing a popup should remove it from the DOM; {n2} remain (expected 1)")

        # 4. a third makes it two again (still independent)
        n3 = d.execute_script("""
          const c=state.grid.points.findIndex(p=>p.land&&p.ew===60&&p.ns===0);
          openWindfieldPopup(c);
          return document.querySelectorAll('.wf-panel').length;
        """)
        if n3 != 2:
            fail.append(f"opening another popup should give 2 again, got {n3}")

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
