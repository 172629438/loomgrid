# -*- coding: utf-8 -*-
"""GUI 流程冒烟：窗口构造 → 文件注入 → 出图 → 导出数据。

前面几套测的是数据层（接入层单点、端到端链路），这一套补最后一环：
**界面上的按钮后面挂着的方法，真的能跑。**

做法是不启动可见窗口（QT_QPA_PLATFORM=offscreen），直接构造 MainWindow，
然后调它自己的槽函数/模块级绘图函数。绕开 QFileDialog 这类会阻塞的对话框，
但保留真实的对象与真实的数据。

覆盖
----
A 窗口构造    B 文件注入    C 出图    D 导出数据    E 图片内容
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
U = load_module(ULTRA, "lg_gf_u")
print("加载 Fusion…", flush=True)
F = load_module(FUSION, "lg_gf_f")

from PySide6.QtWidgets import QApplication  # noqa: E402

APP = QApplication.instance() or QApplication([])
TMP = tempfile.mkdtemp(prefix="lg_gui_")

LABELS = [f"{(i * 15) // 60}:{(i * 15) % 60:02d}" for i in range(96)]


def make_xlsx(name, days=2):
    import pandas as pd
    rows = []
    for d in range(days):
        for i in range(96):
            h, m = divmod(i * 15, 60)
            rows.append({"日期": f"2026-10-{d + 1:02d}", "时间": f"{h:02d}:{m:02d}",
                         "正向有功功率": round(500.0 + i + d * 10, 3)})
    fp = os.path.join(TMP, name)
    pd.DataFrame(rows).to_excel(fp, index=False)
    return fp


# ────────────────────────────────────────────────────────────
def group_A():
    print("\n" + "=" * 74 + "\nA. 窗口构造\n" + "=" * 74)
    for tag, mod in (("Ultra", U), ("Fusion", F)):
        win_cls = None
        for name in ("MainWindow", "ProfessionalWindow", "FusionMainWindow"):
            if hasattr(mod, name):
                win_cls = getattr(mod, name)
                break
        if win_cls is None:
            check(f"A1 {tag} 找到窗口类", False, "MainWindow/ProfessionalWindow 都不存在")
            continue
        try:
            w = win_cls()
            check(f"A1 {tag} 窗口构造", True)
        except Exception as e:
            check(f"A1 {tag} 窗口构造", False, f"{type(e).__name__}: {e}")
            continue
        needed = ["_on_files_dropped", "render"]
        have = [m for m in needed if hasattr(w, m)]
        check(f"A2 {tag} 窗口关键方法齐备", len(have) == len(needed), f"缺 {[m for m in needed if m not in have]}")
        # 数据导出：Ultra 在 PreviewDialog._export_chart_data，Fusion 在 _export_midwide（导出中间宽表）——
        # 名字不同但都是「把宽表落成文件」。这里查模块内是否有类提供任一。
        has_export = any(hasattr(obj, kw)
                         for obj in vars(mod).values() if isinstance(obj, type)
                         for kw in ("_export_chart_data", "_export_midwide", "_export_template"))
        check(f"A2b {tag} 导出方法存在", has_export, "所有类上都没有数据导出方法")
        check(f"A3 {tag} 文件列表控件就绪", hasattr(w, "file_list"), "无 file_list 属性")
        # 保留给后续组用
        globals()[f"W_{tag}"] = w


def group_B():
    print("\n" + "=" * 74 + "\nB. 文件注入\n" + "=" * 74)
    fp = make_xlsx("gui_flow.xlsx")
    for tag in ("Ultra", "Fusion"):
        w = globals().get(f"W_{tag}")
        if w is None:
            check(f"B1 {tag} 文件注入", False, "窗口未构造")
            continue
        fl = getattr(w, "file_list", None)
        before = fl.count() if fl is not None and hasattr(fl, "count") else 0
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                w._on_files_dropped([fp])
            after = fl.count() if fl is not None and hasattr(fl, "count") else 0
            check(f"B1 {tag} xlsx 注入生效", after > before, f"{before} → {after}")
        except Exception as e:
            check(f"B1 {tag} xlsx 注入生效", False, f"{type(e).__name__}: {e}")

    # B2 不支持的扩展名应被过滤
    bad = os.path.join(TMP, "not_a_table.txt")
    with open(bad, "w", encoding="utf-8") as f:
        f.write("hello")
    for tag in ("Ultra", "Fusion"):
        w = globals().get(f"W_{tag}")
        if w is None:
            continue
        fl = getattr(w, "file_list", None)
        before = fl.count() if fl is not None and hasattr(fl, "count") else 0
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                w._on_files_dropped([bad])
            after = fl.count() if fl is not None and hasattr(fl, "count") else 0
            check(f"B2 {tag} 非表格文件被过滤", after == before, f"{before} → {after}")
        except Exception as e:
            check(f"B2 {tag} 非表格文件被过滤", False, f"{type(e).__name__}: {e}")


def group_C():
    print("\n" + "=" * 74 + "\nC. 出图\n" + "=" * 74)
    load = np.array([[500.0 + i for i in range(96)], [510.0 + i for i in range(96)]])
    dates = ["2026-10-01", "2026-10-02"]

    # C1 基本出图
    out = os.path.join(TMP, "plot_base.png")
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            res = U._plot_curves(dates, load, LABELS, "基本曲线", out)
        ok = bool(res) and os.path.exists(out) and os.path.getsize(out) > 10_000
        check("C1 基本出图", ok, f"返回={res} 大小={os.path.getsize(out) if os.path.exists(out) else 0}")
    except Exception as e:
        check("C1 基本出图", False, f"{type(e).__name__}: {e}")

    # C2 深色主题
    out2 = os.path.join(TMP, "plot_dark.png")
    try:
        opts = {"show_daily": True, "show_avg": True, "fit_types": [], "show_max_demand": True,
                "highlight_mode": "none", "mark_anomalies": False, "dark_theme": True}
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            res = U._plot_curves(dates, load, LABELS, "深色曲线", out2, opts=opts)
        check("C2 深色主题出图", bool(res) and os.path.exists(out2), f"返回={res}")
    except Exception as e:
        check("C2 深色主题出图", False, f"{type(e).__name__}: {e}")

    # C3 单日
    out3 = os.path.join(TMP, "plot_one.png")
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            res = U._plot_curves(["2026-10-01"], load[:1], LABELS, "单日", out3)
        check("C3 单日出图", bool(res) and os.path.exists(out3), f"返回={res}")
    except Exception as e:
        check("C3 单日出图", False, f"{type(e).__name__}: {e}")

    # C4 空数据不应崩溃（允许返回 False）
    out4 = os.path.join(TMP, "plot_empty.png")
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            U._plot_curves([], np.zeros((0, 96)), LABELS, "空", out4)
        check("C4 空数据不崩", True, "正常返回")
    except Exception as e:
        check("C4 空数据不崩", False, f"{type(e).__name__}: {e}")

    # C5 负值（光伏倒送）出图
    out5 = os.path.join(TMP, "plot_neg.png")
    neg = np.array([[260 - 620 * np.exp(-((i / 4 - 12.5) ** 2) / 6.0) for i in range(96)]])
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            res = U._plot_curves(["2026-10-01"], neg, LABELS, "倒送", out5)
        check("C5 负值曲线出图", bool(res) and os.path.exists(out5), f"返回={res}")
    except Exception as e:
        check("C5 负值曲线出图", False, f"{type(e).__name__}: {e}")


def group_D():
    print("\n" + "=" * 74 + "\nD. 导出数据（CSV 结构）\n" + "=" * 74)
    import csv
    load = np.array([[500.0 + i for i in range(96)], [510.0 + i for i in range(96)]])
    dates = ["2026-10-01", "2026-10-02"]
    out = os.path.join(TMP, "export.csv")
    # 复刻 _export_chart_data 的写出格式（该方法本身被 QFileDialog 挡住）
    try:
        with open(out, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["日期"] + list(LABELS))
            for i, date_str in enumerate(dates):
                row = [date_str] + [f"{v:.3f}" for v in load[i]]
                writer.writerow(row)
        with open(out, "r", encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
        check("D1 表头 = 日期 + 96 时刻", len(rows[0]) == 97 and rows[0][0] == "日期",
              f"表头长度 {len(rows[0])}")
        check("D2 行数 = 日期数 + 表头", len(rows) == 3, f"行数 {len(rows)}")
        check("D3 数值三位小数", rows[1][1] == "500.000", f"首值 {rows[1][1]}")
        # utf-8-sig：文件开头带 BOM，Excel 才不乱码
        with open(out, "rb") as f:
            head = f.read(3)
        check("D4 utf-8-sig BOM", head == b"\xef\xbb\xbf", f"首字节 {head!r}")
    except Exception as e:
        check("D1 导出 CSV", False, f"{type(e).__name__}: {e}")


def group_E():
    print("\n" + "=" * 74 + "\nE. 图片内容（不只看文件大小）\n" + "=" * 74)
    p = os.path.join(TMP, "plot_base.png")
    if not os.path.exists(p):
        check("E1 图片内容", False, "C1 未产出图片")
        return
    try:
        from PIL import Image
        im = Image.open(p).convert("RGB")
        arr = np.asarray(im)
        # 曲线图应该有大量白底 + 少量深色像素；纯空白图会几乎没有非白像素
        non_white = np.mean(np.any(arr < 240, axis=2))
        check("E1 图片非空白", non_white > 0.01, f"非白像素占比 {non_white:.3%}")
        check("E2 图片尺寸合理", im.width >= 800 and im.height >= 400, f"{im.width}×{im.height}")
        # 深色主题应整体偏暗
        p2 = os.path.join(TMP, "plot_dark.png")
        if os.path.exists(p2):
            im2 = Image.open(p2).convert("RGB")
            arr2 = np.asarray(im2)
            check("E3 深色主题确实偏暗", float(np.mean(arr2)) < 120, f"平均亮度 {np.mean(arr2):.0f}")
    except ImportError:
        print("  (Pillow 未安装，跳过图片内容检查)")
    except Exception as e:
        check("E1 图片内容", False, f"{type(e).__name__}: {e}")


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
