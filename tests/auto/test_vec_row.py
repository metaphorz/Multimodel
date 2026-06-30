"""Verify the input-vector parameter row: hidden under Mean/Max, shown for a
single vector, and that it tracks the slider value."""
import time, os
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

URL = "http://localhost:8012/web/index.html"
OUT = os.path.join(os.path.dirname(__file__), "vec_row.png")

opts = Options()
opts.add_argument("--headless=new")
opts.add_argument("--window-size=480,1000")
d = webdriver.Chrome(options=opts)
try:
    d.get(URL)
    time.sleep(3)  # let JSON loads + first paint finish
    row = d.find_element(By.ID, "vecRow")

    # default view is Mean -> row hidden
    print("default (Mean on)  hidden:", "hidden" in row.get_attribute("class"),
          "| text:", repr(row.text))

    # turn Mean off -> single-vector mode -> row visible with v1 params
    d.find_element(By.ID, "btnMean").click()
    time.sleep(1)
    print("single vector v1   hidden:", "hidden" in row.get_attribute("class"))
    print("  text:", row.text.replace("\n", " "))

    # move slider to vector 50 -> values should change
    vec = d.find_element(By.ID, "vector")
    d.execute_script(
        "arguments[0].value=50;"
        "arguments[0].dispatchEvent(new Event('input',{bubbles:true}));", vec)
    time.sleep(1)
    print("vector 50          label:", d.find_element(By.ID, "vectorLabel").text)
    print("  text:", row.text.replace("\n", " "))

    d.save_screenshot(OUT)
    print("screenshot:", OUT)
finally:
    d.quit()
