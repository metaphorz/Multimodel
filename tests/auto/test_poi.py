"""Verify the Points of Interest panel: defaults, add, delete, detail page, print.

Loads the viewer, waits for init, then checks:
  - the 5 default POIs are listed and 5 markers are on the map
  - adding (30,-6) -> 6 rows/markers; a bad coord shows an error and adds nothing
  - clicking a POI opens the detail panel with hover text + 2 SVG plots + Print btn
  - deleting a POI -> back to the previous count
  - no severe console errors
"""
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

URL = "http://localhost:8012/web/index.html"

opts = Options()
opts.add_argument("--headless=new")
opts.add_argument("--window-size=1500,950")
driver = webdriver.Chrome(options=opts)


def rows():
    return driver.find_elements(By.CSS_SELECTOR, "#poiList .poi-row")


def markers():
    return driver.execute_script("return Object.keys(poi.markers).length;")


try:
    driver.get(URL)
    # clear any persisted POIs from a previous run, then reload to defaults
    for _ in range(40):
        time.sleep(0.5)
        if driver.execute_script("return (typeof state!=='undefined') && !!(state.grid && state.powell && state.vuln);"):
            break
    driver.execute_script("localStorage.removeItem('formS6_poi');")
    driver.get(URL)
    for _ in range(40):
        time.sleep(0.5)
        if driver.execute_script("return (typeof poi!=='undefined') && poi.list && poi.list.length>0;"):
            break

    # 1) defaults
    assert len(rows()) == 5, f"expected 5 default POIs, got {len(rows())}"
    assert markers() == 5, f"expected 5 markers, got {markers()}"
    labels = [r.find_element(By.CSS_SELECTOR, ".poi-view").text for r in rows()]
    assert labels == ["(9,15)", "(15,0)", "(60,0)", "(12,-12)", "(6,45)"], labels
    print("defaults:", labels)

    # 2) add a valid point
    driver.find_element(By.ID, "poiInput").send_keys("30,-6")
    driver.find_element(By.ID, "poiAdd").click()
    time.sleep(0.4)
    assert len(rows()) == 6, f"expected 6 after add, got {len(rows())}"
    assert markers() == 6, f"expected 6 markers, got {markers()}"

    # 3) bad coord -> error, no add
    driver.find_element(By.ID, "poiInput").send_keys("1,1")
    driver.find_element(By.ID, "poiAdd").click()
    time.sleep(0.3)
    err = driver.find_element(By.ID, "poiErr").text
    assert "not a grid vertex" in err, f"expected grid-vertex error, got '{err}'"
    assert len(rows()) == 6, "bad coord should not add a row"
    print("bad-coord error:", err)

    # 4) open a detail page (first POI) and check contents
    rows()[0].find_element(By.CSS_SELECTOR, ".poi-view").click()
    time.sleep(1.2)
    panel = driver.find_element(By.CSS_SELECTOR, ".poi-detail")
    assert panel.is_displayed(), "detail panel not shown"
    info_txt = driver.find_element(By.CSS_SELECTOR, ".poi-detail .poi-info").text
    assert "mph" in info_txt and "land" in info_txt, f"hover details missing: {info_txt!r}"
    n_svg = len(driver.find_elements(By.CSS_SELECTOR, ".poi-detail .ap-body svg"))
    assert n_svg == 2, f"expected 2 SVG plots (isotach + time series), got {n_svg}"
    assert driver.find_elements(By.CSS_SELECTOR, ".poi-detail .poi-print"), "Print button missing"
    print("detail info line:", info_txt.split(chr(10))[0], "| SVGs:", n_svg)

    # 5) delete one -> 5
    rows()[0].find_element(By.CSS_SELECTOR, ".poi-del").click()
    time.sleep(0.4)
    assert len(rows()) == 5, f"expected 5 after delete, got {len(rows())}"
    assert markers() == 5, f"expected 5 markers after delete, got {markers()}"

    errors = [e for e in driver.get_log("browser")
              if e["level"] == "SEVERE" and "favicon.ico" not in e["message"]]
    assert not errors, f"console errors: {errors}"
    print("PASS: POI add/delete/detail/print all working; no console errors.")
finally:
    driver.quit()
