# -*- coding: utf-8 -*-
"""测试覆盖缺口审计：找出 core/ services/ plotting/ 里从未被任何脚本引用的函数。

用途
----
回答「测试套件够不够全」这个问题，找的是**结构性缺口**，不是宣称覆盖率。

判据：函数名（或方法名）是否出现在 tests/、tools/、主程序里。
局限：只认名字字面出现，间接调用（getattr / 字典分派 / 信号槽）会被误判为未覆盖。
      所以「未引用」只说明「没有脚本直接碰过它」，不等于死代码。

口径：
  被引用     —— 名字在某个脚本里出现过（测试或主程序）
  从未引用   —— 没有任何脚本出现这个名字

读法：从未引用清单里，如果某项是「数据形态判定」「配置持久化」「取数核心」，
      那就是真正的高价值缺口；如果是纯格式化辅助（_fmt_date 之类），可以放着。

用法：
    python tools/coverage_audit.py
"""
import ast
import glob
import io
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")


def collect_targets():
    """core/ services/ plotting/ 里的顶层函数与类方法。"""
    targets = {}
    for pkg in ("core", "services", "plotting"):
        for p in sorted(glob.glob(os.path.join(REPO, pkg, "*.py"))):
            rel = os.path.relpath(p, REPO).replace("\\", "/")
            try:
                tree = ast.parse(io.open(p, encoding="utf-8", errors="replace").read())
            except SyntaxError:
                continue
            for n in tree.body:
                if isinstance(n, ast.FunctionDef) and not n.name.startswith("__"):
                    targets.setdefault(n.name, (rel, n.lineno))
                elif isinstance(n, ast.ClassDef):
                    for m in n.body:
                        if isinstance(m, ast.FunctionDef) and not m.name.startswith("__"):
                            targets.setdefault(f"{n.name}.{m.name}", (rel, m.lineno))
    return targets


def collect_uses():
    used = set()
    scan = (glob.glob(os.path.join(REPO, "tests", "*.py"))
            + glob.glob(os.path.join(REPO, "tools", "*.py"))
            + glob.glob(os.path.join(REPO, "apps", "*.py")))
    for p in scan:
        try:
            tree = ast.parse(io.open(p, encoding="utf-8", errors="replace").read())
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.Name):
                used.add(n.id)
            elif isinstance(n, ast.Attribute):
                used.add(n.attr)
    return used


def main():
    targets = collect_targets()
    used = collect_uses()
    covered, uncovered = [], []
    for name, (path, ln) in sorted(targets.items()):
        simple = name.split(".")[-1]
        (covered if simple in used else uncovered).append((name, path, ln))

    print(f"core/ services/ plotting/ 共 {len(targets)} 个函数与方法")
    print(f"  被任何脚本引用过: {len(covered)}")
    print(f"  从未被引用:       {len(uncovered)}")
    print()

    by_mod = {}
    for name, path, ln in uncovered:
        by_mod.setdefault(path, []).append((name, ln))
    print("=== 从未被引用（按模块）===")
    for path in sorted(by_mod):
        print(f"\n{path}")
        for name, ln in by_mod[path]:
            print(f"    L{ln:<5} {name}")
    print()
    print("提示：判据是「名字是否出现」，间接调用会被误判。")
    print("      重点看「数据形态判定 / 配置持久化 / 取数核心」这几类，格式化辅助可以放着。")


if __name__ == "__main__":
    main()
