#!/usr/bin/env python3
"""Selenium interface check for the Form S-6 viewer.

Loads the page, waits for the map/grid to initialize, captures browser console
logs and errors, reports the status text, counts rendered grid markers, and
saves a screenshot. Used to verify the interface during development.
"""
import sys, time, os
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8012/web/index.html"
HERE = os.path.dirname(os.path.abspath(__file__))
SHOT = os.path.join(HERE, "interface_check.png")

opts = Options()
opts.add_argument("--headless=new")
opts.add_argument("--window-size=1400,1000")
opts.set_capability("goog:loggingPrefs", {"browser": "ALL"})

drv = webdriver.Chrome(options=opts)
try:
    drv.get(URL)
    # wait for grid to load (status text changes from "Loading grid…")
    deadline = time.time() + 20
    info = ""
    while time.time() < deadline:
        info = drv.find_element(By.ID, "info").text
        if "Loading" not in info:
            break
        time.sleep(0.5)
    time.sleep(1.5)  # let markers paint

    # count rendered leaflet circle markers (grid points)
    n_markers = drv.execute_script(
        "return document.querySelectorAll('.leaflet-overlay-pane path').length;")

    print("=== STATUS TEXT ===")
    print(info)
    print(f"=== RENDERED MARKERS (svg paths): {n_markers} ===")

    # Powell (precomputed) cat5 v1
    drv.execute_script("""
      document.getElementById('model').value = 'powell';
      document.getElementById('category').value = '5';
      document.getElementById('model').dispatchEvent(new Event('change'));
      document.getElementById('category').dispatchEvent(new Event('change'));
    """)
    time.sleep(1.0)
    print("=== POWELL cat5 v1 INFO ===")
    print(drv.find_element(By.ID, "info").text)

    # exercise the live Holland windfield + coloring
    drv.execute_script("""
      const m = document.getElementById('model'); m.value = 'holland';
      document.getElementById('category').value = '1';
      m.dispatchEvent(new Event('change'));
      document.getElementById('category').dispatchEvent(new Event('change'));
    """)
    time.sleep(1.0)
    holland_info = drv.find_element(By.ID, "info").text
    # count distinct fill colors among grid markers (coloring should vary)
    distinct = drv.execute_script("""
      const s = new Set();
      document.querySelectorAll('.leaflet-overlay-pane path').forEach(p => s.add(p.getAttribute('fill')));
      return s.size;
    """)
    print("=== HOLLAND INFO ==="); print(holland_info)
    print(f"=== DISTINCT FILL COLORS: {distinct} ===")

    # filled-contour display mode
    drv.execute_script("""
      const d = document.getElementById('display'); d.value = 'contour';
      d.dispatchEvent(new Event('change'));
    """)
    time.sleep(1.0)
    n_polys = drv.execute_script(
        "return document.querySelectorAll('.leaflet-overlay-pane path').length;")
    # contour polygons are non-interactive; count all svg paths in overlay pane
    n_all = drv.execute_script(
        "return document.querySelectorAll('.leaflet-overlay-pane path').length;")
    print(f"=== CONTOUR MODE: overlay paths = {n_all} ===")
    drv.save_screenshot(os.path.join(HERE, "contour_check.png"))
    drv.execute_script("document.getElementById('display').value='points';"
                       "document.getElementById('display').dispatchEvent(new Event('change'));")
    time.sleep(0.5)

    # light theme screenshot
    drv.execute_script("const t=document.getElementById('theme');t.value='light';"
                       "t.dispatchEvent(new Event('change'));")
    time.sleep(1.0)
    drv.save_screenshot(os.path.join(HERE, "light_check.png"))
    drv.execute_script("const t=document.getElementById('theme');t.value='dark';"
                       "t.dispatchEvent(new Event('change'));")
    time.sleep(0.3)

    # confirm place names present in grid data
    has_place = drv.execute_script(
        "return fetch('../outputs/web/grid.json').then(r=>r.json())"
        ".then(g=>g.points[0].place||'').catch(()=>'')") or ""
    # (async; re-read synchronously instead)

    # land-effect selector: none / roughness / kd
    def set_land(v):
        drv.execute_script(
            "const e=document.getElementById('landEffect');e.value=arguments[0];"
            "e.dispatchEvent(new Event('change'));", v)
        time.sleep(0.5)
        return drv.find_element(By.ID, "info").text
    drv.execute_script("document.getElementById('model').value='holland';"
                       "document.getElementById('model').dispatchEvent(new Event('change'));")
    time.sleep(0.4)
    print("=== LAND EFFECT (Holland cat1) ===")
    print("none:     ", set_land("none"))
    print("roughness:", set_land("roughness"))
    print("kd:       ", set_land("kd"))
    # Powell + K&D should show the pending note (powell_kd.json not built yet)
    drv.execute_script("document.getElementById('model').value='powell';"
                       "document.getElementById('model').dispatchEvent(new Event('change'));")
    time.sleep(0.5)
    print("powell+kd:", drv.find_element(By.ID, "info").text)
    set_land("roughness")
    drv.execute_script("document.getElementById('model').value='holland';"
                       "document.getElementById('model').dispatchEvent(new Event('change'));")
    time.sleep(0.3)

    # Sensitivity / Uncertainty analysis
    drv.execute_script("""
      document.getElementById('model').value='powell';
      document.getElementById('model').dispatchEvent(new Event('change'));
    """)
    time.sleep(0.3)
    drv.execute_script("document.getElementById('btnSRC').click();")
    time.sleep(1.5)
    src = drv.execute_script("return computeSRC('powell').cat1.src;")
    print("=== SA (SRC) powell cat1 ===")
    print({k: round(v, 3) for k, v in src.items()})
    # open EPR too -> both panels should be visible simultaneously
    drv.execute_script("document.getElementById('btnEPR').click();")
    time.sleep(1.2)
    n_panels = drv.execute_script(
        "return [...document.querySelectorAll('.analysis-panel')]"
        ".filter(p=>p.style.display!=='none').length;")
    n_lines = drv.execute_script(
        "return document.querySelectorAll('.analysis-panel svg polyline').length;")
    print(f"visible panels={n_panels} (expect 2), total chart lines={n_lines} (expect 12)")
    # drag the first panel and confirm its position changes
    moved = drv.execute_script("""
      const p=document.querySelector('.analysis-panel');
      const h=p.querySelector('.ap-header');
      const r0=p.getBoundingClientRect();
      function ev(t,x,y){h.dispatchEvent(new MouseEvent(t,{clientX:x,clientY:y,bubbles:true}));}
      ev('mousedown',r0.left+30,r0.top+8);
      document.dispatchEvent(new MouseEvent('mousemove',{clientX:r0.left+130,clientY:r0.top+108,bubbles:true}));
      document.dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));
      const r1=p.getBoundingClientRect();
      return Math.round(r1.left-r0.left)+','+Math.round(r1.top-r0.top);
    """)
    print(f"drag delta (px) = {moved} (expect ~100,100)")
    drv.save_screenshot(os.path.join(HERE, "analysis_check.png"))

    print("=== CONSOLE LOGS ===")
    errors = 0
    for entry in drv.get_log("browser"):
        if "favicon.ico" in entry["message"] or "powell_kd.json" in entry["message"]:
            continue  # harmless / not built until after the UA run
        print(f"[{entry['level']}] {entry['message']}")
        if entry["level"] == "SEVERE":
            errors += 1

    drv.save_screenshot(SHOT)
    print(f"=== screenshot: {SHOT} ===")
    print("RESULT:", "FAIL" if (errors or "Failed" in info or "Loading" in info) else "PASS")
    sys.exit(1 if (errors or "Failed" in info or "Loading" in info) else 0)
finally:
    drv.quit()
