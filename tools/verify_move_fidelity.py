# -*- coding: utf-8 -*-
"""搬移忠实度核验：core/ 里的每个符号与拆分前单文件是否结构等价。

为什么用 AST 而不是逐行比
------------------------
逐行比会被这些无关差异干扰：缩进风格（`len(parts)>=2` vs `len(parts) >= 2`）、
注释补充、空行。但注释与空白**不改变行为**，而结构改变一定改变行为。

所以用 ast.dump 归一化比对：语法树完全相同 = 结构完全等价，与排版无关。
再附一份「源码文本差异行」供人工看究竟改了什么。

用法
----
    python tools/verify_move_fidelity.py timegrid
    python tools/verify_move_fidelity.py timegrid --ref HEAD~1
"""
from __future__ import annotations

import argparse
import ast
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FUSION_REL = "负荷曲线批量生成工具 Pro 融合版 (v2.3).py"


def load_source(text_or_path: str) -> str:
    if os.path.exists(text_or_path):
        return open(text_or_path, encoding="utf-8").read()
    return text_or_path


def git_show(ref: str) -> str:
    r = subprocess.run(["git", "show", f"{ref}:{FUSION_REL}"], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SystemExit(f"取不到 {ref} 版本：{r.stderr[:200]}")
    return r.stdout


def collect(src: str) -> dict:
    """从源码里收集顶层符号 → (ast node, 源码片段)。"""
    out = {}
    tree = ast.parse(src)
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out[n.name] = n
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = n
    return out, src


def norm_ast(node) -> str:
    """归一化 AST：抹掉行号等位置信息，只留结构。"""
    clone = ast.parse(ast.unparse(node))
    return ast.dump(clone)


def main() -> int:
    ap = argparse.ArgumentParser(description="核验模块搬移的忠实度")
    ap.add_argument("module", help="模块名，如 timegrid / fonts")
    ap.add_argument("--layer", default="core", help="所在层目录，默认 core；plotting 模块传 --layer plotting")
    ap.add_argument("--ref", default="HEAD~1", help="对比的基准提交（默认上一个提交）")
    ap.add_argument("--detail", action="store_true", help="打印文本差异行")
    a = ap.parse_args()

    target = os.path.join(ROOT, a.layer, f"{a.module}.py")
    if not os.path.exists(target):
        raise SystemExit(f"找不到 {a.layer}/{a.module}.py")
    new_src = load_source(target)
    old_src = git_show(a.ref)
    new_syms, _ = collect(new_src)
    old_syms, _ = collect(old_src)

    # 以新模块里出现、且旧单文件里也有的符号为对象
    common = [k for k in new_syms if k in old_syms]
    common.sort()

    print(f"核验 core/{a.module}.py  ←  {a.ref}:主程序")
    print(f"共同符号 {len(common)} 个\n")

    same, diff = [], []
    for name in common:
        n_new = norm_ast(new_syms[name])
        n_old = norm_ast(old_syms[name])
        if n_new == n_old:
            same.append(name)
        else:
            diff.append(name)

    print(f"结构完全等价：{len(same)} 个")
    if same:
        print("  " + ", ".join(same))
    if diff:
        print(f"\n结构有差异：{len(diff)} 个")
        for name in diff:
            print(f"  - {name}")
            if a.detail:
                import difflib
                a_txt = ast.unparse(old_syms[name]).splitlines()
                b_txt = ast.unparse(new_syms[name]).splitlines()
                for line in difflib.unified_diff(a_txt, b_txt, "原单文件", "core",
                                                 lineterm="", n=2):
                    print("      " + line)

    # 只在新模块里的符号（新增的辅助，不应有除非说明）
    extra = [k for k in new_syms if k not in old_syms]
    if extra:
        print(f"\n仅存在于 core（新增）：{extra}")
    missing = [k for k in old_syms if k not in new_syms and not k.startswith("__")]
    # 主程序里没搬的符号很多，这里只在显式指定时提示
    verdict = "通过：全部搬移符号结构等价" if not diff else f"注意：{len(diff)} 个符号结构有差异"
    print(f"\n{a.module}: {verdict}")
    return 0 if not diff else 1


if __name__ == "__main__":
    sys.exit(main())
