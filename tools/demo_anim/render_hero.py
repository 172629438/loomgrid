# -*- coding: utf-8 -*-
"""渲染 hero.html → assets/hero.png（2× 光栅化后缩到 2560×1440）。

关键：device_scale_factor=2 让 Chromium 按 5120×2880 光栅化字体与 SVG，
再缩到 2560×1440。比 1× 直接渲染锐得多——文字边缘的灰阶过渡被压在
更少的像素里，肉眼就是「清楚」。

不用 viewport 缩半的做法：CSS 里全是绝对 px，改 body 尺寸要重排所有数值。
"""
import os

from PIL import Image
from playwright.sync_api import sync_playwright

HTML = r"C:\Users\LENOVO\.hanako\_tmp\shots\anim\hero.html"
RAW = r"C:\Users\LENOVO\.hanako\_tmp\shots\hero_raw.png"
OUT = r"C:\Users\LENOVO\.hanako\repos\172629438__loomgrid\assets\hero.png"
W, H = 2560, 1440

with sync_playwright() as pw:
    b = pw.chromium.launch(args=["--force-color-profile=srgb",
                                 "--font-render-hinting=none"])
    p = b.new_page(viewport={"width": W, "height": H}, device_scale_factor=2)
    p.goto("file:///" + HTML.replace("\\", "/"))
    p.wait_for_timeout(900)
    p.screenshot(path=RAW)
    b.close()

im = Image.open(RAW)
print(f"光栅化: {im.size}")
if im.size != (W, H):
    im = im.resize((W, H), Image.LANCZOS)
im.save(OUT, optimize=True)

# 锐度指标：文字区的中间灰 / 纯亮比
crop = im.convert("RGB").crop((100, 60, 900, 480))
white = gray = 0
for px in crop.getdata():
    m = sum(px) / 3
    if m > 230:
        white += 1
    elif 90 < m < 200:
        gray += 1
print(f"输出 {im.size}  {os.path.getsize(OUT)/1024:.0f} KB")
print(f"纯亮 {white}  中间灰 {gray}  灰/亮 = {gray/max(white,1):.2f}  (越低越锐)")
