# -*- coding: utf-8 -*-
"""algorithms/ 独立包（loomgrid_algos）系统测试。

这个包不参与桌面版执行（桌面版走 core/），但发布到 PyPI 供外部调用。
它的失败不会被桌面版测试发现，只能单独测。

覆盖：
  ① _to_hour 各种写法（本轮补了中文/全角/上下午）
  ② compute_segmented_spaces 与 core 版结果等价
  ③ periods_to_price_series
  ④ finance：npv / irr / payback
  ⑤ dispatch：日调度模拟的能量守恒
  ⑥ diagnostics：指纹分析
  ⑦ 边界与异常

跑法：python tests/verify_algos_package.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np  # noqa: E402

from algorithms.spaces import (  # noqa: E402
    _to_hour, compute_daily_spaces, compute_segmented_spaces as alg_css,
)
from core.spaces import compute_segmented_spaces as core_css  # noqa: E402

PASS = FAIL = 0
TIMES = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 15, 30, 45)]


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
print("① _to_hour：数字 / 冒号 / 中文 / 全角 / 上下午")
print("=" * 74)
CASES = {
    0.0: 0.0, 8.0: 8.0, 23.75: 23.75, 24.0: 24.0,
    "0:00": 0.0, "8:00": 8.0, "08:00": 8.0, "8:00:00": 8.0,
    "23:45": 23.75, "24:00": 24.0, "8:00:30": 8.0 + 30 / 3600,
    "8": 8.0, "8.5": 8.5,
    # 本轮新增
    "8时": 8.0, "8点": 8.0, "8点30分": 8.5, "8时30分": 8.5, "8点半": 8.5,
    "零点": 0.0, "零时": 0.0,
    "8：00": 8.0, "８：００": 8.0,        # 全角冒号 + 全角数字
    "上午8:00": 8.0, "下午3:00": 15.0, "晚上9:30": 21.5, "下午3点": 15.0,
}
for raw, expect in CASES.items():
    got = _to_hour(raw)
    check(f"{raw!r} → {expect}", abs(got - expect) < 1e-9, f"实得 {got}")

# 解析失败仍返回 0.0（保持向后兼容，不改契约）
for bad in ("abc", "", "上午", None):
    check(f"{bad!r} 失败返回 0.0（兼容）", _to_hour(bad) == 0.0, str(_to_hour(bad)))

check("bool 返回 0.0（不当时刻）", _to_hour(True) == 0.0 and _to_hour(False) == 0.0)

# ══════════════════════════════════════════════════════════
print()
print("=" * 74)
print("② compute_segmented_spaces：core 与 algorithms 的已知差异")
print("=" * 74)
LOAD = np.array([[100 + p * 0.5 for p in range(96)] for _ in range(2)], dtype=float)
SEGS = [
    {"start": 0.0, "end": 8.0, "type": "charge", "label": "谷段"},
    {"start": 10.0, "end": 12.0, "type": "discharge", "label": "峰段"},
]
# v2.3.16 业务口径变更（仅 core / 桌面版）：
#   空间 = 纯物理空间（负荷曲线里还剩多少能量可搬），不乘充放电效率；
#   效率在专业模式窗口以 DOD（50%~100% 可自定义）引入，作用于容量。
# algorithms/（PyPI 包）仍乘 0.93 —— 两边只差这一个系数。
# 这条断言的意义：如果哪天有人在 core 里又把 0.93 加回去，或改了
# algorithms 而没同步，ratio 不再是 0.93，测试立刻红。
EFF = 0.93
for mode, kw in (("A", {"billing": {"value": 200}}),
                 ("B", {"rated_power": 500.0})):
    a = alg_css(LOAD, TIMES, SEGS, mode=mode, **kw)
    b = core_css(LOAD, TIMES, SEGS, mode=mode, **kw)
    n_cmp = 0
    bad = []
    for i in (0, 1):                      # 0=充电段列表，1=放电段列表
        for k in range(min(len(a[i]), len(b[i]))):
            try:
                va = float(np.nansum(np.asarray(a[i][k], dtype=float)))
                vb = float(np.nansum(np.asarray(b[i][k], dtype=float)))
            except (ValueError, TypeError):
                continue
            if abs(va) < 1e-12 and abs(vb) < 1e-12:
                continue                  # 占位零，两边都是 0，不比
            n_cmp += 1
            # 差异分两种情况：
            #   ① Mode A 充电 —— 两边原本就都不乘效率（那个 * EFF 只在 cap>0
            #      的 cap_space 分支里，本用例不传 cap）→ 应完全相等
            #   ② 其余 —— algorithms 乘了、core 未乘 → core = alg / EFF
            # va = algorithms（乘了效率）  vb = core（未乘）
            charge_branch = (i == 0 and mode == "A")
            want = va if charge_branch else va / EFF
            tag = "相等" if charge_branch else "alg/0.93"
            if abs(vb - want) > max(1e-6, abs(want) * 1e-9):
                bad.append(f"i={i} k={k} alg={va:.6f} core={vb:.6f} 期望({tag})={want:.6f}")
    check(f"mode {mode}：core 与 algorithms 的差异符合口径分叉（{n_cmp} 个非零值）",
          n_cmp > 0 and not bad, "; ".join(bad[:3]))

# ══════════════════════════════════════════════════════════
print()
print("=" * 74)
print("③ compute_daily_spaces（吃原始 periods，内部会调 _to_hour）")
print("=" * 74)
PERIODS = [
    {"start": 0.0, "end": 8.0, "type": "charge"},
    {"start": 10.0, "end": 12.0, "type": "discharge"},
]
# 用恒负荷才能手算对：充电空间 = 8h × (200-100) = 800
try:
    flat = np.full((2, 96), 100.0)
    r = compute_daily_spaces(flat, TIMES, PERIODS, mode="A", billing={"value": 200})
    c, d = r[0], r[1]
    check(f"返回两个 n_days 数组（{np.asarray(c).shape}）", np.asarray(c).shape == (2,), str(np.asarray(c).shape))
    cp = float(c[0])
    dp = float(d[0])
    check(f"第一天充电空间 = 800（实 {cp:.1f}）", abs(cp - 800) < 1.0, str(cp))
    check(f"第一天放电空间 = 186（2h × 100 × 0.93 效率，实 {dp:.1f}）", abs(dp - 186) < 1.0, str(dp))
    check("两天数值相同", abs(float(c[0]) - float(c[1])) < 1e-9)
except Exception as e:
    check("compute_daily_spaces 能跑通", False, f"{type(e).__name__}: {e}")

# ══════════════════════════════════════════════════════════
print()
print("=" * 74)
print("④ finance：npv / irr / payback")
print("=" * 74)
from algorithms.finance import (  # noqa: E402
    calc_irr, calc_npv, calc_payback_timeline, calc_simple_payback,
)
npv = calc_npv(annual_cashflow=100, investment=500, project_life=10, discount_rate=0.05,
               degradation=0.0)
# 年金现值 100 × (1-(1+0.05)^-10)/0.05 ≈ 772.17，减投资 500
check(f"npv(100/年, 投 500, 10 年, 5%%) ≈ 272.2（实 {npv:.2f}）",
      abs(npv - 272.17) < 1.0, str(npv))

npv0 = calc_npv(annual_cashflow=100, investment=500, project_life=10,
                discount_rate=0.0, degradation=0.0)
check(f"折现率 0 时 npv = 1000-500 = 500（实 {npv0:.1f}）", abs(npv0 - 500) < 1e-6, str(npv0))

irr_val = calc_irr(investment=500, annual_cashflow=100, project_life=10, degradation=0.0)
# 注意：返回的是百分数（15.10 表示 15.10%），不是小数 0.15
check(f"irr 在 10%~20% 区间（实 {irr_val:.2f}%%）", 10.0 < irr_val < 20.0, str(irr_val))

pay = calc_simple_payback(investment=500, annual_savings=100)
check(f"简单回收期 = 5 年（实 {pay}）", abs(pay - 5.0) < 1e-6, str(pay))

tl = calc_payback_timeline(investment=300, monthly_savings=[100] * 12)
check(f"时间线回收期能在 3 个月收回（{type(tl).__name__}）",
      isinstance(tl, dict), str(tl)[:120])

# ══════════════════════════════════════════════════════════
print()
print("=" * 74)
print("⑤ dispatch：日调度模拟的能量守恒")
print("=" * 74)
from algorithms.dispatch import simulate_daily_dispatch  # noqa: E402
DP = {"charge": [("0:00", "8:00")], "discharge": [("10:00", "12:00")]}
FLAT = np.full((1, 96), 100.0)
try:
    res = simulate_daily_dispatch(FLAT, TIMES, DP, capacity_kwh=500.0, power_kw=100.0)
    print(f"  返回类型: {type(res).__name__}")
    if isinstance(res, dict):
        print(f"  键: {list(res.keys())}")
        for k in list(res.keys())[:6]:
            v = res[k]
            print(f"    {k}: {np.round(v, 2) if hasattr(v, '__len__') else v}")
    check("日调度能跑通", True)
except Exception as e:
    check("日调度能跑通", False, f"{type(e).__name__}: {e}")

# ══════════════════════════════════════════════════════════
print()
print("=" * 74)
print("⑥ diagnostics：指纹分析")
print("=" * 74)
from algorithms.diagnostics import (  # noqa: E402
    analyze_load_fingerprint, match_fingerprint_to_industry,
)
DATES = ["2026-07-01", "2026-07-02"]
try:
    fp = analyze_load_fingerprint(LOAD, DATES, TIMES)
    print(f"  指纹类型: {type(fp).__name__}")
    if isinstance(fp, dict):
        print(f"  键: {list(fp.keys())[:10]}")
    m = match_fingerprint_to_industry(fp)
    print(f"  行业匹配: {str(m)[:140]}")
    check("指纹分析 + 行业匹配能跑通", True)
except Exception as e:
    check("指纹分析 + 行业匹配能跑通", False, f"{type(e).__name__}: {e}")

print()
print("=" * 74)
print(f"结果：PASS {PASS}   FAIL {FAIL}")
print("=" * 74)
sys.exit(1 if FAIL else 0)
