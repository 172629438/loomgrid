# -*- coding: utf-8 -*-
"""导出文件名：消毒与反查匹配。

两个缺陷（v2.3.11 之前）
------------------------
① `export_midwide` 直接用 `summary.asset_value` 拼文件名，未消毒。
   资产列的值只经 `_normalize_asset`（strip + 空值替换），不做字符过滤。
   用户表里写 `A/B栋` → 路径插斜杠 → OSError 或文件落到意外子目录。
   `plot_group` 走了 `_sanitize_name`，只有这条漏了。

② `generate_report` 用 `base.startswith(_sanitize_name(asset))` 反查。
   资产 `1` 会匹配 `10_202601_负荷曲线.png`，那一行的表号/月份/源文件全取错。

跑法：python tests/verify_export_naming.py
"""
import io
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np  # noqa: E402

from core.models import GroupSummary  # noqa: E402
from plotting.naming import generate_filename  # noqa: E402
from services.export import (  # noqa: E402
    DEFAULT_FILENAME_TEMPLATE, export_midwide, generate_report,
)

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}  {detail}")


def mk(asset, month="2026-07"):
    return GroupSummary(
        file_path=rf"C:\src\{asset}.xlsx", sheet_name="Sheet1",
        asset_value=asset, month_str=month, date_count=1, time_points=4,
        min_val=0.0, max_val=0.0,
        mapping={"date_col": "日期", "load_col": ["00:00"], "format_type": "wide"},
    )


def data():
    return {"load_array": np.zeros((1, 4)), "date_labels": ["2026-07-01"],
            "time_labels": ["00:00", "00:15", "00:30", "00:45"]}


out = tempfile.mkdtemp(prefix="lgm-exp-")

# ── ① 中间宽表：非法字符必须被消毒 ──────────────────────────
p = export_midwide(mk("A/B栋"), data(), out)
base = os.path.basename(p)
check("含斜杠的资产名写出成功", os.path.exists(p), p)
check("文件名里没有斜杠", "/" not in base and "\\" not in base, base)
check("斜杠被替换成下划线", "A_B栋" in base, base)

p2 = export_midwide(mk("1#楼:东"), data(), out)
check("含 # 与冒号的资产名写出成功", os.path.exists(p2), p2)
check("冒号被替换", ":" not in os.path.basename(p2), os.path.basename(p2))

# 正常资产名不应变形
p3 = export_midwide(mk("1002"), data(), out)
check("正常资产名保持原样", os.path.basename(p3).startswith("中间宽表_1002_"),
      os.path.basename(p3))

# ── ② 报告反查：资产 1 不得吃掉 10 ──────────────────────────
s1 = mk("1", "2026-01")
s10 = mk("10", "2026-02")


def expect_name(s):
    ctx = {"year_month": s.month_str, "table_no": s.asset_value,
           "asset": s.asset_value, "sheet": s.sheet_name}
    return generate_filename(DEFAULT_FILENAME_TEMPLATE, ctx)


name10 = expect_name(s10)
rp = generate_report([name10], [], [s1, s10], out)
html = io.open(rp, encoding="utf-8").read()

check("报告里出现了 10 的文件名", name10 in html, name10)
# 关键判据：该行三列里，表号必须是 10、月份必须是 2026-02
row_ok = ("<td>10</td>" in html) and ("<td>2026-02</td>" in html)
check("该行表号/月份取的是 10 而不是 1", row_ok,
      "html 里未同时出现 <td>10</td> 与 <td>2026-02</td>")
check("不再出现被 1 吃掉的迹象", "<td>2026-01</td>" not in html)

# 反向：只有 1 时仍能正确匹配
rp2 = generate_report([expect_name(s1)], [], [s1], out)
html2 = io.open(rp2, encoding="utf-8").read()
check("只有资产 1 时匹配正确",
      "<td>1</td>" in html2 and "<td>2026-01</td>" in html2)

# 找不到对应 summary 时显示 ?，而不是乱配
rp3 = generate_report(["不存在的文件.png"], [], [s1], out)
html3 = io.open(rp3, encoding="utf-8").read()
check("未匹配时显示 ?", "<td>?</td>" in html3)

print()
print(f"结果：PASS {PASS}   FAIL {FAIL}")
sys.exit(1 if FAIL else 0)
