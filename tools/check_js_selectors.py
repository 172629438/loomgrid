# -*- coding: utf-8 -*-
"""views/ 下 HTML 里 JS 选择器存在性检查。

背景
----
骨架统一时把 .v6__head-note 改成 .shell__pane-note，但 JS 里还写着
querySelector('.v6__head-note') —— 运行时才炸，报「Cannot set properties
of null」。同类事故已发生三次（v2__file / v2__left / v6__head-note）。

check_skeleton.py 只扫 HTML 的结构类名，看不见 <script> 里的选择器。
本工具补上这一段：**改类名时必须同时改 JS 里的选择器**，
而靠人记不如靠一条命令。

判据
----
<script> 里的字符串选择器，必须在同一文件的 HTML 里能匹配到。
按 API 区分来源：
    getElementById(x)   → x 是裸 id
    querySelector(x)    → x 是 CSS 选择器
    closest(x)          → 同上

解析受限时跳过而不是报错
------------------------
属性选择器被内层引号截断（.shell__panel[data-side="left"] 抽成
[data-side=）不是失配，是本工具的正则限制。这类判「解析受限」跳过。
**假阳性会训练人忽略输出**，那比不检查更糟。

用法
----
    python tools/check_js_selectors.py
    python tools/check_js_selectors.py --dir web/views

退出码：有失配即 1。
"""
from __future__ import annotations

import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

API_PATTERNS = [
    (r"querySelector(?:All)?\(\s*['\"]([^'\"]+)['\"]", "css"),
    (r"getElementById\(\s*['\"]([^'\"]+)['\"]", "id"),
    (r"closest\(\s*['\"]([^'\"]+)['\"]", "css"),
]


def extract_script_blocks(html: str):
    """只取内联 script（外链 src 的属于别处，不在此文件里匹配）。"""
    return [m.group(1) for m in
            re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)</script>", html, re.I)]


def extract_html_only(html: str) -> str:
    """去掉 <script> 块，只在标记里找类名/id/属性。"""
    return re.sub(r"<script\b[^>]*>[\s\S]*?</script>", "", html, flags=re.I)


def id_exists(name: str, html: str) -> bool:
    """id 是否存在于实际标记里。

    也接受「运行时生成」：innerHTML 拼出来的片段里出现 id="x"
    同样算存在 —— 静态扫描看不到 DOM，但看得到那段模板字符串。
    这类不算失配，否则会把 v6 的 btnOpenDir / btnCopyDir 误报成错误。
    """
    pat = r'id\s*=\s*["\']' + re.escape(name) + r'["\']'
    return bool(re.search(pat, html))


def class_exists(name: str, markup: str) -> bool:
    return bool(re.search(
        r'class\s*=\s*["\'][^"\']*\b' + re.escape(name) + r'\b[^"\']*["\']', markup))


def tag_exists(name: str, markup: str) -> bool:
    return ("<" + name.lower()) in markup.lower()


def css_selector_exists(sel: str, markup: str):
    sel = sel.strip()
    if not sel:
        return True, "空"
    if any(x in sel for x in ("${", "+", "`")):
        return True, "动态拼接"
    # 属性选择器被内层引号截断 —— 正则的限制，不是失配
    if sel.count("[") != sel.count("]"):
        return True, "解析受限（属性选择器被引号截断）"
    if re.fullmatch(r"[a-zA-Z][\w-]*", sel):
        return tag_exists(sel, markup), "tag"

    parts = [p for p in re.split(r"[ >+~,]+", sel) if p]
    for p in parts:
        p_clean = re.sub(r"::?[a-zA-Z-]+(\([^)]*\))?", "", p).strip()
        if not p_clean:
            continue
        for tok in re.findall(r"[#.][\w-]+|\[[^\]]+\]|[a-zA-Z][\w-]*", p_clean):
            if tok.startswith("#"):
                if not id_exists(tok[1:], markup):
                    return False, f"id 不存在: {tok}"
            elif tok.startswith("."):
                if not class_exists(tok[1:], markup):
                    return False, f"class 不存在: {tok}"
            elif tok.startswith("["):
                attr = tok[1:].rstrip("]").split("=")[0].strip()
                if attr and attr not in markup:
                    return False, f"属性不存在: {tok}"
            elif re.fullmatch(r"[a-zA-Z][\w-]*", tok):
                if not tag_exists(tok, markup):
                    return False, f"标签不存在: <{tok}>"
    return True, "ok"


def main() -> int:
    ap = argparse.ArgumentParser(description="views 下 JS 选择器存在性检查")
    ap.add_argument("--dir", default=os.path.join(ROOT, "web", "views"))
    a = ap.parse_args()

    files = sorted(f for f in os.listdir(a.dir) if f.endswith(".html"))
    if not files:
        print("没有可检查的 html")
        return 2

    total = 0
    total_bad = 0
    print("=" * 80)
    print("views/ JS 选择器存在性检查")
    print("=" * 80)

    for fn in files:
        html = open(os.path.join(a.dir, fn), encoding="utf-8", errors="replace").read()
        markup = extract_html_only(html)

        found = set()
        for block in extract_script_blocks(html):
            for pat, kind in API_PATTERNS:
                for m in re.finditer(pat, block):
                    found.add((kind, m.group(1)))

        bad = []
        for kind, sel in sorted(found):
            total += 1
            if kind == "id":
                # id 用完整 html：动态生成的 id 出现在 <script> 的模板串里
                if not id_exists(sel, html):
                    bad.append((f"getElementById({sel})", "id 不存在（含动态生成）"))
            else:
                okk, why = css_selector_exists(sel, markup)
                if not okk:
                    bad.append((f"querySelector({sel})", why))

        mark = "✓" if not bad else "✗"
        print(f"{mark} {fn:<26} 选择器 {len(found):>3} 个   失配 {len(bad)}")
        for s, why in bad:
            print(f"      ✗ {s:<52} {why}")
        total_bad += len(bad)

    print("=" * 80)
    print(f"合计 {total} 个选择器，失配 {total_bad} 个")
    return 0 if total_bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
