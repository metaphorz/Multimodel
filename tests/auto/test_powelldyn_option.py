#!/usr/bin/env python3
"""Smoke: Powell (dynamic) option renders footprints for all 4 checkbox states,
degrades gracefully (analysis gate), no severe console errors."""
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

URL = "http://localhost:8012/web/index.html"
opts = Options()
opts.add_argument("--headless=new")
opts.add_argument("--window-size=1400,1000")
d = webdriver.Chrome(options=opts)
fail = []
try:
    d.get(URL)
    for _ in range(40):
        time.sleep(0.5)
        if d.execute_script("return typeof state!=='undefined' && !!(state.grid && state.inputs && state.powellDyn);"):
            break
    else:
        fail.append("powellDyn data not loaded")
    d.execute_script("const e=document.getElementById('model');e.value='powelldyn';e.dispatchEvent(new Event('change'));")
    time.sleep(0.8)
    for rough in (False, True):
        for decay in (False, True):
            d.execute_script(
                "for (const [id,v] of [['landRoughness',arguments[0]],['landDecay',arguments[1]]]) {"
                "const c=document.getElementById(id); if(c.checked!==v){c.checked=v;c.dispatchEvent(new Event('change'));}}",
                rough, decay)
            time.sleep(0.6)
            w = d.execute_script("const w=computeWindCached(); return (w && typeof w!=='string') ? w.length : String(w);")
            if w != 840:
                fail.append(f"rough={rough} decay={decay}: footprint not rendered ({w})")
    d.execute_script("document.getElementById('btnProf').click();")
    time.sleep(0.6)
    txt = d.execute_script("return [...document.querySelectorAll('.note')].map(n=>n.textContent).join(' ');")
    if "peaks-only" not in txt:
        fail.append("analysis gate note missing")
    bad = [e for e in d.get_log("browser") if e["level"] == "SEVERE" and "favicon" not in e["message"]]
    if bad:
        fail.append(f"console errors: {bad[:3]}")
finally:
    d.quit()
print("FAIL:" if fail else "PASS", fail if fail else "")
