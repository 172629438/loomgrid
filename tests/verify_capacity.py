# -*- coding: utf-8 -*-
"""容量与吞吐量口径验证。

验什么
------
1. min_feasible 的定义：min(所有段当日的值)
2. full_load 的定义：max(min(每对的充, 放))
3. daily_throughput 的定义：sum(min(charge_i, discharge_i))，量纲是 kWh/天
4. 两者量纲不同 —— 数值不该可比，单段配置吞吐必为 0
5. relevant_segments 的四种选择模式

为什么单独验
------------
C3 那条待确认项的根子就是量纲混用：一个「装多大」的容量被记成「各段 min 之和」，
而后者是「一天搬多少」。这组断言把两个量纲钉死在定义上。

不 import 主程序、不依赖 Qt。
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402

from core.models import PeriodType  # noqa: E402
from core.spaces import compute_segmented_spaces  # noqa: E402
from services.capacity import (  # noqa: E402
    DemandBasis, daily_demand_series, daily_throughput_series,
    relevant_segments, summarize_daily_demand, throughput_breakdown,
)

VERBOSE = "-v" in sys.argv
PASS = FAIL = 0
FAILS = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        if VERBOSE:
            print(f"[PASS] {name}")
    else:
        FAIL += 1
        FAILS.append((name, detail))
        print(f"[FAIL] {name}  {detail}")


N = 96
LABELS = [f"{(i * 15) // 60:02d}:{(i * 15) % 60:02d}" for i in range(N)]


def seg(start, end, kind):
    return {"start": start, "end": end, "label": kind,
            "type": PeriodType.CHARGE if kind.startswith("c") else PeriodType.DISCHARGE}


# ── A. 口径定义 ─────────────────────────────────────────────
print("A 日需求口径")
# 两天、四段：充电 0-8、放电 8-11、充电 11-14、放电 14-20
segs = [seg(0, 8, "c1"), seg(8, 11, "d1"), seg(11, 14, "c2"), seg(14, 20, "d2")]
load = np.full((2, N), 300.0)
ch, dis, cl, dl = compute_segmented_spaces(load, LABELS, segs,
                                           billing={"value": 500.0}, mode="A")
# ch/dis 是四个段 × 两天；每段一个数组
check("A0 段数正确", len(ch) == 4 and len(dis) == 4, f"{len(ch)}/{len(dis)}")

minf = daily_demand_series(ch, dis, DemandBasis.min_feasible)
# 第 0 天：四段值 = [1600(充1), 0(占位), 800(充2), 0(占位)] 与放电侧对应
# min_feasible = min(所有非空段值) —— 含占位零，所以应为 0
expected_min = min([float(a[0]) for a in ch] + [float(a[0]) for a in dis])
check("A1 min_feasible = min(所有段值)", abs(float(minf[0]) - expected_min) < 1e-9,
      f"{minf[0]:.3f} vs {expected_min:.3f}")

full = daily_demand_series(ch, dis, DemandBasis.full_load)
# full_load = max over pairs of min(charge_i, discharge_i)
n_pairs = max(len(ch), len(dis))
best = 0.0
for i in range(n_pairs):
    cv = float(ch[i][0]) if i < len(ch) else float("inf")
    dv = float(dis[i][0]) if i < len(dis) else float("inf")
    pm = min(cv, dv)
    if pm != float("inf"):
        best = max(best, pm)
check("A2 full_load = max(min(每对充放))", abs(float(full[0]) - best) < 1e-9,
      f"{full[0]:.3f} vs {best:.3f}")

s = summarize_daily_demand(ch, dis, DemandBasis.min_feasible)
check("A3 summarize 返回 basis 与 value", s["basis"] == "min_feasible" and "value_kwh" in s)
check("A4 summarize 的 daily 长度 = 天数", len(s["daily"]) == 2, f"{len(s['daily'])}")

# ── B. 日吞吐量 ────────────────────────────────────────────
print("B 日吞吐量")
tp = daily_throughput_series(ch, dis)
total_c = np.zeros(2)
total_d = np.zeros(2)
for a in ch:
    total_c += np.asarray(a, dtype=float)
for a in dis:
    total_d += np.asarray(a, dtype=float)
manual = np.minimum(total_c, total_d)
check("B1 吞吐量 = min(当日总充, 当日总放)",
      np.allclose(tp, manual, atol=1e-9), f"{tp} vs {manual}")

# 旧版用「逐段 min 再求和」，充放通常在不同时段 → 恒为 0；现在应该非零
check("B2 充放分段时吞吐量非零（旧公式这里恒为 0）",
      float(np.sum(tp)) > 0, f"合计 {float(np.sum(tp)):.1f}")

# 瓶颈方向
tb = throughput_breakdown(ch, dis)
check("B3 min_side 指出瓶颈侧",
      tb["min_side"] in ("charge", "discharge", "balanced"),
      f"min_side={tb['min_side']}  充 {tb['total_charge_kwh']:.1f} / 放 {tb['total_discharge_kwh']:.1f}")
check("B4 典型值 = 逐日均值",
      abs(tb["value_kwh_per_day"] - float(np.mean(tp))) < 1e-6,
      f"{tb['value_kwh_per_day']:.3f}")
check("B5 逐日序列长度 = 天数", len(tb["per_day"]) == 2, f"{len(tb['per_day'])}")

# ── C. 单侧为 0 ────────────────────────────────────────────
print("C 单侧为 0")
only_c = [seg(0, 8, "c1")]
ch2, dis2, _, _ = compute_segmented_spaces(load, LABELS, only_c,
                                           billing={"value": 500.0}, mode="A")
tp2 = daily_throughput_series(ch2, dis2)
check("C1 只有充电段时吞吐量为 0（确实搬不动）", abs(float(np.sum(tp2))) < 1e-9,
      f"合计 {float(np.sum(tp2)):.3f}")
check("C2 min_side 标为 discharge（缺放这一侧）",
      throughput_breakdown(ch2, dis2)["min_side"] == "discharge",
      throughput_breakdown(ch2, dis2)["min_side"])

# ── D. 段选择 ──────────────────────────────────────────────
print("D 段选择")
c_segs = [np.array([100.0, 200.0]), np.array([50.0, 60.0])]
d_segs = [np.array([80.0, 90.0])]
labels_c = ["充电1", "充电2"]
labels_d = ["放电1"]

sc, sd = relevant_segments(c_segs, d_segs, labels_c, labels_d, mode="all")
check("D1 all 返回全部", len(sc) == 2 and len(sd) == 1)

sc, sd = relevant_segments(c_segs, d_segs, labels_c, labels_d, charge_pick="充电2")
check("D2 按名字取单段", len(sc) == 1 and np.allclose(sc[0], c_segs[1]))

sc, sd = relevant_segments(c_segs, d_segs, labels_c, labels_d, charge_pick="merge")
check("D3 merge 合并前两段", len(sc) == 1 and np.allclose(sc[0], c_segs[0] + c_segs[1]))

sc, sd = relevant_segments(c_segs, d_segs, labels_c, labels_d, charge_pick="不存在")
check("D4 未知名回退到第一段", len(sc) == 1 and np.allclose(sc[0], c_segs[0]))

# ── E. 量纲区分 ────────────────────────────────────────────
print("E 量纲区分与占位零")
load2 = np.vstack([np.full(N, 100.0), np.full(N, 900.0)])
ch3, dis3, _, _ = compute_segmented_spaces(load2, LABELS, segs,
                                           billing={"value": 500.0}, mode="A")

# 当前界面口径：不过滤占位零。每个段在非本方向那一侧是占位 0，
# 所以 min_feasible 的 min(所有值) 与 full_load 的 max(min(每对)) 都恒为 0。
# 这是 C3 待确认口径问题的实质——本节把它钉住：行为不变，但问题必须可见。
d_min = summarize_daily_demand(ch3, dis3, DemandBasis.min_feasible)["value_kwh"]
d_full = summarize_daily_demand(ch3, dis3, DemandBasis.full_load)["value_kwh"]
check("E1 不过滤占位零时两种口径都为 0（已知行为）",
      abs(d_min) < 1e-9 and abs(d_full) < 1e-9,
      f"min_feasible={d_min:.1f} / full_load={d_full:.1f}")

# 过滤占位零后应有非零结果
minf_f = daily_demand_series(ch3, dis3, DemandBasis.min_feasible, drop_zero_placeholders=True)
full_f = daily_demand_series(ch3, dis3, DemandBasis.full_load, drop_zero_placeholders=True)
check("E2 过滤占位零后 min_feasible 非零", float(np.max(minf_f)) > 0,
      f"max={float(np.max(minf_f)):.2f}")
check("E3 过滤后两种口径给出不同值",
      abs(float(np.max(minf_f)) - float(np.max(full_f))) > 1e-9,
      f"min_feasible={float(np.max(minf_f)):.1f} / full_load={float(np.max(full_f)):.1f}")
check("E4 两者都是 kWh 量级（不是吞吐量）",
      float(np.max(full_f)) < 1e6, "若出现 1e6 以上说明误把吞吐量当成容量")

print()
if FAILS:
    print("失败项：")
    for n, d in FAILS:
        print(f"  - {n}  {d}")
print(f"结果：PASS {PASS}   FAIL {FAIL}")
sys.exit(1 if FAIL else 0)
