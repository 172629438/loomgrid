# -*- coding: utf-8 -*-
"""拖放端到端实测：构造真 QDropEvent 投给主窗口，验文件是否真的进了列表。

这一份和 verify_ux_a11y_theme.py 的分工：
  · verify_ux_a11y_theme  验「代码结构对不对」（源码契约 + 控件属性）
  · 本文件                 验「行为对不对」（真的投事件，真的数文件）

为什么值得单独一份：前一份能证明 setAcceptDrops 被调过、浮层不带
WindowStaysOnTopHint、子控件都关了拖放——但证明不了 dropEvent 那段
处理逻辑本身会不会把文件吃掉。构造真 QDropEvent 直接调 dropEvent()，
断言的是 win.files 的长度和内容。

已知边界（照实写）：
  · 不走真实 DnD 通道，所以验不到光标反馈、拖拽中的浮层、Windows 与
    资源管理器之间的 IPC
  · dragEnterEvent 不在这里测——它会 setVisible(True)+raise_() 一个浮层，
    offscreen 平台下会卡住，那是平台限制不是被测逻辑
  · 175% 缩放下程序化鼠标不可靠，真窗口仍需人眼过
"""
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(REPO, "apps", "负荷曲线批量生成工具 Pro 融合版 (v2.3).py")

sys.path.insert(0, os.path.join(REPO, "apps"))
sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

passed, failed = [], []


def check(name, cond, detail=""):
    if cond:
        passed.append(name)
        print(f"  [PASS] {name}")
    else:
        failed.append(name)
        print(f"  [FAIL] {name}  {detail}")


try:
    from PySide6.QtWidgets import QApplication, QWidget
    from PySide6.QtGui import QDropEvent
    from PySide6.QtCore import QMimeData, QUrl, QPointF, Qt
except ImportError:
    print("[SKIP] 无 PySide6，跳过拖放实测")
    sys.exit(0)

app = QApplication.instance() or QApplication([])
app.setStyle("Fusion")

import importlib.util

spec = importlib.util.spec_from_file_location("fm_drop_e2e", MAIN)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

# ── 造样本 ──────────────────────────────────────────────────────────
tmp = tempfile.mkdtemp(prefix="drop_e2e_")
xlsx = os.path.join(tmp, "曲线导出-1-2026-07.xlsx")
csvf = os.path.join(tmp, "data.csv")
txt = os.path.join(tmp, "readme.txt")
xlsx3 = os.path.join(tmp, "曲线导出-3.xlsx")
for p in (xlsx, csvf, xlsx3):
    with open(p, "wb") as f:
        f.write(b"dummy")
with open(txt, "w", encoding="utf-8") as f:
    f.write("x")
sub = os.path.join(tmp, "子目录")
os.makedirs(sub, exist_ok=True)
with open(os.path.join(sub, "曲线导出-2.xlsx"), "wb") as f:
    f.write(b"dummy")

win = mod.MainWindow()
fl = win.findChild(mod.FileListWidget)


def n():
    return len(win.files)


def drop(widget, paths):
    m = QMimeData()
    m.setUrls([QUrl.fromLocalFile(p) for p in paths])
    ev = QDropEvent(QPointF(20, 20), Qt.CopyAction, m, Qt.LeftButton, Qt.NoModifier)
    widget.dropEvent(ev)
    return ev


print("=" * 72)
print("① 主窗口 dropEvent：文件真的进列表了吗")
print("=" * 72)
b = n()
ev = drop(win, [xlsx])
check("单个 xlsx 进列表", n() == b + 1, f"{b} → {n()}")
check("事件被接受", ev.isAccepted())
check("列表末项是那个文件",
      bool(win.files) and win.files[-1].endswith(".xlsx"), str(win.files[-1:]))

b = n()
drop(win, [csvf])
check("csv 进列表", n() == b + 1, f"{b} → {n()}")

print()
print("=" * 72)
print("② 过滤与目录递归")
print("=" * 72)
b = n()
drop(win, [txt])
check(".txt 被过滤（不进列表）", n() == b, f"{b} → {n()}")

b = n()
drop(win, [sub])
check("目录递归出内部 xlsx", n() == b + 1, f"{b} → {n()}")
check("递归到的是子目录那个",
      any("曲线导出-2" in p for p in win.files), str(win.files[-2:]))

print()
print("=" * 72)
print("③ FileListWidget 自己的 dropEvent")
print("=" * 72)
b = n()
drop(fl, [xlsx3])
check("拖到列表上也能进", n() == b + 1, f"{b} → {n()}")

print()
print("=" * 72)
print("④ 去重")
print("=" * 72)
b = n()
drop(win, [xlsx])
check("同一文件不重复添加", n() == b, f"{b} → {n()}")

print()
print("=" * 72)
print("⑤ 关键区域不再是死区")
print("=" * 72)
log = getattr(win, "log_area", None)
check("日志区 acceptDrops=False（事件会冒泡给主窗口）",
      log is not None and log.acceptDrops() is False)

bad_children = [w for w in win.findChildren(QWidget)
                if not isinstance(w, mod.FileListWidget) and w.acceptDrops()]
check(f"无其它子控件截获拖放（发现 {len(bad_children)} 个）",
      not bad_children,
      f"{[type(w).__name__ for w in bad_children][:6]}")

check("FileListWidget 自身保留拖放",
      fl is not None and fl.acceptDrops() is True)

print()
print("=" * 72)
print(f"结果：PASS {len(passed)}   FAIL {len(failed)}")
for x in failed:
    print("  -", x)
print("=" * 72)

win.close()
sys.exit(1 if failed else 0)
