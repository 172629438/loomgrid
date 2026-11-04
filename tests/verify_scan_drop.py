# -*- coding: utf-8 -*-
"""扫描层的「丢弃可见性」契约。

背景
----
`_scan_wide_chunk` / `_scan_long_chunk` 对无法归类日期的行是 `continue`。
在 v2.3.11 之前，这些行被丢掉后没有任何回传：
- 整表都无效 → `_scan_file_worker` 返回 `[]`，界面表现与「这个 sheet 是空的」完全一样
- 部分行无效 → 只少几个月，用户不知道少了多少

现在约定：
- 有数据但全丢 → `{'skipped': 'no_valid_rows', 'dropped': N}`
- 空工作表     → `{'skipped': 'empty_sheet'}`
- 部分丢       → 正常返回 list，每个 summary 带 `dropped_rows`
- 没丢          → `dropped_rows == 0`

本脚本也用来看住「2026年7月份」这条曾经整表消失的写法。

跑法：python tests/verify_scan_drop.py
"""
import io
import os
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

from core.ingest import _scan_file_worker  # noqa: E402

TIMES = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 15, 30, 45)]
HEAD = "日期," + ",".join(TIMES) + "\n"
VALS = ",".join(str(10 + i) for i in range(len(TIMES)))

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}  {detail}")


def build(rows):
    d = tempfile.mkdtemp(prefix="lgm-scan-")
    p = os.path.join(d, "case.csv")
    with io.open(p, "w", encoding="utf-8", newline="") as f:
        f.write(HEAD)
        for r in rows:
            f.write(f"{r},{VALS}\n")
    return p


def scan(path):
    mapping = {
        "date_col": "日期", "load_col": list(TIMES), "asset_col": None,
        "format_type": "wide", "header_row": 0, "delimiter": ",", "transpose": False,
    }
    return _scan_file_worker((path, "CSV文件", mapping, "未命名", threading.Event(), None))


# ① 正常数据
res = scan(build(["2026-07-01", "2026-07-02", "2026-07-03"]))
check("正常数据返回 list", isinstance(res, list) and len(res) == 1, repr(res)[:120])
if isinstance(res, list) and res:
    s = res[0]
    check("正常数据 month=2026-07", s["month_str"] == "2026-07", s["month_str"])
    check("正常数据 dropped_rows=0", s.get("dropped_rows") == 0, s.get("dropped_rows"))
    check("正常数据 date_count=3", s["date_count"] == 3, s["date_count"])

# ② 「2026年7月份」—— 曾经整表静默消失
res = scan(build(["2026年7月份"] * 3))
check("「2026年7月份」能分组（回归）", isinstance(res, list) and len(res) == 1, repr(res)[:140])
if isinstance(res, list) and res:
    check("「2026年7月份」month=2026-07", res[0]["month_str"] == "2026-07", res[0]["month_str"])

# ③ 日期全乱码 → 必须报 no_valid_rows，不能返回空 list
res = scan(build(["xyz", "abc", "???"]))
check("日期全乱码返回 skipped", isinstance(res, dict) and res.get("skipped") == "no_valid_rows", repr(res)[:140])
if isinstance(res, dict):
    check("日期全乱码 dropped=3", res.get("dropped") == 3, res.get("dropped"))

# ④ 空工作表（只有表头）
res = scan(build([]))
check("空表返回 empty_sheet", isinstance(res, dict) and res.get("skipped") == "empty_sheet", repr(res)[:140])

# ⑤ 部分丢弃：2 行正常 + 1 行乱码
res = scan(build(["2026-07-01", "2026-07-02", "乱码"]))
check("混合输入返回 list", isinstance(res, list) and len(res) == 1, repr(res)[:140])
if isinstance(res, list) and res:
    check("混合输入 dropped_rows=1", res[0].get("dropped_rows") == 1, res[0].get("dropped_rows"))
    check("混合输入 date_count=2", res[0]["date_count"] == 2, res[0]["date_count"])

print()
print(f"结果：PASS {PASS}   FAIL {FAIL}")
sys.exit(1 if FAIL else 0)
