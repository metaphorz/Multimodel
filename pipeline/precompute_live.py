#!/usr/bin/env python3
"""Precompute the Holland and Willoughby footprint peak-wind envelopes.

These two models were computed live in the browser, which froze the UI for ~13 s
on every model/category change (100 vectors x 840 vertices x 2161 steps each).
This script drives the *existing* JS field code (computeLiveWind /
computeLiveWindKD) headlessly so the precomputed values match the live physics
exactly -- no Python re-implementation to drift out of sync -- and writes them in
the same shape as powell.json so the viewer can look them up like Powell.

B comes from the default Uniform WSP->B mapping (1.0..2.5); the interactive B
distribution control therefore reshapes only the live single-point profiler, not
the precomputed footprint.

Prereq: the viewer server must be running (./start). Then:
    ./venv/bin/python pipeline/precompute_live.py                # legacy 3-cat design
    ./venv/bin/python pipeline/precompute_live.py --constrained  # lumped n=200 design

--constrained targets the lumped constrained design: one "all" population of 200
storms, B taken directly from each storm record (rec.B) via the viewer's recB()
adapter, and output written to {model}_constrained.json / {model}_kd_constrained
.json. The migrated viewer (server running) must be serving the _constrained data.
Author: Pro Team & Claude Code
"""
import json, sys, time
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "outputs" / "web"
URL = "http://localhost:8012/web/index.html"
MODELS = ("holland", "willoughby")

# compute one (model, cat): all vectors in the group -> marine + K&D peak arrays
# (840 each), using the same functions the viewer uses, rounded to 1 dp like
# powell.json. B = recB(rec): rec.B for the constrained design, WSP->B otherwise.
JS = r"""
const [model, cat] = arguments;
const pts = state.grid.points, recs = state.inputs[cat];
const r1 = x => Math.round(x * 10) / 10;
const marine = [], kd = [];
for (let v = 0; v < recs.length; v++) {
  const rec = recs[v], B = recB(rec);
  marine.push(Array.from(computeLiveWind(model, rec, B, pts), r1));
  kd.push(Array.from(computeLiveWindKD(model, rec, B, pts), r1));
}
return { marine, kd };
"""


def main():
    constrained = "--constrained" in sys.argv
    cats = ("all",) if constrained else ("cat1", "cat3", "cat5")
    suffix = "_constrained" if constrained else ""
    note_b = "constrained design B (rec.B)" if constrained else "Uniform WSP->B"
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1200,900")
    drv = webdriver.Chrome(options=opts)
    drv.set_script_timeout(600)
    try:
        drv.get(URL)
        WebDriverWait(drv, 30).until(
            lambda d: "Loading" not in d.find_element(By.ID, "info").text)
        # ensure the default Uniform B mapping is selected
        drv.execute_script(
            "document.getElementById('bdist').value='uniform';"
            "document.getElementById('bdist').dispatchEvent(new Event('change'));")
        time.sleep(0.3)

        for model in MODELS:
            marine = {"unit": "mph", "note": f"live-model precompute ({note_b})"}
            kd = {"unit": "mph", "note": "Kaplan-DeMaria inland decay + Gulf recovery"}
            for cat in cats:
                t = time.time()
                res = drv.execute_script(JS, model, cat)
                marine[cat] = res["marine"]
                kd[cat] = res["kd"]
                pk = max(max(v) for v in res["marine"])
                print(f"  {model} {cat}: {len(res['marine'])} vectors, "
                      f"peak {pk:.1f} mph ({time.time()-t:.0f}s)", flush=True)
            for obj, path in ((marine, WEB / f"{model}{suffix}.json"),
                              (kd, WEB / f"{model}_kd{suffix}.json")):
                json.dump(obj, open(path, "w"))
                print(f"Wrote {path.name} ({path.stat().st_size/1e6:.2f} MB)", flush=True)
    finally:
        drv.quit()
    print("Done.")


if __name__ == "__main__":
    main()
