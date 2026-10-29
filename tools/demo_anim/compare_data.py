# -*- coding: utf-8 -*-
"""为落地页的「配置对照器」生成真实网格数据。

三个引擎走 engine_common 的统一口径（同一负荷、同一电价、逐点计费、含效率）。
网格：容量 6 档 × 功率 4 档 × 价差 5 档 = 120 格。

前端只在「价差」这一维做线性插值，容量与功率吸附到网格点——
读者拖滑块看到的数字，就是算法真跑出来的数字，不是拟合出来的。

用法：
    python tools/demo_anim/compare_data.py
产出：
    docs/assets/compare_data.js
"""
from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
REPO = os.path.dirname(os.path.dirname(HERE))
OUT = os.path.join(REPO, "docs", "assets", "compare_data.js")

import engine_common as EC  # noqa: E402

CAPS = [1000, 1500, 2000, 2500, 3000, 4000]
PMS = [200, 300, 400, 600]
SPREADS = [0.40, 0.60, 0.80, 0.90, 1.10]


def main():
    load = EC.load_day()
    peak = float(load.max())

    out = {"lp": [], "sim": [], "greedy": []}
    t0 = time.time()
    total = len(CAPS) * len(PMS) * len(SPREADS)
    done = 0

    for cap in CAPS:
        lp_c, sim_c, gr_c = [], [], []
        for pm in PMS:
            lp_p, sim_p, gr_p = [], [], []
            for sp in SPREADS:
                price = EC.price_day(sp)
                lp = EC.run_lp(load, price, cap, pm)
                sim = EC.run_sim(load, price, cap, pm)
                gr = EC.run_greedy(load, price, cap, pm)
                lp_p.append(round(lp["saving"], 1) if lp["ok"] else None)
                sim_p.append(round(sim["saving"], 1) if sim["ok"] else None)
                gr_p.append(round(gr["saving"], 1) if gr["ok"] else None)
                done += 1
            lp_c.append(lp_p); sim_c.append(sim_p); gr_c.append(gr_p)
        out["lp"].append(lp_c); out["sim"].append(sim_c); out["greedy"].append(gr_c)
        print(f"  容量 {cap:>5} kWh  完成 {done}/{total}  {time.time()-t0:.1f}s")

    data = {
        "meta": {
            "caps": CAPS, "pms": PMS, "spreads": SPREADS,
            "eta": EC.ETA,
            "soc": [EC.SOC_MIN, EC.SOC_MAX, EC.SOC_INIT],
            "dt": EC.DT,
            "load_peak_kw": round(peak, 0),
            "price_base": EC.PRICE_BASE,
            "note": ("三个引擎在同一 96 点负荷与同一电价序列上求解；"
                     "口径为「基准购电成本 − 实际购电成本」，含充放效率，"
                     "逐点计价（不取最高/最低价），单位元/日。"),
        },
        "data": out,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("window.COMPARE_DATA = "
                + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n")

    print(f"\n→ {OUT}  {os.path.getsize(OUT)/1024:.1f} KB  用时 {time.time()-t0:.0f}s")

    i = CAPS.index(2000); j = PMS.index(400); k = SPREADS.index(0.90)
    print(f"\n默认工况（2000 kWh / 400 kW / 0.90 元）："
          f"LP ¥{out['lp'][i][j][k]}  仿真 ¥{out['sim'][i][j][k]}  "
          f"贪心 ¥{out['greedy'][i][j][k]}")


if __name__ == "__main__":
    main()
