# -*- coding: utf-8 -*-
"""转置宽表检测：日期列判定的样本量 + 时刻列候选范围。

两处修正：
  ① 日期列判定原来只取前 5 个值（60% 门槛 = 3 个）
     实测「备注」列前 5 行为 ['1月','2月','3月','备注','备注']，
     ratio = 0.60 过门槛 → 被误判成日期列；用 20 个样本降到 0.15
  ② 时刻列只在第 1~3 列里找
     实测「序号/区域/备注/时刻/…日期列」这种排布，时刻在第 4 位完全看不到
"""
import os
import sys

import numpy as np
import pandas as pd

REPO = r"C:\Users\LENOVO\.hanako\repos\172629438__loomgrid"
sys.path.insert(0, REPO)
sys.stdout.reconfigure(encoding="utf-8")

from core.ingest import _detect_transposed_wide, _ym_valid_ratio

ok, bad = [], []


def check(name, cond, detail=""):
    if cond:
        ok.append(name)
        print(f"  [PASS] {name}")
    else:
        bad.append(name)
        print(f"  [FAIL] {name}   {detail}")


N = 96
TIMES = [f"{i//4:02d}:{(i%4)*15:02d}" for i in range(N)]


def wide(date_count=10, extra=None):
    d = {"时刻": TIMES}
    for k in range(1, date_count + 1):
        d[f"2026-01-{k:02d}"] = np.random.default_rng(k).uniform(100, 600, N)
    if extra:
        d[extra[0]] = extra[1]
    return pd.DataFrame(d)


print("=" * 80)
print("① 正常转置宽表")
print("=" * 80)
df = wide(10)
r = _detect_transposed_wide(df, list(df.columns))
print(f"  time_col = {r['time_col'] if r else None}，"
      f"date_cols {len(r['date_cols']) if r else 0} 个")
check("识别为转置宽表", bool(r and r.get("transpose")))
check("时刻列正确", r and r["time_col"] == "时刻", r and r["time_col"])
check("10 个日期列全在", r and len(r["date_cols"]) == 10,
      r and len(r["date_cols"]))

print()
print("=" * 80)
print("② 前 5 行恰好 3 个像日期的「备注」列，不能被判成日期列")
print("=" * 80)
notes = ["1月", "2月", "3月"] + ["备注"] * (N - 3)
df2 = wide(10, extra=("备注", notes))
top5 = df2["备注"].dropna().head(5)
top20 = df2["备注"].dropna().head(20)
print(f"  前 5 行 ratio = {_ym_valid_ratio(top5):.2f}（若样本只取 5 个就会误判）")
print(f"  前 20 行 ratio = {_ym_valid_ratio(top20):.2f}")
r2 = _detect_transposed_wide(df2, list(df2.columns))
has_note = bool(r2 and "备注" in r2["date_cols"])
print(f"  检测结果含「备注」: {has_note}")
check("备注列不被判成日期列", not has_note, "仍被误判")

print()
print("=" * 80)
print("③ 时刻列在第 4 位也能识别")
print("=" * 80)
d4 = {"序号": list(range(1, N + 1)), "区域": ["A"] * N,
      "备注": ["x"] * N, "时刻": TIMES}
for k in range(1, 9):
    d4[f"2026-01-{k:02d}"] = np.random.default_rng(k).uniform(100, 600, N)
df4 = pd.DataFrame(d4)
r4 = _detect_transposed_wide(df4, list(df4.columns))
print(f"  结果: {r4['time_col'] if r4 else None}")
check("第 4 位的时刻列被识别", bool(r4) and r4["time_col"] == "时刻",
      r4 and r4.get("time_col"))
if r4:
    check("日期列数正确", len(r4["date_cols"]) == 8, len(r4["date_cols"]))

print()
print("=" * 80)
print("④ 反向：真正的长表不能被误判成转置宽表")
print("=" * 80)
# 长表：日期 / 时间 / 负荷
long_rows = []
for d in ("2026-01-01", "2026-01-02", "2026-01-03"):
    for t in TIMES:
        long_rows.append({"日期": d, "时间": t,
                          "负荷": float(np.random.default_rng(1).uniform(100, 600))})
long_df = pd.DataFrame(long_rows)
rl = _detect_transposed_wide(long_df, list(long_df.columns))
print(f"  长表检测结果: {rl}")
check("长表不被判成转置宽表", rl is None, str(rl))

print()
print("=" * 80)
print("⑤ 反向：数值列不能被当时刻列")
print("=" * 80)
# 前几列是数值（表号、户号），值域 1000~9999
d5 = {"表号": [12345] * N, "户号": ["H001"] * N}
for k in range(1, 9):
    d5[f"2026-01-{k:02d}"] = np.random.default_rng(k).uniform(100, 600, N)
df5 = pd.DataFrame(d5)
r5 = _detect_transposed_wide(df5, list(df5.columns))
print(f"  结果: {r5}")
# 表号全是 12345 → _parse_min 会当 HHMM → 无效；且唯一率 0（全同一个值）
check("数值列不被当时刻列", r5 is None or r5["time_col"] not in ("表号", "户号"),
      r5 and r5.get("time_col"))

print()
print("=" * 80)
print(f"结果：PASS {len(ok)}   FAIL {len(bad)}")
for b in bad:
    print("  -", b)
print("=" * 80)
sys.exit(1 if bad else 0)
