# -*- coding: utf-8 -*-
"""GUI 信号连接与批量导入：按钮点下去真的会发生什么。

与 verify_gui_flow 的分工
------------------------
verify_gui_flow 直接调槽函数（`w._on_files_dropped(...)`），验的是「槽函数本身对不对」；
这一套用 QTest 真点按钮，验的是「按钮 → 信号 → 槽」这一跳有没有连上，
以及状态联动（没文件时开始按钮该灰着）。

覆盖
----
A 按钮状态联动   B 真点击   C 多工作表   D 多文件   E 快捷键与对话框
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import shutil
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pandas as pd

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
U = load_module(ULTRA, "lg_gs_u")
print("加载 Fusion…", flush=True)
F = load_module(FUSION, "lg_gs_f")

from PySide6.QtWidgets import QApplication, QPushButton, QMessageBox  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402

APP = QApplication.instance() or QApplication([])
TMP = tempfile.mkdtemp(prefix="lg_gs_")

# 对话框一律自动应答，避免 offscreen 下卡死
_orig_q = QMessageBox.question
_orig_i = QMessageBox.information
_orig_w = QMessageBox.warning
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)


def find_button(win, keyword):
    """按文字关键词找按钮（代码里多数按钮没设 objectName）。"""
    for b in win.findChildren(QPushButton):
        if keyword in b.text():
            return b
    return None


def make_xlsx(name, month=10, days=2, base=500.0):
    rows = []
    for d in range(days):
        for i in range(96):
            h, m = divmod(i * 15, 60)
            rows.append({"日期": f"2026-{month:02d}-{d + 1:02d}", "时间": f"{h:02d}:{m:02d}",
                         "正向有功功率": round(base + i, 3)})
    fp = os.path.join(TMP, name)
    pd.DataFrame(rows).to_excel(fp, index=False)
    return fp


def make_multi_sheet(name, months=(10, 11)):
    fp = os.path.join(TMP, name)
    with pd.ExcelWriter(fp) as xw:
        for mth in months:
            rows = []
            for d in range(2):
                for i in range(96):
                    h, mm = divmod(i * 15, 60)
                    rows.append({"日期": f"2026-{mth:02d}-{d + 1:02d}",
                                 "时间": f"{h:02d}:{mm:02d}",
                                 "正向有功功率": round(400.0 + mth * 10 + i, 3)})
            pd.DataFrame(rows).to_excel(xw, sheet_name=f"{mth}月", index=False)
    return fp


def scan_mapping(fp, sheet):
    """走一次探测+分类+扫描，返回 summaries。"""
    import threading
    fmt = U._detect_file_format_type(fp, sheet)
    df = U._read_file_full(fp, sheet if sheet != "CSV文件" else None)
    mapping = U._smart_classify_columns(list(df.columns), df)
    mapping["format_type"] = fmt if fmt in ("wide", "wide_transposed", "accumulated") else mapping.get("format_type", "long")
    mapping.setdefault("header_row", 0)
    args = (fp, sheet, mapping, None, threading.Event(), None, False)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        try:
            return U._scan_file_worker(args)
        except ValueError:
            return U._scan_file_worker(args[:6])


# ────────────────────────────────────────────────────────────
def group_A():
    print("\n" + "=" * 74 + "\nA. 按钮状态联动\n" + "=" * 74)
    for tag, mod in (("Ultra", U), ("Fusion", F)):
        try:
            w = mod.MainWindow()
        except Exception as e:
            check(f"A1 {tag} 窗口构造", False, f"{type(e).__name__}: {e}")
            continue
        sb = getattr(w, "start_btn", None)
        check(f"A1 {tag} 存在开始按钮", sb is not None, "无 start_btn")
        if sb is None:
            continue
        # A2 空列表时开始按钮应为 disabled（没有数据不该能开始）
        check(f"A2 {tag} 空列表时开始按钮禁用", not sb.isEnabled(), f"enabled={sb.isEnabled()}")
        # A3 注入文件后应变 enabled
        fp = make_xlsx(f"A_{tag}.xlsx")
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                w._on_files_dropped([fp])
            check(f"A3 {tag} 有文件后开始按钮可用", sb.isEnabled(), f"enabled={sb.isEnabled()}")
        except Exception as e:
            check(f"A3 {tag} 有文件后开始按钮可用", False, f"{type(e).__name__}: {e}")
        globals()[f"W_{tag}"] = w
        globals()[f"FP_{tag}"] = fp


def group_B():
    print("\n" + "=" * 74 + "\nB. 真点击\n" + "=" * 74)
    for tag in ("Ultra", "Fusion"):
        w = globals().get(f"W_{tag}")
        if w is None:
            check(f"B {tag}", False, "窗口未构造")
            continue
        fl = getattr(w, "file_list", None)
        before = fl.count() if fl is not None and hasattr(fl, "count") else 0
        btn = find_button(w, "清空列表")
        if btn is None:
            check(f"B1 {tag} 找到清空按钮", False, "按钮文字不匹配")
            continue
        try:
            QTest.mouseClick(btn, Qt.LeftButton)
            after = fl.count() if fl is not None and hasattr(fl, "count") else 0
            check(f"B1 {tag} 点击清空列表生效", after < before or before == 0, f"{before} → {after}")
        except Exception as e:
            check(f"B1 {tag} 点击清空列表生效", False, f"{type(e).__name__}: {e}")

        # B2 主题切换按钮：点击后不应崩溃
        tb = find_button(w, "主题")
        if tb is not None:
            try:
                QTest.mouseClick(tb, Qt.LeftButton)
                check(f"B2 {tag} 点击主题切换不崩", True)
            except Exception as e:
                check(f"B2 {tag} 点击主题切换不崩", False, f"{type(e).__name__}: {e}")
        else:
            # Fusion 没有全局主题按钮（只有图表层的 dark_theme 选项），属功能差异非缺陷
            check(f"B2 {tag} 主题切换（无此按钮则不计缺陷）", True, "无主题按钮")


def group_C():
    print("\n" + "=" * 74 + "\nC. 多工作表\n" + "=" * 74)
    fp = make_multi_sheet("multi.xlsx", months=(10, 11))
    sheets = U._safe_excel_sheets(fp) if hasattr(U, "_safe_excel_sheets") else []
    check("C1 识别出两个工作表", len(sheets) == 2, f"{sheets}")
    all_groups = []
    for sh in sheets:
        res = scan_mapping(fp, sh)
        if isinstance(res, dict):
            check(f"C2 扫描 {sh}", False, str(res)[:120])
            continue
        all_groups.extend(res)
    months = sorted({g["month_str"] for g in all_groups})
    check("C2 两个月各成一组", months == ["2026-10", "2026-11"], f"{months}")
    for g in all_groups:
        expect_max = 400.0 + int(g["month_str"].split("-")[1]) * 10 + 95
        ok = abs(float(g["max_val"]) - expect_max) < 1e-6
        check(f"C3 {g['month_str']}/{g['sheet_name']} 峰值正确", ok,
              f"实际 {g['max_val']:.1f} 期望 {expect_max:.1f}")

    # C4 三个月工作表（更接近真实：一个季度一个文件）
    fp3 = make_multi_sheet("quarter.xlsx", months=(9, 10, 11))
    sh3 = U._safe_excel_sheets(fp3) if hasattr(U, "_safe_excel_sheets") else []
    check("C4 三个月工作表全识别", len(sh3) == 3, f"{sh3}")
    g3 = []
    for sh in sh3:
        res = scan_mapping(fp3, sh)
        if isinstance(res, list):
            g3.extend(res)
    months3 = sorted({g["month_str"] for g in g3})
    check("C5 三个月各成一组", months3 == ["2026-09", "2026-10", "2026-11"], f"{months3}")
    check("C6 每组时间点数 = 96", all(g["time_points"] == 96 for g in g3),
          ",".join(str(g["time_points"]) for g in g3))


def group_D():
    print("\n" + "=" * 74 + "\nD. 多文件\n" + "=" * 74)
    fp1 = make_xlsx("D_oct.xlsx", month=10, base=500.0)
    fp2 = make_xlsx("D_nov.xlsx", month=11, base=600.0)
    groups = []
    for fp in (fp1, fp2):
        res = scan_mapping(fp, "Sheet1")
        if isinstance(res, list):
            groups.extend(res)
    months = sorted({g["month_str"] for g in groups})
    check("D1 两文件分属两月", months == ["2026-10", "2026-11"], f"{months}")
    check("D2 每组独立（不合并）", len(groups) == 2, f"分组数 {len(groups)}")
    if len(groups) == 2:
        vals = sorted(float(g["max_val"]) for g in groups)
        check("D3 两文件数值各自独立", abs(vals[0] - 595.0) < 1e-6 and abs(vals[1] - 695.0) < 1e-6,
              f"{vals}")

    # D5 三个文件 × 不同月份
    fp3 = make_xlsx("D_dec.xlsx", month=12, base=700.0)
    g3 = []
    for fp in (fp1, fp2, fp3):
        res = scan_mapping(fp, "Sheet1")
        if isinstance(res, list):
            g3.extend(res)
    m3 = sorted({g["month_str"] for g in g3})
    check("D5 三文件分属三月", m3 == ["2026-10", "2026-11", "2026-12"], f"{m3}")
    check("D6 三组互不合并", len(g3) == 3, f"分组数 {len(g3)}")

    # D4 重复注入同一文件不应重复计
    w = globals().get("W_Ultra")
    if w is not None:
        fl = getattr(w, "file_list", None)
        cnt0 = fl.count() if fl is not None else 0
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                w._on_files_dropped([fp1])
                w._on_files_dropped([fp1])
            cnt1 = fl.count() if fl is not None else 0
            check("D4 重复注入不重复计数", cnt1 - cnt0 <= 1, f"{cnt0} → {cnt1}")
        except Exception as e:
            check("D4 重复注入不重复计数", False, f"{type(e).__name__}: {e}")


def group_E():
    print("\n" + "=" * 74 + "\nE. 快捷键与边界\n" + "=" * 74)
    w = globals().get("W_Ultra")
    if w is None:
        check("E Ultra 窗口", False, "未构造")
        return
    # E1 Ctrl+Enter 在无文件时不应崩
    try:
        QTest.keyClick(w, Qt.Key_Return, Qt.ControlModifier)
        check("E1 Ctrl+Enter 无文件时不崩", True)
    except Exception as e:
        check("E1 Ctrl+Enter 无文件时不崩", False, f"{type(e).__name__}: {e}")
    # E2 Esc（取消）无任务时不应崩
    try:
        QTest.keyClick(w, Qt.Key_Escape)
        check("E2 Esc 无任务时不崩", True)
    except Exception as e:
        check("E2 Esc 无任务时不崩", False, f"{type(e).__name__}: {e}")
    # E3 注入文件夹（递归）
    sub = os.path.join(TMP, "folder_case")
    os.makedirs(sub, exist_ok=True)
    make_xlsx("folder_case/inner.xlsx", month=12, base=700.0)
    fl = getattr(w, "file_list", None)
    before = fl.count() if fl is not None else 0
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            w._on_files_dropped([sub])
        after = fl.count() if fl is not None else 0
        check("E3 拖入文件夹递归展开", after > before, f"{before} → {after}")
    except Exception as e:
        check("E3 拖入文件夹递归展开", False, f"{type(e).__name__}: {e}")


# ────────────────────────────────────────────────────────────
GROUPS = {"A": group_A, "B": group_B, "C": group_C, "D": group_D, "E": group_E}
ONLY = next((a for a in sys.argv[1:] if a in "ABCDE" and len(a) == 1), None)

try:
    for k, fn in GROUPS.items():
        if ONLY and k != ONLY:
            continue
        try:
            fn()
        except Exception:
            import traceback
            traceback.print_exc()
            check(f"组 {k} 执行异常", False, "见 traceback")
finally:
    QMessageBox.question = _orig_q
    QMessageBox.information = _orig_i
    QMessageBox.warning = _orig_w
    shutil.rmtree(TMP, ignore_errors=True)

print()
print("=" * 74)
print(f"结果：PASS {PASS}   FAIL {FAIL}")
print("=" * 74)
if FAILS:
    print("\n失败项：")
    for n, d in FAILS:
        print(f"  - {n}  {d}")
sys.exit(1 if FAIL else 0)
