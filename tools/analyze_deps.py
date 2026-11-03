# -*- coding: utf-8 -*-
"""依赖闭包分析器：算出「把一组函数搬出单文件」真正要带走多少东西。

为什么需要它
------------
阶段 1 的估工不能按「目标函数有多少行」算——真实成本是**依赖闭包**：
你搬 `_parse_min`，它就带出 `TIME_CACHE` → `LRUCache` → `TIME_CACHE_MAXSIZE`，
再带出 `_normalize_time_column_name`、两个正则、一个全局标志。

按函数行数估会系统性低估；这个工具把闭包算出来，用事实定工期。

判定口径
--------
  readable   闭包内不引用任何 Qt 名字 → 可以直接进 core/
  needs_qt   闭包里有 Qt 引用 → 需要先解耦，或留在 UI 层
  external   闭包里用到的第三方库（numpy/pandas/matplotlib/loguru/...）

用法
----
    python tools/analyze_deps.py _parse_min _fmt_time
    python tools/analyze_deps.py --group timegrid
    python tools/analyze_deps.py --group timegrid --detail

估工的正确用法（别再用入口行数）
--------------------------------
实测依赖扩散倍数为 1.1～1.2：
    timegrid  入口 261 行 → 闭包 302 行
    spaces    入口 150 行 → 闭包 277 行
估一块的工时，先用本工具量出**闭包规模**，再乘以实测速率；
直接拿入口函数的行数估会系统性低估，因为被带出的基础设施（缓存类、正则、
常量、辅助函数）看不见。
"""
from __future__ import annotations

import argparse
import ast
import re
import sys

HERE = __import__("os").path.dirname(__import__("os").path.abspath(__file__))
ROOT = __import__("os").path.dirname(HERE)
FUSION = __import__("os").path.join(ROOT, "负荷曲线批量生成工具 Pro 融合版 (v2.3).py")

QT_RE = re.compile(r"^(Q[A-Z][A-Za-z0-9_]*|pyqtSignal|pyqtSlot|Qt)$")
STDLIB = {
    "sys", "os", "re", "json", "math", "time", "io", "logging", "atexit", "warnings",
    "subprocess", "traceback", "gc", "csv", "shutil", "tempfile", "threading",
    "functools", "datetime", "collections", "dataclasses", "typing", "enum", "hashlib",
    "importlib", "pathlib", "itertools", "contextlib", "zipfile", "struct", "base64",
    "random", "statistics", "copy", "textwrap", "unicodedata", "string", "glob",
    "multiprocessing", "concurrent", "abc", "inspect", "operator", "bisect",
}
THIRD_PARTY = {
    "numpy": "numpy", "np": "numpy", "pandas": "pandas", "pd": "pandas",
    "matplotlib": "matplotlib", "plt": "matplotlib", "loguru": "loguru",
    "chardet": "chardet", "openpyxl": "openpyxl", "scipy": "scipy",
    "chinese_calendar": "chinese_calendar", "psutil": "psutil",
    "PySide6": "PySide6", "xlrd": "xlrd", "calamine": "calamine",
}

GROUPS = {
    # 阶段 1 各块的入口符号（按方案命名，供 --group 使用）
    "models": ["PeriodType", "CurveLabel", "ColorConst", "PlotOptions", "GroupSummary"],
    "timegrid": ["parse_flexible_time", "_parse_min", "_fmt_time", "_fmt_hour",
                 "_fmt_date", "_get_day_type", "_expand_overnight", "_valid_periods",
                 "_hour_x", "_parse_date_flexible", "_normalize_time_column_name"],
    "spaces": ["compute_segmented_spaces", "calc_capacity_for_days", "calc_days_for_capacity"],
    "periods": ["_segment_periods", "_valid_periods"],
    "plotting": ["_draw_curves_core", "_plot_curves", "_plot_single_worker",
                 "_find_cjk_font", "_get_cached_font", "_setup_font", "_compute_fits",
                 "_clean_plot", "_clean_series", "_downsample"],
    "ingest": ["_read_file_full", "_scan_file_worker", "_scan_wide_chunk", "_scan_long_chunk",
               "_smart_classify_columns", "_detect_wide_matrix", "_detect_transposed_wide",
               "_wide_row_accumulated_to_power", "_detect_accumulated_pattern",
               "_convert_accumulated_to_power", "_parse_long_to_standard",
               "_detect_csv_delimiter", "_detect_enc", "_detect_file_format_type",
               "_parse_file_meta_worker", "_extract_asset_from_name", "_mapping_is_long_shape",
               "_normalize_mapping", "_safe_read_excel", "_safe_excel_sheets",
               "get_columns_fast", "_valid_time_cols"],
}


class Program:
    def __init__(self, path: str):
        self.src = open(path, encoding="utf-8").read()
        self.tree = ast.parse(self.src)
        self.nodes = {}          # name -> ast node（函数 / 类 / 赋值）
        self.imports = {}        # 本地名 -> 模块名
        for n in self.tree.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                self.nodes[n.name] = n
            elif isinstance(n, ast.Assign):
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        self.nodes[t.id] = n
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                for a in n.names:
                    local = a.asname or a.name.split(".")[0]
                    mod = (n.module or "").split(".")[0] if isinstance(n, ast.ImportFrom) else a.name.split(".")[0]
                    self.imports[local] = mod

    def line_count(self, name: str) -> int:
        n = self.nodes.get(name)
        if n is None:
            return 0
        return getattr(n, "end_lineno", n.lineno) - n.lineno + 1

    def free_names(self, node) -> list[str]:
        """节点里引用但未在本地绑定的名字。"""
        assigned = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.arg):
                assigned.add(sub.arg)
            elif isinstance(sub, ast.Name) and isinstance(sub.ctx, (ast.Store, ast.Del)):
                assigned.add(sub.id)
            elif isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and sub is not node:
                assigned.add(sub.name)
            elif isinstance(sub, ast.ExceptHandler) and sub.name:
                assigned.add(sub.name)
            elif isinstance(sub, ast.Global):
                assigned.update(sub.names)
        used = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                used.add(sub.id)
        return sorted(used - assigned)

    def closure(self, roots: list[str]) -> tuple[set[str], set[str]]:
        """递归求依赖闭包。返回（单文件内符号, 外部名字）。"""
        seen: set[str] = set()
        external: set[str] = set()
        stack = [r for r in roots if r in self.nodes]
        missing = [r for r in roots if r not in self.nodes]
        for m in missing:
            external.add(f"<缺失:{m}>")
        while stack:
            name = stack.pop()
            if name in seen:
                continue
            seen.add(name)
            for free in self.free_names(self.nodes[name]):
                if free in self.nodes:
                    if free not in seen:
                        stack.append(free)
                elif free in self.imports:
                    external.add(free)
                else:
                    # 可能是内建名或真正的未定义
                    if free in dir(__builtins__):
                        continue
                    external.add(free)
        return seen, external

    def classify_external(self, names: set[str]) -> dict[str, list[str]]:
        buckets: dict[str, list[str]] = {"stdlib": [], "third_party": [], "qt": [], "unknown": [], "missing": []}
        for n in sorted(names):
            if n.startswith("<缺失:"):
                buckets["missing"].append(n[4:-1])
            elif QT_RE.match(n):
                buckets["qt"].append(n)
            elif n in self.imports:
                mod = self.imports[n]
                if QT_RE.match(mod) or mod.startswith("PySide6"):
                    buckets["qt"].append(n)
                elif mod in THIRD_PARTY:
                    buckets["third_party"].append(f"{n}({THIRD_PARTY[mod]})")
                elif mod in STDLIB:
                    buckets["stdlib"].append(n)
                else:
                    buckets["unknown"].append(f"{n}({mod})")
            elif n in THIRD_PARTY:
                buckets["third_party"].append(n)
            elif n in STDLIB:
                buckets["stdlib"].append(n)
            else:
                buckets["unknown"].append(n)
        return buckets


def report(prog: Program, roots: list[str], detail: bool) -> int:
    names, external = prog.closure(roots)
    owned = {n: prog.line_count(n) for n in names}
    total = sum(owned.values())
    buckets = prog.classify_external(external)

    funcs = [n for n in names if isinstance(prog.nodes[n], (ast.FunctionDef, ast.AsyncFunctionDef))]
    classes = [n for n in names if isinstance(prog.nodes[n], ast.ClassDef)]
    consts = [n for n in names if n not in funcs and n not in classes]

    print(f"入口符号 {len(roots)} 个 → 依赖闭包 {len(names)} 个符号 / {total} 行")
    print(f"  函数 {len(funcs)} · 类 {len(classes)} · 常量与模块级 {len(consts)}")

    if detail:
        print("\n闭包明细（行数降序）：")
        for n, c in sorted(owned.items(), key=lambda x: -x[1]):
            kind = "class" if n in classes else ("const" if n in consts else "func")
            print(f"  {c:5d}  {kind:6s} {n}")

    print("\n外部依赖分类：")
    for key, label in (("qt", "Qt（阻断条件）"), ("third_party", "第三方库"),
                       ("stdlib", "标准库"), ("unknown", "未识别"), ("missing", "单文件内找不到")):
        items = buckets[key]
        if items:
            print(f"  {label}: {', '.join(items)}")

    verdict = "可搬（零 Qt）" if not buckets["qt"] else "需先解耦（闭包含 Qt）"
    print(f"\n判定：{verdict}")
    if buckets["missing"]:
        print(f"注意：以下名字在单文件顶层找不到，可能是局部变量或拼错 → {buckets['missing']}")
    return 0 if not buckets["qt"] else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="计算搬移一组函数的依赖闭包")
    ap.add_argument("symbols", nargs="*", help="要搬的函数/类名")
    ap.add_argument("--group", help=f"预置分组：{', '.join(GROUPS)}")
    ap.add_argument("--detail", action="store_true", help="列出闭包明细")
    ap.add_argument("--file", default=FUSION, help="分析的对象文件")
    a = ap.parse_args()

    roots = list(a.symbols)
    if a.group:
        if a.group not in GROUPS:
            print(f"未知分组 {a.group}，可选：{', '.join(GROUPS)}")
            return 2
        roots = GROUPS[a.group] + roots
    if not roots:
        print(__doc__)
        return 2

    return report(Program(a.file), roots, a.detail)


if __name__ == "__main__":
    sys.exit(main())
