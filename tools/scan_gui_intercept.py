# -*- coding: utf-8 -*-
"""GUI 交互拦截扫描：找出「装饰控件挡住交互」与「信号重复连接」。

为什么要有这个工具
------------------
2026-11-04 用户报「有时无法正常拖拽」。查下来是一条很典型的 Qt 陷阱：

    _empty_label 铺满整个 QListWidget（setGeometry(self.rect())），
    而 QLabel 默认 acceptDrops=False —— 它把拖拽事件吃掉，不转发给父控件。

这类问题在 8000 行单文件里人工翻很难找全，而且形态高度相似：
装饰控件、覆盖层、重复 connect、循环弹窗。

本工具把它们一次性列出来，交由人判断哪些是真问题。
**它只报告，不修改。**

用法：
    python tools/scan_gui_intercept.py                    # 扫默认的 Fusion 主程序
    python tools/scan_gui_intercept.py <目标.py>          # 扫指定文件
    python tools/scan_gui_intercept.py --only A           # 只跑某一项检查

判读要点（每条检查都有假警报，别照单全改）：
    A 铺满父控件的子控件 —— 若它本身就是可交互控件（如另一个列表），不需要穿透
    B 重复 connect       —— 必须确认两个 connect 是否在互斥的 if/elif 分支里
    C 循环内弹窗          —— 有些是在用户主动触发的循环里，逐个确认也合理
    D 事件接受不处理      —— 有些事件的意义就是"接力"，需看具体语义
    E except: pass       —— 清理临时文件、可选依赖探测这类是合理的

已知假警报（2026-11-04 实测）：
    · ignore_neg_cb / rated_power_spin 的 connect×2 —— 在 mode A/B/C 互斥分支里
    · setEnabled(False) 多于 True —— 构造期一次性禁用、或操作完主动禁用自己
    · Event.wait(0.5) —— 在工作线程里轮询，不是 UI 线程阻塞

跑完输出供人读的报告，退出码恒为 0（它不是闸门，是检索器）。
"""
from __future__ import annotations

import ast
import io
import os
import re
import sys
from collections import defaultdict

DEFAULT_TARGET = ("apps", "负荷曲线批量生成工具 Pro 融合版 (v2.3).py")


def load(path):
    src = io.open(path, encoding="utf-8").read()
    return src, src.splitlines(), ast.parse(src)


def check_a(tree, src, lines):
    """铺满父控件的子控件"""
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "setGeometry":
            seg = ast.get_source_segment(src, node) or ""
            if "rect()" in seg or "geometry()" in seg:
                hits.append((node.lineno, re.sub(r"\s+", "", seg)[:70]))
    return sorted(hits)


def check_c(tree, src, lines):
    """同一函数内同一信号 connect 多次"""
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        seen = defaultdict(list)
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) \
                    and sub.func.attr == "connect":
                key = re.sub(r"\s+", "", ast.get_source_segment(src, sub.func.value) or "")
                seen[key].append(sub.lineno)
        for k, v in seen.items():
            if len(v) > 1:
                out.append((node.name, node.lineno, k, v))
    return out


def check_d(tree, src, lines):
    """循环体内创建对话框"""
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.For, ast.While)):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                f = sub.func
                nm = f.id if isinstance(f, ast.Name) else (
                    f.attr if isinstance(f, ast.Attribute) else "")
                if nm in ("QDialog", "QInputDialog", "QFileDialog") or (
                        isinstance(f, ast.Attribute)
                        and f.attr in ("information", "warning", "critical", "question")):
                    out.append((sub.lineno, node.lineno, nm))
                    break
    return out


def check_e(tree, src, lines):
    """except: pass"""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                out.append(node.lineno)
    return sorted(out)


def main(argv):
    only = None
    target = None
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == "--only" and i + 1 < len(argv):
            only = argv[i + 1].upper()
            i += 2
            continue
        if not a.startswith("-"):
            target = a
        i += 1

    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = target or os.path.join(repo, *DEFAULT_TARGET)
    if not os.path.exists(path):
        print(f"找不到目标文件: {path}")
        return 0

    src, lines, tree = load(path)
    print(f"目标: {path}")
    print(f"行数: {len(lines)}\n")

    run = lambda k: (only is None or only == k)

    if run("A"):
        print("=" * 72)
        print("A. 铺满父控件的子控件（装饰层可能吞掉交互）")
        print("=" * 72)
        pts = check_a(tree, src, lines)
        transparent = [i + 1 for i, l in enumerate(lines)
                       if "WA_TransparentForMouseEvents" in l]
        for ln, seg in pts:
            near = [t for t in transparent if abs(t - ln) < 40]
            mark = "已穿透" if near else "**未穿透**"
            print(f"  L{ln:<6} {mark:<10} {seg}")
        if not pts:
            print("  （无）")
        print()

    if run("C"):
        print("=" * 72)
        print("C. 同一函数内同一信号 connect 多次（回调会执行多次）")
        print("=" * 72)
        dups = check_c(tree, src, lines)
        for fn, ln, key, locs in dups:
            print(f"  {fn} (L{ln}): {key}  ×{len(locs)}  行 {locs}")
            print("      注意：若几个 connect 在互斥的 if/elif 分支里，属假警报")
        if not dups:
            print("  （无）")
        print()

    if run("D"):
        print("=" * 72)
        print("D. 循环体内创建对话框（批量时逐个弹窗）")
        print("=" * 72)
        for ln, loop_ln, nm in check_d(tree, src, lines):
            print(f"  L{ln}: 在循环 L{loop_ln} 内 -> {nm}")
        if not check_d(tree, src, lines):
            print("  （无）")
        print()

    if run("E"):
        print("=" * 72)
        print("E. except: pass（清理/可选依赖这类是合理的）")
        print("=" * 72)
        pts = check_e(tree, src, lines)
        print(f"  共 {len(pts)} 处")
        if pts:
            print(f"  行号: {pts}")
        print()

    print("=" * 72)
    print("提示：本工具只报告，不修改。每条都要人工确认——")
    print("      已知假警报见文件头部 docstring。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
