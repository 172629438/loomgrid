# -*- coding: utf-8 -*-
"""界面行为四条（P2-7 / P2-12 / P2-16 / P2-17 / P2-18）。

这四条都在主程序 UI 层，直接 import 会拉起 PySide6，进不了 --core。
这里按**源码文本**做回归断言：不 import 主程序，只读文件切片。
断言得比较实（函数体切片 + 唯一文案），不会因为无关改动误报。

- P2-7  `_update_cap_label`：容量线 0 显示「未设置」，不再显示 0.00
- P2-12 同一函数：不再自动勾选 need_cap_cb
- P2-16 `_fill_daily_table`：当天全零显示「—」
- P2-17 导出确认表：不再写「⚠ 将覆盖」，改「同名，将自动加序号」（3 处）
- P2-18 `_import_config`：替换配置时持 Config 的锁，并提示需重启生效

跑法：python tests/verify_ui_labels.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

MAIN = os.path.join(ROOT, "负荷曲线批量生成工具 Pro 融合版 (v2.3).py")

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}  {detail}")


with open(MAIN, encoding="utf-8") as f:
    SRC = f.read()


def func_src(name, occurrence=0):
    """截出第 occurrence 个 `    def name(` 到下一个同级 def 之间的源码。"""
    needle = f"    def {name}("
    start = -1
    for _ in range(occurrence + 1):
        start = SRC.find(needle, start + 1)
        if start < 0:
            return ""
    end = SRC.find("\n    def ", start + len(needle))
    if end < 0:
        end = len(SRC)
    return SRC[start:end]


cap = func_src("_update_cap_label")
check("_update_cap_label 存在", bool(cap))
check("P2-7 容量线为 0 时显示「未设置」", "容量线:未设置" in cap)
check("P2-7 有值时仍显示数值、无值时显示未设置",
      'f"容量线:{cap_val:.2f}"' in cap and "容量线:未设置" in cap)
check("P2-12 不再自动勾选 need_cap_cb", "need_cap_cb.setChecked(True)" not in cap)

daily = func_src("_fill_daily_table")
check("_fill_daily_table 存在", bool(daily))
check("P2-16 全零天显示「—」", "_all_zero" in daily and '"—"' in daily)

space = func_src("_populate_space_table")
check("月表仍按数值输出（未被误改）", 'f"{min_val:.2f}"' in space)

n_overwrite = SRC.count("⚠ 将覆盖")
check("P2-17 全文不再出现「⚠ 将覆盖」", n_overwrite == 0, f"出现 {n_overwrite} 次")
n_new = SRC.count("同名，将自动加序号")
check("P2-17 三处提示都改成「同名，将自动加序号」", n_new == 3, f"出现 {n_new} 次")

imp = func_src("_import_config")
check("_import_config 存在", bool(imp))
check("P2-18 导入时持 Config 的锁", "with self.config_manager._lock:" in imp)
check("P2-18 提示需重启生效", "重启程序后全部生效" in imp)

print()
print(f"结果：PASS {PASS}   FAIL {FAIL}")
sys.exit(1 if FAIL else 0)
