# -*- coding: utf-8 -*-
"""逐日吞吐量口径：三处消费点必须共用同一个实现。

背景
----
`compute_segmented_spaces` 返回的四个平行列表里，每个段在非本方向那一侧是
**占位零**（充电段在放电侧为 0），目的是让列表等长。

主程序原先三处各自实现「逐段配对 min」：

  `_get_all_filtered_daily_mins`  满载分支
  `_check_day_satisfied`          「满足天数」判据
  `_fill_daily_table`             日明细表「日最小值」列

同一索引的充段与放段是**不同时段**，`min(cv, dv)` 必然被占位零压成 0。
后果：
  - 日表该列恒为 0.000
  - 容量 > 0 时「满足天数」恒为 0（用户会误判容量太小而往大调，方向反了）
  - 容量 = 0 时 `0 >= 0` 又全真（显示全部满足）

正确口径在 `services/capacity.daily_throughput_series`：
**当日总充与当日总放取瓶颈侧**，与 `services/capacity.py` 的注释一致。

本脚本做两件事：
  ① 纯函数对拍（占位零 / 单侧为零 / 多段求和）
  ② AST 契约：主程序三处必须都调用 daily_throughput_series，且不再有 n_pairs 配对 min

跑法：python tests/verify_daily_throughput.py
"""
import ast
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
sys.stdout.reconfigure(encoding="utf-8")

from services.capacity import daily_throughput_series  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}  {detail}")


# ── ① 纯函数对拍 ────────────────────────────────────────────
# 充电段 300/天，放电段 280/天（各带一个占位零段）
c = [[300.0, 300.0], [0.0, 0.0]]
d = [[0.0, 0.0], [280.0, 280.0]]
tp = daily_throughput_series(c, d)
check("占位零场景：min(总充 300, 总放 280) = 280",
      list(tp) == [280.0, 280.0], list(tp))

# 旧口径会算成 0：min(300, 0) 与 min(0, 280) 都非正
check("旧口径（逐段配对 min 取 max）= 0，与正确值不同",
      max(min(300.0, 0.0), min(0.0, 280.0)) == 0.0)

# 多段求和：2 个充电段各 150，1 个放电段 280 → 总充 300，取 280
c2 = [[150.0], [150.0]]
d2 = [[280.0]]
check("多段求和：min(150+150, 280) = 280",
      list(daily_throughput_series(c2, d2)) == [280.0], list(daily_throughput_series(c2, d2)))

# 单侧为零
check("放电全零 → 吞吐 0",
      list(daily_throughput_series([[100.0]], [[0.0]])) == [0.0])
check("充电全零 → 吞吐 0",
      list(daily_throughput_series([[0.0]], [[100.0]])) == [0.0])

# 空输入
check("空输入不崩", list(daily_throughput_series([], [])) == [])

# 「满足天数」语义：cap=200, dod=95 → usable=190，吞吐 280 >= 190 应为满足
check("cap=200 dod=95 时 280 >= 190（满足）", 280.0 >= 200 * 95 / 100.0)
# 若沿用旧口径日需求为 0，则 0 >= 190 不成立 → 判不满足（错误）
check("旧口径日需求 0 会判为不满足（复现缺陷）", not (0.0 >= 200 * 95 / 100.0))

# ── ② AST 契约 ──────────────────────────────────────────────
MAIN = os.path.join(REPO, "负荷曲线批量生成工具 Pro 融合版 (v2.3).py")
tree = ast.parse(io.open(MAIN, encoding="utf-8", errors="replace").read())


def find_method(tree, cls_name, meth_name):
    for n in tree.body:
        if isinstance(n, ast.ClassDef) and n.name == cls_name:
            for m in n.body:
                if isinstance(m, ast.FunctionDef) and m.name == meth_name:
                    return m
    return None


for meth in ("_check_day_satisfied", "_get_all_filtered_daily_mins", "_fill_daily_table"):
    node = find_method(tree, "ProfessionalWindow", meth)
    check(f"ProfessionalWindow.{meth} 存在", node is not None)
    if node is None:
        continue
    src = ast.unparse(node)
    check(f"{meth} 调用 daily_throughput_series", "daily_throughput_series" in src)
    check(f"{meth} 不再有 n_pairs 配对 min", "n_pairs" not in src, "仍存在 n_pairs")

check("主程序已 import daily_throughput_series",
      "from services.capacity import daily_throughput_series" in
      io.open(MAIN, encoding="utf-8", errors="replace").read())

print()
print(f"结果：PASS {PASS}   FAIL {FAIL}")
sys.exit(1 if FAIL else 0)
