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
                          "colorBy": "wind", "display": "points", "roughness": True}, None),
    ("powell_cat5_contour", {"model": "powell", "category": "5", "display": "contour"}, None),
    ("holland_cat3_contour", {"model": "holland", "category": "3", "display": "contour"}, None),
    ("willoughby_cat5_contour", {"model": "willoughby", "category": "5", "display": "contour"}, None),
    ("light_theme",      {"theme": "light", "model": "powell", "category": "3",
                          "display": "points", "colorBy": "wind"}, None),
    ("analysis_src",     {"model": "powell", "_btn": "btnSRC"}, ".analysis-panel"),
    ("analysis_epr",     {"model": "powell", "_btn": "btnEPR"}, ".analysis-panel"),
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
        if k == "_btn":
            continue
        driver.execute_script(JS_SET, k, str(v) if not isinstance(v, bool) else v)
        time.sleep(0.15)
    if "_btn" in controls:
        driver.execute_script(f"document.getElementById('{controls['_btn']}').click();")


def main():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1500,950")
    opts.add_argument("--force-device-scale-factor=2")
    opts.add_argument("--hide-scrollbars")
    drv = webdriver.Chrome(options=opts)
    try:
        for name, controls, sel in FIGURES:
            drv.get(URL)
            WebDriverWait(drv, 20).until(
                lambda d: "Loading" not in d.find_element(By.ID, "info").text)
            apply(drv, controls)
            time.sleep(2.5)  # render + tiles
            out = FIG / f"{name}.png"
            if sel:
                # enlarge the floating window so chart + legend + note all show
                drv.execute_script(
                    "const p=document.querySelector('.analysis-panel');"
                    "if(p){p.style.width='540px';p.style.height='560px';}")
                time.sleep(0.4)
                drv.find_element(By.CSS_SELECTOR, sel).screenshot(str(out))
            else:
                drv.save_screenshot(str(out))
            print(f"  saved {out.name} ({out.stat().st_size/1024:.0f} KB)")
    finally:
        drv.quit()
    print(f"Done. {len(FIGURES)} figures in {FIG}")


if __name__ == "__main__":
    main()
