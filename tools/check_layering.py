# -*- coding: utf-8 -*-
"""分层守卫：用 AST 检查各层有没有引入不该引入的框架。

为什么不用 grep
---------------
文本 grep 会命中注释与 docstring 里的说明文字。core/ 的模块文档里写着
「不得 import UI 框架」，如果按字面 grep 库名，这份说明本身就会被判为违规。
检查器必须看**真的 import 语句**，不是看有没有出现过这个词。

规则（方案 D 第 3 节）
---------------------
    core/      不得 import UI 框架 / 绘图库 / Web 框架
    plotting/  不得 import UI 框架 / Web 框架
    services/  不得 import UI 框架 / Web 框架
    api/       不得 import UI 框架

用法
----
    python tools/check_layering.py
    python tools/check_layering.py -v        # 打印每个文件引入了什么

退出码：零违规 0，有违规 1。
"""
from __future__ import annotations

import argparse
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# 包的识别名 → 归入哪一类
FORBIDDEN = {
    "ui": {"PySide6", "PyQt5", "PyQt6", "PySide2", "tkinter", "kivy", "wx"},
    "plot": {"matplotlib", "seaborn", "plotly", "bokeh", "mpl_toolkits"},
    "web": {"fastapi", "starlette", "flask", "django", "uvicorn", "webview"},
}

# 每层禁用的类别
LAYERS = {
    "core": {"ui", "plot", "web"},
    "plotting": {"ui", "web"},
    "services": {"ui", "web"},
    "api": {"ui"},
}


def module_root(name: str) -> str:
    return (name or "").split(".")[0]


def scan_file(path: str) -> list[tuple[int, str, str]]:
    """返回 [(行号, 模块名, import 形式)]。"""
    src = open(path, encoding="utf-8", errors="replace").read()
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [(e.lineno or 0, f"<语法错误: {e.msg}>", "parse")]
    hits = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                hits.append((n.lineno, a.name, "import"))
        elif isinstance(n, ast.ImportFrom):
            # 相对导入（level>0）不涉及外部框架
            if getattr(n, "level", 0):
                continue
            hits.append((n.lineno, n.module or "", "from"))
    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description="分层守卫：AST 检查越界依赖")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    cats = {k: set(v) for k, v in FORBIDDEN.items()}
    violations = []
    checked = 0
    seen_dirs = []

    for layer, banned_cats in LAYERS.items():
        d = os.path.join(ROOT, layer)
        if not os.path.isdir(d):
            continue
        seen_dirs.append(layer)
        for name in sorted(os.listdir(d)):
            if not name.endswith(".py"):
                continue
            path = os.path.join(d, name)
            checked += 1
            rel = f"{layer}/{name}"
            imports = scan_file(path)
            if a.verbose:
                mods = sorted({module_root(m) for _, m, _ in imports if m})
                print(f"  {rel:28s} imports: {', '.join(mods) if mods else '—'}")
            for lineno, mod, kind in imports:
                root = module_root(mod)
                for cat in banned_cats:
                    if root in cats[cat]:
                        violations.append((rel, lineno, mod, cat))

    if not seen_dirs:
        print("没有找到任何层目录（core/ plotting/ services/ api/），无需检查")
        return 0

    print(f"检查 {checked} 个文件，层级：{', '.join(seen_dirs)}")
    if violations:
        print(f"\n发现 {len(violations)} 处越界依赖：")
        for rel, lineno, mod, cat in violations:
            print(f"  {rel}:{lineno}  import {mod}  （该层禁用 {cat}）")
        return 1

    print("\n全部干净：各层没有引入被禁用的框架。")
    print("（注：本检查基于 AST，只看真实 import 语句，不受注释与文档措辞影响。）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
