# -*- coding: utf-8 -*-
"""三引擎对拍：同一条负荷、同一套电价，三种调度策略的 SOC 轨迹与收益对比。

产出 tools/demo_anim/engines_data.js 供对比图读取。

为什么要这张图：README 里写着「三套调度引擎并存，不是冗余」，但那只是断言。
实跑一遍才能看出差多少——而且差异比预想大得多：

    LP       充 2989 kWh  放 2736 kWh  省 ¥1676
    逐日仿真  充 2189 kWh  放 1656 kWh
    贪心      充    0 kWh  放   38 kWh   （新能源场景专用，纯套利下不动）
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, REPO)

import numpy as np  # noqa: E402

from algorithms.dispatch import (  # noqa: E402
    greedy_dispatch_annual,
    optimal_dispatch_lp,
    simulate_daily_dispatch,
)

N, DT = 96, 0.25
OUT = os.path.join(HERE, "engines_data.js")
CAP, PMAX, ETA = 2000.0, 400.0, 0.95


def build():
    hours = np.arange(N) * DT

    def price_at(h):
        if h < 7 or h >= 23:
            return 0.30
        if (10 <= h < 12) or (17 <= h < 22):
            return 1.05
        if 14 <= h < 16:
            return 1.20
        return 0.65

    prices = np.array([price_at(h) for h in hours])

    def load_at(h):
        g = lambda c, s, a: a * np.exp(-((h - c) ** 2) / (2 * s * s))
        return float(max(150 + g(10.2, 2.6, 780) + g(12.4, 0.8, -220)
                         + g(16.4, 2.4, 880) + g(19.6, 1.6, 460)
                         + g(3.5, 2.0, -55), 90.0))

    rng = np.random.default_rng(20261029)
    load = np.clip(np.array([load_at(h) for h in hours]) + rng.normal(0, 6.0, N),
                   80, None)
    base_cost = float(np.sum(load * prices * DT))

    # ---- 1. LP：全局最优 ----
    lp = optimal_dispatch_lp(
        load_kw=load, buy_price=prices, sell_price=prices,
        batt_kwh=CAP, Pmax=PMAX, eta_c=ETA, eta_d=ETA,
        soc_min=0.08, soc_max=0.95, soc_init=0.10, dt=DT)
    lp_ch, lp_dis = np.asarray(lp["ch"], float), np.asarray(lp["dis"], float)
    lp_soc = np.asarray(lp["soc"], float) / CAP
    lp_cost = float(np.sum((load - lp_dis + lp_ch) * prices * DT))

    # ---- 2. 逐日仿真：按时段规则跑 ----
    periods, cur = [], 0.0
    while cur < 24:
        h = cur + 0.01
        if h < 7 or h >= 23:
            t = "valley"
        elif (10 <= h < 12) or (17 <= h < 22) or (14 <= h < 16):
            t = "peak"
        else:
            t = "flat"
        periods.append((cur, min(cur + 0.25, 24), t))
        cur += 0.25

    sim = simulate_daily_dispatch(
        load_array=load.reshape(1, -1),
        time_labels=[f"{int(h):02d}:{int(round((h % 1) * 60)):02d}" for h in hours],
        periods=periods, capacity_kwh=CAP, power_kw=PMAX,
        efficiency=ETA, initial_soc=0.10, soc_min=0.08, soc_max=0.95)
    raw = np.asarray(sim["soc_trajectory"][0], float)
    # 求解器返回的是百分比（0-100），归一到 0-1；长度是 n+1，取前 n
    sim_soc = (raw / 100.0 if raw.max() > 1.5 else raw)[:N]
    sim_ch_total = float(sim["charge_kwh"][0])
    sim_dis_total = float(sim["discharge_kwh"][0])
    # 由 SOC 差分反推净功率，用于估算购电成本
    dsoc = np.diff(np.concatenate([[0.10], sim_soc])) * CAP
    net = dsoc / DT          # 正=充，负=放
    sim_cost = float(np.sum((load + net) * prices * DT))

    # ---- 3. 贪心：新能源消纳优先 ----
    gr = greedy_dispatch_annual(
        load_kw=load, pv_kw=None, wind_kw=None,
        batt_kwh=CAP, c_rate=PMAX / CAP, eta_roundtrip=0.90,
        soc_init_frac=0.10, soc_min_frac=0.08, soc_max_frac=0.95,
        dt_hours=DT)
    gr_ch_arr = np.asarray(gr["bess_charge"], float)
    gr_dis_arr = np.asarray(gr["bess_discharge"], float)
    gr_ch = float(np.sum(gr_ch_arr)) * DT
    gr_dis = float(np.sum(gr_dis_arr)) * DT
    gr_cost = base_cost - gr_dis * prices.max() + gr_ch * prices.min()

    # greedy 返回的 soc 单位不可靠（实测 1.6 量级，而容量是 2000 kWh），
    # 用充放功率自己积分一条，与另外两个引擎同口径
    gr_soc = np.empty(N)
    s = 0.10
    for i in range(N):
        s += (gr_ch_arr[i] * 0.95 - gr_dis_arr[i] / 0.95) * DT / CAP
        gr_soc[i] = float(np.clip(s, 0.08, 0.95))

    rows = [
        {"name": "LP", "en": "linear programming", "color": "#F0CE72",
         "charge": round(float(lp_ch.sum() * DT)),
         "discharge": round(float(lp_dis.sum() * DT)),
         "saving": round(base_cost - lp_cost),
         "soc": [round(float(v), 4) for v in lp_soc[:N]],
         "note": "全局最优，连续松弛下保证最优解"},
        {"name": "逐日仿真", "en": "daily simulation", "color": "#8FDDFF",
         "charge": round(sim_ch_total),
         "discharge": round(sim_dis_total),
         "saving": round(base_cost - sim_cost),
         "soc": [round(float(v), 4) for v in sim_soc],
         "note": "按时段规则执行，含 SOC 上下限保护"},
        {"name": "贪心", "en": "greedy", "color": "#9AA3B5",
         "charge": round(gr_ch),
         "discharge": round(gr_dis),
         "saving": round(gr_dis * prices.max() - gr_ch * prices.min()),
         "soc": [round(float(v), 4) for v in gr_soc],
         "note": "新能源消纳优先，纯套利场景下不主动买电"},
    ]

    for r in rows:
        print(f"{r['name']:<8} 充 {r['charge']:>5}  放 {r['discharge']:>5}  "
              f"省 ¥{r['saving']:>5}")

    return {
        "hours": [round(float(h), 3) for h in hours],
        "load": [round(float(v), 1) for v in load],
        "price": [round(float(v), 3) for v in prices],
        "engines": rows,
        "stats": {"peak_kw": round(float(load.max())),
                  "cap_kwh": CAP, "p_max": PMAX,
                  "spread": round(float(prices.max() - prices.min()), 2)},
    }


def main():
    d = build()
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("window.ENGINES = " + json.dumps(d, ensure_ascii=False) + ";\n")
    print(f"\n→ {OUT}  {os.path.getsize(OUT)/1024:.1f} KB")


if __name__ == "__main__":
    main()
