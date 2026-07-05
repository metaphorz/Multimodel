#!/usr/bin/env python3
"""Selenium test: open windfield popups (and the POI detail panel) refresh when a
left-side selection changes, instead of freezing at open time.

  1. Open a left-click windfield popup; its title names the current model/cat/vector.
  2. Change the WINDFIELD MODEL -> the open popup's title updates to the new model,
     and its isotach/time-series SVGs re-render.
  3. Change the CATEGORY -> the popup updates.
  4. Two popups open at once both update on a model change.
  5. The POI detail panel refreshes on a control change (it shows loss, so the title
     stays but the body re-renders under the new selection).
  6. No severe console errors.

Run:  source venv/bin/activate && python tests/auto/test_popup_refresh.py
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
            if d.execute_script("return typeof state!=='undefined' && !!(state.grid && state.inputs && state.holland);"):
                break

        def sel(i, v):
            d.execute_script("const e=document.getElementById(arguments[0]);e.value=arguments[1];"
                             "e.dispatchEvent(new Event('change'));", i, v); time.sleep(0.4)

        sel("model", "holland")
        # 1. open a windfield popup on a land vertex
        d.execute_script("openWindfieldPopup(state.grid.points.findIndex("
                         "p=>p.land&&p.ew===30&&p.ns===0));")
        time.sleep(0.3)
        t0 = d.execute_script("return document.querySelector('.wf-panel .ap-title').textContent;")
        print("popup title @open:", t0)
        if "holland" not in t0.lower():
            fail.append(f"popup title should name holland at open, got {t0}")

        # 2. change model -> popup updates
        sel("model", "willoughby")
        t1 = d.execute_script("return document.querySelector('.wf-panel .ap-title').textContent;")
        svgs = d.execute_script("return document.querySelector('.wf-panel .ap-body').querySelectorAll('svg').length;")
        print("popup title after model->willoughby:", t1, "| svgs:", svgs)
        if "willoughby" not in t1.lower():
            fail.append(f"popup title should update to willoughby, got {t1}")
        if svgs < 2:
            fail.append(f"popup body should re-render its 2 SVGs, got {svgs}")

        # 3. change category -> popup updates
        sel("category", "1")
        t2 = d.execute_script("return document.querySelector('.wf-panel .ap-title').textContent;")
        print("popup title after category->1:", t2)
        if "CAT1" not in t2:
            fail.append(f"popup title should show CAT1, got {t2}")

        # 4. two popups both update
        d.execute_script("openWindfieldPopup(state.grid.points.findIndex("
                         "p=>p.land&&p.ew===60&&p.ns===0));")
        time.sleep(0.3)
        sel("model", "holland")
        titles = d.execute_script("return [...document.querySelectorAll('.wf-panel .ap-title')].map(t=>t.textContent);")
        print("both popup titles after model->holland:", titles)
        if len(titles) != 2 or not all("holland" in t.lower() for t in titles):
            fail.append(f"both popups should update to holland, got {titles}")

        # 5. POI detail panel refreshes
        d.execute_script("poiOpenDetail(state.grid.points.findIndex(p=>p.land&&p.ew===9&&p.ns===15));")
        time.sleep(0.3)
        body0 = d.execute_script("return poi.panel.body.textContent;")
        sel("model", "willoughby")
        body1 = d.execute_script("return poi.panel.body.textContent;")
        vis = d.execute_script("return poi.panel.el.style.display!=='none';")
        print("POI detail refreshed (body changed):", body0 != body1, "| visible:", vis)
        if not vis:
            fail.append("POI detail panel should stay open")
        if body0 == body1:
            fail.append("POI detail body should re-render on model change (loss/wind differ)")

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
