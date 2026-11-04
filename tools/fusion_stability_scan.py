# -*- coding: utf-8 -*-
"""Fusion 静默失败面扫描（只读 / 可复跑）。

面向「正式投用」的取证工具：找出不报错、不崩溃、但可能给出错误结果的地方。

覆盖：
  1 空 except（等价 pass）—— 吞掉异常
  2 裸 except
  3 except Exception 后无 raise（需人工判是否合理兜底）
  4 长函数（>120 行）
  5 深层嵌套（>5 层）
  6 可变默认参数
  7 硬编码盘符路径
  8 TODO / FIXME / HACK
  9 线程与进程池创建点
 10 版本号常量定义与引用

用法：
    python tools/fusion_stability_scan.py              # 文本总览
    python tools/fusion_stability_scan.py --json       # 全量 JSON
    python tools/fusion_stability_scan.py --out=x.txt  # 写文件（Windows 重定向会变 UTF-16）

判读纪律：数量本身不是结论。空 except 里约有一半是合理兜底
（清理临时文件、可选依赖探测、窗口状态恢复），真正要看的是
tools/fusion_except_context.py 给出的「吞异常时正在做什么」。
"""
import ast
import io
import json
import os
import re
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

NEST = (ast.If, ast.For, ast.While, ast.Try, ast.With, ast.AsyncFor, ast.AsyncWith)


def const_only(body):
    """body 是否等价于空：全 Pass / ... / 常量字符串"""
    for st in body:
        if isinstance(st, ast.Pass):
            continue
        if isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant):
            continue
        return False
    return True


def has_raise(body):
    for n in body:
        for sub in ast.walk(n):
            if isinstance(sub, ast.Raise):
                return True
    return False


def depth_of(node, cur=0):
    """返回该子树内最大控制流嵌套深度（不下钻嵌套函数体）"""
    best = cur
    for ch in ast.iter_child_nodes(node):
        if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)) and ch is not node:
            continue
        inc = 1 if isinstance(ch, NEST) else 0
        best = max(best, depth_of(ch, cur + inc))
    return best


def scan(display, path):
    src = io.open(path, encoding="utf-8", errors="replace").read()
    lines = src.splitlines()
    tree = ast.parse(src)
    out = {
        "file": display,
        "lines": len(lines),
        "empty_except": [],
        "bare_except": [],
        "broad_except": [],
        "long_funcs": [],
        "deep_nest": [],
        "mutable_default": [],
        "hardcoded_path": [],
        "todo": [],
        "threads": [],
        "version_const": [],
    }

    for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        span = (fn.end_lineno or fn.lineno) - fn.lineno + 1
        if span > 120:
            out["long_funcs"].append((fn.lineno, fn.name, span))
        d = depth_of(fn)
        if d > 5:
            out["deep_nest"].append((fn.lineno, fn.name, d))
        a = fn.args
        for dv in list(a.defaults) + [d for d in a.kw_defaults if d is not None]:
            if isinstance(dv, (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)):
                out["mutable_default"].append((fn.lineno, fn.name))

    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if node.type is None:
            out["bare_except"].append((node.lineno, lines[node.lineno - 1].strip()[:80]))
            continue
        tname = ast.unparse(node.type)
        if const_only(node.body):
            out["empty_except"].append((node.lineno, tname))
        elif tname in ("Exception", "BaseException") and not has_raise(node.body):
            out["broad_except"].append((node.lineno, tname))

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if re.search(r"[A-Za-z]:\\\\", node.value) or re.search(r"[A-Za-z]:\\", node.value):
                out["hardcoded_path"].append((node.lineno, node.value[:70]))

    for i, ln in enumerate(lines, 1):
        if re.search(r"\b(TODO|FIXME|HACK|XXX)\b", ln):
            out["todo"].append((i, ln.strip()[:90]))
        if re.search(r"\b(ProcessPoolExecutor|ThreadPoolExecutor|threading\.Thread)\s*\(", ln):
            out["threads"].append((i, ln.strip()[:90]))
        if re.search(r"\b(FUSION_VERSION|FUSION_APP_NAME|ULTRA_VERSION)\b", ln):
            out["version_const"].append((i, ln.strip()[:90]))
    return out


NAMES = {
    "empty_except": "空 except（等价 pass）",
    "bare_except": "裸 except",
    "broad_except": "except Exception 未 re-raise",
    "long_funcs": "长函数 >120 行",
    "deep_nest": "嵌套 >5 层",
    "mutable_default": "可变默认参数",
    "hardcoded_path": "硬编码盘符路径",
    "todo": "TODO/FIXME/HACK",
    "threads": "线程/进程池创建点",
    "version_const": "版本常量引用行",
}


def main():
    outs = [a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--out=")]
    if outs:
        sys.stdout = io.open(outs[0], "w", encoding="utf-8")

    res = [scan(d, p) for d, p in TARGETS if os.path.exists(p)]
    if "--json" in sys.argv:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0

    agg = {}
    for r in res:
        for k, v in r.items():
            if isinstance(v, list):
                agg[k] = agg.get(k, 0) + len(v)
    print("=" * 78)
    print("总览")
    print("=" * 78)
    for k, label in NAMES.items():
        print(f"  {label:<28} {agg.get(k, 0):>4}")

    for r in res:
        hits = [(NAMES[k], v) for k, v in r.items() if isinstance(v, list) and v]
        if not hits:
            continue
        print("\n" + "-" * 78)
        print(f"[{r['file']}] {r['lines']} 行")
        for label, items in hits:
            print(f"\n  ## {label}  ({len(items)})")
            for it in items[:40]:
                print("     " + "  ".join(str(x) for x in it))
            if len(items) > 40:
                print(f"     ... 另 {len(items)-40} 条（--json 取全量）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
