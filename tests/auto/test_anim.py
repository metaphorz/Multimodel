#!/usr/bin/env python3
"""Selenium test: optional storm animation (east->west, extended domain + zoom).

  1. #simBar exists; nothing animates by default (ANIM.active false, no animContour).
  2. Play enters sim mode: extended domain (> 840 pts), 73 frames, animContour +
     eye marker present, and the map ZOOMS OUT (zoom decreases) to show the approach.
  3. At frame 0 (t=-12) the eye is offshore — east of the grid's east edge
     (lon > -79.975); the time readout reads t=-12.0 h. Last frame reads t=+24.0 h.
  4. Reset exits: animContour gone, markers visible again, and the map returns to
     the saved (default) zoom.
  5. All three models precompute without error (Holland/Willoughby/Powell).
  6. Changing a sidebar control while animating drops out of sim mode.
  7. No severe console errors.

Run:  source venv/bin/activate && python tests/auto/test_anim.py
"""
import sys
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

URL = "http://localhost:8012/web/index.html"


def main():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1500,1100")
    d = webdriver.Chrome(options=opts)
    fail = []
    try:
        d.get(URL)
        for _ in range(40):
            time.sleep(0.5)
            if d.execute_script("return typeof state!=='undefined' && typeof ANIM!=='undefined' && "
                                "!!(state.grid && state.inputs && state.holland && state.powellField);"):
                break

        def sel(i, v):
            d.execute_script("const e=document.getElementById(arguments[0]);e.value=arguments[1];"
                             "e.dispatchEvent(new Event('change'));", i, v)
            time.sleep(0.3)

        sel("model", "holland")
        # 1. default: not animating
        if d.execute_script("return ANIM.active;"):
            fail.append("animation should be off by default")
        if not d.find_elements("id", "simBar"):
            fail.append("#simBar control not present")

        zoom0 = d.execute_script("return state.map.getZoom();")

        # 2. enter via Play
        d.execute_script("document.getElementById('simPlay').click();")
        time.sleep(0.6)
        st = d.execute_script("return {active:ANIM.active, frames:ANIM.frames, "
                              "npts:ANIM.ext?ANIM.ext.grid.points.length:0, "
                              "contour:!!state.animContour, eye:!!ANIM.eye, zoom:state.map.getZoom()};")
        print("after Play:", st)
        if not st["active"]:
            fail.append("Play should enter sim mode")
        if st["frames"] != 73:
            fail.append(f"expected 73 frames, got {st['frames']}")
        if st["npts"] <= 840:
            fail.append(f"extended domain should exceed 840 pts, got {st['npts']}")
        if not st["contour"] or not st["eye"]:
            fail.append("animation contour + eye marker should be present")
        if not (st["zoom"] < zoom0):
            fail.append(f"map should zoom OUT on enter: before={zoom0} after={st['zoom']}")

        # 3. frame 0 = t-12, eye offshore (east of grid east edge -79.975)
        d.execute_script("animPause(); animRenderFrame(0);")
        time.sleep(0.2)
        f0 = d.execute_script("return {t:document.getElementById('simTime').textContent, "
                              "eyelon:ANIM.eye.getLatLng().lng};")
        print("frame0:", f0)
        if "-12.0" not in f0["t"] and "−12.0" not in f0["t"]:
            fail.append(f"frame 0 should read t=-12.0 h, got {f0['t']}")
        if not (f0["eyelon"] > -79.975):
            fail.append(f"at t=-12 eye should be offshore (lon>-79.975), got {f0['eyelon']:.3f}")
        # last frame = t+24
        d.execute_script("animRenderFrame(72);")
        time.sleep(0.2)
        tlast = d.execute_script("return document.getElementById('simTime').textContent;")
        if "24.0" not in tlast:
            fail.append(f"last frame should read t=+24.0 h, got {tlast}")

        # 4. Reset restores static + default zoom
        d.execute_script("document.getElementById('simReset').click();")
        time.sleep(0.5)
        after = d.execute_script("return {active:ANIM.active, contour:!!state.animContour, "
                                 "zoom:state.map.getZoom(), markvis:state.markers[0].options.opacity};")
        print("after Reset:", after, "zoom0:", zoom0)
        if after["active"] or after["contour"]:
            fail.append("Reset should exit sim mode and clear the contour")
        if abs(after["zoom"] - zoom0) > 0.001:
            fail.append(f"Reset should restore default zoom {zoom0}, got {after['zoom']}")
        if after["markvis"] == 0:
            fail.append("Reset should restore the static grid markers")

        # 5. all three models precompute
        for m in ("holland", "willoughby", "powell"):
            sel("model", m)
            ok = d.execute_script("ANIM.fields=null; ANIM.key=null; return animPrecompute();")
            n = d.execute_script("return ANIM.fields ? ANIM.fields.length : 0;")
            print(f"model {m}: precompute ok={ok} frames={n}")
            if not ok or n != 73:
                fail.append(f"{m} animation precompute failed (ok={ok}, frames={n})")

        # 6. sidebar change while animating drops out
        sel("model", "holland")
        d.execute_script("document.getElementById('simSlider').value=10;"
                         "document.getElementById('simSlider').dispatchEvent(new Event('input'));")
        time.sleep(0.4)
        if not d.execute_script("return ANIM.active;"):
            fail.append("scrub should have entered sim mode")
        sel("category", "3")   # a sidebar change
        time.sleep(0.3)
        if d.execute_script("return ANIM.active;"):
            fail.append("changing a sidebar control should exit sim mode")

        # 8. separate speed slider maps to the playback frame interval (slow->fast)
        if not d.find_elements("id", "simSpeed"):
            fail.append("speed slider (#simSpeed) missing")
        ms1 = d.execute_script("ANIM.speed=1; return animFrameMs();")
        ms10 = d.execute_script("ANIM.speed=10; return animFrameMs();")
        print(f"speed: frameMs @1={ms1} @10={ms10}")
        if not (ms1 > ms10 > 0):
            fail.append(f"speed slider should shorten the interval as it rises: @1={ms1} @10={ms10}")

        errs = [e["message"][:160] for e in d.get_log("browser")
                if e["level"] == "SEVERE" and "favicon.ico" not in e["message"]]
        if errs:
            fail.append(f"console errors: {errs}")
    finally:
        d.quit()

    if fail:
        print("FAIL:\n  - " + "\n  - ".join(fail))
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
