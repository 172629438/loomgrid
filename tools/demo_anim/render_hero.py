# -*- coding: utf-8 -*-
"""渲染 hero.html 成 assets/hero.png（2560×1440）。"""
import os
import sys

from playwright.sync_api import sync_playwright

HTML = r"C:\Users\LENOVO\.hanako\_tmp\shots\anim\hero.html"
OUT = r"C:\Users\LENOVO\.hanako\repos\172629438__loomgrid\assets\hero.png"
OUT_W, OUT_H = 2560, 1440

with sync_playwright() as pw:
    b = pw.chromium.launch()
    p = b.new_page(viewport={"width": 2560, "height": 1440},
                   device_scale_factor=1)
    p.goto("file:///" + HTML.replace("\\", "/"))
    p.wait_for_timeout(700)
    p.screenshot(path=OUT)
    b.close()

from PIL import Image
im = Image.open(OUT)
if im.size != (OUT_W, OUT_H):
    im = im.resize((OUT_W, OUT_H), Image.LANCZOS)
    im.save(OUT)
print(f"saved {OUT}  {os.path.getsize(OUT)/1024:.0f} KB  {im.size}")
