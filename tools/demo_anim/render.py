# -*- coding: utf-8 -*-
"""生成 README 顶部那条储能充放演示动画。

## 为什么有这个东西

README 里写着「这条动画的每一个点都是 algorithms/dispatch.py 真跑出来的」。
既然这么写，生成流程就必须留在仓库里——否则下一次改版，那句话会悄悄变成
谎话（或者图过期而没人发现）。

## 三步

1. `algorithms.dispatch.optimal_dispatch_lp` 算一天的最优充放策略
2. 把结果写成 `demo_data.js` 供页面读取
3. Playwright 逐帧渲染 `demo.html`，编码成动画 WebP

## 用法

    python tools/demo_anim/render.py

依赖：playwright（含 chromium）、pillow、scipy（LP 求解）。

## 为什么是 WebP 不是 GIF

GIF 只有 256 色，细线和小字会糊成毛边；APNG 1600×900 约 12 MB 太重。
WebP 同为真彩色，体积只有前者的零头，GitHub README 直接支持。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, REPO)

import numpy as np  # noqa: E402

N_STEPS, DT = 96, 0.25
FRAMES = 64          # 生长帧数
HOLD = 18            # 结尾停顿（以最后一帧的长时长实现）
FRAME_MS = 70        # ≈14 fps
OUT_W, OUT_H = 1600, 900

HTML = os.path.join(HERE, "demo.html")
DATA_JS = os.path.join(HERE, "demo_data.js")
TMP = os.path.join(HERE, "_frames")
ASSETS = os.path.join(REPO, "assets")


# ---------- 1. 算法驱动 ----------

def run_algorithm() -> dict:
    from algorithms.dispatch import optimal_dispatch_lp

    hours = np.arange(N_STEPS) * DT

    def price_at(h: float) -> float:
        if h < 7 or h >= 22:
            return 0.30          # 谷
        if (10 <= h < 12) or (18 <= h < 22):
            return 1.05          # 峰
        if 14 <= h < 16:
            return 1.20          # 尖
        return 0.65              # 平

    prices = np.array([price_at(h) for h in hours])

    def load_at(h: float) -> float:
        g = lambda c, s, a: a * np.exp(-((h - c) ** 2) / (2 * s * s))
        v = (210 + g(10.5, 2.4, 300) + g(12.6, 0.7, -95)
             + g(17.2, 2.9, 390) + g(3.0, 2.2, -60))
        return float(max(v, 40.0))

    rng = np.random.default_rng(20261029)
    load = np.array([load_at(h) for h in hours]) + rng.normal(0, 4.5, N_STEPS)

    cap, p_max, eta = 2000.0, 320.0, 0.95
    res = optimal_dispatch_lp(
        load_kw=load, buy_price=prices, sell_price=prices,
        batt_kwh=cap, Pmax=p_max, eta_c=eta, eta_d=eta,
        soc_min=0.08, soc_max=0.95, soc_init=0.12, dt=DT)

    if not res["feasible"]:
        raise SystemExit(f"LP 无可行解: {res.get('message')}")

    ch = np.asarray(res["ch"], dtype=float)
    dis = np.asarray(res["dis"], dtype=float)
    soc_pct = np.asarray(res["soc"], dtype=float) / cap * 100

    print(f"  LP 最优解：充 {ch.sum()*DT:,.0f} kWh / 放 {dis.sum()*DT:,.0f} kWh "
          f"/ 收益 {res['revenue']:,.0f} 元")
    print(f"  SOC {soc_pct.min():.1f}% ~ {soc_pct.max():.1f}%")

    return {
        "hours": hours.tolist(), "price": prices.tolist(),
        "load": load.tolist(), "p_ch": ch.tolist(), "p_dis": dis.tolist(),
        "soc": soc_pct.tolist(),
        "chg_kwh": round(float(ch.sum() * DT)),
        "dis_kwh": round(float(dis.sum() * DT)),
        "revenue": round(float(res["revenue"])),
        "cap": cap, "p_max": p_max, "eta": eta,
        "source": "algorithms.dispatch.optimal_dispatch_lp",
    }


# ---------- 2/3. 渲染 ----------

def render(data: dict) -> None:
    from playwright.sync_api import sync_playwright
    from PIL import Image

    with open(DATA_JS, "w", encoding="utf-8") as f:
        f.write("window.ALGO = " + json.dumps(data, ensure_ascii=False) + ";")

    os.makedirs(TMP, exist_ok=True)
    for f in os.listdir(TMP):
        if f.endswith(".png"):
            os.remove(os.path.join(TMP, f))

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1920, "height": 1080},
                                device_scale_factor=1)
        page.goto("file:///" + HTML.replace("\\", "/"))
        page.wait_for_timeout(600)
        for k in range(FRAMES):
            page.evaluate("t => window.renderFrame(t)", (k + 1) / FRAMES)
            page.wait_for_timeout(14)
            page.screenshot(path=os.path.join(TMP, f"f{k:03d}.png"))
            if (k + 1) % 16 == 0:
                print(f"  渲染 {k+1}/{FRAMES}")
        browser.close()

    frames = [Image.open(os.path.join(TMP, f"f{k:03d}.png")).convert("RGB")
              .resize((OUT_W, OUT_H), Image.LANCZOS) for k in range(FRAMES)]

    # Pillow 会消掉内容相同的帧，所以「停顿」要用最后一帧的长时长表达
    durs = [FRAME_MS] * (FRAMES - 1) + [FRAME_MS * HOLD]

    os.makedirs(ASSETS, exist_ok=True)
    webp = os.path.join(ASSETS, "demo.webp")
    frames[0].save(webp, format="WEBP", save_all=True,
                   append_images=frames[1:], duration=durs, loop=0,
                   quality=82, method=6)
    print(f"\n  WebP → {webp}  {os.path.getsize(webp)/1024:.0f} KB  "
          f"{OUT_W}x{OUT_H}  {sum(durs)/1000:.1f}s")

    gif = os.path.join(ASSETS, "demo.gif")
    pal = [f.convert("P", palette=Image.ADAPTIVE, colors=128) for f in frames]
    pal[0].save(gif, format="GIF", save_all=True, append_images=pal[1:],
                duration=durs, loop=0, optimize=True)
    print(f"  GIF  → {gif}  {os.path.getsize(gif)/1024:.0f} KB（回退用）")


if __name__ == "__main__":
    print("[1/2] 跑算法…")
    d = run_algorithm()
    print("[2/2] 渲染…")
    render(d)
    print("\n完成。README 引用的是 assets/demo.webp")
