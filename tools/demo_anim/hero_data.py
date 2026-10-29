# -*- coding: utf-8 -*-
"""为首页 hero 生成真实算法数据。

hero 之前那条曲线是手写的 25 个点，和 demo.webp 的「真实 LP 解」不是一回事。
这个脚本把它换成真数据：造一条商业综合体日曲线，跑 optimal_dispatch_lp，
把功率、充放、SOC 三条轨迹全部输出。

用法：
    python tools/demo_anim/hero_data.py

产出 tools/demo_anim/hero_data.js，供 hero.html 读取。
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, REPO)

import numpy as np  # noqa: E402

N_STEPS, DT = 96, 0.25
OUT = os.path.join(HERE, "hero_data.js")


def build() -> dict:
    from algorithms.dispatch import optimal_dispatch_lp

    hours = np.arange(N_STEPS) * DT

    # 分时电价：谷 0.30 / 平 0.65 / 峰 1.05 / 尖 1.20
    def price_at(h: float) -> float:
        if h < 7 or h >= 23:
            return 0.30
        if (10 <= h < 12) or (17 <= h < 22):
            return 1.05
        if 14 <= h < 16:
            return 1.20
        return 0.65

    prices = np.array([price_at(h) for h in hours])

    # 商业综合体：早高峰 8-11、午后 14-17、晚间 18-21 三段
    def load_at(h: float) -> float:
        g = lambda c, s, a: a * np.exp(-((h - c) ** 2) / (2 * s * s))
        v = (150 + g(10.2, 2.6, 780) + g(12.4, 0.8, -220)
             + g(16.4, 2.4, 880) + g(19.6, 1.6, 460) + g(3.5, 2.0, -55))
        return float(max(v, 90.0))

    rng = np.random.default_rng(20261029)
    load = np.array([load_at(h) for h in hours]) + rng.normal(0, 6.0, N_STEPS)
    load = np.clip(load, 80, None)

    cap, p_max, eta = 2000.0, 400.0, 0.95
    res = optimal_dispatch_lp(
        load_kw=load, buy_price=prices, sell_price=prices,
        batt_kwh=cap, Pmax=p_max, eta_c=eta, eta_d=eta,
        soc_min=0.08, soc_max=0.95, soc_init=0.10, dt=DT)

    if not res or "ch" not in res:
        raise SystemExit(f"LP 求解失败: {res}")

    p_ch = np.asarray(res["ch"], dtype=float)
    p_dis = np.asarray(res["dis"], dtype=float)
    soc_raw = np.asarray(res.get("soc", []), dtype=float)
    if soc_raw.size != N_STEPS:
        # 自己推一遍 SOC（与求解器同口径）
        soc_raw = np.empty(N_STEPS)
        s = 0.10 * cap
        for i in range(N_STEPS):
            s += (p_ch[i] * eta - p_dis[i] / eta) * DT
            soc_raw[i] = s
    # 求解器返回的是 kWh，归一化成 0~1
    soc = np.clip(soc_raw / cap, 0.0, 1.0)

    net = load - p_dis + p_ch
    cost = float(np.sum(load * prices * DT))
    saving = cost - float(np.sum(net * prices * DT))

    print(f"充 {p_ch.sum()*DT:.0f} kWh  放 {p_dis.sum()*DT:.0f} kWh")
    print(f"SOC {soc.min()*100:.1f}% ~ {soc.max()*100:.1f}%")
    print(f"日省 ¥{saving:.0f}   峰 {load.max():.0f} kW")

    return {
        "hours": [round(float(h), 3) for h in hours],
        "load": [round(float(v), 1) for v in load],
        "price": [round(float(v), 3) for v in prices],
        "charge": [round(float(v), 1) for v in p_ch],
        "discharge": [round(float(v), 1) for v in p_dis],
        "soc": [round(float(v), 4) for v in soc],
        "stats": {
            "peak_kw": round(float(load.max()), 0),
            "charge_kwh": round(float(p_ch.sum() * DT), 0),
            "discharge_kwh": round(float(p_dis.sum() * DT), 0),
            "saving_cny": round(saving, 0),
            "soc_min": round(float(soc.min()) * 100, 1),
            "soc_max": round(float(soc.max()) * 100, 1),
            "cap_kwh": cap,
            "p_max": p_max,
            "spread": round(float(prices.max() - prices.min()), 2),
        },
    }


def main():
    d = build()
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("window.HERO_DATA = " + json.dumps(d, ensure_ascii=False) + ";\n")
    print(f"\n→ {OUT}  {os.path.getsize(OUT)/1024:.1f} KB")


if __name__ == "__main__":
    main()
