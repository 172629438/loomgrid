# -*- coding: utf-8 -*-
"""
v2.3.10 月份解析契约

背景（两处真实缺陷，同一根因）：
  core.ingest._extract_ym 无法识别日期时返回哨兵字符串「未知月份」。
  两处消费方都没有处理这个哨兵：
    ① PreviewDialog._periods_for_idx   int() 失败后 except 兜成 1 月
       → 夏季数据静默套用冬季分时电价，图正常、测算错。
    ② ProfessionalWindow._recalc_spaces  int(month_num) 裸调用
       → ValueError 抽穿 __init__，专业分析窗口根本打不开。

本脚本不 import 主程序（无 PySide6 依赖，可进 CI）：
用 AST 从仓库真源里取出 _parse_month_num 编译执行，再对两处消费点做契约断言。
"""
import ast
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MAIN = os.path.join(REPO, "负荷曲线批量生成工具 Pro 融合版 (v2.3).py")

_p = 0
_f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"[PASS] {name}")
    else:
        _f += 1
        print(f"[FAIL] {name}" + (f"  {detail}" if detail else ""))


src = io.open(MAIN, encoding="utf-8", errors="replace").read()
tree = ast.parse(src)

# ── 从真源取出 _parse_month_num 并编译执行 ──────────────────────
fn_node = None
for n in tree.body:
    if isinstance(n, ast.FunctionDef) and n.name == "_parse_month_num":
        fn_node = n
        break

check("主程序存在模块级 _parse_month_num", fn_node is not None)

if fn_node is not None:
    mod = ast.Module(body=[fn_node], type_ignores=[])
    ns = {}
    exec(compile(ast.fix_missing_locations(mod), "<extract>", "exec"), ns)
    pm = ns["_parse_month_num"]

    check("'2026-07' -> 7", pm("2026-07") == 7, repr(pm("2026-07")))
    check("'2026-7' -> 7（不补零）", pm("2026-7") == 7, repr(pm("2026-7")))
    check("'2026-07-15' -> 7（带日）", pm("2026-07-15") == 7, repr(pm("2026-07-15")))
    check("纯数字 '7' -> 7", pm("7") == 7, repr(pm("7")))
    check("int 输入 12 -> 12", pm(12) == 12, repr(pm(12)))

    check("哨兵「未知月份」-> None（不猜 1 月）", pm("未知月份") is None, repr(pm("未知月份")))
    check("None -> None", pm(None) is None, repr(pm(None)))
    check("空串 -> None", pm("") is None, repr(pm("")))
    check("乱码 'abc' -> None", pm("abc") is None, repr(pm("abc")))
    check("越界 '13' -> None", pm("13") is None, repr(pm("13")))
    check("越界 '0' -> None", pm("0") is None, repr(pm("0")))
    check("4 位年份 '2026' -> None（不得当年份当月）", pm("2026") is None, repr(pm("2026")))
    check("'2026' 单独出现不误判成 2026 月", pm("2026") != 2026)

    # 行为对照：旧兜底会把哨兵变成 1 月
    check("旧行为（哨兵->1月）与新行为不同", pm("未知月份") != 1)

# ── 消费点契约 ────────────────────────────────────────────────
def find_method(tree, cls_name, meth_name):
    for n in tree.body:
        if isinstance(n, ast.ClassDef) and n.name == cls_name:
            for m in n.body:
                if isinstance(m, ast.FunctionDef) and m.name == meth_name:
                    return m
    return None


# ① PreviewDialog._periods_for_idx 不得再兜成 1 月
pf = find_method(tree, "PreviewDialog", "_periods_for_idx")
check("PreviewDialog._periods_for_idx 存在", pf is not None)
if pf is not None:
    body_dump = ast.dump(pf)
    check(
        "旧兜底 month_int = 1 已移除",
        "month_int = 1" not in ast.unparse(pf),
        "代码里仍出现 month_int = 1",
    )
    check(
        "_periods_for_idx 调用 _parse_month_num",
        "_parse_month_num" in body_dump,
    )
    check(
        "_periods_for_idx 对 None 有显式分支",
        "if month_int is None" in ast.unparse(pf),
    )

# ② ProfessionalWindow._recalc_spaces 不得再对 month_num 裸调 int()
rc = find_method(tree, "ProfessionalWindow", "_recalc_spaces")
check("ProfessionalWindow._recalc_spaces 存在", rc is not None)
if rc is not None:
    bad = []
    for node in ast.walk(rc):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "int":
            for a in node.args:
                if isinstance(a, ast.Name) and a.id == "month_num":
                    bad.append(node.lineno)
    check("不再对 month_num 裸调 int()", not bad, f"命中行号 {bad}")
    check(
        "_recalc_spaces 里有跳过分支",
        "if month_num is None" in ast.unparse(rc),
    )

print()
print(f"结果：PASS {_p}   FAIL {_f}")
sys.exit(1 if _f else 0)
