#!/usr/bin/env python3
"""Selenium test: optional storm animation (Narrow/Wide, speed + opacity sliders).

  1. #simBar exists; nothing animates by default (ANIM.active false, no animContour).
  2. Play (Narrow default): grid-only domain (840 pts), 73 frames, animContour +
     eye marker, and the map does NOT zoom out (default view kept).
  3. Wide: extended domain (> 840 pts) and the map zooms OUT to show the approach;
     at frame 0 (t=-12) the eye is offshore (lon > -79.975). Narrow returns to the
     840-pt grid at the default zoom.
  4. Reset exits: animContour gone, markers visible, map back to the default zoom.
  5. Opacity slider drives ANIM.fillOpacity; speed slider drives the frame interval.
  6. All three models precompute without error (Holland/Willoughby/Powell).
  7. Changing a sidebar control while animating drops out of sim mode.
  8. No severe console errors.

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

        def click(i):
            d.execute_script("document.getElementById(arguments[0]).click();", i)
            time.sleep(0.5)

        sel("model", "holland")
        # 1. default off
        if d.execute_script("return ANIM.active;"):
            fail.append("animation should be off by default")
        for eid in ("simBar", "simNarrow", "simWide", "simOpacity", "simSpeed"):
            if not d.find_elements("id", eid):
                fail.append(f"#{eid} control missing")
        if d.execute_script("return ANIM.mode;") != "narrow":
            fail.append("default mode should be narrow")

        zoom0 = d.execute_script("return state.map.getZoom();")

        # 2. Play, Narrow default -> grid-only, no zoom-out
        click("simPlay")
        st = d.execute_script("return {active:ANIM.active, frames:ANIM.frames, "
                              "npts:ANIM.ext?ANIM.ext.grid.points.length:0, mode:ANIM.mode, "
                              "contour:!!state.animContour, eye:!!ANIM.eye, zoom:state.map.getZoom()};")
        print("Play (narrow):", st)
        if not st["active"] or st["frames"] != 73:
            fail.append(f"Play should enter sim with 73 frames, got {st}")
        if st["npts"] != 840:
            fail.append(f"narrow domain should be the 840-pt grid, got {st['npts']}")
        if abs(st["zoom"] - zoom0) > 0.001:
            fail.append(f"narrow should NOT zoom out: before={zoom0} after={st['zoom']}")
        if not st["contour"] or not st["eye"]:
            fail.append("contour + eye should be present")

        # 3. Wide -> extended domain + zoom out; frame0 eye offshore
        click("simWide")
        w = d.execute_script("return {npts:ANIM.ext.grid.points.length, zoom:state.map.getZoom(), mode:ANIM.mode};")
        print("Wide:", w, "zoom0:", zoom0)
        if w["mode"] != "wide" or w["npts"] <= 840:
            fail.append(f"Wide should extend the domain (>840), got {w}")
        if not (w["zoom"] < zoom0):
            fail.append(f"Wide should zoom OUT: before={zoom0} after={w['zoom']}")
        d.execute_script("animPause(); animRenderFrame(0);")
        time.sleep(0.2)
        eye = d.execute_script("return ANIM.eye.getLatLng().lng;")
        if not (eye > -79.975):
            fail.append(f"at t=-12 the eye should be offshore (lon>-79.975), got {eye:.3f}")
        # Narrow returns to grid + default zoom
        click("simNarrow")
        n = d.execute_script("return {npts:ANIM.ext.grid.points.length, zoom:state.map.getZoom()};")
        if n["npts"] != 840 or abs(n["zoom"] - zoom0) > 0.001:
            fail.append(f"Narrow should return to 840-pt grid at default zoom, got {n}")

        # 4. Reset exits + restores
        click("simReset")
        after = d.execute_script("return {active:ANIM.active, contour:!!state.animContour, "
                                 "zoom:state.map.getZoom(), markvis:state.markers[0].options.opacity};")
        print("Reset:", after)
        if after["active"] or after["contour"]:
            fail.append("Reset should exit and clear the contour")
        if abs(after["zoom"] - zoom0) > 0.001:
            fail.append(f"Reset should restore default zoom {zoom0}, got {after['zoom']}")
        if after["markvis"] == 0:
            fail.append("Reset should restore the grid markers")

        # 5. opacity + speed sliders
        d.execute_script("document.getElementById('simOpacity').value=20;"
                         "document.getElementById('simOpacity').dispatchEvent(new Event('input'));")
        op = d.execute_script("return ANIM.fillOpacity;")
        if abs(op - 0.20) > 0.001:
            fail.append(f"opacity slider should set fillOpacity 0.20, got {op}")
        ms1 = d.execute_script("ANIM.speed=1; return animFrameMs();")
        ms10 = d.execute_script("ANIM.speed=10; return animFrameMs();")
        print(f"opacity={op}  frameMs @1={ms1} @10={ms10}")
        if not (ms1 > ms10 > 0):
            fail.append(f"speed slider should shorten the interval as it rises: @1={ms1} @10={ms10}")

        # 6. all three models precompute (narrow)
        for m in ("holland", "willoughby", "powell"):
            sel("model", m)
            ok = d.execute_script("ANIM.fields=null; ANIM.key=null; return animPrecompute();")
            nf = d.execute_script("return ANIM.fields ? ANIM.fields.length : 0;")
            print(f"model {m}: precompute ok={ok} frames={nf}")
            if not ok or nf != 73:
                fail.append(f"{m} precompute failed (ok={ok}, frames={nf})")

        # 7. sidebar change while animating drops out
        sel("model", "holland")
        d.execute_script("document.getElementById('simSlider').value=10;"
                         "document.getElementById('simSlider').dispatchEvent(new Event('input'));")
        time.sleep(0.4)
        if not d.execute_script("return ANIM.active;"):
            fail.append("scrub should have entered sim mode")
        sel("category", "3")
        time.sleep(0.3)
        if d.execute_script("return ANIM.active;"):
            fail.append("changing a sidebar control should exit sim mode")

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
