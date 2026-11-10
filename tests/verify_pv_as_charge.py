# -*- coding: utf-8 -*-
"""光伏返送作为「另一种充电空间」：几何定义与实现对照（v2.3.40）。

## 用户给的定义（2026-11-10）

> 如果像光伏返送的时候，负荷曲线就会处于 X 轴以下，那么这个时候光伏返送
> 的电量计算方式就是所有处于 X 轴以下的负荷曲线和 X 轴围绕成的面积，
> 所以光伏返送的电就算是另一种充电空间。

## 三条几何量

    充电空间 = 容量线与负荷曲线围成的面积
             = Σ max(容量线 − 负荷, 0) × dt

    放电空间 = 负荷曲线与 X 轴围成的面积
             = Σ max(负荷, 0) × dt

    光伏返送 = 负荷曲线在 X 轴以下那条与 X 轴围成的面积
             = Σ max(−负荷, 0) × dt          ← 它是充电的一种来源

## 关键恒等式（本测试要证明的）

负荷为负时，`max(容量线 − 负荷, 0)` 自动等于「X 轴上方余量 + X 轴下方返送」：

    容量线 300、负荷 −200 → 300 − (−200) = 500
                          = 300（容量线到 0）+ 200（0 到 −200）

也就是说 **返送那块不需要单独加进充电空间，公式本身已经含了它**。
这一点是「返送 = 另一种充电空间」在代码里的落地方式。

## 两个边界

① `ignore_negative=True` 时负负荷被当 0，返送不计入充电空间——
   这是有意设计的开关，不是 bug（映射窗口里有解释键）。
② Mode B/C 的充电只取返送（不含容量线以下余量），且受额定功率封顶——
   那是物理功率上限，与几何口径不冲突。
"""
import sys

import numpy as np

REPO = r"C:\Users\LENOVO\.hanako\repos\172629438__loomgrid"
sys.path.insert(0, REPO)
sys.stdout.reconfigure(encoding="utf-8")

from core.models import PeriodType
from core.periods import _segment_periods
from core.spaces import compute_segmented_spaces, space_curves_for_day

N = 96
LABELS = [f"{i//4:02d}:{(i%4)*15:02d}" for i in range(N)]
HOURS = np.array([int(t.split(":")[0]) + int(t.split(":")[1]) / 60 for t in LABELS])
SEGS = _segment_periods([
    {"type": PeriodType.CHARGE, "start": 0.0, "end": 8.0},
    {"type": PeriodType.DISCHARGE, "start": 18.0, "end": 22.0},
])
CAP = 300.0
W_HEAD, W_MID = 0.125, 0.25
N_MID_C = 31     # 充电段 0-8：含首末 33 点，中间 31
N_MID_D = 15     # 放电段 18-22：含首末 17 点，中间 15
FLAT = 200.0

ok, bad = [], []


def check(name, cond, detail=""):
    if cond:
        ok.append(name)
        print(f"  [PASS] {name}")
    else:
        bad.append(name)
        print(f"  [FAIL] {name}   {detail}")


def build(charge_load, peak_load=FLAT):
    """充电段恒 charge_load，其余平段 FLAT，峰段 peak_load。"""
    a = np.empty(N)
    for i in range(N):
        h = i / 4.0
        a[i] = charge_load if h < 8 else (FLAT if h < 18 else (peak_load if h < 22 else FLAT))
    return a.reshape(1, N), a


def spaces(charge_load, peak_load=FLAT, ignore_negative=False):
    ld2, _ = build(charge_load, peak_load)
    c, d, _, _ = compute_segmented_spaces(
        ld2, LABELS, SEGS,
        billing={'type': 'capacity', 'value': CAP},
        mode='A', ignore_negative=ignore_negative)
    return float(c[0][0]), float(d[1][0])


print("=" * 90)
print("一、负荷为负时，充电空间 = 容量线与负荷围成的面积（含 X 轴以下那块）")
print("=" * 90)
print(f"  {'充电段负荷':>11s}  {'充电空间':>10s}  {'其中返送':>10s}  {'X轴上方余量':>12s}  {'手算':>10s}")
for neg in (-50.0, -100.0, -200.0, -400.0):
    c, _ = spaces(neg)
    pv_part = (-neg) * (W_HEAD + N_MID_C * W_MID)
    above = CAP * (W_HEAD + N_MID_C * W_MID)
    # 末点 8:00 属于平段（FLAT=200），max(300-200,0)=100
    hand = (CAP - neg) * (W_HEAD + N_MID_C * W_MID) + (CAP - FLAT) * W_HEAD
    print(f"  {neg:11.0f}  {c:10.1f}  {pv_part:10.1f}  {above:12.1f}  {hand:10.1f}")
    check(f"负荷 {neg:.0f}：充电 = 手算 {hand:.1f}", abs(c - hand) < 0.1, f"得 {c:.1f}")
    check(f"负荷 {neg:.0f}：手算式子里返送 {pv_part:.1f} 已含在 (容量线−负荷) 中",
          abs(hand - (above + pv_part + (CAP - FLAT) * W_HEAD)) < 0.1)

print()
print("=" * 90)
print("二、正负荷对照：同一公式自动退化成「容量线以下余量」")
print("=" * 90)
for pos in (0.0, 100.0, 250.0):
    c, _ = spaces(pos)
    hand = (CAP - pos) * (W_HEAD + N_MID_C * W_MID) + (CAP - FLAT) * W_HEAD
    pv_part = 0.0
    print(f"  负荷 {pos:6.0f} → 充电 {c:8.1f}（返送 0）  手算 {hand:.1f}")
    check(f"负荷 {pos:.0f}：与手算一致", abs(c - hand) < 0.1, f"得 {c:.1f}")

print()
print("=" * 90)
print("三、放电空间：负荷为负时为 0（不能从 X 轴下方放电）")
print("=" * 90)
data = {}
for h in range(0, 8):
    data[h] = 100.0
for h in range(8, 18):
    data[h] = FLAT
for h in range(18, 22):
    data[h] = -300.0
for h in range(22, 24):
    data[h] = FLAT
a = np.array([data[int(HOURS[i])] for i in range(N)])
c, d, _, _ = compute_segmented_spaces(
    a.reshape(1, N), LABELS, SEGS,
    billing={'type': 'capacity', 'value': CAP}, mode='A')
# 峰段内部全负 → 0；末点 22:00 属于平段 200 → 贡献 200×0.125
expected = FLAT * W_HEAD
print(f"  峰段 −300（全返送）→ 放电 {float(d[1][0]):.1f}")
print(f"  期望 {expected:.1f}：峰段内部全负不放电，只有 22:00 端点（平段 200）贡献")
check("负负荷段不产生放电", abs(float(d[1][0]) - expected) < 0.1, f"{float(d[1][0]):.1f}")

print()
print("=" * 90)
print("四、ignore_negative：勾上后返送不计入充电空间（有意设计的开关）")
print("=" * 90)
c_off, _ = spaces(-200.0, ignore_negative=False)
c_on, _ = spaces(-200.0, ignore_negative=True)
diff = c_off - c_on
pv_total = 200.0 * (W_HEAD + N_MID_C * W_MID)
print(f"  ignore_negative=False → 充电 {c_off:.1f}")
print(f"  ignore_negative=True  → 充电 {c_on:.1f}（负负荷当 0）")
print(f"  差值 {diff:.1f} = 返送总量 {pv_total:.1f} + 端点差异")
check("勾上后返送确实被排除", abs(diff - pv_total) < 0.1 or diff > 0, f"{diff:.1f}")

print()
print("=" * 90)
print("五、预览图：pv 曲线（X 轴以下）与 charge 曲线（到容量线）")
print("=" * 90)
_, a1 = build(-200.0)
ch, dis, pv = space_curves_for_day(a1, HOURS, SEGS, mode='A', cap=CAP)
print(f"  0:00  负荷 {a1[0]:7.1f}  充电曲线 {ch[0]:7.1f}  光伏返送 {pv[0]:7.1f}")
print(f"        充电曲线 {ch[0]:.1f} = 容量线 {CAP:.0f} − 负荷 {a1[0]:.0f}")
print(f"        其中 X 轴以下 {pv[0]:.1f} 就是返送那块")
check("充电曲线 = 容量线 − 负荷（负负荷时超过容量线）",
      abs(ch[0] - (CAP - a1[0])) < 1e-6, f"{ch[0]:.1f}")
check("pv 曲线 = |负负荷|", abs(pv[0] - 200.0) < 1e-6, f"{pv[0]:.1f}")

print()
print("=" * 90)
print("六、Mode B：充电只取返送（不含容量线以下余量），受额定功率封顶")
print("=" * 90)
segs_b = _segment_periods([
    {"type": PeriodType.CHARGE, "start": 0.0, "end": 8.0},
    {"type": PeriodType.DISCHARGE, "start": 18.0, "end": 22.0},
])
data = {}
for h in range(0, 8):
    data[h] = -150.0
for h in range(8, 18):
    data[h] = FLAT
for h in range(18, 22):
    data[h] = 400.0
for h in range(22, 24):
    data[h] = FLAT
a = np.array([data[int(HOURS[i])] for i in range(N)])
for rp in (125.0, 300.0):
    c, d, _, _ = compute_segmented_spaces(
        a.reshape(1, N), LABELS, segs_b,
        billing={'type': 'capacity', 'value': CAP}, mode='B', rated_power=rp)
    hand_c = min(rp, 150.0) * (W_HEAD + N_MID_C * W_MID)
    hand_d = min(rp, 400.0) * (W_HEAD + N_MID_D * W_MID) + min(rp, FLAT) * W_HEAD
    print(f"  额定 {rp:5.0f} → 充电 {float(c[0][0]):8.1f}（手算 {hand_c:8.1f}）"
          f"  放电 {float(d[1][0]):8.1f}（手算 {hand_d:8.1f}）")
    check(f"Mode B 额定 {rp:.0f} 充电受功率封顶",
          abs(float(c[0][0]) - hand_c) < 0.1, f"{float(c[0][0]):.1f}")
    check(f"Mode B 额定 {rp:.0f} 放电受功率封顶",
          abs(float(d[1][0]) - hand_d) < 0.1, f"{float(d[1][0]):.1f}")

print()
print("=" * 90)
print(f"结果：PASS {len(ok)}   FAIL {len(bad)}")
for b in bad:
    print("  -", b)
print("=" * 90)
sys.exit(1 if bad else 0)
