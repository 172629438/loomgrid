# -*- coding: utf-8 -*-
"""三引擎对拍：同一条负荷、同一套电价，三种调度策略的 SOC 轨迹与收益对比。

产出 tools/demo_anim/engines_data.js 供对比图读取。

## 为什么重写

旧版自己算一遍，和落地页对照器（compare_data.py）对不上，三处口径不一致：

1. 仿真的效率被忽略（`dsoc/DT` 当功率用），充电成本低估 5%、放电收入高估 5%
2. 贪心的收益用「放电量×最高价 − 充电量×最低价」硬算，等于假设所有动作
   都卡在极端价上
3. 时段粒度一个 4 段、一个 96 段

现在是统一口径后跑出来的真数字：

    LP       充 2989 kWh  放 2736 kWh  省 ¥1676
    逐日仿真  充 2189 kWh  放 1653 kWh  省 ¥1199
    贪心      充    0 kWh  放   38 kWh  省 ¥  11

（旧版写的是 ¥1258 / ¥46 —— 两个都偏高。贪心那条偏得最多，虚高 4 倍。）
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
REPO = os.path.dirname(os.path.dirname(HERE))
OUT = os.path.join(HERE, "engines_data.js")

import numpy as np  # noqa: E402

import engine_common as EC  # noqa: E402


def build():
    load = EC.load_day()
    price = EC.price_day()
    cap, pmax = EC.CAP_DEFAULT, EC.PMAX_DEFAULT

    r = EC.run_all(load, price, cap, pmax)
    lp, sim, gr = r["lp"], r["sim"], r["greedy"]

    if not (lp["ok"] and sim["ok"] and gr["ok"]):
        raise SystemExit(f"求解失败: lp={lp['ok']} sim={sim['ok']} gr={gr['ok']}")

    rows = [
        {"name": "LP", "en": "linear programming", "color": "#F0CE72",
         "charge": round(float(np.sum(lp["ch"]) * EC.DT)),
         "discharge": round(float(np.sum(lp["dis"]) * EC.DT)),
         "saving": round(lp["saving"]),
         "soc": [round(float(v), 4) for v in lp["soc"][:EC.N]],
         "note": "全局最优，跨时段联合寻优"},
        {"name": "逐日仿真", "en": "daily simulation", "color": "#8FDDFF",
         "charge": round(float(np.sum(sim["ch"]) * EC.DT)),
         "discharge": round(float(np.sum(sim["dis"]) * EC.DT)),
         "saving": round(sim["saving"]),
         "soc": [round(float(v), 4) for v in sim["soc"][:EC.N]],
         "note": "按时段规则执行，含 SOC 上下限保护"},
        {"name": "贪心", "en": "greedy", "color": "#9AA3B5",
         "charge": round(float(np.sum(gr["ch"]) * EC.DT)),
         "discharge": round(float(np.sum(gr["dis"]) * EC.DT)),
         "saving": round(gr["saving"]),
         "soc": [round(float(v), 4) for v in gr["soc"][:EC.N]],
         "note": "新能源消纳优先，纯套利场景下不主动买电"},
    ]

    for x in rows:
        print(f"{x['name']:<6} 充 {x['charge']:>5}  放 {x['discharge']:>5}  "
              f"省 ¥{x['saving']:>5}")

    return {
        "hours": [round(float(h), 3) for h in EC.HOURS],
        "load": [round(float(v), 1) for v in load],
        "price": [round(float(v), 3) for v in price],
        "engines": rows,
        "stats": {"peak_kw": round(float(load.max())),
                  "cap_kwh": cap, "p_max": pmax,
                  "spread": round(float(price.max() - price.min()), 2)},
    }


def main():
    d = build()
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("window.ENGINES = " + json.dumps(d, ensure_ascii=False) + ";\n")
    print(f"\n→ {OUT}  {os.path.getsize(OUT)/1024:.1f} KB")


if __name__ == "__main__":
    main()
