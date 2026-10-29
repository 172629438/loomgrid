# -*- coding: utf-8 -*-
"""渲染 hero.html → assets/hero.png（2× 光栅化后缩到 2560×1440）。

## 两步

1. `hero_data.py` 先跑算法，生成 `hero_data.js`
2. 本脚本渲染 `hero.html`（它 `<script src="hero_data.js">`）

    python tools/demo_anim/hero_data.py
    python tools/demo_anim/render_hero.py

## 为什么要 2× 光栅化

`device_scale_factor=2` 让 Chromium 按 5120×2880 光栅化字体与 SVG，再缩到
2560×1440。比 1× 直接渲染锐得多——文字边缘的灰阶过渡被压进更少的像素，
在 GitHub 上按 900px 宽显示时才不会糊。

**别改回 1×**：输出尺寸一样是 2560×1440，但字会明显发软。

## 为什么要复制到临时目录

`hero.html` 用相对路径引 `hero_data.js`。直接 file:// 打开本目录也行，
但把两个文件复制到独立工作区能确保只读到这两个，不受同目录其他文件干扰。
"""
from __future__ import annotations

import os
import shutil

from PIL import Image
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))

HTML = os.path.join(HERE, "hero.html")
DATA = os.path.join(HERE, "hero_data.js")
OUT = os.path.join(REPO, "assets", "hero.png")
WORK = os.path.join(REPO, "tools", "demo_anim", "_render_tmp")
W, H = 2560, 1440

if not os.path.exists(DATA):
    raise SystemExit(f"缺 {DATA}\n先跑: python tools/demo_anim/hero_data.py")

os.makedirs(WORK, exist_ok=True)
shutil.copy2(HTML, os.path.join(WORK, "index.html"))
shutil.copy2(DATA, os.path.join(WORK, "hero_data.js"))
RAW = os.path.join(WORK, "raw.png")

with sync_playwright() as pw:
    b = pw.chromium.launch(args=["--force-color-profile=srgb",
                                 "--font-render-hinting=none"])
    p = b.new_page(viewport={"width": W, "height": H}, device_scale_factor=2)
    p.goto("file:///" + os.path.join(WORK, "index.html").replace("\\", "/"))
    p.wait_for_timeout(900)
    has_data = p.evaluate("!!(window.HERO_DATA && window.HERO_DATA.load)")
    p.screenshot(path=RAW)
    b.close()

if not has_data:
    raise SystemExit("页面没有读到 HERO_DATA —— hero_data.js 加载失败")

im = Image.open(RAW)
print(f"光栅化 {im.size}")
if im.size != (W, H):
    im = im.resize((W, H), Image.LANCZOS)
im.convert("RGB").save(OUT, optimize=True)

# 锐度自检：文字区的中间灰 / 纯亮比，越低越锐
crop = im.convert("RGB").crop((100, 60, 900, 480))
white = gray = 0
for px in crop.getdata():
    m = sum(px) / 3
    if m > 230:
        white += 1
    elif 90 < m < 200:
        gray += 1
print(f"→ {OUT}  {os.path.getsize(OUT)/1024:.0f} KB")
print(f"   灰/亮 = {gray/max(white,1):.2f}（1× 渲染时是 1.10 左右）")

shutil.rmtree(WORK, ignore_errors=True)
