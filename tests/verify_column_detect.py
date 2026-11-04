# -*- coding: utf-8 -*-
"""列/表型识别的三处缺口（P2-1 / P2-14 / P2-15）+ 长表重复记录口径（P2-8）。

- P2-1 `_detect_file_format_type` 读表失败时返回 'long'：宽表被预置成长表，
  用户直接点确定就报「缺少列」，以为是列选错了。改为返回 None。
- P2-14 `_safe_get_col` 相似度 0.6 就自动换列：中文列名两个字相同就能过，
  「负荷1 找不到 → 用负荷2」，静默算错。阈值提到 0.85 并升级日志。
- P2-15 宽表列名是纯数字 1..96 时，`_parse_min("1")~"24"` 能过，
  25..96 全部 INVALID，于是只有前 24 列被当成时刻列，曲线静默只剩 6 小时。
  整组纯数字且最大 >24 时全部剔除，让下游明确报「没有有效的时间列」。
- P2-8 扫描阶段 `drop_duplicates` 只留首条，加载阶段却用 groupby mean，
  同一份数据的统计口径不一致。改为均值聚合并记录丢弃条数。

跑法：python tests/verify_column_detect.py
"""
import logging
import os
import shutil
import sys
import tempfile

logging.disable(logging.CRITICAL)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd  # noqa: E402

from core.ingest import (  # noqa: E402
    _detect_file_format_type, _safe_get_col, _scan_long_chunk, _valid_time_cols,
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


# ── P2-15：纯数字序号列不再被当时刻列 ────────────────────────
seq96 = [str(i) for i in range(1, 97)]
check("纯数字 1..96 列名全部剔除", _valid_time_cols(seq96) == [], _valid_time_cols(seq96))

seq24 = [str(i) for i in range(1, 25)]
check("纯数字 1..24 保持原行为（24 列当小时）", len(_valid_time_cols(seq24)) == 24)

clock = [f"{i // 4:02d}:{(i % 4) * 15:02d}" for i in range(96)]
check("HH:MM 列名 96 列全部识别", len(_valid_time_cols(clock)) == 96)

# ── P2-1：检测失败返回 None，不再伪装成长表 ──────────────────
tmp = tempfile.mkdtemp(prefix="loomgrid_detect_")
try:
    bad = os.path.join(tmp, "坏.xlsx")
    with open(bad, "wb") as f:
        f.write(b"not an excel")
    check("坏文件检测返回 None", _detect_file_format_type(bad, "Sheet1") is None,
          _detect_file_format_type(bad, "Sheet1"))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ── P2-14：相似度阈值 ────────────────────────────────────────
df = pd.DataFrame({"负荷1": [1.0], "负荷2": [2.0]})
try:
    _safe_get_col(df, "负荷3")
    check("相似度 0.67 的列不再自动替换", False, "未抛 KeyError")
except KeyError:
    check("相似度 0.67 的列不再自动替换", True)

df2 = pd.DataFrame({"正向有功功率": [1.0]})
try:
    s = _safe_get_col(df2, "正向有功功功率")   # 多一个字，相似度 ~0.93
    check("高相似度列仍然自动替换", float(s.iloc[0]) == 1.0)
except Exception as e:
    check("高相似度列仍然自动替换", False, f"{type(e).__name__}: {e}")

# ── P2-8：同一分钟两条记录按均值聚合 ─────────────────────────
rows = [
    ("2026-01-01", "00:00", 100.0),
    ("2026-01-01", "00:00", 900.0),
    ("2026-01-01", "00:15", 200.0),
    ("2026-01-01", "00:15", 200.0),
]
long_df = pd.DataFrame(rows, columns=["日期", "时间", "负荷"])
mapping = {"date_col": "日期", "time_col": "时间", "load_col": "负荷",
           "format_type": "long", "header_row": 0}
stats = {}
_scan_long_chunk("x.xlsx", "S", mapping, long_df, "", stats, None)
s = stats.get(("2026-01", "未命名"))
check("重复时间点被聚合为一行", s is not None and s.get("date_count") == 1, stats)
check("最大值取均值（500）而不是首条（100）",
      s is not None and abs(float(s.get("max_val", 0)) - 500.0) < 1e-9,
      s.get("max_val") if s else None)
check("时间点数为 2", s is not None and len(s.get("time_set", ())) == 2,
      len(s.get("time_set", ())) if s else None)

print()
print(f"结果：PASS {PASS}   FAIL {FAIL}")
sys.exit(1 if FAIL else 0)
