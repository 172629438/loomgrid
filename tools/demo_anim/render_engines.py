# -*- coding: utf-8 -*-
"""渲染三引擎对拍图 -> assets/engines.png。

两步：
    python tools/demo_anim/engines_data.py    # 跑三个引擎，出数据
    python tools/demo_anim/render_engines.py  # 渲染成图
"""
import os
import shutil

from PIL import Image
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))

HTML = os.path.join(HERE, "engines.html")
DATA = os.path.join(HERE, "engines_data.js")
OUT = os.path.join(REPO, "assets", "engines.png")
WORK = os.path.join(HERE, "_render_tmp")
W, H = 2400, 1320

if not os.path.exists(DATA):
    raise SystemExit(f"缺 {DATA}\n先跑: python tools/demo_anim/engines_data.py")

os.makedirs(WORK, exist_ok=True)
shutil.copy2(HTML, os.path.join(WORK, "index.html"))
shutil.copy2(DATA, os.path.join(WORK, "engines_data.js"))
RAW = os.path.join(WORK, "raw.png")

with sync_playwright() as pw:
    b = pw.chromium.launch(args=["--force-color-profile=srgb",
                                 "--font-render-hinting=none"])
    p = b.new_page(viewport={"width": W, "height": H}, device_scale_factor=2)
    p.goto("file:///" + os.path.join(WORK, "index.html").replace("\\", "/"))
    p.wait_for_timeout(800)
    ok = p.evaluate("!!(window.ENGINES && window.ENGINES.engines)")
    p.screenshot(path=RAW)
    b.close()

if not ok:
    raise SystemExit("页面没有读到 ENGINES —— engines_data.js 加载失败")

im = Image.open(RAW)
print(f"光栅化 {im.size}")
if im.size != (W, H):
    im = im.resize((W, H), Image.LANCZOS)
im.convert("RGB").save(OUT, optimize=True)
print(f"OK {OUT}  {os.path.getsize(OUT)/1024:.0f} KB")

shutil.rmtree(WORK, ignore_errors=True)
