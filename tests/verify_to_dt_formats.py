# -*- coding: utf-8 -*-
"""日期解析全格式验证。

重点：24:00 这个写法在斜杠/点分日期下会失败——Excel 导出的 CSV 常是
「2026/1/1 24:00」，两者一组合，每天的最后一个采样点静默消失
（96 点变 95 点，容量测算少算最后一刻钟）。

反向证明：先断言旧实现在三种非连字符写法下确实给 NaT。
"""
import sys

import numpy as np
import pandas as pd

REPO = r"C:\Users\LENOVO\.hanako\repos\172629438__loomgrid"
sys.path.insert(0, REPO)
sys.stdout.reconfigure(encoding="utf-8")

from core.ingest import _to_dt

ok, bad = [], []


def check(name, cond, detail=""):
    if cond:
        ok.append(name)
        print(f"  [PASS] {name}")
    else:
        bad.append(name)
        print(f"  [FAIL] {name}   {detail}")


def one(v):
    return _to_dt(pd.Series([v])).iloc[0]


print("=" * 78)
print("① 24:00 在四种日期写法下都要解析（旧实现只有连字符能过）")
print("=" * 78)
# 反向证明：旧正则只认连字符
OLD_RE = r"(\d{4}-\d{1,2}-\d{1,2})"
for raw in ("2026/1/1 24:00", "2026.1.1 24:00"):
    got = pd.Series([raw]).astype(str).str.extract(OLD_RE)[0].iloc[0]
    print(f"  旧正则对 {raw:18s} 提取到: {got}")
    check(f"旧正则确实提取失败：{raw}", pd.isna(got), str(got))

for raw, desc in (
    ("2026-01-01 24:00", "连字符"),
    ("2026/1/1 24:00", "斜杠不补零"),
    ("2026/01/01 24:00", "斜杠补零"),
    ("2026.1.1 24:00", "点分"),
    ("2026年1月1日 24:00", "中文"),
    ("1/1 24:00", "无年份斜杠"),
):
    r = one(raw)
    expect = pd.Timestamp("2026-01-02")
    okv = pd.notna(r) and pd.Timestamp(r).normalize() == expect
    print(f"  {desc:12s} {raw:22s} → {r}")
    check(f"24:00 解析正确：{desc}", okv, str(r))

print()
print("=" * 78)
print("② 一天 96 点 + 末点 24:00：四种写法都要 97/97")
print("=" * 78)
for date_fmt, desc in (
    ("2026-01-01", "连字符"),
    ("2026/01/01", "斜杠"),
    ("2026.01.01", "点分"),
    ("2026年1月1日", "中文"),
):
    vals = [f"{date_fmt} {h:02d}:{m:02d}"
            for h in range(24) for m in (0, 15, 30, 45)]
    vals.append(f"{date_fmt} 24:00")
    out = _to_dt(pd.Series(vals))
    n_ok = int(out.notna().sum())
    print(f"  {desc:12s} {n_ok}/{len(vals)}")
    check(f"97 点全解析：{desc}", n_ok == len(vals), f"{n_ok}/{len(vals)}")
    if n_ok == len(vals):
        check(f"末点落在次日 00:00：{desc}",
              pd.Timestamp(out.iloc[-1]).normalize() == pd.Timestamp("2026-01-02"))

print()
print("=" * 78)
print("③ 常规格式不能因这次修改被破坏")
print("=" * 78)
NORMAL = [
    ("2026-01-01", "2026-01-01"),
    ("2026/1/1", "2026-01-01"),
    ("2026.1.1", "2026-01-01"),
    ("2026年1月1日", "2026-01-01"),
    ("2026-01-01 23:45", "2026-01-01 23:45"),
    ("2026/1/1 23:45", "2026-01-01 23:45"),
    ("2026-01-01 00:00", "2026-01-01 00:00"),
]
for raw, expect in NORMAL:
    r = one(raw)
    same = pd.notna(r) and pd.Timestamp(r) == pd.Timestamp(expect)
    got = str(pd.Timestamp(r)) if pd.notna(r) else "NaT"
    print(f"  {raw:22s} → {got}  {'✓' if same else '✗ 期望 ' + expect}")
    check(f"常规格式：{raw}", same, got)

print()
print("=" * 78)
print("④ 边界与异常")
print("=" * 78)
check("空字符串 → NaT（不抛）", pd.isna(one("")))
check("纯文字 → NaT（不抛）", pd.isna(one("abc")))
check("None → NaT（不抛）", pd.isna(one(None)))
check("2026-13-45 非法日期 → NaT", pd.isna(one("2026-13-45")))
check("23:59 不被误当 24:00",
      pd.Timestamp(one("2026-01-01 23:59")) == pd.Timestamp("2026-01-01 23:59"))

# Excel 日期序列号（数字）
r_num = _to_dt(pd.Series([45658])).iloc[0]
print(f"  Excel 序列号 45658 → {r_num}")
check("Excel 序列号不崩（值可能不对，取决于调用方）", True)

# 全 NaN 列
try:
    r_nan = _to_dt(pd.Series([np.nan, np.nan]))
    check("全 NaN 不抛", r_nan.isna().all())
except Exception as e:
    check("全 NaN 不抛", False, f"{type(e).__name__}: {e}")

# 空 Series
try:
    r_empty = _to_dt(pd.Series([], dtype=object))
    check("空 Series 不抛", len(r_empty) == 0)
except Exception as e:
    check("空 Series 不抛", False, f"{type(e).__name__}: {e}")

print()
print("=" * 78)
print(f"结果：PASS {len(ok)}   FAIL {len(bad)}")
for b in bad:
    print("  -", b)
print("=" * 78)
sys.exit(1 if bad else 0)
