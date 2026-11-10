# -*- coding: utf-8 -*-
"""放电空间口径：几何定义 + 与旧实现的差异（v2.3.40 确立）。

## 用户给的定义（2026-11-10）

> 在负荷曲线已经绘制出来的时候，人工计算充电空间就是算在单端充电时段中，
> 容量线和负荷曲线围绕成的面积；放电空间就是计算在单端放电时段中，
> 负荷曲线和 X 轴围绕成的面积。
>
> 如果像光伏返送的时候，负荷曲线就会处于 X 轴以下，那么这个时候光伏返送
> 的电量计算方式就是所有处于 X 轴以下的负荷曲线和 X 轴围绕成的面积，
> 所以光伏返送的电就算是另一种充电空间。

三条合起来：

    充电空间 = Σ max(容量线 − 负荷, 0) × dt     充电受「上限」约束（容量线）
    放电空间 = Σ max(负荷, 0)        × dt     放电受「下限」约束（X 轴）
    光伏返送 = Σ max(−负荷, 0)       × dt     它是充电的一种来源

**两侧不对称是设计如此**：充电不能超过申报需量，放电放到零为止。

## 我在此前（v2.3.31~v2.3.33）的两处误判，本文件一并纠正

误判一：「负荷没超容量线时仍报出放电空间，应该是 0」

    错在拿「削峰量」的思路去审视。
    按几何定义，峰段负荷 100、容量线 300 时，
    负荷与 X 轴围成的面积就是 100 × 3.875h = 387.5 + 端点 25 = 412.5 —— 对。

误判二：把 `min(负荷, 容量线)` 的封顶当成「可能需要保留的设计」

    实测峰段负荷从 350 涨到 800，旧实现恒为 1187.5，与输入无关。
    一个与输入无关的输出不可能是正确算法。

## 旧实现错在哪

`compute_segmented_spaces` 的 Mode A 放电原为：

    space += np.sum(np.minimum(pos_sub_load, cap) * seg_dt, axis=1)

多封了一层顶，等于把「向下到 X 轴」改成了「向下到容量线」。

历史：Fusion v2.1 到 v2.3、Ultra v3.2.0~v3.2.8，八个版本写法一致。
原注释写「受储能容量限制，此处仅计算空间，不考虑储能容量」——
想限制的是储能容量（kWh），实际用的是容量线（kW）。

## 本测试断言什么

断言新口径（几何面积），并保留旧值作对照，证明差异真实存在。
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
W_HEAD, W_MID = 0.125, 0.25
N_MID_D = 15     # 放电段 18-22：含首末 17 点，中间 15
TAIL = 200.0     # 22:00 端点属于平段

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
        a[i] = 100.0 if h < 8 else (200.0 if h < 18 else (peak if h < 22 else TAIL))
    return a.reshape(1, N)


def discharge_of(peak):
    c, d, _, _ = compute_segmented_spaces(
        load_with_peak(peak), LABELS, SEGS,
        billing={'type': 'capacity', 'value': CAP}, mode='A')
    return float(d[1][0])


def hand_discharge(peak):
    """负荷与 X 轴围成的面积（含端点权重）。"""
    return peak * (W_HEAD + N_MID_D * W_MID) + TAIL * W_HEAD


def old_capped(peak):
    """旧实现的封顶值，用于对照。"""
    return (min(peak, CAP) * (W_HEAD + N_MID_D * W_MID)
            + min(TAIL, CAP) * W_HEAD)


print("=" * 88)
print("一、放电空间 = 负荷与 X 轴围成的面积（新口径 vs 旧封顶）")
print("=" * 88)
print(f"  {'峰段负荷':>10s}  {'手算(面积)':>11s}  {'代码':>10s}  {'旧实现':>10s}  {'持平?':>6s}")
rows = []
for peak in (100.0, 200.0, 300.0, 350.0, 400.0, 500.0, 800.0):
    got = discharge_of(peak)
    hand = hand_discharge(peak)
    old = old_capped(peak)
    rows.append((peak, got, hand, old))
    same = "是" if abs(got - old) < 0.1 else "否"
    print(f"  {peak:10.0f}  {hand:11.1f}  {got:10.1f}  {old:10.1f}  {same:>6s}")

print()
print("=" * 88)
print("二、断言")
print("=" * 88)

for peak, got, hand, old in rows:
    check(f"峰段 {peak:.0f}：代码 = 手算面积 {hand:.1f}",
          abs(got - hand) < 0.1, f"得 {got:.1f}")

# 关键反向证明：旧实现确实与负荷无关
over = [r for r in rows if r[0] > CAP]
olds = [r[3] for r in over]
check("反向：旧实现在峰段超容量线后恒定（证明 bug 存在过）",
      max(olds) - min(olds) < 1e-6, f"{min(olds):.1f} ~ {max(olds):.1f}")
check("反向：旧实现恒定值 = 300×3.875 + 200×0.125 = 1187.5",
      abs(olds[0] - 1187.5) < 0.1, f"{olds[0]:.1f}")

new_over = [r[1] for r in over]
check("新实现在峰段超容量线后随负荷递增",
      new_over == sorted(new_over) and new_over[-1] > new_over[0] + 1,
      f"{[round(v,1) for v in new_over]}")

# 恰好等于容量线时新旧一致（方便判断「为什么以前没被发现」）
eq = [r for r in rows if abs(r[0] - CAP) < 1e-9][0]
check("峰段 = 容量线时新旧结果相同（所以这个 bug 长期没暴露）",
      abs(eq[1] - eq[3]) < 0.1, f"新 {eq[1]:.1f} 旧 {eq[3]:.1f}")

print()
print("=" * 88)
print("三、充电侧：容量线与负荷围成的面积（未改动，作对照）")
print("=" * 88)
for cap in (100.0, 150.0, 200.0, 300.0, 600.0):
    c, d, _, _ = compute_segmented_spaces(
        load_with_peak(500.0), LABELS, SEGS,
        billing={'type': 'capacity', 'value': cap}, mode='A')
    print(f"  容量线 {cap:5.0f} → 充电 {float(c[0][0]):8.1f}   放电 {float(d[1][0]):8.1f}")

c_eq, _, _, _ = compute_segmented_spaces(
    load_with_peak(500.0), LABELS, SEGS,
    billing={'type': 'capacity', 'value': 100.0}, mode='A')
check("容量线等于谷段负荷时充电空间为 0",
      abs(float(c_eq[0][0])) < 0.1,
      f"{float(c_eq[0][0]):.1f}")

print()
print("=" * 88)
print(f"结果：PASS {len(ok)}   FAIL {len(bad)}")
for b in bad:
    print("  -", b)
print("=" * 88)
sys.exit(1 if bad else 0)
