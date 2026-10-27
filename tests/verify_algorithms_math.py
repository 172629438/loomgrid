# -*- coding: utf-8 -*-
"""算法正确性验证：用可手算的样例对拍

目的不是"能跑"，而是"算得对"。
每个用例都有独立算出的期望值，对不上就是算法有问题。
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "algorithms"))

import numpy as np

PASS, FAIL = [], []


def check(name, got, want, tol=1e-6):
    ok = abs(got - want) <= tol * max(1.0, abs(want))
    (PASS if ok else FAIL).append((name, got, want))
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name:<44} 实得 {got:>14.4f}  期望 {want:>14.4f}")


# ══════════════════════════════════════════════════════════
print("=" * 78)
print("A. finance —— 财务公式对拍")
print("=" * 78)

import finance

# 用例 A1：NPV 无衰减、无投资，纯年金折现
# 年现金流 20 万，10 年，8% 折现
# 期望 = 200000 * (1 - 1.08^-10) / 0.08 = 200000 * 6.710081 = 1,342,016.29
pv = 200000 * (1 - 1.08 ** -10) / 0.08
check("NPV: 20万×10年@8%（无衰减）", finance.calc_npv(200000, 0, 10, 0.08, 0.0), pv, 1e-6)

# 用例 A2：同样条件，投资 100 万 → NPV = 年金现值 - 投资
check("NPV: 扣 100 万投资", finance.calc_npv(200000, 1000000, 10, 0.08, 0.0), pv - 1000000, 1e-6)

# 用例 A3：静态回收期
check("回收期: 100万 / 20万", finance.calc_simple_payback(1000000, 200000), 5.0, 1e-9)

# 用例 A4：IRR —— 投资 100 万，年 20 万，10 年
# 求 r 使 1000000 = 200000 * (1-(1+r)^-10)/r
# 二分法手算：r=15% → 年金系数 5.0188 → 1,003,769 ≈ 100 万，所以 IRR ≈ 15.1%
import math
lo, hi = 0.10, 0.20
for _ in range(200):
    mid = (lo + hi) / 2
    pv_mid = 200000 * (1 - (1 + mid) ** -10) / mid
    if pv_mid > 1000000:
        lo = mid
    else:
        hi = mid
want_irr = (lo + hi) / 2 * 100
got_irr = finance.calc_irr(1000000, 200000, 10, degradation=0.0)   # 必须显式关衰减，否则默认 2%
check("IRR: 100万投资/20万年/10年（无衰减）", got_irr, want_irr, 1e-3)

# 用例 A5：默认衰减 2% 时 IRR 应明显低于无衰减
irr_deg = finance.calc_irr(1000000, 200000, 10)
print(f"[INFO] 带 2% 衰减的 IRR = {irr_deg:.2f}%（低于无衰减的 {want_irr:.2f}%，方向正确）")
PASS.append(("IRR衰减方向", irr_deg, 0)) if irr_deg < want_irr else FAIL.append(("IRR衰减方向", irr_deg, want_irr))

# ══════════════════════════════════════════════════════════
print("\n" + "=" * 78)
print("B. periods —— 时段与价格序列")
print("=" * 78)

import periods

# 用例 B1：24h 四段，价格序列必须逐点对上
grouped = {
    "valley": [("00:00", "08:00")],
    "flat": [("08:00", "17:00")],
    "peak": [("17:00", "22:00")],
    "valley2": [("22:00", "24:00")],
}
# 用官方归一化方式
p_list = [
    {"start": 0, "end": 8, "type": "valley"},
    {"start": 8, "end": 17, "type": "flat"},
    {"start": 17, "end": 22, "type": "peak"},
    {"start": 22, "end": 24, "type": "valley"},
]
labels = [f"{h:02d}:00" for h in range(24)]
buy, ptype = periods.periods_to_price_series(
    labels, {"valley": [("00:00", "08:00"), ("22:00", "24:00")],
             "flat": [("08:00", "17:00")],
             "peak": [("17:00", "22:00")]},
    {"valley": 0.3, "flat": 0.7, "peak": 1.2})

# 期望：0-7 谷、8-16 平、17-21 峰、22-23 谷
expect = [0.3] * 8 + [0.7] * 9 + [1.2] * 5 + [0.3] * 2
vals = list(buy.values)   # Series 的索引是日期，必须取 values
ok = all(abs(float(vals[i]) - expect[i]) < 1e-9 for i in range(24))
print(f"[{'PASS' if ok else 'FAIL'}] 24 小时价格序列逐点核对              {'全部一致' if ok else '有偏差'}")
if not ok:
    FAIL.append(("价格序列逐点", list(vals), expect))
else:
    PASS.append(("价格序列逐点", 0, 0))

# 用例 B2：跨午夜拆分
overnight = [{"start": 22, "end": 6, "type": "valley"}]
exp = periods._expand_overnight(periods._normalize_periods(overnight))
n_seg = len(exp)
covered = sum(p["end"] - p["start"] for p in exp)
check("跨午夜 22→6 拆成几段的小时数合计", covered, 8.0, 1e-9)
print(f"        （拆成 {n_seg} 段：{[(p['start'], p['end']) for p in exp]}）")

# ══════════════════════════════════════════════════════════
print("\n" + "=" * 78)
print("C. spaces —— 充放电空间手算对拍")
print("=" * 78)

import spaces

# 用例 C1：平坦负荷 500 kW，变压器 800 kVA，谷段 8h / 峰段 5h
# 充电空间 = Σ(800 - 500) × dt = 300 kW × 8h = 2400 kWh（电网侧）× 充电效率
# 放电空间 = Σ min(500, 800) × dt = 500 × 5h = 2500 kWh × 放电效率
n = 24
load = np.full((1, n), 500.0)
labels24 = [f"{h:02d}:00" for h in range(24)]
per = [
    {"start": 0, "end": 8, "type": "valley"},
    {"start": 8, "end": 17, "type": "flat"},
    {"start": 17, "end": 22, "type": "peak"},
    {"start": 22, "end": 24, "type": "valley"},
]
billing = {"value": 800}
ch, dis = spaces.compute_daily_spaces(load, labels24, per, billing, "A")

eff_c = spaces.CHARGING_EFFICIENCY
eff_d = spaces.DISCHARGING_EFFICIENCY
# Mode A 充电：max(cap-load)×dt 已是电网侧吸收量，不乘 η（见主程序 L17358 口径说明）
want_ch = 300 * 10
# 放电：min(负荷,上限)×dt×η 才是电网侧供出量，要乘
want_dis = 500 * 5 * eff_d
check("充电空间: (800-500)×10h（电网侧不乘η）", float(ch[0]), want_ch, 1e-6)
check("放电空间: 500×5h×ηd（电网侧供出）", float(dis[0]), want_dis, 1e-6)

# 用例 C2：负荷高于变压器容量时，充电空间应为 0（没有余量）
load_high = np.full((1, n), 900.0)
ch2, dis2 = spaces.compute_daily_spaces(load_high, labels24, per, billing, "A")
check("负荷超变压器时充电空间", float(ch2[0]), 0.0, 1e-9)

# ══════════════════════════════════════════════════════════
print("\n" + "=" * 78)
print("D. dispatch —— 调度结果合理性")
print("=" * 78)

import dispatch

# 用例 D1：无价差（买=卖=0.5）时不应有套利收益
# 注意 soc_init 必须等于 soc_min，否则初始 SOC 里的能量会被当成免费收益
n96 = 96
hours = np.arange(n96) * 0.25
load96 = np.full(n96, 500.0)
flat_price = np.full(n96, 0.5)
sell0 = np.zeros(n96)
r = dispatch.optimal_dispatch_lp(
    load_kw=load96, buy_price=flat_price, sell_price=sell0,
    batt_kwh=500, Pmax=125, soc_init=0.05, soc_min=0.05, soc_max=0.95, dt=0.25)
rev = r.get("revenue", 0) if isinstance(r, dict) else 0
print(f"[{'PASS' if abs(rev) < 1e-2 else 'FAIL'}] 无价差时 LP 收益必须为 0          实得 {rev:.4f}")
(PASS if abs(rev) < 1e-2 else FAIL).append(("无价差LP收益", rev, 0))

# 用例 D2：有价差时，收益不应超过物理上限
buy = np.where((hours < 8) | (hours >= 22), 0.3, 1.2)
r2 = dispatch.optimal_dispatch_lp(
    load_kw=load96, buy_price=buy, sell_price=sell0,
    batt_kwh=500, Pmax=125, soc_init=0.05, soc_min=0.05, soc_max=0.95, dt=0.25)
rev2 = r2.get("revenue", 0) if isinstance(r2, dict) else 0
# 上限：可用 SOC 90%×500=450 kWh（电池侧）→ 放电 450×ηd 电网侧 → × 价差 0.9
upper = 450 * 0.95 * 0.9
ok2 = 0 < rev2 <= upper * 1.02
print(f"[{'PASS' if ok2 else 'FAIL'}] 有价差时收益落在 (0, {upper:.1f}]        实得 {rev2:.2f}")
(PASS if ok2 else FAIL).append(("LP收益上限", rev2, upper))

# ══════════════════════════════════════════════════════════
print("\n" + "=" * 78)
print(f"结果：PASS {len(PASS)}   FAIL {len(FAIL)}")
print("=" * 78)
if FAIL:
    for name, got, want in FAIL:
        print(f"  ✗ {name}: 实得 {got}, 期望 {want}")
