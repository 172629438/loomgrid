# -*- coding: utf-8 -*-
"""静态检查新模块里有没有「未定义但被使用」的名字。

为什么需要它
------------
搬移过程中逐个靠 NameError 发现漏搬的符号，一次只暴露一个，效率极低。
这个脚本一次性列出所有疑似未定义的名字，一次补齐 import 或常量。

口径
----
对每个文件：收集 使用(Load) - 模块级定义 - 导入 - 内建 - 函数参数/局部赋值。
剩下的就是「要么该 import，要么该搬过来」的候选。

用法
----
    python tools/check_modules.py            # 检查 core/ 与 plotting/
    python tools/check_modules.py -v
"""
from __future__ import annotations

import argparse
import ast
import builtins
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LAYERS = ["core", "plotting", "services", "api"]


def module_names(tree) -> set[str]:
    """模块级能提供名字的东西：定义、赋值、导入、条件导入。"""
    names = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(n.name)
        elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            names.add(n.id)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                names.add(a.asname or a.name.split(".")[0])
        elif isinstance(n, ast.arg):
            names.add(n.arg)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            names.add(n.name)
        elif isinstance(n, ast.Global):
            names.update(n.names)
    return names


def used_names(tree) -> set[str]:
    return {n.id for n in ast.walk(tree)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


def check_file(path: str) -> list[str]:
    src = open(path, encoding="utf-8", errors="replace").read()
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [f"<语法错误 L{e.lineno}: {e.msg}>"]
    return sorted(used_names(tree) - module_names(tree) - set(dir(builtins)))


def main() -> int:
    ap = argparse.ArgumentParser(description="检查新模块的未定义名")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    total = 0
    for layer in LAYERS:
        d = os.path.join(ROOT, layer)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(".py"):
                continue
            path = os.path.join(d, name)
            missing = check_file(path)
            rel = f"{layer}/{name}"
            if missing:
                total += len(missing)
                print(f"✗ {rel:28s} 疑似未定义 {len(missing)} 个：{', '.join(missing)}")
            elif a.verbose:
                print(f"✓ {rel:28s} 干净")

    print()
    if total:
        print(f"共 {total} 处疑似未定义。逐项确认：要么补 import，要么把定义搬过来。")
        return 1
    print("所有模块的对外依赖都能解析。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
