# -*- coding: utf-8 -*-
"""逐段配对口径：一充一放与两充两放共用同一套判据，但索引含义是「第 i 次循环」。

背景（v2.3.8 → v2.3.10 的一次回滚）
------------------------------------
v2.3.8 曾把主程序三处「逐段配对 min」改成「当日总充与总放取瓶颈」，
理由是「剔除占位零后 c_arrs[i] 与 d_arrs[i] 不再是同一循环」。

**那个理由是错的。** `_drop_zero_segments` 保留原顺序：

    c_segs = [300,   0, 250,   0]      四个段：充电1 / 放电1 / 充电2 / 放电2
    d_segs = [  0, 280,   0, 240]
    剔除全零后
    c_arrs = [300, 250]                充电1、充电2
    d_arrs = [280, 240]                放电1、放电2

索引 i 就是第 i 次循环。逐段配对 `min(c_arrs[i], d_arrs[i])` 算的正是
「第 i 次循环能搬多少」，`max(...)` 取最好那次，`min(...)` 取最差那次。

总量口径会把两次循环合并，掩盖「单次循环不够」的事实：

    两次循环各 120/120，容量 200
      逐段配对 → 不满足（单次 120 < 200）
      总量口径 → 满足（min(240, 240) = 240 >= 200）

跑法：python tests/verify_daily_throughput.py
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


# ── ① 两种口径的分歧点（纯函数对照）────────────────────────
def pair_based(c_arrs, d_arrs, day, cap):
    """某一次循环的充放都够 → 满足"""
    n = max(len(c_arrs), len(d_arrs))
    for i in range(n):
        cv = c_arrs[i][day] if i < len(c_arrs) and day < len(c_arrs[i]) else float("inf")
        dv = d_arrs[i][day] if i < len(d_arrs) and day < len(d_arrs[i]) else float("inf")
        if cv >= cap and dv >= cap:
            return True
    return False


def total_based(c_arrs, d_arrs, day, cap):
    tc = sum(a[day] for a in c_arrs if day < len(a))
    td = sum(a[day] for a in d_arrs if day < len(a))
    return min(tc, td) >= cap


# 两次循环各 120/120，容量 200
c2 = [[120.0], [120.0]]
d2 = [[120.0], [120.0]]
check("两次循环各 120、容量 200 → 逐段配对判不满足", pair_based(c2, d2, 0, 200.0) is False)
check("同场景 → 总量口径会判满足（说明两种口径确实不同）", total_based(c2, d2, 0, 200.0) is True)

# 一次循环够即满足（两充两放里只要有一次能搬够）
c3 = [[300.0], [120.0]]
d3 = [[280.0], [110.0]]
check("第一次循环 300/280、第二次 120/110，容量 200 → 满足",
      pair_based(c3, d3, 0, 200.0) is True)

# 一充一放：列表长度 1，同一套判据
check("一充一放 250/240、容量 200 → 满足", pair_based([[250.0]], [[240.0]], 0, 200.0) is True)
check("一充一放 150/140、容量 200 → 不满足", pair_based([[150.0]], [[140.0]], 0, 200.0) is False)

# 单侧缺失按 inf 处理（不影响判定）
check("某侧缺段按 inf 处理", pair_based([[250.0]], [], 0, 200.0) is True)

# ── ② AST 契约：三处必须用逐段配对 ─────────────────────────
MAIN = os.path.join(REPO, "apps", "负荷曲线批量生成工具 Pro 融合版 (v2.3).py")
src = io.open(MAIN, encoding="utf-8", errors="replace").read()
tree = ast.parse(src)


def find_method(tree, cls_name, meth_name):
    for n in tree.body:
        if isinstance(n, ast.ClassDef) and n.name == cls_name:
            for m in n.body:
                if isinstance(m, ast.FunctionDef) and m.name == meth_name:
                    return m
    return None


pw = None
for n in tree.body:
    if isinstance(n, ast.ClassDef) and n.name == "ProfessionalWindow":
        pw = n
        break

check("主程序存在 ProfessionalWindow", pw is not None)

for meth in ("_check_day_satisfied", "_get_all_filtered_daily_mins", "_fill_daily_table"):
    node = find_method(tree, "ProfessionalWindow", meth)
    check(f"ProfessionalWindow.{meth} 存在", node is not None)
    if node is None:
        continue
    body = ast.unparse(node)
    has_pair = ("min(cv, dv)" in body) or ("min(_cv, _dv)" in body) or \
               ("cv >= usable_cap and dv >= usable_cap" in body)
    check(f"{meth} 使用逐段配对", has_pair)
    check(f"{meth} 不调用 daily_throughput_series", "daily_throughput_series" not in body)

# 主程序不应再 import daily_throughput_series（总量口径已回滚）
check("主程序不再 import daily_throughput_series",
      "from services.capacity import daily_throughput_series" not in src)

# 该函数本身仍保留在 services 层（其他函数在用）
cap_src = io.open(os.path.join(REPO, "services", "capacity.py"),
                  encoding="utf-8", errors="replace").read()
check("services/capacity.py 仍定义 daily_throughput_series",
      "def daily_throughput_series" in cap_src)

print()
print(f"结果：PASS {PASS}   FAIL {FAIL}")
sys.exit(1 if FAIL else 0)
