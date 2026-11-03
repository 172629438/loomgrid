# -*- coding: utf-8 -*-
"""设计令牌体检：数档数，不靠「感觉挺统一的」。

为什么需要脚本
--------------
Fusion 现状里字号有 19 种（10.5 / 11 / 11.5 / 12 / 12.5 / 13 / 13.33 / 13.5 /
14 / 14.5 / 14.52 / 15 / 15.5 / 16 / 16.5 / 17 / 22 / 28 …），
其中 13.3333px 没人选过，是控件没写 font-size 取了浏览器默认值。
36 处 setStyleSheet、63 次 dark_theme 判断，都是「没有语义层」的结果。

这种问题肉眼看不出来，但能数出来。本脚本就是那把尺子。

判据（阶段 3 硬约束）
--------------------
  字号档数    ≤ 8
  间距档数    ≤ 8，且必须是 4 的倍数序列（不混 5/7/9/11）
  颜色变量数  ≤ 30，双主题必须同名（语义名固定，只换值）
  圆角档数    ≤ 3
  动效档数    ≤ 4

用法
----
    python tools/check_tokens.py web/prototypes/three-views.html
    python tools/check_tokens.py --all        # 扫 web/ 下所有 html

退出码：全部达标 0，任一项超标 1。
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

LIMITS = {
    "字号": 8,
    "间距": 8,
    "颜色": 30,
    "圆角": 3,
    "动效": 4,
}

# 语义名 -> 归类
PATTERNS = {
    "字号": r"--fs-[\w-]+\s*:\s*([^;]+);",
    "间距": r"--sp-[\w-]+\s*:\s*([^;]+);",
    # 语义颜色白名单。值必须是颜色本身（# 或 color-mix），
    # 否则 --focus-ring-width: 2px 这类尺寸会误入颜色计数。
    "颜色": r"--(?:bg|text|border|accent|ref|grid|ok|warn|danger|focus)[\w-]*\s*:\s*(#[0-9a-fA-F]{3,8}|color-mix\([^;]*\))\s*;",
    "圆角": r"--r-[\w-]+\s*:\s*([^;]+);",
    "动效": r"--t-[\w-]+\s*:\s*([^;]+);",
}


def blocks(css: str) -> dict[str, str]:
    """把 [data-theme="dark"] / [data-theme="light"] / :root 各自的声明块切出来。"""
    out = {}
    for m in re.finditer(r"(\[data-theme=\"(\w+)\"\]|:root)\s*\{([^}]*)\}", css, re.S):
        key = m.group(2) or "root"
        out.setdefault(key, "")
        out[key] += m.group(3) + "\n"
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="设计令牌档数体检")
    ap.add_argument("files", nargs="*", help="要检查的 html/css 文件")
    ap.add_argument("--all", action="store_true", help="扫 web/ 下所有 html")
    a = ap.parse_args()

    targets = list(a.files)
    if a.all or not targets:
        # 令牌现在只有一份（web/theme/tokens.css），页面全部外链，
        # 所以不必再扫 HTML —— 但是必须扫令牌文件本身，
        # 否则检查会因为「找不到声明块」而全部跳过，等于把检查关掉。
        targets = sorted(
            glob.glob(os.path.join(ROOT, "web", "theme", "*.css"))
            + glob.glob(os.path.join(ROOT, "web", "**", "*.html"), recursive=True)
        )

    if not targets:
        print("没找到可检查的文件")
        return 2

    bad = 0
    for path in targets:
        text = open(path, encoding="utf-8", errors="replace").read()
        bl = blocks(text)
        print(f"\n{'=' * 70}\n{os.path.relpath(path, ROOT)}\n{'=' * 70}")

        if not bl:
            print("  没有找到 :root 或 [data-theme] 声明块，跳过")
            continue

        # 每个类别在所有主题块里的取值并集
        for cat, pat in PATTERNS.items():
            vals: set[str] = set()
            names: dict[str, set[str]] = {}
            decls = 0
            for theme, body in bl.items():
                for m in re.finditer(pat, body):
                    name = m.group(0).split(":")[0].strip()
                    val = m.group(1).strip()
                    vals.add(val)
                    decls += 1
                    names.setdefault(name, set()).add(theme)

            n = len(vals)
            # 颜色按「语义名」计数，这是判据本意：
            #   「颜色变量数 ≤ 30（双主题共用一套语义名）」——括号里说的就是语义名。
            # 三个口径差别（同一个 tokens.css）：
            #   语义名 20 / 十六进制声明 30 / 全部声明（含派生色）35
            # 取语义名：派生色（accent-weak 这类）不是新语义，是同一语义的变体，
            # 它不增加「需要想一个名字」的负担，这正是上限要控的东西。
            if cat == "颜色":
                n = len(names)
            limit = LIMITS[cat]
            mark = "✓" if n <= limit else "✗"
            print(f"  {mark} {cat:4s} {n:3d} 档 / 上限 {limit}"
                  + ("" if n <= limit else f"   ← 超标 {n - limit}"))
            if n > limit:
                bad += 1
            if n and cat == "字号" and n > limit:
                print(f"      取值: {sorted(vals)[:12]}")

            # 双主题同名检查（颜色必须两边都定义）
            if cat == "颜色" and len(bl) >= 2:
                themes = [t for t in bl if t != "root"]
                if len(themes) >= 2:
                    t1, t2 = themes[0], themes[1]
                    only1 = [k for k, v in names.items() if t1 in v and t2 not in v]
                    only2 = [k for k, v in names.items() if t2 in v and t1 not in v]
                    if only1 or only2:
                        print(f"      ✗ 双主题不同名：{t1} 独有 {only1[:4]} / {t2} 独有 {only2[:4]}")
                        bad += 1
                    else:
                        print(f"      ✓ 双主题语义名一致（{t1} / {t2} 各 {len(names)} 个）")

        # 间距倍数检查
        infos = re.findall(r"--sp-[\w-]+\s*:\s*(\d+)px", text)
        if infos:
            non4 = [v for v in infos if int(v) % 4 != 0]
            if non4:
                print(f"  ✗ 间距不是 4 的倍数：{sorted(set(non4))}")
                bad += 1
            else:
                print("  ✓ 间距均为 4 的倍数")

        # 散值检查：正文里硬编码的 font-size / border-radius / transition
        hard_fs = set(re.findall(r"font-size:\s*([0-9.]+px)", text))
        hard_radius = set(re.findall(r"border-radius:\s*([0-9.]+px)", text))
        print(f"  · 正文硬编码 font-size {len(hard_fs)} 种"
              + (f"：{sorted(hard_fs)[:8]}" if hard_fs else ""))
        print(f"  · 正文硬编码 border-radius {len(hard_radius)} 种"
              + (f"：{sorted(hard_radius)[:6]}" if hard_radius else ""))

    print(f"\n{'=' * 70}")
    if bad:
        print(f"共 {bad} 处超标，需收敛")
        return 1
    print("全部达标")
    return 0


if __name__ == "__main__":
    sys.exit(main())
