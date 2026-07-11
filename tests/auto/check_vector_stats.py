#!/usr/bin/env python3
"""Selenium check: the map always shows ONE input vector.

Verifies the statistician's requirement: no across-vector aggregation on the map,
the input-vector slider is always live, and the Status panel reports the outputs
that ARE of interest -- aggregates over the land grid points for the current
vector (total loss cost, and the spatial mean/max of peak wind).

Also checks light is the default theme.

Run:  ./venv/bin/python tests/auto/check_vector_stats.py
"""
import sys, time, os, re
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8012/web/index.html"
HERE = os.path.dirname(os.path.abspath(__file__))
LOG = open(os.path.join(HERE, "check_vector_stats.log"), "w")


def log(m):
    print(m)
    LOG.write(m + "\n")
    LOG.flush()


opts = Options()
opts.add_argument("--headless=new")
opts.add_argument("--window-size=1500,1050")
opts.set_capability("goog:loggingPrefs", {"browser": "ALL"})
drv = webdriver.Chrome(options=opts)
fails = []
try:
    drv.get(URL)
    deadline = time.time() + 25
    while time.time() < deadline:
        if "Loading" not in drv.find_element(By.ID, "info").text:
            break
        time.sleep(0.5)
    time.sleep(1.0)

    # 1. the Mean/Max across-vector buttons are gone
    gone = drv.execute_script(
        "return !document.getElementById('btnMean') && !document.getElementById('btnMax')")
    log(f"[1] across-vector Mean/Max buttons removed: {gone}")
    if not gone:
        fails.append("btnMean/btnMax still present")

    # 2. the slider is live at startup (it used to open disabled in mean mode)
    dis = drv.execute_script("return document.getElementById('vector').disabled")
    log(f"[2] input-vector slider disabled at startup: {dis} (must be False)")
    if dis:
        fails.append("vector slider still starts disabled")

    # 3. light is the default theme
    theme = drv.execute_script("return document.getElementById('theme').value")
    light = drv.execute_script("return document.body.classList.contains('theme-light')")
    log(f"[3] default theme = {theme!r}, body.theme-light = {light}")
    if theme != "light" or not light:
        fails.append(f"default theme is not light (value={theme}, class={light})")

    # 4. Status reports per-vector spatial outputs, and they CHANGE with the vector
    def status():
        return drv.find_element(By.ID, "info").text

    seen = {}
    for v in ("1", "50", "100"):
        drv.execute_script(
            "const s=document.getElementById('vector'); s.value=arguments[0];"
            "s.dispatchEvent(new Event('input'));", v)
        time.sleep(0.8)
        txt = status()
        m = re.search(r"wind mean\s+([\d.]+)\s+·\s+max\s+([\d.]+)", txt)
        seen[v] = (txt, m.groups() if m else None)
        log(f"[4] vector {v:>3}: {txt.replace(chr(10), ' | ')}")
        if not m:
            fails.append(f"vector {v}: no spatial wind mean/max in Status")
        if f"v{v}" not in txt:
            fails.append(f"vector {v}: Status does not name the vector")

    vals = [seen[v][1] for v in seen if seen[v][1]]
    if len(set(vals)) < len(vals):
        fails.append("spatial stats identical across vectors — slider not driving the field")
    else:
        log("[5] spatial stats differ per vector (slider drives the field)")

    # 6. the loss view reports TLC over the land points for this vector
    drv.execute_script(
        "const c=document.getElementById('colorBy'); c.value='loss';"
        "c.dispatchEvent(new Event('change'));")
    time.sleep(1.0)
    txt = status()
    log(f"[6] loss view: {txt.replace(chr(10), ' | ')}")
    if "TLC" not in txt:
        fails.append("loss view does not report TLC")

    drv.save_screenshot(os.path.join(HERE, "vector_stats.png"))

    errs = [e for e in drv.get_log("browser")
            if e["level"] == "SEVERE" and "favicon" not in e["message"]]
    log(f"[7] severe console errors: {len(errs)}")
    for e in errs[:5]:
        log("    " + e["message"][:160])
        fails.append("console error: " + e["message"][:80])
finally:
    drv.quit()

log("\n" + ("FAILED:\n  - " + "\n  - ".join(fails) if fails else "ALL CHECKS PASSED"))
LOG.close()
sys.exit(1 if fails else 0)
