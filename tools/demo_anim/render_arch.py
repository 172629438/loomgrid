# -*- coding: utf-8 -*-
"""渲染架构图 -> assets/architecture.png。"""
import os

from PIL import Image
from playwright.sync_api import sync_playwright

HTML = r"C:\Users\LENOVO\.hanako\_tmp\shots\anim\arch.html"
RAW = r"C:\Users\LENOVO\.hanako\_tmp\shots\arch_raw.png"
OUT = r"C:\Users\LENOVO\.hanako\repos\172629438__loomgrid\assets\architecture.png"
W, H = 2400, 1220

with sync_playwright() as pw:
    b = pw.chromium.launch(args=["--force-color-profile=srgb",
                                 "--font-render-hinting=none"])
    p = b.new_page(viewport={"width": W, "height": H}, device_scale_factor=2)
    p.goto("file:///" + HTML.replace("\\", "/"))
    p.wait_for_timeout(700)
    p.screenshot(path=RAW)
    b.close()

im = Image.open(RAW)
print(f"光栅化 {im.size}")
if im.size != (W, H):
    im = im.resize((W, H), Image.LANCZOS)
im.convert("RGB").save(OUT, optimize=True)
print(f"OK {OUT}  {os.path.getsize(OUT)/1024:.0f} KB  {im.size}")
