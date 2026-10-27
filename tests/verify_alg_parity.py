# -*- coding: utf-8 -*-
"""算法一致性守卫：确保 algorithms/ 剥离层与主程序同名函数不漂移。

为什么需要这个
--------------
仓库里同一套算法有三份实现：主程序（Ultra）、剥离层（algorithms/）、历史快照（legacy/）。
2026-10 的两轮修复里，有 3 处修复只落在剥离层、主程序没跟上（充电口径、LP 放电收益、
分段返回结构），造成「同一算法两个都会跑、谁对没有权威判定」。这份脚本把「两边必须一致」
变成断言：源码骨架一致 + 同输入数值一致。

三层检查
--------
A. 源码骨架：去 docstring / 注释 / 空行后逐字比对
B. 数值输出：固定输入跑两版，比对结果（含结构长度）
C. 常量一致：效率、SOC 边界等模块级常量两边相同

用法
----
    python tests/verify_alg_parity.py          # 全部检查
    python tests/verify_alg_parity.py -v       # 打印每条明细
"""
from __future__ import annotations

import ast
import os
import sys
import re

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ULTRA = os.path.join(ROOT, "负荷曲线批量生成Ultra.py")
ALG_DIR = os.path.join(ROOT, "algorithms")
sys.path.insert(0, ALG_DIR)

VERBOSE = "-v" in sys.argv

PASS = FAIL = 0
FAILURES = []


def ok(name, detail=""):
    global PASS
    PASS += 1
    print(f"[PASS] {name}" + (f"  {detail}" if VERBOSE and detail else ""))


def bad(name, detail=""):
    global FAIL
    FAIL += 1
    FAILURES.append((name, detail))
    print(f"[FAIL] {name}  {detail}")


def norm(src: str) -> list[str]:
    """去掉 docstring、注释、空行，只留代码骨架。"""
    src = re.sub(r'"""[\s\S]*?"""', "", src)
    src = re.sub(r"'''[\s\S]*?'''", "", src)
    return [ln.strip() for ln in src.splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


def top_funcs(path):
    src = open(path, encoding="utf-8", errors="replace").read()
    tree = ast.parse(src)
    return {n.name: ast.get_source_segment(src, n) or "" for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def const_block(path, names):
    """取模块级常量赋值（形如 NAME = 0.93）。"""
    src = open(path, encoding="utf-8", errors="replace").read()
    out = {}
    for ln in src.splitlines():
        m = re.match(r"^([A-Z][A-Z0-9_]{2,})\s*=\s*([0-9.]+)\s*(?:#.*)?$", ln.strip())
        if m and m.group(1) in names:
            out[m.group(1)] = float(m.group(2))
    return out


# ────────────────────────────────────────────────────────────────
# A. 源码骨架一致
# ────────────────────────────────────────────────────────────────
print("=" * 78)
print("A. 源码骨架一致（Ultra 主程序 vs algorithms 剥离层）")
print("=" * 78)

import spaces, dispatch, periods, finance, loadgen, diagnostics  # noqa: E402

MODULES = [spaces, dispatch, periods, finance, loadgen, diagnostics]
alg_funcs = {}
for mod in MODULES:
    for name, code in top_funcs(mod.__file__).items():
        alg_funcs.setdefault(name, (mod.__name__, code))

ultra_funcs = top_funcs(ULTRA)
shared = sorted(set(ultra_funcs) & set(alg_funcs))
print(f"同名顶层函数 {len(shared)} 个\n")

same, diff = [], []
for name in shared:
    a, b = norm(ultra_funcs[name]), norm(alg_funcs[name][1])
    (same if a == b else diff).append(name)

if diff:
    for name in diff:
        a, b = norm(ultra_funcs[name]), norm(alg_funcs[name][1])
        only_u = [x for x in a if x not in b]
        only_a = [x for x in b if x not in a]
        bad(f"源码不一致: {name}",
            f"Ultra 独有 {len(only_u)} 行 / algorithms 独有 {len(only_a)} 行"
            + (f" | 首个差异: {only_u[0][:80]}" if only_u else "")
            + (f" | {only_a[0][:80]}" if only_a else ""))
else:
    ok(f"全部 {len(shared)} 个同名函数源码骨架一致")

if VERBOSE:
    print("  一致:", ", ".join(same))

# ────────────────────────────────────────────────────────────────
# B. 常量一致
# ────────────────────────────────────────────────────────────────
print()
print("=" * 78)
print("B. 关键常量一致")
print("=" * 78)

CONSTS = ["BESS_EFF_CHARGE", "BESS_EFF_DISCHARGE", "BESS_SOC_MIN", "BESS_SOC_MAX", "BESS_DOD_DEFAULT"]
ultra_const = const_block(ULTRA, set(CONSTS))
alg_const = {}
for mod in MODULES:
    alg_const.update(const_block(mod.__file__, set(CONSTS)))

for c in CONSTS:
    u, a = ultra_const.get(c), alg_const.get(c)
    if u is None and a is None:
        ok(f"{c} 两边均未定义为常量（跳过）")
    elif u is not None and a is not None and abs(u - a) < 1e-9:
        ok(f"{c} = {u}")
    else:
        bad(f"{c} 不一致", f"Ultra={u} algorithms={a}")


# ────────────────────────────────────────────────────────────────
# C. 数值输出一致
# ────────────────────────────────────────────────────────────────
print()
print("=" * 78)
print("C. 同输入数值一致")
print("=" * 78)


def mk(module, name):
    """把 Ultra 版函数注入一份独立命名空间（不影响模块本身）。"""
    g = dict(module.__dict__)
    exec(compile(ultra_funcs[name], f"<ultra:{name}>", "exec"), g)
    return g[name]


N = 96
LABELS = [f"{(i * 15) // 60}:{(i * 15) % 60:02d}" for i in range(N)]
RAW = [(0, 8, "valley"), (8, 11, "flat"), (11, 14, "valley"),
       (14, 16, "flat"), (16, 23, "peak"), (23, 24, "flat")]

# C1 平坦负荷，三种模式
flat = np.full((5, N), 500.0)
peaky = np.tile(np.concatenate([np.full(32, 300.0), np.full(32, 900.0), np.full(32, 400.0)]), (5, 1))
neg = np.tile(np.concatenate([np.full(32, -200.0), np.full(64, 600.0)]), (5, 1))

u_daily = mk(spaces, "compute_daily_spaces")
for mode, data, tag in (("A", flat, "Mode A 平坦"), ("A", peaky, "Mode A 峰谷"),
                        ("B", neg, "Mode B 负负荷"), ("C", neg, "Mode C 负负荷")):
    kwargs = dict(billing={"value": 800.0}, mode=mode, rated_power=500.0)
    uc, ud = u_daily(data, LABELS, RAW, **kwargs)
    ac, ad = spaces.compute_daily_spaces(data, LABELS, RAW, **kwargs)
    d = max(abs(np.nanmean(uc) - np.nanmean(ac)), abs(np.nanmean(ud) - np.nanmean(ad)))
    (ok if d < 1e-6 else bad)(f"compute_daily_spaces {tag}",
                             f"充电 {np.nanmean(uc):.3f}/{np.nanmean(ac):.3f} 放电 {np.nanmean(ud):.3f}/{np.nanmean(ad):.3f}")

# C2 分段空间：结构 + 能量
segs = periods._segment_periods(RAW)
u_seg = mk(spaces, "compute_segmented_spaces")
ur = u_seg(flat, LABELS, segs, billing={"value": 800.0}, mode="A")
ar = spaces.compute_segmented_spaces(flat, LABELS, segs, billing={"value": 800.0}, mode="A")
struct_same = (len(ur[0]) == len(ar[0]) and len(ur[1]) == len(ar[1])
               and ur[2] == ar[2] and ur[3] == ar[3])
energy_same = (abs(sum(float(np.nansum(x)) for x in ur[0]) - sum(float(np.nansum(x)) for x in ar[0])) < 1e-6
               and abs(sum(float(np.nansum(x)) for x in ur[1]) - sum(float(np.nansum(x)) for x in ar[1])) < 1e-6)
if struct_same and energy_same:
    ok("compute_segmented_spaces 结构与能量一致", f"{len(ar[0])}/{len(ar[1])} 段")
else:
    bad("compute_segmented_spaces 不一致",
        f"结构 {len(ur[0])}/{len(ur[1])} vs {len(ar[0])}/{len(ar[1])}；能量一致={energy_same}")

# C3 财务三式
from finance import calc_npv, calc_irr, calc_simple_payback  # noqa: E402
u_npv = mk(finance, "calc_npv")
u_irr = mk(finance, "calc_irr")
u_pb = mk(finance, "calc_simple_payback")
cases = [(200000.0, 1000000.0, 10, 0.08), (80507.0, 750000.0, 15, 0.06)]
npv_ok = all(abs(u_npv(*c) - calc_npv(*c)) < 1e-6 for c in cases)
(ok if npv_ok else bad)("calc_npv 一致", f"{[round(calc_npv(*c), 2) for c in cases]}")
irr_ok = all(abs((u_irr(*c[:3]) or 0) - (calc_irr(*c[:3]) or 0)) < 1e-6 for c in cases)
(ok if irr_ok else bad)("calc_irr 一致", f"{[calc_irr(*c[:3]) for c in cases]}")
pb_ok = abs(u_pb(1000000.0, 200000.0) - calc_simple_payback(1000000.0, 200000.0)) < 1e-9
(ok if pb_ok else bad)("calc_simple_payback 一致", f"{calc_simple_payback(1000000.0, 200000.0)}")

# C4 LP 调度
buy = np.array([0.3] * 32 + [0.65] * 12 + [0.3] * 12 + [0.65] * 8 + [1.1] * 28 + [0.65] * 4)[:N]
u_lp = mk(dispatch, "optimal_dispatch_lp")
for tag, sell in (("sell=0", np.zeros(N)), ("sell=0.35×buy", buy * 0.35)):
    ur_lp = u_lp(flat[0], buy, sell, batt_kwh=1000, Pmax=500, soc_min=0.05, soc_max=0.95)
    ar_lp = dispatch.optimal_dispatch_lp(flat[0], buy, sell, batt_kwh=1000, Pmax=500, soc_min=0.05, soc_max=0.95)
    if ur_lp.get("feasible") and ar_lp.get("feasible"):
        d = abs(ur_lp["revenue"] - ar_lp["revenue"])
        (ok if d < 0.02 else bad)(f"optimal_dispatch_lp {tag}", f"收益 {ar_lp['revenue']:.2f}（差 {d:.4f}）")
    else:
        bad(f"optimal_dispatch_lp {tag}", f"可行性不一致 {ur_lp.get('feasible')}/{ar_lp.get('feasible')}")

# C5 时段归一化三态
u_norm = mk(periods, "_normalize_periods")
forms = {
    "dict": [{"start": 8.0, "end": 11.0, "type": "flat"}],
    "tuple": [(8, 11, "flat")],
    "HH:MM": [("08:00", "11:00", "flat")],
}
for tag, p in forms.items():
    try:
        r = u_norm(p)
        r2 = periods._normalize_periods(p)
        (ok if r == r2 else bad)(f"_normalize_periods {tag}", f"{r}")
    except Exception as e:
        bad(f"_normalize_periods {tag}", f"{type(e).__name__}: {e}")

# ────────────────────────────────────────────────────────────────
print()
print("=" * 78)
print(f"结果：PASS {PASS}   FAIL {FAIL}")
print("=" * 78)
if FAILURES:
    print("\n失败项：")
    for n, d in FAILURES:
        print(f"  - {n}  {d}")
sys.exit(1 if FAIL else 0)
