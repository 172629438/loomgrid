# -*- coding: utf-8 -*-
"""组件层体检：挡「一次性样式」渗透进来。

为什么必须有这条
----------------
Fusion 现在 19 种字号、36 处 setStyleSheet、63 次 dark_theme 判断，
起点都是「这一个地方特殊一下」。单看每一处都不算错，攒起来就收不回去了。

两条硬判据（阶段 3 要求，零命中）：
  1. components/*.css 里不许出现裸的 font-size 数字（必须走 var(--fs-*)）
  2. components/*.css 里不许出现裸的颜色值（必须走 var(--...)）

判定方式说明
------------
不用正则负向断言，因为 `font-size\\s*:\\s*(?!var\\()` 会被回溯绕过 ——
`\\s*` 少匹配一个空格，断言看的位置就不是 var 了。改成「取出值再判断」，
判据写在 Python 里，比写在正则里可读也更可靠。

用法
----
    python tools/check_components.py
    python tools/check_components.py --dir web/components

退出码：两条硬判据任一命中即 1。
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

DECL = re.compile(r"([-\w]+)\s*:\s*([^;{}]+);")
COLOR_LITERAL = re.compile(r"^(#[0-9a-fA-F]{3,8}|rgba?\(|hsla?\()")


def strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def scan(path: str) -> dict:
    raw = open(path, encoding="utf-8", errors="replace").read()
    css = strip_comments(raw)
    out = {"裸字号": [], "裸颜色": [], "box-shadow": [], "裸圆角": [], "裸动效": []}
    for i, line in enumerate(css.splitlines(), 1):
        for m in DECL.finditer(line):
            prop, val = m.group(1).lower(), m.group(2).strip()
            if prop == "font-size":
                if not val.startswith("var("):
                    out["裸字号"].append((i, val))
            elif prop in ("color", "background-color", "border-color",
                          "outline-color", "fill", "stroke"):
                # fill/stroke 用 var 或 none 合法；颜色字面量不合法
                if COLOR_LITERAL.match(val):
                    out["裸颜色"].append((i, f"{prop}: {val}"))
            elif prop == "box-shadow":
                out["box-shadow"].append((i, val))
            elif prop == "border-radius":
                if not val.startswith("var("):
                    out["裸圆角"].append((i, val))
            elif prop in ("transition", "transition-duration", "animation-duration"):
                if re.search(r"\d+(?:\.\d+)?m?s", val) and "var(" not in val:
                    out["裸动效"].append((i, val))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="组件层体检")
    ap.add_argument("--dir", default=os.path.join(ROOT, "web", "components"))
    a = ap.parse_args()

    files = sorted(glob.glob(os.path.join(a.dir, "*.css")))
    if not files:
        print(f"{a.dir} 下没有 css")
        return 2

    hard = 0
    for path in files:
        rel = os.path.relpath(path, ROOT)
        r = scan(path)
        print(f"\n{rel}")
        for label in ("裸字号", "裸颜色"):
            hits = r[label]
            if hits:
                hard += len(hits)
                print(f"  ✗ {label} {len(hits)} 处")
                for i, v in hits[:8]:
                    print(f"      L{i}: {v}")
            else:
                print(f"  ✓ {label} 0 处")
        for label in ("box-shadow", "裸圆角", "裸动效"):
            hits = r[label]
            if hits:
                vals = sorted({v for _, v in hits})
                print(f"  · {label} {len(hits)} 处：{vals[:4]}")

    print(f"\n{'=' * 66}")
    if hard:
        print(f"硬判据命中 {hard} 处 —— 组件层不许出现一次性样式")
        return 1
    print("两条硬判据零命中")
    return 0


if __name__ == "__main__":
    sys.exit(main())
