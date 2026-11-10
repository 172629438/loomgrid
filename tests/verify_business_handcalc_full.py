# -*- coding: utf-8 -*-
"""全链路手算对照：core 层四个业务函数逐个核数字。

造一份答案已知的负荷数据，独立手算，与代码输出逐项比对。
不依赖 Qt / API，只调 core。

设计的数据（96 点 / 15 分钟）：
    00:00-08:00   谷段，负荷恒 100 kW      （32 点）
    08:00-18:00   平段，负荷恒 200 kW      （40 点）
    18:00-22:00   峰段，负荷恒 500 kW      （16 点）
    22:00-24:00   平段，负荷恒 200 kW      （8 点）

容量线 cap = 300 kW
"""
import os
import sys

import numpy as np

REPO = r"C:\Users\LENOVO\.hanako\repos\172629438__loomgrid"
sys.path.insert(0, REPO)
sys.stdout.reconfigure(encoding="utf-8")

from core.models import PeriodType
from core.periods import _segment_periods
from core.spaces import (compute_segmented_spaces, calc_capacity_for_days,
                         calc_days_for_capacity)

ok, bad = [], []


def check(name, cond, got, expect, tol=1e-6):
    if cond:
        ok.append(name)
        print(f"  [PASS] {name}   得 {got}")
    else:
        bad.append(name)
        print(f"  [FAIL] {name}   得 {got}  应 {expect}")


# ---------- 造数据 ----------
N = 96
labels = [f"{i//4:02d}:{(i%4)*15:02d}" for i in range(N)]
load = np.empty(N)
for i in range(N):
    h = i / 4.0
    if h < 8:
        load[i] = 100.0
    elif h < 18:
        load[i] = 200.0
    elif h < 22:
        load[i] = 500.0
    else:
        load[i] = 200.0
load_2d = load.reshape(1, N)
CAP = 300.0

print("=" * 80)
print("数据")
print("=" * 80)
print(f"  96 点：0-8 谷段 100kW / 8-18 平段 200kW / 18-22 峰段 500kW / 22-24 平段 200kW")
print(f"  容量线 {CAP} kW，一天")

# ---------- ① 时段切分 ----------
print()
print("=" * 80)
print("① _segment_periods：峰谷切段")
print("=" * 80)
periods = [
    {"type": PeriodType.CHARGE, "start": 0.0, "end": 8.0},
    {"type": PeriodType.DISCHARGE, "start": 18.0, "end": 22.0},
]
segs = _segment_periods(periods)
print(f"  输入 2 段 → 输出 {len(segs)} 段")
for s in segs:
    print(f"    {s['type']:<12} {s['start']:.2f}-{s['end']:.2f}  label={s['label']}")
check("切段数 = 2", len(segs) == 2, len(segs), 2)
check("第一段是充电", segs[0]['type'] in (PeriodType.CHARGE, PeriodType.CHARGE_LABEL),
      segs[0]['type'], "charge")
check("第二段是放电", segs[1]['type'] in (PeriodType.DISCHARGE, PeriodType.DISCHARGE_LABEL),
      segs[1]['type'], "discharge")
check("充电段跨 0-8", abs(segs[0]['start'] - 0) < 1e-9 and abs(segs[0]['end'] - 8) < 1e-9,
      f"{segs[0]['start']}-{segs[0]['end']}", "0-8")
check("放电段跨 18-22", abs(segs[1]['start'] - 18) < 1e-9 and abs(segs[1]['end'] - 22) < 1e-9,
      f"{segs[1]['start']}-{segs[1]['end']}", "18-22")

# ---------- ② Mode A 空间 ----------
print()
print("=" * 80)
print("② compute_segmented_spaces · Mode A")
print("=" * 80)
print("  口径（读代码得出）：")
print("    充电 = Σ max(cap − 负荷, 0) × dt   ← 容量线以下的余量")
print("    放电 = Σ min(负荷, cap) × dt        ← 负荷被容量线削顶后的部分")
print()
c_segs, d_segs, c_lab, d_lab = compute_segmented_spaces(
    load_2d, labels, segs, billing={'type': 'capacity', 'value': CAP}, mode='A')

print(f"  返回 charge_segs {len(c_segs)} 个，discharge_segs {len(d_segs)} 个")
c_val = float(c_segs[0][0])
d_val = float(d_segs[1][0])
print(f"  充电空间 {c_val:.1f} kWh   放电空间 {d_val:.1f} kWh")

# 手算
# ⚠ 段是「含左含右」的：0-8 段取 0:00…8:00 共 33 点，首末点权重 0.125。
# 而 8:00 这个点在数据里属于平段（负荷 200），不是谷段的 100。
# 所以不能简单用 (cap-100)×8，必须算端点那 0.125h 的差异。
# 这正是 verify_v4_handcalc.py docstring 里提醒的：
# 「恒负荷测试无法区分端点约定，必须用变负荷」。
w_head = 0.125                      # 首末点权重
w_mid = 0.25                        # 中间点权重
n_mid_charge = 31                   # 0:15 … 7:45，共 31 点
hand_charge = ((CAP - 100.0) * (w_head + n_mid_charge * w_mid)
               + (CAP - 200.0) * w_head)      # 末点 8:00 是平段负荷 200
# 放电段 18-22：中间 15 点（18:15…21:45）负荗 500，末点 22:00 是 200
n_mid_dsch = 15
hand_discharge_min = (min(500.0, CAP) * (w_head + n_mid_dsch * w_mid)
                      + min(200.0, CAP) * w_head)
hand_discharge_alt = (max(500.0 - CAP, 0) * (w_head + n_mid_dsch * w_mid)
                      + max(200.0 - CAP, 0) * w_head)
print()
print("  手算（含端点权重 0.125）")
print(f"    充电 = (300−100)×7.875 + (300−200)×0.125 = {hand_charge:.1f}")
print(f"    放电 min 口径 = min(500,300)×3.875 + min(200,300)×0.125 = {hand_discharge_min:.1f}")
print(f"    放电 max 口径 = (500−300)×3.875 + 0×0.125 = {hand_discharge_alt:.1f}")
print()
print(f"  若按「整段恒负荷」粗算（错误做法）：充电 (300−100)×8 = 1600，放电 1200")
check("充电空间与手算一致", abs(c_val - hand_charge) < 0.1, f"{c_val:.1f}", round(hand_charge, 1))
check("放电空间用 min(负荷,cap) 口径",
      abs(d_val - hand_discharge_min) < 0.1, f"{d_val:.1f}", round(hand_discharge_min, 1))
print()
print(f"  两种放电口径差 {hand_discharge_min - hand_discharge_alt:.0f} kWh"
      f"（{hand_discharge_min:.0f} vs {hand_discharge_alt:.0f}）")
print(f"  代码走的是 min(负荷, cap)，即「负荷被容量线削顶后的部分」")

# 占位零检查
print()
print(f"  四个列表同长：{len(c_segs)} == {len(d_segs)} "
      f"→ {len(c_segs) == len(d_segs)}")
check("四个返回列表同长", len(c_segs) == len(d_segs) == len(c_lab) == len(d_lab),
      (len(c_segs), len(d_segs), len(c_lab), len(d_lab)), "全部相等")
check("充电段在放电侧是占位零", float(d_segs[0][0]) == 0.0, float(d_segs[0][0]), 0.0)
check("放电段在充电侧是占位零", float(c_segs[1][0]) == 0.0, float(c_segs[1][0]), 0.0)

# ---------- ③ 容量反推 ----------
print()
print("=" * 80)
print("③ calc_capacity_for_days：满足 N 天")
print("=" * 80)
# 造 5 天的日最小容量序列
daily_mins = np.array([100.0, 200.0, 300.0, 400.0, 500.0])
for n_days in (1, 2, 3, 5):
    got = calc_capacity_for_days(n_days, daily_mins)
    # 满足 n 天 = 取第 (len-n+1) 小 = 升序第 (len-n) 个
    expect = sorted(daily_mins)[len(daily_mins) - n_days]
    print(f"  满足 {n_days} 天 → {got}  （升序第 {len(daily_mins)-n_days+1} 小 = {expect}）")
    check(f"满足 {n_days} 天", abs(got - expect) < 1e-9, got, expect)

print()
print("  语义核对：容量越小越容易满足")
# calc_days_for_capacity(capacity, charge_kwh, discharge_kwh) 需要两个数组。
# 这里把同一个 daily_mins 同时传给充放两侧——两方向空间相同时，
# 判定等价于「容量 ≤ 该日空间」，便于单独验方向。
for cap in (50.0, 150.0, 250.0, 450.0, 550.0):
    d = calc_days_for_capacity(np.array([cap]), daily_mins, daily_mins)
    print(f"    容量 {cap:5.0f} → 满足 {d} 天")
check("容量 50 满足全部 5 天",
      calc_days_for_capacity(np.array([50.0]), daily_mins, daily_mins) == 5,
      calc_days_for_capacity(np.array([50.0]), daily_mins, daily_mins), 5)
check("容量 550 满足 0 天（超过所有日最小值）",
      calc_days_for_capacity(np.array([550.0]), daily_mins, daily_mins) == 0,
      calc_days_for_capacity(np.array([550.0]), daily_mins, daily_mins), 0)
check("容量 300 满足 3 天",
      calc_days_for_capacity(np.array([300.0]), daily_mins, daily_mins) == 3,
      calc_days_for_capacity(np.array([300.0]), daily_mins, daily_mins), 3)

# 反推与正推互逆
print()
for n in (1, 2, 3, 4, 5):
    cap = calc_capacity_for_days(n, daily_mins)
    back = calc_days_for_capacity(np.array([cap]), daily_mins, daily_mins)
    print(f"    满足 {n} 天 → 容量 {cap:5.0f} → 反推满足 {back} 天")
    check(f"互逆：{n} 天", back >= n, back, f">= {n}")

# ---------- ④ 边界 ----------
print()
print("=" * 80)
print("④ 边界")
print("=" * 80)
# 全 NaN 必须报错
try:
    compute_segmented_spaces(np.full((2, N), np.nan), labels, segs, mode='A')
    check("全 NaN 报错", False, "没报错", "ValueError")
except ValueError as e:
    print(f"  全 NaN → ValueError: {str(e)[:46]}")
    check("全 NaN 报错", True, "ValueError", "ValueError")

# 无容量线 → 空间为 0
c0, d0, _, _ = compute_segmented_spaces(load_2d, labels, segs, billing=None, mode='A')
check("无容量线时充电空间为 0", float(c0[0][0]) == 0.0, float(c0[0][0]), 0.0)
check("无容量线时放电空间为 0", float(d0[1][0]) == 0.0, float(d0[1][0]), 0.0)

# 容量线高于所有负荷 → 充电空间大、放电空间=负荷总和
c2, d2, _, _ = compute_segmented_spaces(
    load_2d, labels, segs, billing={'type': 'capacity', 'value': 1000.0}, mode='A')
print(f"  容量线 1000：充电 {float(c2[0][0]):.1f}  放电 {float(d2[1][0]):.1f}")
check("容量线 1000 时充电（含端点）",
      abs(float(c2[0][0]) - ((1000 - 100) * 7.875 + (1000 - 200) * 0.125)) < 0.1,
      float(c2[0][0]), 7175.0)
check("容量线 1000 时放电（含端点）= min(500,1000)×3.875 + min(200,1000)×0.125",
      abs(float(d2[1][0]) - (500 * 3.875 + 200 * 0.125)) < 0.1,
      float(d2[1][0]), 1962.5)

print()
print("=" * 80)
print(f"结果：PASS {len(ok)}   FAIL {len(bad)}")
for b in bad:
    print("  -", b)
print("=" * 80)
sys.exit(1 if bad else 0)
