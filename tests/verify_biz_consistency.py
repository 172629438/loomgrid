# -*- coding: utf-8 -*-
"""业务口径自洽性：容量反推 ↔ 满足天数，两个方向必须闭环。

为什么这条最重要
----------------
程序里有两套「当天特征容量」的算法：

    最小可行容量：min(当天所有充放段的空间)     ← 用户描述的口径
    满载利用率：  max(每一次循环的 min(充,放))  ← 最优循环口径

容量反推（calc_capacity_for_days）用它们算出的 daily_mins 取分位数。
而「满足天数」的判据（_check_day_satisfied）也必须用同一套口径，
否则「反推出的容量」再反查天数会对不上 —— 这正是 v2.3.11 之前的状态：
反推用两套、判定只用满载那套。

本脚本用纯函数模拟两条链路，断言它们闭环。

业务语义（用户原话）
--------------------
每天的「最小容量」= 四个空间里最小的那个（两充两放 → 2 充 2 放）。
全年按大小排，要满足 N 天 → 取第 (总天数-N+1) 小。
容量越小越容易满足（装得比空间大会充不满/放不完）。

跑法：python tests/verify_biz_consistency.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np  # noqa: E402

from core.spaces import calc_capacity_for_days, calc_days_for_capacity  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}  {detail}")


# ── 模拟「当天四个空间」──────────────────────────────────
def make_day(c1, c2, d1, d2):
    """返回 (充空间列表, 放空间列表)，已剔除占位零。"""
    return ([c1, c2], [d1, d2])


def daily_cap_min_feasible(c_vals, d_vals):
    """最小可行：四个空间取最小 —— 用户描述的「当天的最小容量」。"""
    vals = list(c_vals) + list(d_vals)
    return min(vals) if vals else 0.0


def daily_cap_full_load(c_vals, d_vals):
    """满载：max(每次循环的 min(充, 放))。"""
    n = max(len(c_vals), len(d_vals))
    best = 0.0
    for i in range(n):
        cv = c_vals[i] if i < len(c_vals) else float("inf")
        dv = d_vals[i] if i < len(d_vals) else float("inf")
        m = min(cv, dv)
        if m != float("inf"):
            best = max(best, m)
    return best


def satisfied_min_feasible(c_vals, d_vals, cap):
    """容量 <= 当天最小空间 → 满足（容量越小越容易满足）。"""
    vals = list(c_vals) + list(d_vals)
    return bool(vals) and min(vals) >= cap


def satisfied_full_load(c_vals, d_vals, cap):
    """存在某次循环，其充放空间都 >= 容量 → 满足。"""
    n = max(len(c_vals), len(d_vals))
    for i in range(n):
        cv = c_vals[i] if i < len(c_vals) else float("inf")
        dv = d_vals[i] if i < len(d_vals) else float("inf")
        if cv >= cap and dv >= cap:
            return True
    return False


# ══════════════════════════════════════════════════════════
print("=" * 78)
print("① 最小可行口径：反推的容量，再反查应恰好得到目标天数")
print("=" * 78)

# 构造 40 天，每天四个空间：c1 递减、c2/c1 的 0.9、d1 的 0.8、d2 的 0.7
# 于是每天的最小空间 = d2 = 0.7*c1，随天数递减
N_DAYS = 40
days_data = []
for k in range(N_DAYS):
    base = 1000.0 - k * 20.0          # 1000 → 220
    days_data.append(make_day(base, base * 0.9, base * 0.8, base * 0.7))

mm = np.array([daily_cap_min_feasible(c, d) for c, d in days_data])
print(f"  日最小容量的范围：{mm.min():.0f} ~ {mm.max():.0f}（{N_DAYS} 天）")

for target in (1, 5, 20, 35, 40):
    cap = calc_capacity_for_days(target, mm)
    got = sum(1 for c, d in days_data if satisfied_min_feasible(c, d, cap))
    check(f"最小可行 · 目标 {target:>2} 天 → 容量 {cap:6.0f} → 反查 {got:>2} 天",
          got == target, f"期望 {target} 实得 {got}")

print()
print("=" * 78)
print("② 满载口径：同样闭环")
print("=" * 78)

fl = np.array([daily_cap_full_load(c, d) for c, d in days_data])
print(f"  日满载利用率范围：{fl.min():.0f} ~ {fl.max():.0f}")

for target in (1, 10, 25, 40):
    cap = calc_capacity_for_days(target, fl)
    got = sum(1 for c, d in days_data if satisfied_full_load(c, d, cap))
    check(f"满载 · 目标 {target:>2} 天 → 容量 {cap:6.0f} → 反查 {got:>2} 天",
          got == target, f"期望 {target} 实得 {got}")

print()
print("=" * 78)
print("③ 单调性：目标天数越大，容量越小（容量越小越容易满足）")
print("=" * 78)

caps = [calc_capacity_for_days(t, mm) for t in range(1, N_DAYS + 1)]
check("最小可行：容量随目标天数单调不增",
      all(caps[i] >= caps[i + 1] for i in range(len(caps) - 1)),
      str([round(c) for c in caps[:5]] + ["..."] + [round(c) for c in caps[-3:]]))
check("满足全部天数 → 容量 = 最小日容量",
      abs(caps[-1] - mm.min()) < 1e-9, f"{caps[-1]} vs {mm.min()}")
check("只满足 1 天 → 容量 = 最大日容量",
      abs(caps[0] - mm.max()) < 1e-9, f"{caps[0]} vs {mm.max()}")

print()
print("=" * 78)
print("④ 用户描述的算例：365 天要满足 330 天 → 第 36 小")
print("=" * 78)

d365 = np.arange(365, 0, -1, dtype=float) * 10.0   # 3650 → 10
cap330 = calc_capacity_for_days(330, d365)
# 365 天满足 330 天 → 取第 (365-330+1)=36 小 = 360
# （注意是「小」不是「大」：容量越小越容易满足）
expect = np.sort(d365)[365 - 330]
check(f"365→330 的容量 = 第 36 小 = {expect:.0f}", abs(cap330 - expect) < 1e-9,
      f"实得 {cap330:.0f}")
covered = int(np.sum(d365 >= cap330))
check(f"该容量能覆盖 {covered} 天（应 = 330）", covered == 330, str(covered))

print()
print("=" * 78)
print("⑤ 两套口径的差异：满载口径的日容量恒 >= 最小可行口径")
print("=" * 78)

check("满载 >= 最小可行（后者是四空间取最小，最保守）",
      bool(np.all(fl >= mm - 1e-9)),
      f"最大差值 {float(np.max(fl - mm)):.1f}")

print()
print("=" * 78)
print(f"结果：PASS {PASS}   FAIL {FAIL}")
print("=" * 78)
sys.exit(1 if FAIL else 0)
