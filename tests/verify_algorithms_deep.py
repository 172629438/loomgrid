# -*- coding: utf-8 -*-
"""算法深水区测试：套利调度、用户侧最优调度、MILP 容量规划。

前面几套测的是「数据怎么进来」和「扫描式容量测算」。这一套测真正的算法核心——
那些用求解器算最优调度的函数。它们是 Ultra 的卖点，此前零断言。

断言原则：只看物理与数学，不看实现细节。
  · 能量守恒：充入的能量减放出的能量 = SOC 变化
  · SOC 与功率边界：不越界、不超额定
  · 充放互斥：同一时刻不会既充又放
  · 价格单调：谷时充电量 > 峰时充电量
  · 成本分解：总成本 = 电度 + 需量

覆盖
----
A 套利调度   B 效率影响   C 用户侧调度   D MILP   E 边界
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ULTRA = os.path.join(ROOT, "负荷曲线批量生成Ultra.py")

VERBOSE = "-v" in sys.argv
PASS = FAIL = 0
FAILS = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        if VERBOSE:
            print(f"[PASS] {name}")
    else:
        FAIL += 1
        FAILS.append((name, detail))
        print(f"[FAIL] {name}  {detail}")


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        spec.loader.exec_module(mod)
    return mod


print("加载主程序…", flush=True)
U = load_module(ULTRA, "lg_ad")

# 24 小时电价：谷 0.3（0-8）、峰 1.2（10-12/17-21）、平 0.7
PRICES = []
for _h in range(24):
    if _h < 8:
        PRICES.append(0.3)
    elif _h in (10, 11, 17, 18, 19, 20):
        PRICES.append(1.2)
    else:
        PRICES.append(0.7)

CAP, PMAX, ETA, SOC0 = 20.0, 5.0, 0.95, 10.0


def arb(prices=None, capacity=CAP, p_max=PMAX, eta=ETA, initial_soc=SOC0, terminal=False):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return U.solve_bess_arbitrage(prices or PRICES, capacity=capacity, p_max=p_max,
                                      eta=eta, initial_soc=initial_soc, dt=1.0,
                                      enforce_terminal_soc=terminal)


# ────────────────────────────────────────────────────────────
def group_A():
    print("\n" + "=" * 74 + "\nA. 套利调度\n" + "=" * 74)
    try:
        r = arb()
    except Exception as e:
        check("A0 套利调度可调用", False, f"{type(e).__name__}: {e}")
        return
    keys = set(r.keys())
    check("A1 返回结构完整", {"objective", "p_ch", "p_dis", "soc"} <= keys, f"{sorted(keys)}")

    p_ch = np.asarray(r["p_ch"], dtype=float)
    p_dis = np.asarray(r["p_dis"], dtype=float)
    soc = np.asarray(r["soc"], dtype=float)

    # A2 能量守恒：充入×ηc − 放出/ηd = SOC 变化
    net = float(p_ch.sum() * ETA - p_dis.sum() / ETA)
    dsoc = float(soc[-1] - soc[0])
    check("A2 能量守恒", abs(net - dsoc) < 0.05, f"净充放 {net:.4f} vs SOC 变化 {dsoc:.4f}")

    # A3 SOC 边界
    check("A3 SOC 不越界", soc.min() >= -1e-6 and soc.max() <= CAP + 1e-6,
          f"[{soc.min():.3f}, {soc.max():.3f}] 容量 {CAP}")

    # A4 功率上界
    check("A4 功率不超额定", p_ch.max() <= PMAX + 1e-6 and p_dis.max() <= PMAX + 1e-6,
          f"充 {p_ch.max():.3f} 放 {p_dis.max():.3f} 上限 {PMAX}")

    # A5 充放互斥
    both = int(np.sum((p_ch > 1e-6) & (p_dis > 1e-6)))
    check("A5 充放互斥", both == 0, f"同时充放点数 {both}")

    # A6 谷时充电为主、峰时放电为主
    valley = [i for i, p in enumerate(PRICES) if p <= 0.3]
    peak = [i for i, p in enumerate(PRICES) if p >= 1.2]
    ch_valley = float(p_ch[valley].sum())
    ch_peak = float(p_ch[peak].sum())
    dis_peak = float(p_dis[peak].sum())
    dis_valley = float(p_dis[valley].sum())
    check("A6 谷充峰放", ch_valley > ch_peak and dis_peak > dis_valley,
          f"谷充 {ch_valley:.2f} vs 峰充 {ch_peak:.2f}；峰放 {dis_peak:.2f} vs 谷放 {dis_valley:.2f}")

    # A7 收益为正
    check("A7 套利收益为正", float(r["objective"]) > 0, f"objective={r['objective']:.3f}")

    # A8 容量越大收益越高（同功率下）
    obj_small = float(arb(capacity=10.0, initial_soc=5.0)["objective"])
    obj_big = float(arb(capacity=40.0, initial_soc=20.0)["objective"])
    check("A8 容量越大收益越高", obj_big > obj_small, f"10kWh {obj_small:.2f} vs 40kWh {obj_big:.2f}")


def group_B():
    print("\n" + "=" * 74 + "\nB. 效率影响\n" + "=" * 74)
    try:
        obj_lo = float(arb(eta=0.80)["objective"])
        obj_hi = float(arb(eta=1.00)["objective"])
        check("B1 效率越高收益越高", obj_hi > obj_lo, f"η=0.80 {obj_lo:.2f} vs η=1.00 {obj_hi:.2f}")
    except Exception as e:
        check("B1 效率越高收益越高", False, f"{type(e).__name__}: {e}")

    # B2 平价电价 + 强制末态回到初始 SOC：真无套利空间，收益应接近 0
    try:
        r = arb(prices=[0.6] * 24, terminal=True)
        check("B2 平价且末态归位时无套利", abs(float(r["objective"])) < 1.0, f"objective={r['objective']:.4f}")
    except Exception as e:
        check("B2 平价且末态归位时无套利", False, f"{type(e).__name__}: {e}")

    # B3 默认不强制末态时，允许“变卖初始库存”——收益里含这部分
    try:
        r_free = arb(prices=[0.6] * 24, terminal=False)
        soc = np.asarray(r_free["soc"], dtype=float)
        drained = soc[0] - soc[-1]
        check("B3 不强制末态时会消耗初始库存（记录行为）", drained > 1.0,
              f"objective={r_free['objective']:.3f}，SOC 从 {soc[0]:.1f} 降到 {soc[-1]:.1f}")
    except Exception as e:
        check("B3 不强制末态时会消耗初始库存（记录行为）", False, f"{type(e).__name__}: {e}")

    # B4 初始 SOC 为 0 时，首时段不能放电
    try:
        r = arb(initial_soc=0.0)
        p_dis = np.asarray(r["p_dis"], dtype=float)
        check("B4 SOC=0 时不放电", p_dis[0] < 1e-6, f"首时段放电 {p_dis[0]:.4f}")
    except Exception as e:
        check("B3 SOC=0 时不放电", False, f"{type(e).__name__}: {e}")


def group_C():
    print("\n" + "=" * 74 + "\nC. 用户侧最优调度\n" + "=" * 74)
    try:
        bp = U.BESSParams(capacity=20.0, soc_min=1.0, soc_max=19.0, p_ch_max=5.0, p_dis_max=5.0)
        load = [500.0 + 200 * np.sin(h / 3.0) for h in range(24)]
        di = U.DispatchInput(timestamps=list(range(24)), load_forecast=load, buy_price=PRICES,
                             bess=bp, initial_soc=10.0, demand_charge_rate=40.0, step_hours=1.0)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            res = U.solve_user_side_bess(di)
    except Exception as e:
        check("C0 用户侧调度可调用", False, f"{type(e).__name__}: {e}")
        return

    # C1 总成本 = 电度 + 需量
    total = float(res.energy_cost) + float(res.demand_cost)
    check("C1 成本分解", abs(total - float(res.total_cost)) < 1e-3,
          f"{total:.4f} vs {res.total_cost:.4f}")

    # C2 无约束违反
    check("C2 无约束违反", not res.constraint_violations, f"{res.constraint_violations}")

    # C3 充放互斥
    ch = np.asarray(res.charge_power, dtype=float)
    dis = np.asarray(res.discharge_power, dtype=float)
    both = int(np.sum((ch > 1e-6) & (dis > 1e-6)))
    check("C3 充放互斥", both == 0, f"同时充放点数 {both}")

    # C4 SOC 在 [soc_min, soc_max]
    soc = np.asarray(res.soc, dtype=float)
    check("C4 SOC 在区间内", soc.min() >= 1.0 - 1e-6 and soc.max() <= 19.0 + 1e-6,
          f"[{soc.min():.3f}, {soc.max():.3f}] 期望 [1, 19]")

    # C5 净负荷不低于 0（不向电网倒送）
    gi = np.asarray(res.grid_import, dtype=float)
    check("C5 不从电网倒送", gi.min() >= -1e-6, f"最小购电 {gi.min():.4f}")


def group_D():
    print("\n" + "=" * 74 + "\nD. MILP 容量规划\n" + "=" * 74)
    import pandas as pd
    idx = pd.date_range("2026-10-01", periods=96 * 3, freq="15min")
    load = np.array([500 + 200 * np.sin(i / 8.0) for i in range(len(idx))])
    # MILP 输入契约：datetime / produce / consume / FiT / price / month / hour
    price_ser = np.where(idx.hour < 8, 0.3, np.where(np.isin(idx.hour, [10, 11, 17, 18, 19, 20]), 1.2, 0.7))
    df = pd.DataFrame({
        "datetime": idx, "produce": 0.0, "consume": load,
        "FiT": 0.35, "price": price_ser,
        "month": idx.month, "hour": idx.hour,
    }, index=idx)

    # D1 空 devices 不崩
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            cfg = U.MilpConfig()
            r = U.solve_milp_capacity(df, cfg)
        check("D1 空 devices 不崩", True, f"status={getattr(r, 'status', '?')}")
        check("D2 返回 MilpResult", hasattr(r, "capacity_kwh") and hasattr(r, "power_kw"),
              f"{type(r).__name__}")
        check("D3 容量与功率非负", float(r.capacity_kwh) >= 0 and float(r.power_kw) >= 0,
              f"{r.capacity_kwh} / {r.power_kw}")
        check("D4 求解时间有限", float(r.solve_time_s) < 600, f"{r.solve_time_s:.2f}s")
    except Exception as e:
        check("D1 空 devices 不崩", False, f"{type(e).__name__}: {e}")


def group_E():
    print("\n" + "=" * 74 + "\nE. 边界输入\n" + "=" * 74)
    cases = [
        ("E1 空价格序列", []),
        ("E2 单点价格", [0.5]),
        ("E3 全负价格", [-0.5] * 24),
        ("E4 价格剧烈跳变", [0.1, 5.0] * 12),
    ]
    for label, prices in cases:
        try:
            r = arb(prices=prices)
            soc = np.asarray(r["soc"], dtype=float)
            finite = np.all(np.isfinite(np.asarray(r["p_ch"], dtype=float))) and np.all(np.isfinite(soc))
            check(f"{label} 不崩且输出有限", finite, f"soc len={len(soc)}")
        except Exception as e:
            # 空序列抛明确异常也算合理
            ok = label.startswith("E1") and isinstance(e, (ValueError, IndexError))
            check(f"{label} 不崩或明确报错", ok, f"{type(e).__name__}: {e}")


# ────────────────────────────────────────────────────────────
GROUPS = {"A": group_A, "B": group_B, "C": group_C, "D": group_D, "E": group_E}
ONLY = next((a for a in sys.argv[1:] if a in "ABCDE" and len(a) == 1), None)

for k, fn in GROUPS.items():
    if ONLY and k != ONLY:
        continue
    try:
        fn()
    except Exception:
        import traceback
        traceback.print_exc()
        check(f"组 {k} 执行异常", False, "见 traceback")

print()
print("=" * 74)
print(f"结果：PASS {PASS}   FAIL {FAIL}")
print("=" * 74)
if FAILS:
    print("\n失败项：")
    for n, d in FAILS:
        print(f"  - {n}  {d}")
sys.exit(1 if FAIL else 0)
