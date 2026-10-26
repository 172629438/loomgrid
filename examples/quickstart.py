# -*- coding: utf-8 -*-
"""
LoomGrid 全链路示例
====================

用五个算法模块串起一次完整的储能测算：

    生成负荷曲线 → 定义分时电价 → 算充放空间 → 跑三种调度 → 算财务指标

直接运行：

    python quickstart.py

不需要任何外部数据文件。所有输入都是现场生成的。

依赖：numpy / scipy（dispatch 的 LP 部分）/ pandas（finance 的现金流，可选）
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "algorithms"))

import numpy as np

from loadgen import _generate_load_profile
from periods import _normalize_periods, _segment_periods, periods_to_price_series
from spaces import compute_segmented_spaces
from dispatch import greedy_dispatch_annual, optimal_dispatch_lp
from finance import calc_npv, calc_irr, calc_simple_payback


def rule(title):
    print("\n" + "=" * 68)
    print(title)
    print("=" * 68)


# ══════════════════════════════════════════════════════════════════
# 0. 场景设定
# ══════════════════════════════════════════════════════════════════

PROVINCE = "浙江"
DAILY_KWH = 12000.0           # 日均用电量
PEAK_KW = 900.0               # 峰值功率
N_DAYS = 30
N_POINTS = 96                 # 15 分钟精度

rule("0. 场景设定")
print(f"省份      {PROVINCE}")
print(f"日均用电  {DAILY_KWH:,.0f} kWh")
print(f"峰值功率  {PEAK_KW:,.0f} kW")
print(f"采样      {N_DAYS} 天 × {N_POINTS} 点（{24*60//N_POINTS} 分钟）")


# ══════════════════════════════════════════════════════════════════
# 1. 生成一条负荷曲线
# ══════════════════════════════════════════════════════════════════

rule("1. 生成负荷曲线（三层叠加引擎）")

arr, dates, times = _generate_load_profile(
    industry="电子制造",
    daily_kwh=DAILY_KWH,
    peak_kw=PEAK_KW,
    n_days=N_DAYS,
    n_points=N_POINTS,
    province=PROVINCE,
    weekend_atten=0.40,
    cv=0.12,
    seed=42,
)
arr = np.asarray(arr, float)

print(f"形状        {arr.shape}")
print(f"峰值        {arr.max():,.0f} kW")
print(f"均值        {arr.mean():,.0f} kW")
print(f"负荷率      {arr.mean() / arr.max() * 100:.1f}%")
print(f"日均电量    {arr.sum(axis=1).mean() * 24 / N_POINTS:,.0f} kWh")

# 打印第一天每两小时一档，看看形状
day0 = arr[0]
print("\n第一天典型曲线（每 2 小时）：")
for h in range(0, 24, 2):
    vals = day0[h * 4:(h + 2) * 4]
    bar = "█" * int(vals.mean() / 30)
    print(f"  {h:02d}:00  {vals.mean():6.0f} kW  {bar}")


# ══════════════════════════════════════════════════════════════════
# 2. 定义分时电价
# ══════════════════════════════════════════════════════════════════

rule("2. 分时电价时段")

# 浙江一般工商业峰谷时段（示例用，实际以当地政策为准）
raw_periods = [
    (0, 8, "valley"),
    (8, 11, "flat"),
    (11, 14, "valley"),
    (14, 16, "flat"),
    (16, 23, "peak"),
    (23, 24, "flat"),
]

periods = _normalize_periods(raw_periods)      # 三种格式 → 统一 dict
segments = _segment_periods(periods)           # 相邻同类型 → 连续段

print(f"时段数      {len(periods)}")
print(f"合并成段    {len(segments)} 段")

print("\n分段结果：")
for s in segments:
    print(f"  {s['label']:8s} {s['type']:8s} {s['start']:5.2f} → {s['end']:5.2f}  "
          f"({(s['end'] - s['start']):.1f} h)")

PRICE = {"peak": 1.16, "flat": 0.72, "valley": 0.28}
price_series, price_type = periods_to_price_series(
    times,
    {t: [(f"{int(s):02d}:00", f"{int(e):02d}:00") for s, e, tt in raw_periods if tt == t]
     for t in ("peak", "flat", "valley")},
    price_map=PRICE,
)

print(f"\n电价       峰 {PRICE['peak']} / 平 {PRICE['flat']} / 谷 {PRICE['valley']} 元·kWh")
print(f"峰谷价差   {PRICE['peak'] - PRICE['valley']:.2f} 元/kWh")
print(f"日内价差收益上限（满充满放 1000 kWh）≈ "
      f"{(PRICE['peak'] - PRICE['valley']) * 1000:,.0f} 元/天")


# ══════════════════════════════════════════════════════════════════
# 3. 算充放电空间
# ══════════════════════════════════════════════════════════════════

rule("3. 充放电空间")

CAP_KWH = 1000.0      # 候选容量
POWER_KW = 500.0      # PCS 额定功率

c_segs, d_segs, c_labels, d_labels = compute_segmented_spaces(
    arr,
    times,
    segments,
    # Mode A = 削峰填谷：用变压器容量余量充电（谷段负荷低、余量大）
    # Mode B/C = 光伏余电 / 需量场景：依赖负负荷，本例无光伏所以不适用
    billing={"type": "capacity", "value": 1000.0},   # 变压器容量 1000 kVA
    mode="A",
    rated_power=POWER_KW,
    ignore_negative=False,
)

print(f"充电段      {len(c_segs)} 个")
print(f"放电段      {len(d_segs)} 个")

for label, seg_list in list(zip(c_labels, c_segs))[:6]:
    total = float(np.mean(seg_list))
    print(f"  {label:10s} 日均可充电量 {total:8.1f} kWh")

print()
for label, seg_list in list(zip(d_labels, d_segs))[:6]:
    total = float(np.mean(seg_list))
    print(f"  {label:10s} 日均可放电量 {total:8.1f} kWh")

# 日均可用空间（真正的约束）
daily_c_space = float(np.mean([float(np.mean(s)) for s in c_segs])) if c_segs else 0.0
daily_d_space = float(np.mean([float(np.mean(s)) for s in d_segs])) if d_segs else 0.0
usable_daily = min(daily_c_space, daily_d_space)

print(f"\n日均可用充电空间  {daily_c_space:,.0f} kWh")
print(f"日均可用放电空间  {daily_d_space:,.0f} kWh")
print(f"→ 实际日循环上限  {usable_daily:,.0f} kWh（取两者较小值）")
print(f"→ 电池 {CAP_KWH:.0f} kWh 按 DOD 90% 需 {CAP_KWH*0.9:.0f} kWh 循环空间，"
      f"{'装得下' if usable_daily >= CAP_KWH*0.9 else '装不下，容量会被浪费'}")
valley_hours = sum(e - s for s, e, t in raw_periods if t == "valley")
peak_hours = sum(e - s for s, e, t in raw_periods if t == "peak")
print(f"\n谷段总时长  {valley_hours:.1f} h  ×  {POWER_KW:.0f} kW = "
      f"{valley_hours * POWER_KW:,.0f} kWh 充电能力上限")
print(f"峰段总时长  {peak_hours:.1f} h  ×  {POWER_KW:.0f} kW = "
      f"{peak_hours * POWER_KW:,.0f} kWh 放电能力上限")
print(f"→ 电池 {CAP_KWH:.0f} kWh 的两小时率放电需求 = "
      f"{CAP_KWH / 2:.0f} kWh，被峰段空间{'完全覆盖' if peak_hours * POWER_KW > CAP_KWH / 2 else '限制'}")


# ══════════════════════════════════════════════════════════════════
# 4. 三种调度对照
# ══════════════════════════════════════════════════════════════════

rule("4. 调度引擎对照")

# ── 4.1 贪心 ──
greedy = greedy_dispatch_annual(
    arr.flatten(),
    batt_kwh=CAP_KWH,
    c_rate=0.5,
    eta_roundtrip=0.90,
)
print("贪心调度：")
for k, v in greedy.items():
    print(f"  {k:22s} {v:,.2f}" if isinstance(v, (int, float)) else f"  {k:22s} {v}")

# ── 4.2 LP ──
buy = np.asarray(price_series, float)
sell = buy * 0.35
try:
    lp = optimal_dispatch_lp(
        arr[0],
        buy,
        sell,
        batt_kwh=CAP_KWH,
        Pmax=POWER_KW,
        eta_c=0.95,
        eta_d=0.95,
        soc_min=0.05,
        soc_max=0.95,
    )
    print("\n线性规划调度（单日）：")
    print(f"  收益        {float(np.nansum(lp.get('revenue', []))):,.2f} 元/天")
    print(f"  充电总量    {float(np.nansum(lp.get('ch', []))):,.1f} kWh")
    print(f"  放电总量    {float(np.nansum(lp.get('dis', []))):,.1f} kWh")
    print(f"  可行        {lp.get('feasible')}")
except Exception as e:
    print(f"\nLP 调度跳过（{type(e).__name__}: {e}）")


# ══════════════════════════════════════════════════════════════════
# 5. 财务测算
# ══════════════════════════════════════════════════════════════════

rule("5. 财务指标")

INVEST_PER_KWH = 750.0      # 元/kWh（2026 年工商业储能 EPC 参考价）
O_AND_M = 8.0               # 运维成本，占投资比例 %
YEARS = 15
DEGRADE = 0.015             # 年衰减
RUN_DAYS = 330              # 年有效运行天数
PRICE_SPREAD = PRICE["peak"] - PRICE["valley"]


def annual_gross_of(cap, daily_limit):
    """年毛收益：受「容量×DOD」与「实际空间」双重约束，取较小值。"""
    usable = min(cap * 0.9, daily_limit)
    return PRICE_SPREAD * usable * RUN_DAYS


invest = CAP_KWH * INVEST_PER_KWH
annual_gross = annual_gross_of(CAP_KWH, usable_daily)
annual_net = annual_gross - invest * O_AND_M / 100

print(f"投资        {invest:,.0f} 元（{INVEST_PER_KWH:.0f} 元/kWh）")
print(f"年毛收益    {annual_gross:,.0f} 元（{RUN_DAYS} 天 × {PRICE_SPREAD:.2f} 元价差 × 受空间约束的循环量）")
print(f"年运维      {invest * O_AND_M / 100:,.0f} 元")
print(f"年净收益    {annual_net:,.0f} 元")

payback = calc_simple_payback(invest, annual_net)
print(f"\n静态回收期  {payback:.1f} 年" if payback else "\n静态回收期  不可回收")

# 逐年现金流（含衰减）
cashflows = [-invest]
for y in range(1, YEARS + 1):
    cashflows.append(annual_net * ((1 - DEGRADE) ** (y - 1)))

npv = calc_npv(invest, annual_net, YEARS, 0.06)
irr = calc_irr(invest, annual_net, YEARS)

print(f"NPV（6% 折现，{YEARS} 年）  {npv:,.0f} 元")
print(f"IRR                          {irr}")


# ══════════════════════════════════════════════════════════════════
# 6. 敏感性：容量与投资单价双因子
# ══════════════════════════════════════════════════════════════════

rule("6. 敏感性分析")

print(f"{'容量 kWh':>10s} {'投资 万元':>10s} {'年净收益':>10s} {'回收期':>8s} {'NPV':>12s}")
prev_pb = None
for cap in (500, 800, 1000, 1500, 2000, 3000):
    inv = cap * INVEST_PER_KWH
    rev = annual_gross_of(cap, usable_daily) - inv * O_AND_M / 100
    pb = inv / rev if rev > 0 else float("inf")
    flag = ""
    if prev_pb is not None and pb > prev_pb + 0.01:
        flag = "  ← 拐点"
    prev_pb = pb
    print(f"{cap:>10,} {inv/1e4:>10.1f} {rev:>10,.0f} {pb:>8.1f} {calc_npv(inv, rev, YEARS, 0.06):>12,.0f}{flag}")

print("\n注意：容量不是越大越好。超过某一点后，日均可充电空间不够，")
print("多装的容量会闲置——上面的表里只要回收期开始变长，就是拐点。")


# ══════════════════════════════════════════════════════════════════
# 7. 结论提示
# ══════════════════════════════════════════════════════════════════

rule("7. 这个例子想说明什么")

print("""
1. 容量不是拍出来的
   它受两个物理约束夹住：谷段能充进去多少、峰段能放出来多少。
   STEP 3 打印的两个「能力上限」就是这个边界。

2. 三种调度结果应该接近，差异就是信息
   贪心比 LP 差多少，说明策略还有多少改进空间。
   如果贪心比 LP 还好，那一定是某个假设错了。

3. 经济性对三个参数最敏感
   峰谷价差、投资单价、年利用天数。这三个数一变，结论就翻。
   所以真实项目里，这三个数必须拿到实测或政策原文，不能估。

4. 这个例子的数字是示意值
   750 元/kWh、330 天、90% 往返效率都是常见量级，不是某个具体项目的结果。
   拿去做方案之前，替换成真实参数。
""")
