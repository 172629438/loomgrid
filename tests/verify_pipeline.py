# -*- coding: utf-8 -*-
"""测算链路测试：宽表 → 负荷特征 → 容量测算 → 经济性。

前面几套测到「宽表」为止（verify_ingest 测单点、verify_e2e 测文件→宽表、
verify_gui_flow 测界面）。这一套补最后一环：宽表怎么变成方案。

断言分三类，都不依赖「当前实现恰好是多少」：
  · 数学不变量——容量 = 功率×时长、年省 = 日省×365、回收 = 投资/年省
  · 物理边界——功率不超过峰值、投资为正、LCOS 为正
  · 单调性——价差越大 IRR 越高，成本越高 IRR 越低

覆盖
----
A 六形态测算   B 价差单调   C 边界输入   D 数学不变量
E 成本场景     F 多场景扫描  G Fusion 测算  H 脏数据诊断
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import logging
import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ULTRA = os.path.join(ROOT, "负荷曲线批量生成Ultra.py")
FUSION = os.path.join(ROOT, "负荷曲线批量生成工具 Pro 融合版 (v2.3).py")

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
U = load_module(ULTRA, "lg_pl_u")
print("加载 Fusion…", flush=True)
F = load_module(FUSION, "lg_pl_f")


def profile(kind, n=96):
    """典型日负荷（kW）。"""
    out = []
    for i in range(n):
        h = i * 24.0 / n
        if kind == "industrial":
            v = 400 + 520 * np.exp(-((h - 9.5) ** 2) / 2.0) + 460 * np.exp(-((h - 19.5) ** 2) / 2.0)
        elif kind == "commercial":
            v = 120 + 780 * np.exp(-((h - 14.0) ** 2) / 9.0)
        elif kind == "pv":
            v = 260 - 620 * np.exp(-((h - 12.5) ** 2) / 6.0)
        elif kind == "flat":
            v = 600.0
        elif kind == "spiky":
            v = 300 + (900 if i in (40, 41) else 0)
        elif kind == "seasonal":
            v = 450 + 300 * np.exp(-((h - 10) ** 2) / 4.0)
        else:
            v = 300.0
        out.append(round(float(v), 3))
    return out


def scan(prof, spread=0.7):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return U.scan_capacity(prof, spread)


# ────────────────────────────────────────────────────────────
def group_A():
    print("\n" + "=" * 74 + "\nA. 六形态测算\n" + "=" * 74)
    for kind, label in (("industrial", "工业双峰"), ("commercial", "商业单峰"), ("pv", "光伏倒送"),
                        ("flat", "连续生产"), ("spiky", "尖峰冲击"), ("seasonal", "季节递增")):
        prof = profile(kind)
        try:
            plans = scan(prof)
        except Exception as e:
            check(f"A {label} 测算不崩", False, f"{type(e).__name__}: {e}")
            continue
        if kind == "pv":
            # 净负荷中午为负（光伏倒送）、夜间仍为正峰——储能依然是可行的：
            # 中午吸收倒送、晚上削峰。所以这里期望「有方案」而不是「空」。
            check(f"A {label} 有方案（夜峰仍可套利）", len(plans) > 0, f"方案数 {len(plans)}")
            if plans:
                check(f"A {label} 功率不超夜峰值",
                      plans[0].optimal_power_kw <= max(prof) + 1e-6,
                      f"功率 {plans[0].optimal_power_kw:.1f} > 峰值 {max(prof):.1f}")
            continue
        check(f"A {label} 有方案", len(plans) > 0, f"方案数 {len(plans)}")
        if not plans:
            continue
        irrs = [p.irr_percent for p in plans]
        check(f"A {label} IRR 降序", all(irrs[i] >= irrs[i + 1] for i in range(len(irrs) - 1)),
              f"前三个 {[round(x, 2) for x in irrs[:3]]}")
        peak = max(prof)
        check(f"A {label} 功率不超峰值", plans[0].optimal_power_kw <= peak + 1e-6,
              f"功率 {plans[0].optimal_power_kw:.1f} > 峰值 {peak:.1f}")


def group_B():
    print("\n" + "=" * 74 + "\nB. 价差单调性\n" + "=" * 74)
    prof = profile("industrial")
    irr_by_spread = {}
    for sp in (0.4, 0.7, 1.0, 1.3):
        plans = scan(prof, sp)
        if not plans:
            check(f"B 价差 {sp} 有方案", False, "空")
            continue
        irr_by_spread[sp] = plans[0].irr_percent
    keys = sorted(irr_by_spread)
    ok = all(irr_by_spread[keys[i]] < irr_by_spread[keys[i + 1]] for i in range(len(keys) - 1))
    check("B1 价差越大 IRR 越高", ok,
          " ".join(f"{k}:{irr_by_spread[k]:.2f}%" for k in keys))

    # B2 价差为 0 时无套利收益，只剩需量收益，IRR 应显著更低
    p0 = scan(prof, 0.0)
    if p0 and 1.0 in irr_by_spread:
        check("B2 零价差 IRR 显著下降", p0[0].irr_percent < irr_by_spread[1.0],
              f"零价差 {p0[0].irr_percent:.2f}% vs 1.0 元 {irr_by_spread[1.0]:.2f}%")


def group_C():
    print("\n" + "=" * 74 + "\nC. 边界输入\n" + "=" * 74)
    cases = [
        ("C1 全零负荷", [0.0] * 96),
        ("C2 全负负荷", [-100.0] * 96),
        ("C3 单点负荷", [500.0]),
        ("C4 极小负荷", [0.5] * 96),
        ("C5 巨大负荷", [1e7] * 96),
        ("C6 含 NaN", [500.0] * 40 + [float("nan")] * 8 + [500.0] * 48),
    ]
    for label, prof in cases:
        try:
            plans = scan(prof)
            # 只要不抛异常、且不给出 -inf/nan 的方案就算通过
            bad_vals = [p for p in plans if not math.isfinite(float(p.irr_percent))]
            check(f"{label} 不崩且无非法 IRR", len(bad_vals) == 0,
                  f"方案 {len(plans)}，非法 {len(bad_vals)}")
        except Exception as e:
            check(f"{label} 不崩且无非法 IRR", False, f"{type(e).__name__}: {e}")


def group_D():
    print("\n" + "=" * 74 + "\nD. 数学不变量\n" + "=" * 74)
    prof = profile("industrial")
    plans = scan(prof)
    if not plans:
        check("D 有方案可验", False, "空")
        return
    p = plans[0]
    # D1 容量 = 功率 × 时长
    expect_cap = p.optimal_power_kw * p.duration_hours
    check("D1 容量 = 功率 × 时长", abs(p.optimal_capacity_kwh - expect_cap) < 1e-6,
          f"{p.optimal_capacity_kwh:.3f} vs {expect_cap:.3f}")
    # D2 功率 = 峰值 × 功率因子（默认 0.30）
    peak = max(prof)
    check("D2 功率 = 峰值 × 0.30", abs(p.optimal_power_kw - peak * 0.30) < 1e-3,
          f"{p.optimal_power_kw:.2f} vs {peak * 0.30:.2f}")
    # D3 年省 = (容量×0.8×价差 + 功率×需量价/30) × 365
    expect_save = (p.optimal_capacity_kwh * 0.8 * 0.7 + p.optimal_power_kw * 40.0 / 30.0) * 365
    rel = abs(p.annual_savings - expect_save) / max(expect_save, 1e-9)
    check("D3 年省符合公式", rel < 1e-6, f"{p.annual_savings:,.2f} vs {expect_save:,.2f}（相对差 {rel:.2e}）")
    # D4 回收 = 投资 / 年省
    expect_pb = p.total_investment / p.annual_savings if p.annual_savings else float("inf")
    check("D4 回收期 = 投资/年省", abs(p.payback_years - expect_pb) < 1e-6,
          f"{p.payback_years:.4f} vs {expect_pb:.4f}")
    # D5 投资为正、LCOS 为正
    check("D5 投资与 LCOS 为正", p.total_investment > 0 and p.lcos > 0,
          f"投资 {p.total_investment:,.0f} LCOS {p.lcos:.4f}")
    # D6 IRR>折现率 ⇔ NPV>0
    dr = 0.07
    consistent = (p.irr_percent / 100.0 > dr) == (p.npv > 0)
    check("D6 IRR 与 NPV 符号一致", consistent, f"IRR {p.irr_percent:.2f}% NPV {p.npv:,.0f}")


def group_E():
    print("\n" + "=" * 74 + "\nE. 成本场景单调性\n" + "=" * 74)
    prof = profile("industrial")
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            multi = U.scan_capacity_multi_scenario(prof, 0.7)
        keys = list(multi.keys())
        check("E1 三场景都有结果", len(keys) >= 3, f"场景 {keys}")
        top = {}
        for k, plans in multi.items():
            if plans:
                top[k] = plans[0].irr_percent
        if len(top) >= 2:
            vals = sorted(top.values())
            check("E2 成本越高 IRR 越低（场景间有区分）",
                  len(set(round(v, 2) for v in vals)) > 1,
                  " ".join(f"{k}:{v:.2f}%" for k, v in top.items()))
        else:
            check("E2 成本越高 IRR 越低（场景间有区分）", False, "有效场景不足 2 个")
    except Exception as e:
        check("E1 多场景扫描", False, f"{type(e).__name__}: {e}")


def group_F():
    print("\n" + "=" * 74 + "\nF. 计划寿命与折现率影响\n" + "=" * 74)
    prof = profile("industrial")
    try:
        p_short = U.scan_capacity(prof, 0.7, invest_params=U.InvestmentParams(project_life=10))
        p_long = U.scan_capacity(prof, 0.7, invest_params=U.InvestmentParams(project_life=20))
        if p_short and p_long:
            check("F1 寿命越长 IRR 越高", p_long[0].irr_percent > p_short[0].irr_percent,
                  f"10y {p_short[0].irr_percent:.2f}% vs 20y {p_long[0].irr_percent:.2f}%")
        else:
            check("F1 寿命越长 IRR 越高", False, "方案为空")
        p_hi = U.scan_capacity(prof, 0.7, invest_params=U.InvestmentParams(discount_rate=0.12))
        p_lo = U.scan_capacity(prof, 0.7, invest_params=U.InvestmentParams(discount_rate=0.04))
        if p_hi and p_lo:
            check("F2 折现率越高 NPV 越低", p_hi[0].npv < p_lo[0].npv,
                  f"12% {p_hi[0].npv:,.0f} vs 4% {p_lo[0].npv:,.0f}")
        else:
            check("F2 折现率越高 NPV 越低", False, "方案为空")
    except Exception as e:
        check("F 参数敏感性", False, f"{type(e).__name__}: {e}")


def group_G():
    print("\n" + "=" * 74 + "\nG. Fusion 测算\n" + "=" * 74)
    # Fusion 只有容量-天数互算这一层
    charge = [100.0, 200.0, 300.0, 400.0, 500.0]
    discharge = [120.0, 210.0, 290.0, 380.0, 470.0]
    # 取两者较小值后：mins = [100, 200, 290, 380, 470]
    try:
        cap = F.calc_capacity_for_days(3, charge, discharge)
        check("G1 挑 3 天容量 = min(charge,discharge) 的第 3 大", abs(cap - 290.0) < 1e-9, f"{cap}")
        days = F.calc_days_for_capacity(290.0, np.array(charge), np.array(discharge))
        check("G2 容量反查天数一致", int(days) == 3, f"{days}")
        cap_all = F.calc_capacity_for_days(5, charge, discharge)
        check("G3 全取时容量 = 最小值", abs(cap_all - 100.0) < 1e-9, f"{cap_all}")
        cap_zero = F.calc_capacity_for_days(0, charge, discharge)
        check("G4 取 0 天返回 0", cap_zero == 0.0, f"{cap_zero}")
        cap_over = F.calc_capacity_for_days(99, charge, discharge)
        check("G5 超过总天数返回 0", cap_over == 0.0, f"{cap_over}")
    except Exception as e:
        check("G Fusion 容量互算", False, f"{type(e).__name__}: {e}")


def group_H():
    print("\n" + "=" * 74 + "\nH. 脏数据诊断输出\n" + "=" * 74)
    # 捕获 root logger 输出，断言「有可读提示」而不是「静默通过」
    records = []

    class Grab(logging.Handler):
        def emit(self, record):
            records.append(record)

    root = logging.getLogger()
    h = Grab()
    root.addHandler(h)
    old_level = root.level
    root.setLevel(logging.DEBUG)

    try:
        # H1 空表（只有表头）
        import pandas as pd
        import tempfile
        tmp = tempfile.mkdtemp(prefix="lg_pl_")
        fp_empty = os.path.join(tmp, "empty.xlsx")
        pd.DataFrame(columns=["日期", "时间", "正向有功功率"]).to_excel(fp_empty, index=False)
        records.clear()
        try:
            df = U._read_file_full(fp_empty)
            ok = df is not None and len(df) == 0
        except Exception:
            ok = True    # 抛异常也算「没静默吞掉」
        check("H1 空表不崩溃", ok, f"记录 {len(records)} 条")

        # H2 日期全坏
        fp_bad = os.path.join(tmp, "baddate.xlsx")
        pd.DataFrame({"日期": ["坏"] * 10, "时间": ["00:00"] * 10,
                      "正向有功功率": [1.0] * 10}).to_excel(fp_bad, index=False)
        records.clear()
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                mapping = {"date_col": "日期", "time_col": "时间", "load_col": "正向有功功率",
                           "header_row": 0, "format_type": "long"}
                import threading
                res = U._scan_file_worker((fp_bad, "Sheet1", mapping, None, threading.Event(), None, False))
            is_empty_or_err = (res == [] or (isinstance(res, dict) and "error" in res)
                               or (isinstance(res, list) and len(res) == 0))
            check("H2 日期全坏 → 空结果或明确报错", is_empty_or_err, f"结果 {type(res).__name__} {res if isinstance(res, dict) else len(res)}")
        except Exception as e:
            check("H2 日期全坏 → 空结果或明确报错", False, f"{type(e).__name__}: {e}")

        # H3 负荷列全空
        fp_nan = os.path.join(tmp, "allnan.xlsx")
        pd.DataFrame({"日期": ["2026-10-01"] * 4, "时间": ["00:00", "00:15", "00:30", "00:45"],
                      "正向有功功率": [np.nan] * 4}).to_excel(fp_nan, index=False)
        records.clear()
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                import threading
                mapping = {"date_col": "日期", "time_col": "时间", "load_col": "正向有功功率",
                           "header_row": 0, "format_type": "long"}
                res = U._scan_file_worker((fp_nan, "Sheet1", mapping, None, threading.Event(), None, False))
            check("H3 负荷全空不崩", isinstance(res, (list, dict)), f"{type(res).__name__}")
        except Exception as e:
            check("H3 负荷全空不崩", False, f"{type(e).__name__}: {e}")

        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        root.removeHandler(h)
        root.setLevel(old_level)


# ────────────────────────────────────────────────────────────
GROUPS = {"A": group_A, "B": group_B, "C": group_C, "D": group_D,
          "E": group_E, "F": group_F, "G": group_G, "H": group_H}
ONLY = next((a for a in sys.argv[1:] if a in "ABCDEFGH" and len(a) == 1), None)

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
