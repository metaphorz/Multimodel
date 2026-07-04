#!/usr/bin/env python3
"""Selenium test: Phase B metamodels (GPR / NN), compare panel, Sobol EPR,
grid-point sensitivity colour mode -- and that defaults are unchanged.

Run:  ./venv/bin/python tests/auto/test_metamodels.py   (server up via ./start)
"""
import os, sys, logging

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select

HERE = os.path.dirname(os.path.abspath(__file__))
URL = "http://localhost:8012/web/index.html"
LOG = os.path.join(HERE, "test_metamodels.log")
logging.basicConfig(filename=LOG, filemode="w", level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger()


def info(msg):
    log.info(msg); print(msg, flush=True)


def prof_note(d):
    el = d.find_elements(By.CSS_SELECTOR, ".prof-sliders")
    if not el:
        return ""
    return el[-1].find_element(By.XPATH, "following-sibling::p").text


def main():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1400,950")
    driver = webdriver.Chrome(options=opts)
    fails = []
    try:
        driver.get(URL)
        wait = WebDriverWait(driver, 30)
        wait.until(lambda d: "Loading grid" not in d.find_element(By.ID, "info").text)
        info("init: " + driver.find_element(By.ID, "info").text)

        # default metamodel is Linear (RSM)
        mm = Select(driver.find_element(By.ID, "metamodel"))
        if mm.first_selected_option.get_attribute("value") != "rsm":
            fails.append("default metamodel is not rsm")

        # open profiler; default note must read Linear (RSM)
        # (expand the collapsible Statistics group so the button is interactable)
        driver.execute_script("[...document.querySelectorAll('.analysis-group')]"
                              ".find(g=>g.dataset.grp==='grpStats').click();")
        driver.find_element(By.ID, "btnProf").click()
        wait.until(lambda d: "(100 vectors)" in d.find_element(By.ID, "info").text
                   or "Linear (RSM)" in prof_note(d))
        wait.until(lambda d: "Linear (RSM)" in prof_note(d))
        info("RSM profiler note: " + prof_note(driver))

        # switch to GPR -> note shows Gaussian process
        Select(driver.find_element(By.ID, "metamodel")).select_by_value("gpr")
        wait.until(lambda d: "Gaussian process" in prof_note(d))
        info("GPR profiler note: " + prof_note(driver))
        if "Gaussian process" not in prof_note(driver):
            fails.append("profiler did not switch to GPR")

        # switch to NN -> note shows Neural net
        Select(driver.find_element(By.ID, "metamodel")).select_by_value("mlp")
        wait.until(lambda d: "Neural net" in prof_note(d))
        info("NN profiler note: " + prof_note(driver))

        # Compare panel: 3 metamodel legend entries + table with 3 data rows
        driver.find_element(By.ID, "btnCompare").click()
        wait.until(lambda d: d.find_elements(By.CSS_SELECTOR, ".cmp-tbl"))
        tbl = driver.find_elements(By.CSS_SELECTOR, ".cmp-tbl")[-1]
        body_rows = tbl.find_elements(By.CSS_SELECTOR, "tr")
        info(f"compare table rows (incl header): {len(body_rows)}")
        if len(body_rows) != 4:
            fails.append(f"compare table expected 4 rows (header+3), got {len(body_rows)}")
        cells = [r.find_elements(By.CSS_SELECTOR, "td")[0].text
                 for r in body_rows if r.find_elements(By.CSS_SELECTOR, "td")]
        info(f"compare metamodels: {cells}")
        if set(cells) != {"Linear (RSM)", "Gaussian process", "Neural net"}:
            fails.append(f"compare missing metamodels: {cells}")

        # EPR with GPR -> Sobol footnote (Sobol is GPR-only by design)
        Select(driver.find_element(By.ID, "metamodel")).select_by_value("gpr")
        driver.find_element(By.ID, "btnEPR").click()
        wait.until(lambda d: any("Sobol" in n.text
                   for n in d.find_elements(By.CSS_SELECTOR, ".ap-body .note")))
        sob = [n.text for n in driver.find_elements(By.CSS_SELECTOR, ".ap-body .note")
               if "Sobol" in n.text]
        info(f"EPR GPR note: {sob}")
        if not sob:
            fails.append("EPR did not show Sobol footnote under GPR")

        # grid-point sensitivity colour mode: land markers take variable colours
        var_colors = {"#3b82f6", "#22c55e", "#f59e0b", "#111827", "#ef4444", "#7c3aed"}
        Select(driver.find_element(By.ID, "colorBy")).select_by_value("sensitivity")
        wait.until(lambda d: "Grid-point sensitivity" in d.find_element(By.ID, "info").text)
        fills = driver.execute_script(
            "return state.markers.map((m,i)=>state.grid.points[i].land?m.options.fillColor:null)"
            ".filter(c=>c);")
        used = {c.lower() for c in fills}
        info(f"distinct land fill colours in sensitivity mode: {len(used)} -> {used}")
        if not (used & var_colors):
            fails.append("sensitivity mode did not colour land points by variable")
        legend = driver.find_elements(By.CSS_SELECTOR, "#legend .lg")
        if len(legend) != 6:
            fails.append(f"sensitivity legend expected 6 entries, got {len(legend)}")

        driver.save_screenshot(os.path.join(HERE, "selenium_metamodels.png"))
    finally:
        driver.quit()

    if fails:
        info("FAIL:\n  - " + "\n  - ".join(fails)); sys.exit(1)
    info("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
