# -*- coding: utf-8 -*-
"""端到端 · 容量测算口径：手算对拍。

为什么单独一层
--------------
容量测算是这个工具唯一「直接出钱」的部分。函数单测能证明公式没写错，
但证明不了「口径与业务意图一致」。这里用固定输入手算期望值，
每条断言都能在纸上复现。

梯形权重的 dt（必须记住这个）
----------------------------
N 个采样点的时段，总时长 = (N-2) × 0.25 + 2 × 0.125。

  8 小时时段（33 点）→ 0.125 + 31×0.25 + 0.125 = 8.0
  4 小时时段（17 点）→ 0.125 + 15×0.25 + 0.125 = 4.0

用 "3 小时" 当 "3" 会差 4%——这个错犯过一次。

跑法：python tests/verify_e2e_capacity.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
sys.path.insert(0, HERE)
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np  # noqa: E402
import _e2e_fixtures as F  # noqa: E402
from core.spaces import (  # noqa: E402
    compute_segmented_spaces, calc_capacity_for_days, calc_days_for_capacity)

PASS = FAIL = 0
TIMES = F.TIMES_96


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}  {detail}")


def close(a, b, tol=1e-6):
    return abs(float(a) - float(b)) <= tol


# ══════════════════════════════════════════════════════════
print("=" * 78)
print("① 手算对拍：恒定 100 kW，0-8 充、18-22 放，容量线 500、额定 300")
print("=" * 78)

load = np.full((1, 96), 100.0)
segs = [
    {"type": "charge", "start": 0.0, "end": 8.0, "label": "充电1", "sub_periods": []},
    {"type": "discharge", "start": 18.0, "end": 22.0, "label": "放电1", "sub_periods": []},
]

c_segs, d_segs, c_lab, d_lab = compute_segmented_spaces(
    load, TIMES, segs, billing={"value": 500.0, "type": "kva"},
    mode="A", rated_power=300.0)

# mode A 充电 = Σ(容量线 − 负荷) × dt = (500−100) × 8h = 3200
c_val = float(c_segs[0][0])
check("mode A 充电空间 = (500−100)×8h = 3200", close(c_val, 3200.0), f"实得 {c_val}")

# v2.3.16: 空间不乘效率（纯物理空间，效率在容量层用 DOD 引入）
# mode A 放电 = Σ min(额定, 负荷) × dt = min(300,100) × 4h = 400
d_val = float(d_segs[1][0])
check("mode A 放电空间 = min(300,100)×4h = 400", close(d_val, 400.0), f"实得 {d_val}")

check("四个列表等长", len(c_segs) == len(d_segs) == len(c_lab) == len(d_lab),
      f"{len(c_segs)}/{len(d_segs)}/{len(c_lab)}/{len(d_lab)}")
check("充电段在放电侧是占位零", close(float(d_segs[0][0]), 0.0), str(d_segs[0][0]))
check("放电段在充电侧是占位零", close(float(c_segs[1][0]), 0.0), str(c_segs[1][0]))

print()
print("=" * 78)
print("② mode B/C：充电只吃负负荷")
print("=" * 78)

load_neg = np.full((1, 96), -120.0)   # 全天负负荷（光伏返送）
c_b, d_b, _, _ = compute_segmented_spaces(
    load_neg, TIMES, segs, billing={"value": 500.0, "type": "kva"},
    mode="B", rated_power=300.0)
# v2.3.16: 空间不乘效率；充电 = Σ min(300, 120) × 8h = 960
check("mode B 充电 = min(300,120)×8h = 960",
      close(float(c_b[0][0]), 960.0), f"实得 {float(c_b[0][0])}")

# 同一份负负荷、mode A：充电 = (500−(−120))×8h = 4960（正负荷也能充）
c_a, _, _, _ = compute_segmented_spaces(
    load_neg, TIMES, segs, billing={"value": 500.0, "type": "kva"},
    mode="A", rated_power=300.0)
check("mode A 同数据充电 = (500+120)×8h = 4960",
      close(float(c_a[0][0]), 4960.0), f"实得 {float(c_a[0][0])}")
check("mode A 与 mode B 的充电口径不同（这是两套算法）",
      not close(float(c_a[0][0]), float(c_b[0][0])))

print()
print("=" * 78)
print("③ ignore_negative：把负负荷清零后，mode B 充电归 0")
print("=" * 78)

c_b2, _, _, _ = compute_segmented_spaces(
    load_neg, TIMES, segs, billing={"value": 500.0, "type": "kva"},
    mode="B", rated_power=300.0, ignore_negative=True)
check("mode B + ignore_negative → 充电为 0", close(float(c_b2[0][0]), 0.0),
      f"实得 {float(c_b2[0][0])}")

print()
print("=" * 78)
print("④ 容量线缺失：mode A 充电必须为 0（不是猜一个）")
print("=" * 78)

c_no, _, _, _ = compute_segmented_spaces(load, TIMES, segs, billing=None,
                                         mode="A", rated_power=300.0)
check("mode A 无容量线 → 充电为 0", close(float(c_no[0][0]), 0.0), f"实得 {float(c_no[0][0])}")

print()
print("=" * 78)
print("⑤ 容量-天数互算：同一组日需求，两个方向必须自洽")
print("=" * 78)
print("  语义：容量越小越容易满足（装得比当天空间大会充不满/放不完）。")
print("  「满足 N 天」→ 取第 (总天数-N+1) 小的最小容量。")

# 日需求序列：100,200,...,1000（10 天）
daily = np.array([100.0 * (i + 1) for i in range(10)])

# 满足全部 10 天 → 容量 = 最小（100）
cap_all = calc_capacity_for_days(10, daily)
check("满足全部 10 天 → 容量 = 最小值 100", close(cap_all, 100.0), f"实得 {cap_all}")

# 只满足 1 天 → 容量 = 最大（1000）
cap_one = calc_capacity_for_days(1, daily)
check("只满足 1 天 → 容量 = 最大值 1000", close(cap_one, 1000.0), f"实得 {cap_one}")

# 满足 5 天 → 第 (10-5+1)=6 小 = 600
cap_mid = calc_capacity_for_days(5, daily)
check("满足 5 天 → 第 6 小 = 600", close(cap_mid, 600.0), f"实得 {cap_mid}")

# 两个方向自洽：给定容量，反查天数
for target in (1, 5, 10):
    c = calc_capacity_for_days(target, daily)
    d = calc_days_for_capacity(c, daily, daily)
    check(f"满足 {target} 天 → 容量 {c:.0f} → 反查能得到 {target} 天",
          int(d) >= target, f"反查 {d} 天")

# 单调性：目标天数越大，容量越小（越容易满足）
caps = [calc_capacity_for_days(t, daily) for t in (1, 3, 5, 8, 10)]
check("目标天数越大 → 容量越小（单调不增）",
      all(caps[i] >= caps[i + 1] for i in range(len(caps) - 1)), str(caps))

print()
print("=" * 78)
print("⑥ 跨午夜时段：22:00-06:00 应拆成两段，总时长仍 8 小时")
print("=" * 78)

segs_mid = [
    {"type": "charge", "start": 22.0, "end": 6.0, "label": "充电1", "sub_periods": []},
    {"type": "discharge", "start": 10.0, "end": 12.0, "label": "放电1", "sub_periods": []},
]
c_mid, d_mid, _, _ = compute_segmented_spaces(
    load, TIMES, segs_mid, billing={"value": 500.0, "type": "kva"},
    mode="A", rated_power=300.0)
# 跨午夜充电段总时长 8 小时 → (500−100)×8 = 3200
mid_total = sum(float(a[0]) for a in c_mid)
check("跨午夜充电总空间 = 400×8h = 3200", close(mid_total, 3200.0), f"实得 {mid_total}")

print()
print("=" * 78)
print(f"结果：PASS {PASS}   FAIL {FAIL}")
print("=" * 78)
sys.exit(1 if FAIL else 0)
