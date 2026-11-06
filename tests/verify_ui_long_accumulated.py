# -*- coding: utf-8 -*-
"""长表示数表的 UI 入口（v2.3.13 补的）。

背景
----
「示数表」原来只是宽表模式下的选项（每行一天、多列时刻）。而「采集日期 +
采集时间 + 累计电度」这种长表形态，选长表后 convert_accumulated 恒为 False，
累计读数被当成功率直接画——曲线单调上升，没有任何提示。

后端 _scan_long_chunk 一直支持（format_type='long' + convert_accumulated=True），
只是 UI 没入口。本脚本锁住这个入口不被删掉。

判据都是源码级（Qt 控件要真实窗口才能点），行为正确性由
tests/verify_e2e_forms.py 的「长表示数表·后端能力」保证。

跑法：python tests/verify_ui_long_accumulated.py
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


# ① 控件存在
check("MappingDialog 里定义了 long_acc_cb 勾选框", "self.long_acc_cb = QCheckBox(" in src)

# ② 默认隐藏，只在长表模式显示
md = None
for n in tree.body:
    if isinstance(n, ast.ClassDef) and n.name == "MappingDialog":
        md = n
        break
check("MappingDialog 类存在", md is not None)

if md is not None:
    init_src = ast.unparse(md)
    check("勾选框默认隐藏", "long_acc_cb.setVisible(False)" in init_src)

    fmt_chg = find_method(tree, "MappingDialog", "_fmt_chg")
    check("_fmt_chg 存在", fmt_chg is not None)
    if fmt_chg is not None:
        body = ast.unparse(fmt_chg)
        check("_fmt_chg 按格式控制显隐",
              "long_acc_cb.setVisible(not is_wide_or_accum)" in body, body[:200])

    ok = find_method(tree, "MappingDialog", "_ok")
    check("_ok 存在", ok is not None)
    if ok is not None:
        body = ast.unparse(ok)
        check("_ok 里长表 + 勾选 → convert_accumulated=True",
              "fmt == 'long' and self.long_acc_cb.isChecked()" in body, body[:300])

    save = find_method(tree, "MappingDialog", "_save_pre_auto_state")
    check("_save_pre_auto_state 存在", save is not None)
    if save is not None:
        check("撤销状态记录了 long_acc",
              "long_acc" in ast.unparse(save))

    undo = find_method(tree, "MappingDialog", "_undo_auto")
    check("_undo_auto 存在", undo is not None)
    if undo is not None:
        body = ast.unparse(undo)
        check("撤销时恢复 long_acc 勾选",
              "long_acc_cb.setChecked" in body, body[-300:])

# ③ 后端能力仍在（这条与 e2e 重复，但放在这里让本脚本自成一体）
cap_src = io.open(os.path.join(REPO, "core", "ingest.py"),
                  encoding="utf-8", errors="replace").read()
check("后端 _convert_accumulated_to_power 仍在",
      "def _convert_accumulated_to_power" in cap_src)
check("长表路径读取 convert_accumulated",
      "convert_accumulated = mapping.get('convert_accumulated', False)" in cap_src)

print()
print(f"结果：PASS {PASS}   FAIL {FAIL}")
sys.exit(1 if FAIL else 0)
