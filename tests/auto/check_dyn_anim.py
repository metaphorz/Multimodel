#!/usr/bin/env python3
"""Selenium check: Powell (dynamic) contour animation from precomputed frames.

The dynamic field is not translation-invariant, so unlike the other models it cannot
be animated by advecting one storm-relative field. Its time-resolved field is
precomputed per (cat, vector) and fetched lazily. This drives the real viewer to
prove: the frames load, the animation runs, the field CHANGES between frames, and
the gate messages are correct when the configuration does not match the frames.

Run:  ./venv/bin/python tests/auto/check_dyn_anim.py
"""
import sys, os, time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8012/web/index.html"
HERE = os.path.dirname(os.path.abspath(__file__))
LOG = open(os.path.join(HERE, "check_dyn_anim.log"), "w")


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

    man = drv.execute_script("return window.state ? null : null") or drv.execute_script(
        "return fetch('../outputs/web/dyn_frames.json').then(r=>r.json())")
    log(f"[1] manifest: {man['n_frames']} frames, scale {man['scale']}, "
        f"{len(man['available'])} storms available")
    if man["n_frames"] != 73:
        fails.append(f"expected 73 frames, manifest says {man['n_frames']}")

    # select Powell (dynamic), cat1 (complete), default land config (rough + decay)
    drv.execute_script("""
        const m=document.getElementById('model'); m.value='powelldyn';
        m.dispatchEvent(new Event('change'));
        const c=document.getElementById('category'); c.value='1';
        c.dispatchEvent(new Event('change'));
        for (const id of ['landRoughness','landDecay']) {
          const e=document.getElementById(id);
          if (!e.checked) { e.checked=true; e.dispatchEvent(new Event('change')); }
        }
        const v=document.getElementById('vector'); v.value='7';
        v.dispatchEvent(new Event('input'));
    """)
    time.sleep(1.5)

    # press play. The first animPrecompute() kicks off the lazy 60 KB fetch and
    # returns false; the fetch callback re-runs it. Give it a moment, then press again.
    drv.find_element(By.ID, "simPlay").click()
    time.sleep(2.5)
    if not drv.execute_script("return !!(typeof ANIM!=='undefined' && ANIM.fields)"):
        drv.find_element(By.ID, "simPlay").click()   # frames now cached
        time.sleep(1.5)
    status = drv.find_element(By.ID, "simTime").text
    log(f"[2] after play: simTime = {status!r}")

    got = drv.execute_script("return (typeof ANIM!=='undefined' && ANIM.fields) ? ANIM.fields.length : 0")
    log(f"[3] ANIM.fields built: {got} frames (expect 73)")
    if got != 73:
        fails.append(f"animation fields not built: {got}")

    # the field must actually CHANGE over time -- that is the entire point of the
    # dynamic model; a static field would mean we are advecting a frozen footprint
    stats = drv.execute_script("""
        if (typeof ANIM==='undefined' || !ANIM.fields) return null;
        const out=[];
        for (const i of [0,20,30,40,50,72]) {
          const F=ANIM.fields[i]; let mx=0, am=-1;
          for (let k=0;k<F.length;k++) if (F[k]>mx) { mx=F[k]; am=k; }
          out.push([i, +mx.toFixed(1), am]);
        }
        return out;
    """)
    log("[4] frame -> (max mph, argmax point):")
    for i, mx, am in stats:
        log(f"      t={-12 + i*0.5:+5.1f}h  max {mx:6.1f} mph  at pt {am}")
    maxes = [s[1] for s in stats]
    args = [s[2] for s in stats if s[1] > 1]
    if len(set(maxes)) < 3:
        fails.append("field barely changes across frames — not time-resolved")
    if len(set(args)) < 2:
        fails.append("argmax never moves — storm is not translating")

    drv.save_screenshot(os.path.join(HERE, "dyn_anim.png"))

    # gate: turning decay OFF must refuse, with a useful reason (frames are product D)
    drv.execute_script("""
        const e=document.getElementById('landDecay'); e.checked=false;
        e.dispatchEvent(new Event('change'));
        if (typeof animExit==='function') animExit();
    """)
    time.sleep(0.8)
    drv.find_element(By.ID, "simPlay").click()
    time.sleep(1.2)
    why = drv.find_element(By.ID, "simTime").text
    log(f"[5] decay OFF -> {why!r}")
    if "roughness" not in why.lower() and "decay" not in why.lower():
        fails.append(f"gate message unhelpful when config mismatches: {why!r}")

    errs = [e for e in drv.get_log("browser")
            if e["level"] == "SEVERE" and "favicon" not in e["message"]]
    log(f"[6] severe console errors: {len(errs)}")
    for e in errs[:5]:
        log("    " + e["message"][:150])
        fails.append("console: " + e["message"][:70])
finally:
    drv.quit()

log("\n" + ("FAILED:\n  - " + "\n  - ".join(fails) if fails else "ALL CHECKS PASSED"))
LOG.close()
sys.exit(1 if fails else 0)
