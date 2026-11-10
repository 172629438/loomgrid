# -*- coding: utf-8 -*-
"""v2.3.24 两处修复的回归：CSV 字段并集 + full_load 配对。

两处都是「多出一行/多出一个段就崩或算错」那类边界。
"""
import io
import os
import sys
import tempfile

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.stdout.reconfigure(encoding="utf-8")

passed, failed = [], []


def check(name, cond, detail=""):
    if cond:
        passed.append(name)
        print(f"  [PASS] {name}")
    else:
        failed.append(name)
        print(f"  [FAIL] {name}  {detail}")


print("=" * 76)
print("① export_csv：字段不一致不再崩")
print("=" * 76)
from services.export import export_csv

tmp = tempfile.mkdtemp(prefix="csvtest_")
try:
    # 第一行字段最少，后续行多出字段
    rows = [
        {"月份": "2026-07", "充电空间": 100},
        {"月份": "2026-08", "充电空间": 120, "容量建议": 200},
        {"月份": "2026-09", "充电空间": 90, "容量建议": 180, "备注": "缺数据"},
    ]
    p = export_csv(rows, tmp, "mixed.csv")
    check("三行不同字段能写出", os.path.exists(p))
    content = io.open(p, encoding="utf-8-sig").read()
    lines = content.splitlines()
    print(f"  表头: {lines[0]}")
    check("表头是字段并集（保序）",
          lines[0] == "月份,充电空间,容量建议,备注", lines[0])
    check("缺字段补空而非报错", lines[1] == "2026-07,100,,", lines[1])
    check("三行数据都在", len(lines) == 4, f"{len(lines)} 行")

    # 反向证明：旧的 fieldnames=rows[0].keys() 会崩
    import csv
    crashed = False
    try:
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    except ValueError:
        crashed = True
    check("旧实现确实会 ValueError（证明 bug 真实存在）", crashed)

    # 空列表
    p2 = export_csv([], tmp, "empty.csv")
    check("空列表输出空文件", os.path.exists(p2)
          and os.path.getsize(p2) == 0)

    # 单行
    p3 = export_csv([{"a": 1}], tmp, "one.csv")
    check("单行正常", io.open(p3, encoding="utf-8-sig").read().strip() == "a\n1")
finally:
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

print()
print("=" * 76)
print("② full_load 配对：段数不等时不再把单侧值当真")
print("=" * 76)
from services.capacity import daily_demand_series, DemandBasis

CASES = [
    ("充电 1 个、放电 2 个（旧实现会错）",
     [np.array([100.0])], [np.array([300.0]), np.array([280.0])], 100.0),
    ("充电 2 个、放电 1 个",
     [np.array([300.0]), np.array([250.0])], [np.array([280.0])], 280.0),
    ("段数相等（正常）",
     [np.array([300.0]), np.array([250.0])],
     [np.array([280.0]), np.array([240.0])], 280.0),
    ("充电 1 个、放电 3 个",
     [np.array([50.0])],
     [np.array([400.0]), np.array([300.0]), np.array([200.0])], 50.0),
]
for name, c, d, expect in CASES:
    got = float(daily_demand_series(c, d, DemandBasis.full_load)[0])
    check(f"{name} → {expect}", abs(got - expect) < 1e-9, f"实际 {got}")

# 反向证明：旧的 max(len) 会算错第一个 case
def old_impl(c, d):
    n = max(len(c), len(d))
    best = 0.0
    for i in range(n):
        cv = c[i][0] if i < len(c) else float("inf")
        dv = d[i][0] if i < len(d) else float("inf")
        pm = min(cv, dv)
        if pm != float("inf"):
            best = max(best, pm)
    return best

old = old_impl([np.array([100.0])], [np.array([300.0]), np.array([280.0])])
check("旧实现确实算出 280（证明 bug 真实存在）", abs(old - 280.0) < 1e-9, str(old))

print()
print("=" * 76)
print("③ 两边一致（主程序与 services 同口径）")
print("=" * 76)
main_src = io.open(os.path.join(REPO, "apps",
                                "负荷曲线批量生成工具 Pro 融合版 (v2.3).py"),
                   encoding="utf-8").read()
cap_src = io.open(os.path.join(REPO, "services", "capacity.py"),
                  encoding="utf-8").read()
check("主程序用 min(len) 而非 max(len)",
      "_n = min(len(_c_pair), len(_d_pair))" in main_src,
      "主程序 _fill_daily_table 仍是 max")
check("services 用 min(len) 而非 max(len)",
      "n_pairs = min(len(charge_segs), len(discharge_segs))" in cap_src,
      "services 仍是 max")

print()
print("=" * 76)
print(f"结果：PASS {len(passed)}   FAIL {len(failed)}")
for f in failed:
    print("  -", f)
print("=" * 76)
sys.exit(1 if failed else 0)
