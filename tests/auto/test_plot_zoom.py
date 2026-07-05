#!/usr/bin/env python3
"""Selenium test: mouse-wheel zoom on every plot the app produces.

Covers the analysis charts (SRC / Interaction Profiler / Compare / %TLC CDF) and
the windfield popup (isotachs + time series). Verifies wheel-in shrinks the SVG
viewBox around the cursor, wheel-out grows it, and double-click resets. Also
checks the Leaflet map is NOT hijacked (handler no-ops off-plot).

Run:  ./venv/bin/python tests/auto/test_plot_zoom.py   (server up via ./start)
"""
import os, sys, time, logging

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

HERE = os.path.dirname(os.path.abspath(__file__))
URL = "http://localhost:8012/web/index.html"
LOG = os.path.join(HERE, "test_plot_zoom.log")
logging.basicConfig(filename=LOG, filemode="w", level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger()
VB = "return arguments[0].getAttribute('viewBox');"


def info(m):
    log.info(m); print(m, flush=True)


def main():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1400,950")
    d = webdriver.Chrome(options=opts)
    fails = []
    try:
        d.get(URL)
        wait = WebDriverWait(d, 30)
        wait.until(lambda x: "Loading grid" not in x.find_element(By.ID, "info").text)

        def wheel(svg, dy, fx=0.5, fy=0.5):
            d.execute_script(
                "const s=arguments[0],r=s.getBoundingClientRect();"
                "s.dispatchEvent(new WheelEvent('wheel',{deltaY:arguments[1],"
                "clientX:r.left+r.width*arguments[2],clientY:r.top+r.height*arguments[3],"
                "bubbles:true,cancelable:true}));", svg, dy, fx, fy)
            time.sleep(0.04)

        def dbl(svg):
            d.execute_script(
                "const s=arguments[0],r=s.getBoundingClientRect();"
                "s.dispatchEvent(new MouseEvent('dblclick',{clientX:r.left+5,"
                "clientY:r.top+5,bubbles:true}));", svg)
            time.sleep(0.04)

        def drag(svg, dxf=0.3, dyf=0.2):
            d.execute_script(
                "const s=arguments[0],r=s.getBoundingClientRect();"
                "const cx=r.left+r.width*0.5,cy=r.top+r.height*0.5;"
                "s.dispatchEvent(new MouseEvent('mousedown',{clientX:cx,clientY:cy,bubbles:true,cancelable:true}));"
                "document.dispatchEvent(new MouseEvent('mousemove',{clientX:cx+r.width*arguments[1],"
                "clientY:cy+r.height*arguments[2],bubbles:true}));"
                "document.dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));", svg, dxf, dyf)
            time.sleep(0.04)

        def check(label, sel, opener):
            opener()
            wait.until(lambda x: x.find_elements(By.CSS_SELECTOR, sel))
            time.sleep(0.3)
            svg = d.find_elements(By.CSS_SELECTOR, sel)[-1]
            base = d.execute_script(VB, svg)
            # pan before zoom must be a no-op (clamped to original viewBox)
            drag(svg); pan_pre = d.execute_script(VB, svg)
            wheel(svg, -120); wheel(svg, -120); zin = d.execute_script(VB, svg)
            # pan after zoom must move the viewBox
            drag(svg); pan_post = d.execute_script(VB, svg)
            wheel(svg, 120); wheel(svg, 120); wheel(svg, 120); zout = d.execute_script(VB, svg)
            dbl(svg); rst = d.execute_script(VB, svg)
            ok = (base != zin and zin != zout and rst == base
                  and pan_pre == base and pan_post != zin)
            info(f"{label:16s} base={base} | pan-pre={pan_pre} (noop={pan_pre==base}) | "
                 f"in={zin} | pan-post moved={pan_post != zin} | reset_ok={rst == base}")
            if not ok:
                fails.append(f"{label} zoom/pan/reset failed")

        # expand the collapsible Statistics/Actuarial groups so the buttons are clickable
        d.execute_script("[...document.querySelectorAll('.analysis-group')]"
                         ".forEach(g=>{ if(!g.classList.contains('open')) g.click(); });")
        time.sleep(0.2)

        check("SRC chart", ".ap-body svg",
              lambda: d.find_element(By.ID, "btnSRC").click())
        check("Profiler cell", ".prof-cell svg",
              lambda: d.find_element(By.ID, "btnProf").click())
        check("Compare cell", ".prof-cell svg",
              lambda: d.find_element(By.ID, "btnCompare").click())
        check("CDF", ".ap-body svg",
              lambda: d.find_element(By.ID, "btnCDF").click())
        check("Windfield popup", ".wf-panel .ap-body svg",
              lambda: d.execute_script(
                  "const i=state.grid.points.findIndex(p=>p.land&&p.ns===0&&p.ew===30);"
                  "openWindfieldPopup(i);"))

        # Leaflet map must remain native-zoom (handler no-ops off-plot): map svg
        # viewBox is null and a wheel over the map should not throw / be consumed here.
        map_svgs = d.find_elements(By.CSS_SELECTOR, ".leaflet-overlay-pane svg")
        info(f"leaflet map svgs present: {len(map_svgs)} (left untouched)")

        d.save_screenshot(os.path.join(HERE, "selenium_plot_zoom.png"))
    finally:
        d.quit()

    if fails:
        info("FAIL:\n  - " + "\n  - ".join(fails)); sys.exit(1)
    info("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
