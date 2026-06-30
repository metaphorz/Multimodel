#!/usr/bin/env python3
"""Capture LaTeX figures from the running Form S-6 viewer with Selenium.

Drives headless Chrome to the app, sets the sidebar controls for each figure,
waits for the windfield to render, and saves high-DPI PNGs into docs/figures/.
Analysis figures are captured as element screenshots of the floating window.

Prereq: the server must be running (./start). Then:
    ./venv/bin/python docs/capture_figures.py

Author: Paul Fishwick and Claude Code
"""
import time
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

ROOT = Path(__file__).resolve().parents[1]
FIG = ROOT / "docs" / "figures"
FIG.mkdir(parents=True, exist_ok=True)
URL = "http://localhost:8012/web/index.html"

# each figure: (filename, controls dict, optional element-capture selector)
FIGURES = [
    ("grid_basemap",     {"model": "holland", "colorBy": "landwater", "display": "points"}, None),
    ("powell_cat5_pts",  {"model": "powell", "category": "5", "vector": 1,
                          "colorBy": "wind", "display": "points",
                          "landRoughness": True, "landDecay": False}, None),
    ("powell_cat5_contour", {"model": "powell", "category": "5", "display": "contour"}, None),
    ("holland_cat3_contour", {"model": "holland", "category": "3", "display": "contour"}, None),
    ("willoughby_cat5_contour", {"model": "willoughby", "category": "5", "display": "contour"}, None),
    ("powell_cat5_kd",   {"model": "powell", "category": "5",
                          "landRoughness": False, "landDecay": True,
                          "colorBy": "wind", "display": "contour"}, None),
    ("powell_cat5_loss", {"model": "powell", "category": "5",
                          "landRoughness": False, "landDecay": True,
                          "colorBy": "loss", "display": "contour"}, None),
    ("light_theme",      {"theme": "light", "model": "powell", "category": "3",
                          "display": "points", "colorBy": "wind"}, None),
    ("analysis_src",     {"model": "powell", "_btn": "btnSRC"}, ".analysis-panel"),
    ("analysis_epr",     {"model": "powell", "_btn": "btnEPR"}, ".analysis-panel"),
    ("analysis_profiler", {"model": "powell", "category": "5", "_btn": "btnProf"}, ".analysis-panel"),
    ("analysis_matrix", {"model": "powell", "category": "5", "_btn": "btnProf",
                        "_js": "[...document.querySelectorAll('.prof-tab')]"
                               ".find(b=>b.dataset.view==='matrix').click();"}, ".analysis-panel"),
    ("analysis_matrix_point", {"model": "powell", "category": "5", "_btn": "btnProf",
                        "_js": "document.getElementById('model').value='holland';"
                               "document.getElementById('model').dispatchEvent(new Event('change'));"
                               "document.getElementById('response').value='tlc';"
                               "document.getElementById('response').dispatchEvent(new Event('change'));"
                               "profilerState.scale='point';profilerState.view='matrix';"
                               "profilerPickPoint(state.grid.points.findIndex(p=>p.ew===6&&p.ns===33));"},
                        ".analysis-panel"),
    ("analysis_tlc_cdf",  {"model": "powell", "category": "5", "response": "tlc",
                          "_btn": "btnCDF"}, ".analysis-panel"),
    ("analysis_compare",  {"model": "powell", "category": "5", "_btn": "btnCompare"}, ".analysis-panel"),
    ("grid_sensitivity",  {"model": "powell", "category": "1",
                          "colorBy": "sensitivity", "display": "points"}, None),
    ("windfield_popup",  {"model": "holland", "category": "5", "_click": [30, 0]}, ".wf-panel"),
    ("points_of_interest", {"model": "powell", "category": "5", "colorBy": "wind",
                          "display": "points",
                          "_js": "poiOpenDetail(poiGridIdx(9,15));"}, None),
    ("analysis_financial", {"model": "powell", "category": "5",
                          "_js": "openPanel('fin');"}, ".analysis-panel"),
]

JS_SET = """
const [id, val] = arguments;
const el = document.getElementById(id);
if (el.type === 'checkbox') { el.checked = val; }
else { el.value = val; }
el.dispatchEvent(new Event(el.type === 'range' ? 'input' : 'change'));
"""


def apply(driver, controls):
    for k, v in controls.items():
        if k in ("_btn", "_click", "_js"):
            continue
        driver.execute_script(JS_SET, k, str(v) if not isinstance(v, bool) else v)
        time.sleep(0.15)
    if "_btn" in controls:
        driver.execute_script(f"document.getElementById('{controls['_btn']}').click();")
    if "_click" in controls:
        ew, ns = controls["_click"]
        driver.execute_script(
            "const i=state.grid.points.findIndex(p=>p.land&&p.ns===arguments[1]&&p.ew===arguments[0]);"
            "if(i>=0) openWindfieldPopup(i);", ew, ns)
    if "_js" in controls:
        time.sleep(1.0)   # let any panel opened by _btn finish rendering first
        driver.execute_script(controls["_js"])


def main():
    # optional name filter: `capture_figures.py analysis_src analysis_epr` regenerates
    # only those figures (else all). Lets a small change refresh just its figures.
    import sys
    wanted = set(sys.argv[1:])
    figures = [f for f in FIGURES if not wanted or f[0] in wanted]

    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1500,950")
    opts.add_argument("--force-device-scale-factor=2")
    opts.add_argument("--hide-scrollbars")
    drv = webdriver.Chrome(options=opts)
    drv.set_script_timeout(180)   # live Holland/Willoughby _js figures recompute
                                  # 100 vectors over the 36-h window (can take ~15-30s)
    try:
        for name, controls, sel in figures:
            drv.get(URL)
            WebDriverWait(drv, 20).until(
                lambda d: "Loading" not in d.find_element(By.ID, "info").text)
            # viewer now opens in Mean view by default; these figures are single-vector,
            # so turn Mean off (re-enabling the slider) before applying controls.
            drv.execute_script(
                "if (state.meanMode) document.getElementById('btnMean').click();")
            time.sleep(0.2)
            apply(drv, controls)
            time.sleep(2.5)  # render + tiles
            out = FIG / f"{name}.png"
            if sel:
                # enlarge the floating window so chart + legend + note all show
                # (the financial panel has controls + plot + table -> taller)
                h = "700px" if name in ("analysis_financial", "analysis_matrix",
                                         "analysis_matrix_point") else "560px"
                drv.execute_script(
                    "const p=document.querySelector('.analysis-panel');"
                    f"if(p){{p.style.width='540px';p.style.height='{h}';}}")
                time.sleep(0.4)
                drv.find_element(By.CSS_SELECTOR, sel).screenshot(str(out))
            else:
                drv.save_screenshot(str(out))
            print(f"  saved {out.name} ({out.stat().st_size/1024:.0f} KB)")
    finally:
        drv.quit()
    print(f"Done. {len(figures)} figures in {FIG}")


if __name__ == "__main__":
    main()
