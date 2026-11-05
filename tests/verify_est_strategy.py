# -*- coding: utf-8 -*-
"""两种评估策略在「满足天数」上必须区分。

旧版 v2.3 的 tooltip 写明了定义（作者原话）：

    最小可行容量: min(所有充放段) → 全天候可行, 最保守
    满载利用率:   max(min(c₁,d₁), min(c₂,d₂)) → 最苛刻循环跑满

但旧版 `_calc_days_from_capacity` 里那个 `if use_min_all` 分支是失效的
（取到的列表只用来算 n_days，判定在 `_check_day_satisfied` 里固定走
`_get_relevant_segments_for_month`），于是两种策略算出同一个数字——
这是「想区分但没接上」，不是设计上不该区分。

本脚本守住三件事：
  ① 两种判据在可区分的场景下确实给出不同结果
  ② 最小可行永远不宽于满载（更严）
  ③ `_check_day_satisfied` 里真的按 est_combo 分支了

跑法：python tests/verify_est_strategy.py
"""
import ast
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.stdout.reconfigure(encoding="utf-8")

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}  {detail}")


def check_min_feasible(c_arrs, d_arrs, day, cap):
    """最小可行：所有段都够"""
    vals = [a[day] for a in c_arrs if day < len(a)] + \
           [a[day] for a in d_arrs if day < len(a)]
    if not vals:
        return False
    return min(vals) >= cap


def check_full_load(c_arrs, d_arrs, day, cap):
    """满载：某一次循环的充放都够"""
    n = max(len(c_arrs), len(d_arrs))
    for i in range(n):
        cv = c_arrs[i][day] if i < len(c_arrs) and day < len(c_arrs[i]) else float("inf")
        dv = d_arrs[i][day] if i < len(d_arrs) and day < len(d_arrs[i]) else float("inf")
        if cv >= cap and dv >= cap:
            return True
    return False


# ── ① 可区分的场景：两次循环一强一弱 ────────────────────────
# 第一次 300/280，第二次 120/110，容量 200
c_ok, d_ok = [[300.0], [280.0]], [[280.0], [110.0]]
c_mix, d_mix = [[300.0], [120.0]], [[280.0], [110.0]]

check("满载：第一次循环 300/280 够 200 → 满足", check_full_load(c_mix, d_mix, 0, 200.0) is True)
check("最小可行：第二次 120/110 不够 → 不满足", check_min_feasible(c_mix, d_mix, 0, 200.0) is False)
check("同一组数据两种策略结果不同（这就是必须区分的原因）",
      check_full_load(c_mix, d_mix, 0, 200.0) != check_min_feasible(c_mix, d_mix, 0, 200.0))

# ── ② 最小可行永远不宽于满载 ────────────────────────────────
cases = [
    ([[300.0], [280.0]], [[280.0], [110.0]], 200.0),
    ([[300.0], [250.0]], [[280.0], [240.0]], 200.0),   # 两次都够
    ([[120.0], [110.0]], [[115.0], [105.0]], 200.0),   # 两次都不够
    ([[200.0]], [[200.0]], 200.0),                     # 一充一放刚好
    ([[199.0]], [[199.0]], 200.0),                     # 一充一放差一点
]
ok = True
for c, d, cap in cases:
    a = check_min_feasible(c, d, 0, cap)
    b = check_full_load(c, d, 0, cap)
    if a and not b:
        ok = False
        print(f"   反例：min={a} full={b} cap={cap} c={c} d={d}")
check("最小可行从不宽于满载（5 组场景）", ok)

# 一充一放时两种判据等价（列表只有 1 项）
check("一充一放：200/200 容量 200 → 两者都满足",
      check_min_feasible([[200.0]], [[200.0]], 0, 200.0) is True and
      check_full_load([[200.0]], [[200.0]], 0, 200.0) is True)
check("一充一放：150/150 容量 200 → 两者都不满足",
      check_min_feasible([[150.0]], [[150.0]], 0, 200.0) is False and
      check_full_load([[150.0]], [[150.0]], 0, 200.0) is False)

# ── ③ AST：_check_day_satisfied 真的分支了 ─────────────────
MAIN = os.path.join(REPO, "apps", "负荷曲线批量生成工具 Pro 融合版 (v2.3).py")
src = io.open(MAIN, encoding="utf-8", errors="replace").read()
tree = ast.parse(src)


def find_method(tree, cls, meth):
    for n in tree.body:
        if isinstance(n, ast.ClassDef) and n.name == cls:
            for m in n.body:
                if isinstance(m, ast.FunctionDef) and m.name == meth:
                    return m
    return None


node = find_method(tree, "ProfessionalWindow", "_check_day_satisfied")
check("_check_day_satisfied 存在", node is not None)
if node is not None:
    body = ast.unparse(node)
    check("按 est_combo 分支", "est_combo" in body and "use_min" in body)
    check("最小可行分支用 min(vals)", "min(vals) >= usable_cap" in body)
    check("满载分支用配对 cv/dv", "cv >= usable_cap and dv >= usable_cap" in body)

# 容量反推侧仍保持区分
node2 = find_method(tree, "ProfessionalWindow", "_get_all_filtered_daily_mins")
check("_get_all_filtered_daily_mins 存在", node2 is not None)
if node2 is not None:
    b2 = ast.unparse(node2)
    check("容量反推最小可行用 min(all_vals)", "min(all_vals)" in b2)
    check("容量反推满载用 max(pair_min)", "pair_min" in b2)

# 失效分支已删
node3 = find_method(tree, "ProfessionalWindow", "_calc_days_from_capacity")
check("_calc_days_from_capacity 存在", node3 is not None)
if node3 is not None:
    b3 = ast.unparse(node3)
    check("失效的 use_min_all 分支已移除", "use_min_all" not in b3)

print()
print(f"结果：PASS {PASS}   FAIL {FAIL}")
sys.exit(1 if FAIL else 0)
