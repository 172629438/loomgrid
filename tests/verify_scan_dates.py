# -*- coding: utf-8 -*-
"""扫描层 dates 字段：长表与宽表两条路径必须都给。

`_scan_wide_chunk` 一直填 `monthly_stats[key]['dates']`，
`_scan_long_chunk` 没填——两条路径不对称。

后果：长表数据的 dates 恒为空集，`api/routes_ingest._build_days` 拿不到
「哪天有数据」，Web 版整月都显示成无数据。

实测（修复前）：单文件 5 天 96 点 → date_count=5 但 len(dates)=0。
"""
import os
import shutil
import sys
import threading

import numpy as np
import pandas as pd

REPO = r"C:\Users\LENOVO\.hanako\repos\172629438__loomgrid"
sys.path.insert(0, REPO)
sys.stdout.reconfigure(encoding="utf-8")

from core.ingest import _scan_file_worker

TMP = os.path.join(os.environ.get("TEMP", "."), "vscan_dates")
os.makedirs(TMP, exist_ok=True)

ok, bad = [], []


def check(name, cond, detail=""):
    if cond:
        ok.append(name)
        print(f"  [PASS] {name}")
    else:
        bad.append(name)
        print(f"  [FAIL] {name}   {detail}")


LONG_MAP = {
    "format_type": "long", "date_col": "日期", "time_col": "时间",
    "asset_col": "表号", "load_col": "负荷", "header_row": 0, "delimiter": ",",
}


def run(path, mapping):
    return _scan_file_worker((path, "CSV文件", mapping, {},
                              threading.Event(), {}))


def make_long(path, days, asset="A1", points=96, dup=1):
    rows = []
    for _ in range(dup):
        for d in days:
            for i in range(points):
                rows.append({
                    "日期": f"2026-01-{d:02d}",
                    "时间": f"{i//4:02d}:{(i%4)*15:02d}",
                    "表号": asset,
                    "负荷": 100.0 + i,
                })
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")
    return path


print("=" * 78)
print("① 长表：dates 必须填，且与 date_count 一致")
print("=" * 78)
p = make_long(os.path.join(TMP, "a.csv"), range(1, 6))
r = run(p, LONG_MAP)
print(f"  返回 {len(r) if isinstance(r, list) else r}")
if isinstance(r, list):
    s = r[0]
    n = len(s.get("dates") or [])
    print(f"  date_count={s['date_count']}  len(dates)={n}")
    print(f"  dates 前 3 个: {(s.get('dates') or [])[:3]}")
    check("dates 非空", n > 0, f"{n}")
    check("date_count 与 len(dates) 一致", s["date_count"] == n,
          f"{s['date_count']} vs {n}")
    check("日期格式 YYYY-MM-DD",
          all(len(x) == 10 and x[4] == "-" for x in (s.get("dates") or [])),
          str((s.get("dates") or [])[:2]))
else:
    check("扫描成功", False, str(r))

print()
print("=" * 78)
print("② 多资产：每个资产各有一份 dates")
print("=" * 78)
p2 = make_long(os.path.join(TMP, "b.csv"), range(1, 4))
d = pd.read_csv(p2)
d2 = d.copy(); d2["表号"] = "A2"
pd.concat([d, d2], ignore_index=True).to_csv(p2, index=False, encoding="utf-8-sig")
r2 = run(p2, LONG_MAP)
if isinstance(r2, list):
    print(f"  产生 {len(r2)} 个 summary")
    for s in r2:
        n = len(s.get("dates") or [])
        print(f"    {s['asset_value']}: date_count={s['date_count']}, dates={n}")
    check("两个资产都有 dates",
          all(len(s.get("dates") or []) > 0 for s in r2))
    check("各资产 dates 数一致",
          len({len(s.get("dates") or []) for s in r2}) == 1)
else:
    check("多资产扫描成功", False, str(r2))

print()
print("=" * 78)
print("③ 同一天重复：date_count=1, dates 长度=1")
print("=" * 78)
p3 = make_long(os.path.join(TMP, "c.csv"), [1], dup=3)
r3 = run(p3, LONG_MAP)
if isinstance(r3, list):
    s = r3[0]
    n = len(s.get("dates") or [])
    print(f"  date_count={s['date_count']}, dates={n}, time_points={s['time_points']}")
    check("date_count=1", s["date_count"] == 1, str(s["date_count"]))
    check("dates 长度=1", n == 1, str(n))
    check("time_points=96", s["time_points"] == 96, str(s["time_points"]))

print()
print("=" * 78)
print("④ 宽表路径对照：dates 本来就有")
print("=" * 78)
wide_rows = {"日期": ["2026-01-01", "2026-01-02"]}
for i in range(96):
    wide_rows[f"{i}"] = [100.0 + i, 200.0 + i]
pw = os.path.join(TMP, "wide.csv")
pd.DataFrame(wide_rows).to_csv(pw, index=False, encoding="utf-8-sig")
pw_map = {
    "format_type": "wide", "date_col": "日期",
    "load_col": [f"{i}" for i in range(96)],
    "header_row": 0, "delimiter": ",",
}
rw = run(pw, pw_map)
if isinstance(rw, list):
    for s in rw:
        n = len(s.get("dates") or [])
        print(f"    宽表 {s['month_str']}/{s['asset_value']}: "
              f"date_count={s['date_count']}, dates={n}")
        check("宽表也有 dates", n > 0, str(n))
else:
    print(f"  宽表结果: {rw}")

print()
print("=" * 78)
print("⑤ _build_days 能用上这些日期")
print("=" * 78)
try:
    from api.routes_ingest import _build_days
    days = _build_days("2026-01", (r[0].get("dates") or []) if isinstance(r, list) else [])
    have = [x for x in days if x.get("has_data")]
    print(f"  1 月 {len(days)} 天，其中有数据 {len(have)} 天")
    check("_build_days 认出 5 天有数据", len(have) == 5, str(len(have)))
except Exception as e:
    print(f"  调用失败: {type(e).__name__}: {e}")
    check("_build_days 可调用", False, str(e))

shutil.rmtree(TMP, ignore_errors=True)

print()
print("=" * 78)
print(f"结果：PASS {len(ok)}   FAIL {len(bad)}")
for b in bad:
    print("  -", b)
print("=" * 78)
sys.exit(1 if bad else 0)
