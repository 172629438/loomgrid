"""骨架类名检查：白名单每项必须能在 app-shell.css 里找到定义。

为什么需要行号验收
------------------
上一轮清理骨架残留时，白名单按「名字听起来像骨架」定，结果误删了
.v2__opts / .v2__opt —— 它们听起来像骨架（底栏选项区），但
app-shell.css 里根本没有定义，是这一屏独有的内容类。

判据：白名单里每一项，都要能指出它在 app-shell.css 的定义行号。
指不出行号的，不该在白名单里。

这样白名单从「猜的」变成「可核对的」，而且将来加新骨架类名时，
加白名单的人必须先去 CSS 里确认它真的存在。
"""

import io
import os
import re
import sys

ROOT = r"C:\Users\LENOVO\.hanako\repos\172629438__loomgrid"
SHELL_CSS = os.path.join(ROOT, "web", "components", "app-shell.css")
VIEWS = os.path.join(ROOT, "web", "views")

# 骨架类名白名单 —— 每一项都必须在 app-shell.css 里有定义
WHITELIST = [
    "shell", "shell__bar", "shell__back", "shell__title", "shell__ctx",
    "shell__bar-right", "shell__body", "shell__panel", "shell__pane",
    "shell__pane-head", "shell__pane-note", "shell__scroll", "shell__sec",
    "shell__sec-note", "shell__foot", "shell__foot-note", "shell__foot-opts",
    "shell__actions",
]


def check_whitelist_defined():
    """白名单每一项必须能在 app-shell.css 找到定义行。"""
    css = io.open(SHELL_CSS, encoding="utf-8").read()
    lines = css.split("\n")
    defined = {}
    for i, line in enumerate(lines, 1):
        for m in re.finditer(r"\.(shell(?:__[a-z-]+)?)\b", line):
            defined.setdefault(m.group(1), i)

    print("=== 白名单定义核对（app-shell.css）===")
    bad = 0
    for name in WHITELIST:
        if name in defined:
            print("  %-20s app-shell.css:%d" % (name, defined[name]))
        else:
            print("  %-20s ✗ 未定义 —— 不该在白名单里" % name)
            bad += 1
    return bad


def scan_views():
    """视图里出现 vN__<骨架名> 即残留。骨架名从白名单派生（去掉 shell__ 前缀）。"""
    skeleton = set()
    for name in WHITELIST:
        if name.startswith("shell__"):
            skeleton.add(name[len("shell__"):])

    print("\n=== 视图骨架残留（vN__* 且 * 在白名单内）===")
    bad = 0
    for f in sorted(os.listdir(VIEWS)):
        if not f.endswith(".html"):
            continue
        txt = io.open(os.path.join(VIEWS, f), encoding="utf-8").read()
        for m in re.finditer(r"v[1-6]__([a-z][a-z-]*)", txt):
            if m.group(1) in skeleton:
                print("  %s: %s" % (f, m.group(0)))
                bad += 1
    if bad == 0:
        print("  0 处")
    return bad


def scan_content_classes():
    """反向：视图里出现的 vN__xxx，xxx 不在白名单 —— 这是内容类，正常。
    但列出清单，便于人工确认「没有把骨架误当内容类留下」。"""
    skeleton = set()
    for name in WHITELIST:
        if name.startswith("shell__"):
            skeleton.add(name[len("shell__"):])

    print("\n=== 各视图的内容类（不在白名单，属正常）===")
    for f in sorted(os.listdir(VIEWS)):
        if not f.endswith(".html"):
            continue
        txt = io.open(os.path.join(VIEWS, f), encoding="utf-8").read()
        names = sorted({m.group(1) for m in re.finditer(r"v[1-6]__([a-z][a-z-]*)", txt)})
        content = [n for n in names if n not in skeleton]
        print("  %-22s %d 个：%s" % (f, len(content), " ".join(content[:8]) + ("…" if len(content) > 8 else "")))
    return 0


if __name__ == "__main__":
    bad = check_whitelist_defined()
    bad += scan_views()
    scan_content_classes()
    print("\n" + "=" * 56)
    print("FAIL %d" % bad if bad else "全部通过")
    sys.exit(1 if bad else 0)
