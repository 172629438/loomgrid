# -*- coding: utf-8 -*-
"""异常处理器上下文分析（只读 / 可复跑）。

回答的不是「有多少个 except」，而是「吞掉异常的那一刻正在做什么」。
这决定了它是合理兜底还是静默错误。

行为分类：
  SILENT     body 全为 pass/... -> 完全静默
  LOG        body 只打日志（无 return / raise）
  FALLBACK   有 return <常量/默认值> 或 return None
  CONTINUE   有 continue/break（循环内跳过）
  CALL       body 调了非日志函数（业务处理 / 弹窗）
  RAISE      重新抛出（正确）
  STMT       其他语句（赋值等）

作用域标记：
  [桌面版]   MAIN / core / services / plotting / ui —— Fusion 程序真正执行的路径
  [算法包]   algorithms/ —— 独立 PyPI 包 loomgrid_algos，桌面程序不 import 它

用法：
    python tools/fusion_except_context.py
    python tools/fusion_except_context.py --out=x.txt
    python tools/fusion_except_context.py --scope=desktop   # 只看桌面版路径
    python tools/fusion_except_context.py --scope=pkg       # 只看算法包
"""
import ast
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TARGETS = [("MAIN", os.path.join(REPO, "apps", "负荷曲线批量生成工具 Pro 融合版 (v2.3).py"))]
for pkg in ("core", "algorithms", "services", "plotting", "ui"):
    d = os.path.join(REPO, pkg)
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".py"):
                TARGETS.append((f"{pkg}/{fn}", os.path.join(d, fn)))

PACKAGE_PREFIX = ("algorithms/",)


def scope_of(display):
    return "算法包" if display.startswith(PACKAGE_PREFIX) else "桌面版"


def classify(body):
    kinds = set()
    for st in body:
        if isinstance(st, ast.Pass):
            kinds.add("SILENT")
        elif isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant):
            kinds.add("SILENT")
        elif isinstance(st, ast.Raise):
            kinds.add("RAISE")
        elif isinstance(st, ast.Return):
            kinds.add("FALLBACK")
        elif isinstance(st, (ast.Continue, ast.Break)):
            kinds.add("CONTINUE")
        elif isinstance(st, ast.Expr) and isinstance(st.value, ast.Call):
            f = ast.unparse(st.value.func).lower()
            if any(k in f for k in ("log", "print", "warn", "info", "debug", "error", "exception")):
                kinds.add("LOG")
            else:
                kinds.add("CALL")
        else:
            kinds.add("STMT")
    for k in ("RAISE", "FALLBACK", "CONTINUE", "CALL", "STMT", "LOG", "SILENT"):
        if k in kinds:
            return k
    return "?"


def enclosing_func(tree):
    m = {}
    for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        for node in ast.walk(fn):
            m.setdefault(id(node), fn.name)
    return m


def main():
    outs = [a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--out=")]
    scopes = [a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--scope=")]
    want = scopes[0] if scopes else "all"
    if outs:
        sys.stdout = io.open(outs[0], "w", encoding="utf-8")

    buckets = {}
    for display, path in TARGETS:
        if not os.path.exists(path):
            continue
        src = io.open(path, encoding="utf-8", errors="replace").read()
        tree = ast.parse(src)
        owner = enclosing_func(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            tname = "bare" if node.type is None else ast.unparse(node.type)
            kind = classify(node.body)
            fn = owner.get(id(node), "<module>")
            body_txt = " ".join(ast.unparse(s) for s in node.body)[:100]
            buckets.setdefault((display, fn, tname, kind), []).append((node.lineno, body_txt))

    for scope_name in ("桌面版", "算法包"):
        if want not in ("all", "desktop" if scope_name == "桌面版" else "pkg"):
            continue
        print("=" * 84)
        print(f"[{scope_name}]")
        print("=" * 84)
        for (display, fn, tname, kind), items in sorted(buckets.items()):
            if scope_of(display) != scope_name:
                continue
            print(f"\n{display} :: {fn}()   except {tname}   -> {kind}   x{len(items)}")
            for ln, body in items:
                print(f"   L{ln:<6} {body}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
