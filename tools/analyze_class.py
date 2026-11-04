# -*- coding: utf-8 -*-
"""类级依赖分析：判断一个类能不能整块搬出单文件，以及要带走什么。

为什么现有工具不够
------------------
`analyze_deps.py` 算的是**函数闭包**：看一个函数引用了哪些模块级名字。
但类不一样——方法之间通过 `self` 共享状态，还牵着基类：

    self.xxx 在 A 方法赋值、B 方法读取  → 整体搬走没问题，但拆着搬就会断
    基类是 QObject / QThread           → 这类不能进 core
    属性存着 Qt 对象（如 signals）      → 搬走就要连带处理

所以「类能不能进 core」要单独判：基类是不是 Qt、类体有没有 Qt 引用、
实例属性都是什么类型、方法引用了哪些模块级名字。

用法
----
    python tools/analyze_class.py Config
    python tools/analyze_class.py ProcessingThread --detail

判定口径
--------
  qt_base      基类里有 Qt 类 → 整类不可进 core（或需先解耦）
  qt_body      类体出现 Qt 名字（QLabel / pyqtSignal / Qt. 等）
  qt_attr      实例属性被赋成 Qt 对象（在 qt_body 里已经覆盖大部分）
"""

from __future__ import annotations

import argparse
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FUSION = os.path.join(ROOT, "apps", "负荷曲线批量生成工具 Pro 融合版 (v2.3).py")

sys.path.insert(0, HERE)
from analyze_deps import Program, QT_RE, STDLIB, THIRD_PARTY  # noqa: E402


def self_attrs(func) -> tuple[set[str], set[str]]:
    """返回（被赋值的 self 属性, 被读取的 self 属性）。"""
    writes, reads = set(), set()
    for n in ast.walk(func):
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "self":
            if isinstance(n.ctx, (ast.Store, ast.Del)):
                writes.add(n.attr)
            elif isinstance(n.ctx, ast.Load):
                reads.add(n.attr)
    return writes, reads


def main() -> int:
    ap = argparse.ArgumentParser(description="类级依赖分析")
    ap.add_argument("cls", help="类名")
    ap.add_argument("--detail", action="store_true")
    a = ap.parse_args()

    prog = Program(FUSION)
    node = prog.nodes.get(a.cls)
    if not isinstance(node, ast.ClassDef):
        print(f"{a.cls} 不是顶层类（或不存在）")
        return 2

    lines = node.end_lineno - node.lineno + 1
    bases = [ast.unparse(b) for b in node.bases]
    methods = [n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]

    print(f"类 {a.cls}  L{node.lineno}-{node.end_lineno}  {lines} 行 / {len(methods)} 个方法")
    print(f"基类：{', '.join(bases) if bases else '（无）'}")

    qt_bases = [b for b in bases if QT_RE.match(b.split('.')[-1]) or b.split('.')[0] in ("PySide6", "PyQt5", "PyQt6")]
    if qt_bases:
        print(f"  ⚠ Qt 基类：{qt_bases}")

    # 整个类体引用的模块级名字
    all_free: set[str] = set()
    for m in methods:
        all_free |= set(prog.free_names(m))
    # 类体还包括类级赋值
    for n in node.body:
        if isinstance(n, (ast.Assign, ast.AnnAssign)):
            all_free |= set(prog.free_names(n))

    qt_body = sorted(x for x in all_free if QT_RE.match(x))
    if qt_body:
        print(f"  ⚠ 类体 Qt 引用：{qt_body}")

    # 实例属性
    attr_writes: dict[str, list[str]] = {}
    attr_reads: dict[str, set[str]] = {}
    shared = set()
    for m in methods:
        w, r = self_attrs(m)
        for x in w:
            attr_writes.setdefault(x, []).append(m.name)
        for x in r:
            attr_reads.setdefault(x, set()).add(m.name)

    # 跨方法共享的属性：在 A 写、在 B 读
    for x, writers in attr_writes.items():
        readers = attr_reads.get(x, set())
        if readers - set(writers):
            shared.add(x)

    print(f"\n实例属性 {len(attr_writes)} 个（其中 {len(shared)} 个跨方法共享）")
    if a.detail:
        for x in sorted(attr_writes):
            w = attr_writes[x]
            r = sorted(attr_reads.get(x, set()) - set(w))
            mark = "★" if x in shared else " "
            print(f"  {mark} self.{x:26s} 写:{','.join(w)[:44]:44s} 读:{','.join(r)[:40]}")

    # 外部依赖分类
    buckets = {"qt": qt_body, "core": [], "plotting": [], "third": [], "std": [], "unknown": []}
    for name in sorted(all_free):
        if name in qt_body:
            continue
        if name in prog.imports:
            mod = prog.imports[name]
            if mod in THIRD_PARTY:
                buckets["third"].append(f"{name}({mod})")
            elif mod in STDLIB:
                buckets["std"].append(name)
            elif mod in ("core", "plotting"):
                buckets[mod].append(name)
            else:
                buckets["unknown"].append(f"{name}({mod})")
        elif name in THIRD_PARTY:
            buckets["third"].append(name)
        elif name in STDLIB:
            buckets["std"].append(name)
        else:
            buckets["unknown"].append(name)

    print("\n类体外部依赖：")
    LABELS = {"qt": "Qt（阻断）", "core": "core/", "plotting": "plotting/",
              "third": "第三方", "std": "标准库", "unknown": "单文件内其它符号或未识别"}
    for k in ("qt", "core", "plotting", "third", "std", "unknown"):
        if buckets[k]:
            print(f"  {LABELS[k]}: {', '.join(buckets[k][:14])}"
                  + (f" …共 {len(buckets[k])} 个" if len(buckets[k]) > 14 else ""))

    print()
    if buckets["qt"] or qt_bases:
        print("判定：含 Qt 依赖 —— 整类不能进 core，需先解耦或留在 UI 层。")
    elif buckets["plotting"]:
        print("判定：依赖 plotting —— core 不可反依赖，需先归位相关符号。")
    else:
        print("判定：可搬（基类非 Qt、类体无 Qt 引用）。")
        if shared:
            print(f"      注意：{len(shared)} 个 self 属性跨方法共享，"
                  "整类搬走没问题，但不要试图拆成多个类。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
