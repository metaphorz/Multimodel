#!/usr/bin/env python3
"""Selenium test: Interaction Profiler + %TLC CDF panels and the Response toggle.

Verifies:
  1. Interaction Profiler opens with 6 partial-dependence subplots + 6 sliders.
  2. Moving one variable's slider changes another variable's curve (interaction
     is visible) -- the core of the JMP-style profiler.
  3. Switching Response to %TLC re-renders the profiler (Y = %TLC).
  4. %TLC CDF panel renders an SVG over the 100 input vectors.

Run:  python tests/auto/test_profiler_cdf.py   (server must be up via ./start)
"""
import os, sys, time, logging

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select

HERE = os.path.dirname(os.path.abspath(__file__))
URL = "http://localhost:8012/web/index.html"
LOG = os.path.join(HERE, "test_profiler_cdf.log")

logging.basicConfig(filename=LOG, filemode="w", level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger()


def info(msg):
    log.info(msg)
    print(msg, flush=True)


def polyline_points(driver):
    return driver.execute_script(
        "return Array.from(document.querySelectorAll('.prof-grid polyline'))"
        ".map(e => e.getAttribute('points'));")


def main():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1400,900")
    driver = webdriver.Chrome(options=opts)
    failures = []
    try:
        info(f"Loading {URL}")
        driver.get(URL)
        wait = WebDriverWait(driver, 30)
        wait.until(lambda d: "Loading grid" not in d.find_element(By.ID, "info").text)
        info("App initialized: " + driver.find_element(By.ID, "info").text)

        # analysis buttons live in collapsible Statistics / Actuarial groups
        def open_group(grp):
            driver.execute_script(
                "const g=[...document.querySelectorAll('.analysis-group')]"
                ".find(g=>g.dataset.grp===arguments[0]);"
                "if(g && !g.classList.contains('open')) g.click();", grp)
            time.sleep(0.3)

        # 1. open Interaction Profiler (Statistics group)
        open_group("grpStats")
        driver.find_element(By.ID, "btnProf").click()
        wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR, ".prof-cell")) == 6)
        cells = driver.find_elements(By.CSS_SELECTOR, ".prof-cell")
        sliders = driver.find_elements(By.CSS_SELECTOR, ".prof-sliders input")
        info(f"Profiler: {len(cells)} subplots, {len(sliders)} sliders")
        if len(cells) != 6:
            failures.append(f"expected 6 subplots, got {len(cells)}")
        if len(sliders) != 6:
            failures.append(f"expected 6 sliders, got {len(sliders)}")

        # 2. moving CP (slider 0) must change at least one OTHER curve (interaction)
        before = polyline_points(driver)
        cp_max = driver.execute_script(
            "var s=document.querySelector('.prof-sliders input[data-i=\"0\"]');"
            "s.value=s.max; s.dispatchEvent(new Event('input',{bubbles:true}));"
            "return s.value;")
        info(f"Moved CP slider to max={cp_max}")
        after = polyline_points(driver)
        changed = sum(1 for a, b in zip(before, after) if a != b)
        info(f"Curves changed after moving CP: {changed}/6")
        if changed == 0:
            failures.append("no curve changed when moving CP -> interactions not shown")

        # 2b. Interaction-matrix toggle: 6x6 cells, 6 diagonal, red+blue per off-diagonal
        driver.execute_script("[...document.querySelectorAll('.prof-tab')]"
                              ".find(b=>b.dataset.view==='matrix').click();")
        wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR, ".prof-matrix .prof-cell")) == 36)
        n_diag = len(driver.find_elements(By.CSS_SELECTOR, ".prof-matrix .prof-diag"))
        n_poly = len(driver.find_elements(By.CSS_SELECTOR, ".prof-matrix svg polyline"))
        info(f"Matrix: 36 cells, {n_diag} diagonal, {n_poly} polylines")
        if n_diag != 6:
            failures.append(f"matrix expected 6 diagonal cells, got {n_diag}")
        if n_poly != 60:
            failures.append(f"matrix expected 60 polylines (30x red+blue), got {n_poly}")
        # back to the slider profiler
        driver.execute_script("[...document.querySelectorAll('.prof-tab')]"
                              ".find(b=>b.dataset.view==='profiler').click();")
        wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR, ".prof-sliders input")) == 6)

        # 3. switch Response -> %TLC, profiler re-renders with Y=%TLC
        Select(driver.find_element(By.ID, "response")).select_by_value("tlc")
        wait.until(lambda d: "%TLC" in
                   d.find_element(By.CSS_SELECTOR, "#map .analysis-panel .ap-body .note").text
                   if d.find_elements(By.CSS_SELECTOR, ".prof-grid") else False)
        note = driver.find_element(By.CSS_SELECTOR, ".prof-sliders").find_element(
            By.XPATH, "following-sibling::p").text
        info(f"Profiler note after %TLC: {note!r}")
        if "%TLC" not in note:
            failures.append(f"profiler did not switch to %TLC: {note!r}")

        # 4. CDF panel renders (Actuarial group)
        open_group("grpAct")
        driver.find_element(By.ID, "btnCDF").click()
        wait.until(lambda d: any(
            "input vectors" in p.text
            for p in d.find_elements(By.CSS_SELECTOR, ".ap-body .note")))
        cdf_panel = [p for p in driver.find_elements(By.CSS_SELECTOR, ".ap-body")
                     if "input vectors" in p.text][-1]
        has_path = len(cdf_panel.find_elements(By.CSS_SELECTOR, "svg path")) >= 1
        info(f"CDF note: {[p.text for p in cdf_panel.find_elements(By.CSS_SELECTOR, '.note')]}")
        if not has_path:
            failures.append("CDF panel has no SVG path")

        driver.save_screenshot(os.path.join(HERE, "selenium_profiler_cdf.png"))
    finally:
        driver.quit()

    if failures:
        info("FAIL:\n  - " + "\n  - ".join(failures))
        sys.exit(1)
    info("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
