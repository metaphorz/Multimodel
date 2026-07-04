#!/usr/bin/env python3
"""Verify the storm track is visible over filled contours in dark + light themes.

Drives headless Chrome (same pattern as docs/capture_figures.py) to the running
app, switches to Filled-contour display, and screenshots both themes so the
track-contrast fix can be eyeballed.

Prereq: server running (./start). Then:
    ./venv/bin/python tests/auto/verify_track_contrast.py

Author: Pro Team & Claude Code
"""
import time
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "tests" / "auto"
URL = "http://localhost:8012/web/index.html"

JS_SET = """
const [id, val] = arguments;
const el = document.getElementById(id);
if (el.type === 'checkbox') { el.checked = val; }
else { el.value = val; }
el.dispatchEvent(new Event(el.type === 'range' ? 'input' : 'change'));
"""

CASES = [
    ("track_contour_dark",  {"theme": "dark",  "model": "powell", "category": "5", "display": "contour"}),
    ("track_contour_light", {"theme": "light", "model": "powell", "category": "5", "display": "contour"}),
]


def main():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1500,950")
    opts.add_argument("--force-device-scale-factor=2")
    opts.add_argument("--hide-scrollbars")
    drv = webdriver.Chrome(options=opts)
    try:
        for name, controls in CASES:
            drv.get(URL)
            WebDriverWait(drv, 20).until(
                lambda d: "Loading" not in d.find_element(By.ID, "info").text)
            for k, v in controls.items():
                drv.execute_script(JS_SET, k, str(v))
                time.sleep(0.2)
            time.sleep(2.5)  # render + tiles
            out = OUT / f"{name}.png"
            drv.save_screenshot(str(out))
            print(f"  saved {out.name} ({out.stat().st_size/1024:.0f} KB)")
    finally:
        drv.quit()
    print("Done.")


if __name__ == "__main__":
    main()
