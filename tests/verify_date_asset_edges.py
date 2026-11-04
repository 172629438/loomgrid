# -*- coding: utf-8 -*-
"""日期/资产识别的两个缺口（P2-5 / P2-6）。

- P2-5 转置宽表扫描：`date_values = pd.Series(df_temp.index, index=df_temp.index)`
  把 index 设成日期字符串，而循环里 `idx` 是整数；
  `date_values.get(idx, '')` 永远取不到，`yearless_samples` 恒为空，
  前端「有 N 个日期没年份」的提示永远不出现。
- P2-6 `_extract_asset_from_name` 只排除 4 位年份；
  `20261101_负荷.xlsx` 会把 8 位日期当成表号，图表标题与报告跟着错。

跑法：python tests/verify_date_asset_edges.py
"""
import logging
import os
import sys

logging.disable(logging.CRITICAL)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd  # noqa: E402

from core.ingest import _extract_asset_from_name, _scan_wide_chunk  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}  {detail}")


# ── P2-5：转置宽表的无年份日期样本要被采到 ────────────────────
times = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 30)]
rows = [[t, 100.0 + i, 110.0 + i, 120.0 + i] for i, t in enumerate(times)]
df = pd.DataFrame(rows, columns=["时刻", "1/1", "1/2", "1/3"])
mapping = {"date_col": "1/1", "load_col": ["1/1", "1/2", "1/3"],
           "format_type": "wide_transposed", "transpose": True,
           "time_col": "时刻", "header_row": 0}
stats = {}
_scan_wide_chunk("x.xlsx", "S", mapping, df, "", stats, None)
samples = [s.get("yearless") for s in stats.values()]
check("转置扫描采到了无年份日期样本", bool(stats) and all(samples),
      f"stats={list(stats)} samples={samples}")
if samples and samples[0]:
    check("样本内容是原始 '1/1'", "1/1" in samples[0], samples[0])

# ── P2-6：8 位日期不当表号 ──────────────────────────────────
cases = [
    ("20261101_负荷.xlsx", "未命名", "8 位日期前缀"),
    ("负荷20261101.xlsx", "未命名", "8 位日期后缀"),
    ("20261101.xlsx", "未命名", "8 位日期整名"),
    ("2026年11月.xlsx", "未命名", "4 位年份（回归）"),
    ("表号1234.xlsx", "1234", "显式表号（回归）"),
    ("负荷_123456.xlsx", "123456", "6 位非日期仍当表号（回归）"),
]
for fn, want, label in cases:
    got = _extract_asset_from_name(fn, "Sheet1")
    check(f"{label}: {fn} → {want}", got == want, f"得到 {got}")

print()
print(f"结果：PASS {PASS}   FAIL {FAIL}")
sys.exit(1 if FAIL else 0)
