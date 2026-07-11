#!/usr/bin/env python3
"""Selenium check for the Sobol' sensitivity view.

Drives the real viewer: opens Statistics -> Sensitivity, switches the method tab
from SRC to Sobol', and verifies the panel actually renders bars (not an error or
an empty SVG). Also exercises the per-category redraw and the SRC round-trip, and
fails on any browser console error.

Run:  ./venv/bin/python tests/auto/check_sobol.py
"""
import sys, time, os
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8012/web/index.html"
HERE = os.path.dirname(os.path.abspath(__file__))
LOG = open(os.path.join(HERE, "check_sobol.log"), "w")


def log(msg):
    print(msg)
    LOG.write(msg + "\n")
    LOG.flush()


opts = Options()
opts.add_argument("--headless=new")
opts.add_argument("--window-size=1500,1050")
opts.set_capability("goog:loggingPrefs", {"browser": "ALL"})

drv = webdriver.Chrome(options=opts)
failures = []
try:
    drv.get(URL)

    # 1. wait for the app to finish loading its JSON (grid + metamodels)
    deadline = time.time() + 25
    while time.time() < deadline:
        if "Loading" not in drv.find_element(By.ID, "info").text:
            break
        time.sleep(0.5)
    # state is module-scoped, not on window, so probe the network instead: the app
    # cannot render Sobol' at all unless this file fetched successfully.
    mm = drv.execute_script(
        "return await fetch('../outputs/web/metamodels.json')"
        "  .then(r => r.ok ? r.json() : null).then(j => j ? Object.keys(j.responses) : null)"
        "  .catch(() => null)")
    log(f"[1] metamodels.json reachable; responses = {mm}")
    if not mm:
        failures.append("metamodels.json not reachable from the page")

    # 2. open Statistics -> Sensitivity (SRC)
    drv.execute_script("document.querySelector('[data-grp=grpStats]').click()")
    time.sleep(0.3)
    WebDriverWait(drv, 10).until(EC.element_to_be_clickable((By.ID, "btnSRC"))).click()
    WebDriverWait(drv, 10).until(
        lambda d: d.execute_script("return !!document.querySelector('.sa-tabs')"))
    tabs = drv.find_elements(By.CSS_SELECTOR, ".sa-tab")
    log(f"[2] Sensitivity panel open; method tabs = {[t.text for t in tabs]}")
    if len(tabs) != 2:
        failures.append(f"expected 2 method tabs, got {len(tabs)}")

    # 3. switch to Sobol' and confirm real bars render
    [t for t in tabs if "Sobol" in t.text][0].click()
    time.sleep(0.6)
    bars = drv.execute_script(
        "const p=[...document.querySelectorAll('.ap-body')].find(b=>b.querySelector('.sa-tab.on')?.dataset.m==='sobol');"
        "return p ? p.querySelectorAll('svg rect').length : -1")
    note = drv.execute_script(
        "const p=[...document.querySelectorAll('.ap-body')].find(b=>b.querySelector('.sa-tab.on'));"
        "return p ? p.querySelector('.note').innerText : ''")
    title = drv.execute_script(
        "return [...document.querySelectorAll('.ap-title,.ap-head')].map(e=>e.innerText).join(' | ')")
    drv.save_screenshot(os.path.join(HERE, "sobol_panel.png"))
    log(f"[3] Sobol' view: {bars} <rect> bars rendered")
    log(f"    title: {title}")
    log(f"    note: {note.replace(chr(10), ' / ')}")
    if bars < 6:
        failures.append(f"expected >=6 bars (6 inputs), got {bars}")
    if "SigmaS" not in note.replace(" ", "") and "S1" not in note and "Σ" not in note:
        failures.append("note is missing the sum(S1) summary")

    # 4. per-category redraw (Sobol' is per-category; SRC spans all three)
    for cat in ("1", "3", "5"):
        drv.execute_script(
            "const s=document.getElementById('category'); s.value=arguments[0];"
            "s.dispatchEvent(new Event('change'));", cat)
        time.sleep(0.5)
        n = drv.execute_script(
            "const p=[...document.querySelectorAll('.ap-body')].find(b=>b.querySelector('.sa-tab.on')?.dataset.m==='sobol');"
            "return p ? p.querySelectorAll('svg rect').length : -1")
        log(f"[4] Cat {cat}: {n} bars after category change")
        if n < 6:
            failures.append(f"cat{cat}: Sobol' panel lost its bars on redraw ({n})")

    # 5. switch back to SRC — the line chart must return
    drv.execute_script(
        "[...document.querySelectorAll('.sa-tab')].find(t=>t.dataset.m==='src').click()")
    time.sleep(0.5)
    lines = drv.execute_script(
        "const p=[...document.querySelectorAll('.ap-body')].find(b=>b.querySelector('.sa-tab.on')?.dataset.m==='src');"
        "return p ? p.querySelectorAll('svg polyline').length : -1")
    log(f"[5] back to SRC: {lines} polylines (expect 6, one per input)")
    if lines != 6:
        failures.append(f"SRC round-trip broken: {lines} polylines, expected 6")

    # 6. EPR must no longer be contaminated by Sobol'
    drv.find_element(By.ID, "btnEPR").click()
    time.sleep(0.6)
    epr_note = drv.execute_script(
        "const ps=[...document.querySelectorAll('.ap-body')];"
        "const p=ps.find(b=>b.innerText.includes('EPR'));"
        "return p ? p.innerText : ''")
    has_sobol = "Sobol" in epr_note
    log(f"[6] EPR panel mentions Sobol'? {has_sobol} (must be False)")
    if has_sobol:
        failures.append("EPR panel still references Sobol' (un-hijack failed)")

    # 6b. Sobol' annotations in the profiler + interaction matrix.
    # These are gated to the emulator's config: Powell + roughness, decay OFF.
    drv.execute_script(
        "document.getElementById('model').value='powell';"
        "document.getElementById('model').dispatchEvent(new Event('change'));"
        "const d=document.getElementById('landDecay');"
        "if (d.checked) { d.checked=false; d.dispatchEvent(new Event('change')); }"
        "const r=document.getElementById('landRoughness');"
        "if (!r.checked) { r.checked=true; r.dispatchEvent(new Event('change')); }")
    time.sleep(1.0)
    drv.find_element(By.ID, "btnProf").click()
    time.sleep(1.2)
    prof_lbl = drv.execute_script(
        "const g=document.querySelector('.prof-grid');"
        "return g ? [...g.querySelectorAll('text')].map(t=>t.textContent)"
        "  .filter(t=>t.includes('S₁')).length : -1")
    log(f"[6b] profiler columns carrying S₁/Sₜ labels: {prof_lbl} (expect 6)")
    if prof_lbl != 6:
        failures.append(f"profiler Sobol' labels: {prof_lbl}, expected 6")

    # switch the profiler to its matrix view
    drv.execute_script(
        "[...document.querySelectorAll('.prof-tab')].find(t=>t.dataset.view==='matrix').click()")
    time.sleep(1.5)
    cells = drv.execute_script(
        "const g=document.querySelector('.prof-matrix');"
        "if(!g) return null;"
        "const c=[...g.querySelectorAll('.prof-cell')];"
        "return {tinted: c.filter(e=>e.style.background && e.style.background!=='none').length,"
        " sij: c.filter(e=>[...e.querySelectorAll('text')].some(t=>t.textContent.startsWith('S=')))"
        "        .length,"
        " blue: g.innerHTML.split('#3b82f6').length-1, red: g.innerHTML.split('#ef4444').length-1}")
    log(f"[6c] matrix: {cells}")
    if not cells or cells["sij"] != 30:      # 6x6 minus 6 diagonal = 30 off-diagonal
        failures.append(f"matrix S_ij annotations: {cells}")
    # temperature metaphor: every off-diagonal cell draws a blue (min) and red (max) curve
    if not cells or cells["blue"] < 30 or cells["red"] < 30:
        failures.append(f"matrix low/high colors wrong: {cells}")
    drv.save_screenshot(os.path.join(HERE, "sobol_matrix.png"))

    # 7. console errors (favicon 404 is a browser artifact, not an app error)
    errs = [e for e in drv.get_log("browser")
            if e["level"] == "SEVERE" and "favicon" not in e["message"]]
    log(f"[7] severe console errors: {len(errs)}")
    for e in errs[:5]:
        log("    " + e["message"][:160])
        failures.append("console error: " + e["message"][:80])

finally:
    drv.quit()

log("\n" + ("FAILED:\n  - " + "\n  - ".join(failures) if failures else "ALL CHECKS PASSED"))
LOG.close()
sys.exit(1 if failures else 0)
