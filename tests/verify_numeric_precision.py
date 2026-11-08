# -*- coding: utf-8 -*-
"""数值精度与 NaN 语义：全 NaN 必须报错、极值不截断、浮点误差可接受。

为什么这条重要
--------------
compute_segmented_spaces 大量用 np.nansum。NaN 会被**静默跳过**，
等于把缺失时段当 0 处理。而「按 0 算」不是保守——它会让那天的空间变成 0，
在容量反推里排到最小、优先被「满足」，于是容量建议偏小（高估收益）。

本轮修掉一条：全 NaN 输入原实现安静地返回全 0，用户看到的是「这个月没有
充放空间」而不是「数据有问题」。改为抛 ValueError。

跑法：python tests/verify_numeric_precision.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np  # noqa: E402

import core.spaces as S  # noqa: E402
from core.spaces import (  # noqa: E402
    calc_capacity_for_days, calc_days_for_capacity, compute_segmented_spaces,
)

PASS = FAIL = 0
TIMES = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 15, 30, 45)]
SEGS = [
    {"start": 0.0, "end": 8.0, "type": "charge", "label": "谷段"},
    {"start": 10.0, "end": 12.0, "type": "discharge", "label": "峰段"},
]


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}  {detail}")


# ══════════════════════════════════════════════════════════
print("=" * 74)
print("① 全 NaN 输入必须报错（本轮修复）")
print("=" * 74)
try:
    compute_segmented_spaces(np.full((2, 96), np.nan), TIMES, SEGS,
                             mode="A", billing={"value": 200})
    check("全 NaN → 抛 ValueError", False, "没有抛异常")
except ValueError as e:
    check("全 NaN → 抛 ValueError", True)
    print(f"       消息: {e}")
except Exception as e:
    check("全 NaN → 抛 ValueError", False, f"抛的是 {type(e).__name__}")

# 全 inf 也应报（np.isfinite 判据）
try:
    compute_segmented_spaces(np.full((1, 96), np.inf), TIMES, SEGS,
                             mode="A", billing={"value": 200})
    check("全 inf → 抛 ValueError", False, "没有抛异常")
except ValueError:
    check("全 inf → 抛 ValueError", True)

# 有部分有效值时不该报错
try:
    load = np.full((1, 96), 100.0)
    load[0, :48] = np.nan
    compute_segmented_spaces(load, TIMES, SEGS, mode="A", billing={"value": 200})
    check("部分 NaN（仍有有效值）不报错", True)
except Exception as e:
    check("部分 NaN（仍有有效值）不报错", False, f"{type(e).__name__}: {e}")

# ══════════════════════════════════════════════════════════
print()
print("=" * 74)
print("② 部分 NaN 的语义：按 0 计（记录现状，避免误当 bug）")
print("=" * 74)
load_nan_half = np.full((1, 96), 100.0)
load_nan_half[0, 0:32] = np.nan        # 前 8h 全 NaN，正是充电段
c_nan, _, _, _ = compute_segmented_spaces(load_nan_half, TIMES, SEGS,
                                          mode="A", billing={"value": 200})
load_full = np.full((1, 96), 100.0)
c_full, _, _, _ = compute_segmented_spaces(load_full, TIMES, SEGS,
                                           mode="A", billing={"value": 200})
v_nan = float(np.nansum(c_nan[0]))
v_full = float(np.nansum(c_full[0]))
check(f"缺失时段按 0 计：{v_nan:.0f}（全有效时 {v_full:.0f}）", v_nan == 0.0 and v_full == 800.0,
      f"{v_nan} vs {v_full}")
print("       注：这是 np.nansum 的固有语义，不是 bug。但它会让当天的空间偏小，")
print("           在容量反推里该天被优先「满足」→ 容量建议偏小。记录在案。")

# ══════════════════════════════════════════════════════════
print()
print("=" * 74)
print("③ 极值不被截断（注意放电空间在 d[1]，d[0] 是充电段占位零）")
print("=" * 74)
c, d, _, _ = compute_segmented_spaces(np.full((1, 96), 1e9), TIMES, SEGS,
                                      mode="B", rated_power=1e8)
got = float(np.nansum(d[1]))
expect = 2.0 * 1e8 * 0.93
check(f"负荷 1e9 / 额定 1e8 → {got:.4e}（期望 {expect:.4e}）",
      abs(got - expect) / expect < 1e-9, f"{got}")

c, d, _, _ = compute_segmented_spaces(np.full((1, 96), 1e-9), TIMES, SEGS,
                                      mode="B", rated_power=1.0)
got = float(np.nansum(d[1]))
expect = 2.0 * 1e-9 * 0.93
check(f"负荷 1e-9 → {got:.4e}（期望 {expect:.4e}）",
      abs(got - expect) / expect < 1e-9, f"{got}")

c, d, _, _ = compute_segmented_spaces(np.full((1, 96), 100.0), TIMES, SEGS,
                                      mode="B", rated_power=500.0)
check(f"负荷 100 / 额定 500 → 放电 {float(np.nansum(d[1])):.1f}（期望 186）",
      abs(float(np.nansum(d[1])) - 186.0) < 1e-6)

# ══════════════════════════════════════════════════════════
print()
print("=" * 74)
print("④ 浮点累加误差在可接受范围")
print("=" * 74)
for val in (0.1, 1 / 3, 0.001):
    arr = np.full(96 * 365, val)
    got = float(np.sum(arr))
    exact = val * 96 * 365
    rel = abs(got - exact) / exact if exact else 0.0
    check(f"96×365 个 {val!r} 累加，相对误差 {rel:.2e}",
          rel < 1e-12, f"{got} vs {exact}")

# ══════════════════════════════════════════════════════════
print()
print("=" * 74)
print("⑤ 效率常量与往返效率")
print("=" * 74)
check("CHARGING_EFFICIENCY = 0.93", S.CHARGING_EFFICIENCY == 0.93,
      str(S.CHARGING_EFFICIENCY))
check("DISCHARGING_EFFICIENCY = 0.93", S.DISCHARGING_EFFICIENCY == 0.93,
      str(S.DISCHARGING_EFFICIENCY))
rt = S.CHARGING_EFFICIENCY * S.DISCHARGING_EFFICIENCY
check(f"往返效率 {rt:.4f} = 0.93²", abs(rt - 0.8649) < 1e-9, str(rt))

# ══════════════════════════════════════════════════════════
print()
print("=" * 74)
print("⑥ 容量反推的边界：全相同值、含 NaN")
print("=" * 74)
same = np.full(365, 100.0)
for t in (1, 330, 365):
    cap = calc_capacity_for_days(t, same)
    check(f"全 100、目标 {t} 天 → 容量 100", abs(cap - 100.0) < 1e-9, str(cap))

arr = np.array([100.0, np.nan, 200.0, 300.0])
try:
    cap = calc_capacity_for_days(2, arr)
    print(f"       含 NaN 日需求 → 容量 {cap}（不崩即可，口径另议）")
    check("含 NaN 时容量反推不崩", True)
except Exception as e:
    check("含 NaN 时容量反推不崩", False, f"{type(e).__name__}: {e}")

print()
print("=" * 74)
print(f"结果：PASS {PASS}   FAIL {FAIL}")
print("=" * 74)
sys.exit(1 if FAIL else 0)
