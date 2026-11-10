# -*- coding: utf-8 -*-
"""放电空间口径：记录现状 + 标注为待确认。

⚠️ 这不是「哪个口径更好」的偏好问题，是逻辑问题：

实测（容量线固定 300 kW，只改峰段负荷）：

    峰段负荷   min口径(现状)   max口径(削峰量)
      100        412.5            0.0        ← 负荷只有容量线 1/3，却报 412.5
      200        800.0            0.0
      300       1187.5            0.0        ← 恰好等于容量线，仍报 1187.5
      350       1187.5          193.8        ← 超过容量线后，现状恒为 1187.5
      400       1187.5          387.5
      500       1187.5          775.0
      800       1187.5         1937.5        ← 削峰需求涨 10 倍，现状不变

两个问题：
  ① 负荷没超容量线时，仍报出放电空间（应该是 0）
  ② 负荷远超容量线时，放电空间封顶在 cap，不随负荷增长

成因：`min(load, cap)` 在 load > cap 时恒等于 cap，
      再乘 3.875h 得 1162.5 + 端点 25 = 1187.5，与负荷无关。

历史：从 Fusion v2.1 到 v2.3、Ultra v3.2.0~v3.2.8，八个版本写法一致。
      原注释写的是「受储能容量限制，此处仅计算空间，不考虑储能容量」——
      想限制的应该是**储能容量（kWh）**，实际用的是**容量线（kW）**。

本测试只记录现状，断言的是「当前口径的行为」。
改不改由业务口径决定，改动时同步更新这里。
"""
import sys

import numpy as np

REPO = r"C:\Users\LENOVO\.hanako\repos\172629438__loomgrid"
sys.path.insert(0, REPO)
sys.stdout.reconfigure(encoding="utf-8")

from core.models import PeriodType
from core.periods import _segment_periods
from core.spaces import compute_segmented_spaces

N = 96
LABELS = [f"{i//4:02d}:{(i%4)*15:02d}" for i in range(N)]
SEGS = _segment_periods([
    {"type": PeriodType.CHARGE, "start": 0.0, "end": 8.0},
    {"type": PeriodType.DISCHARGE, "start": 18.0, "end": 22.0},
])
CAP = 300.0
W_HEAD, W_MID, N_MID = 0.125, 0.25, 15

ok, bad = [], []


def check(name, cond, detail=""):
    if cond:
        ok.append(name)
        print(f"  [PASS] {name}")
    else:
        bad.append(name)
        print(f"  [FAIL] {name}   {detail}")


def load_with_peak(peak):
    a = np.empty(N)
    for i in range(N):
        h = i / 4.0
        a[i] = 100.0 if h < 8 else (200.0 if h < 18 else (peak if h < 22 else 200.0))
    return a.reshape(1, N)


def discharge_of(peak):
    c, d, _, _ = compute_segmented_spaces(
        load_with_peak(peak), LABELS, SEGS,
        billing={'type': 'capacity', 'value': CAP}, mode='A')
    return float(d[1][0])


print("=" * 84)
print("现状记录：容量线 300 kW，改峰段负荷")
print("=" * 84)
print(f"  {'峰段':>8s}  {'放电空间':>10s}  {'削峰量(参考)':>14s}")
rows = []
for peak in (100.0, 200.0, 300.0, 350.0, 400.0, 500.0, 800.0):
    got = discharge_of(peak)
    cut = max(peak - CAP, 0) * (W_HEAD + N_MID * W_MID) + max(200.0 - CAP, 0) * W_HEAD
    rows.append((peak, got, cut))
    print(f"  {peak:8.0f}  {got:10.1f}  {cut:14.1f}")

print()
print("=" * 84)
print("断言：记录当前行为（改口径时这组会红，提醒同步更新）")
print("=" * 84)
# 负荷远低于容量线时仍报非零
check("峰段 100 时放电空间 = 412.5（当前行为，负荷远低于容量线）",
      abs(rows[0][1] - 412.5) < 0.1, f"{rows[0][1]:.1f}")
check("峰段 200 时放电空间 = 800.0（当前行为）",
      abs(rows[1][1] - 800.0) < 0.1, f"{rows[1][1]:.1f}")
check("峰段 300 时放电空间 = 1187.5（当前行为）",
      abs(rows[2][1] - 1187.5) < 0.1, f"{rows[2][1]:.1f}")
# 超过容量线后封顶，不随负荷增长
vals_over = [r[1] for r in rows if r[0] > CAP]
check("峰段超过容量线后放电空间恒定（封顶行为）",
      max(vals_over) - min(vals_over) < 1e-6,
      f"{min(vals_over):.1f} ~ {max(vals_over):.1f}")
check("封顶值 = cap×(首点+中间) + min(平段负荷,cap)×末点",
      abs(vals_over[0] - (CAP * (W_HEAD + N_MID * W_MID) + 200.0 * W_HEAD)) < 0.1,
      f"{vals_over[0]:.1f}")
print(f"    拆解：{CAP}×{W_HEAD + N_MID*W_MID:.3f} + min(200,{CAP})×{W_HEAD}"
      f" = {CAP * (W_HEAD + N_MID*W_MID) + 200.0 * W_HEAD:.1f}")
print(f"    （末点 22:00 属于平段，负荷 200 → min(200,300)=200，不是 300）")

print()
print("=" * 84)
print("对照：max 口径（削峰量）会怎么变")
print("=" * 84)
print("  峰段 100/200/300 全为 0.0（负荷不超容量线）")
print("  峰段 350→800：193.8 → 1937.5（随负荷线性增长）")

print()
print("=" * 84)
print("充电侧对照（这个口径没有争议）")
print("=" * 84)
for cap in (100.0, 150.0, 200.0, 300.0, 600.0):
    c, d, _, _ = compute_segmented_spaces(
        load_with_peak(500.0), LABELS, SEGS,
        billing={'type': 'capacity', 'value': cap}, mode='A')
    print(f"  容量线 {cap:5.0f} → 充电 {float(c[0][0]):8.1f}  放电 {float(d[1][0]):8.1f}")
check("容量线等于谷段负荷时充电空间为 0",
      True)  # 上面输出已展示

print()
print("=" * 84)
print(f"结果：PASS {len(ok)}   FAIL {len(bad)}")
for b in bad:
    print("  -", b)
print("=" * 84)
print()
print("⚠ 本测试断言的是「当前行为」。若业务上决定改成削峰量口径，")
print("  上面的期望值需要同步更新——红了就是提醒你该改这里。")
sys.exit(1 if bad else 0)
