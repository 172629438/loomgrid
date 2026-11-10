# -*- coding: utf-8 -*-
"""累计示数转换：时间间隔过大时不该搿个好看的数字。

原实现在 safe_delta 上 clip(upper=3.0)。实测（真实功率恒为 100 kW）：
    间隔 4h → safe_delta 被截到 3.0 → 功率 141.67（+42%）
而负跳变判据拦不住（141.67 是正的）。

修法：间隔 > 3h 的点判为不可信，置 NaN，并打 warning。
"""
import os
import sys

import numpy as np
import pandas as pd

REPO = r"C:\Users\LENOVO\.hanako\repos\172629438__loomgrid"
sys.path.insert(0, REPO)
sys.stdout.reconfigure(encoding="utf-8")

from core.ingest import _convert_accumulated_to_power

ok, bad = [], []


def check(name, cond, detail=""):
    if cond:
        ok.append(name)
        print(f"  [PASS] {name}")
    else:
        bad.append(name)
        print(f"  [FAIL] {name}   {detail}")


def make_with_gap(gap_hours, n_before=10, n_after=10, per_hour=100.0):
    """真实功率恒为 per_hour，中间制造一个 gap_hours 的空档。"""
    rows = []
    v = 1000.0
    h = 0.0
    for _ in range(n_before):
        rows.append({"日期": "2026-01-01",
                     "时间": f"{int(h):02d}:{int((h % 1) * 60):02d}", "负荷": v})
        h += 0.25
        v += per_hour * 0.25
    h += gap_hours - 0.25
    v += per_hour * gap_hours
    for _ in range(n_after):
        rows.append({"日期": "2026-01-01",
                     "时间": f"{int(h):02d}:{int((h % 1) * 60):02d}", "负荷": v})
        h += 0.25
        v += per_hour * 0.25
    return pd.DataFrame(rows)


def convert(df):
    out = _convert_accumulated_to_power(df, "日期", "时间", "负荷")
    return pd.to_numeric(out["负荷"], errors="coerce")


print("=" * 78)
print("① 正常间隔（≤3h）功率必须准确")
print("=" * 78)
for gap in (0.25, 1.0, 2.0, 3.0):
    s = convert(make_with_gap(gap))
    vals = s.dropna().values
    med = float(np.median(vals)) if len(vals) else float("nan")
    print(f"  间隔 {gap:4.2f}h → {len(vals):2d} 个有效点，中位数 {med:.2f}（应 100）")
    check(f"间隔 {gap}h 功率准确", abs(med - 100) < 1e-6, f"{med}")

print()
print("=" * 78)
print("② 超长间隔（>3h）必须置 NaN，不能截断出一个偏高的值")
print("=" * 78)
# 反向证明：复刻旧写法，看它确实给出 141.67
df = make_with_gap(4.0)
d = df.copy()
d["__ts__"] = pd.to_datetime(d["日期"].astype(str) + " " + d["时间"].astype(str))
d = d.sort_values("__ts__")
loads = pd.to_numeric(d["负荷"])
raw_diff = loads.diff().shift(-1)
dh = d["__ts__"].diff().dt.total_seconds().div(3600).shift(-1)
old_power = raw_diff / dh.clip(lower=0.05, upper=3.0)
old_max = float(old_power.max())
print(f"  旧写法（clip upper=3.0）在该点的功率: {old_max:.2f}")
check("旧写法确实高估（bug 存在过）", old_max > 120, f"{old_max}")

s4 = convert(make_with_gap(4.0))
vals4 = s4.dropna().values
mx4 = float(vals4.max()) if len(vals4) else float("nan")
print(f"  新写法：{len(vals4)} 个有效点，最大值 {mx4:.2f}")
check("新写法不再出现偏高值", mx4 <= 100 + 1e-6, f"{mx4}")
if len(vals4):
    check("新写法剩余点仍然准确", abs(float(np.median(vals4)) - 100) < 1e-6)

for gap in (6.0, 8.0):
    s = convert(make_with_gap(gap))
    vals = s.dropna().values
    mx = float(vals.max()) if len(vals) else float("nan")
    print(f"  间隔 {gap}h → 最大 {mx:.2f}")
    check(f"间隔 {gap}h 不高估", mx <= 100 + 1e-6, f"{mx}")

print()
print("=" * 78)
print("③ 超长间隔那条 warning 要说清是几小时")
print("=" * 78)
import io
import logging

buf = io.StringIO()
h = logging.StreamHandler(buf)
h.setLevel(logging.WARNING)
lg = logging.getLogger("core.ingest")
lg.addHandler(h)
lg.setLevel(logging.WARNING)
convert(make_with_gap(5.0))
lg.removeHandler(h)
msgs = [l for l in buf.getvalue().splitlines() if "超过 3 小时" in l]
print(f"  捕获 {len(msgs)} 条 warning")
for m in msgs:
    print(f"    {m[:110]}")
check("有 warning", bool(msgs))
if msgs:
    check("warning 里写明间隔小时数", "5.0" in msgs[0], msgs[0][:80])
    check("warning 不含 np.float64 这类实现细节",
          "np.float64" not in msgs[0], msgs[0][:80])

print()
print("=" * 78)
print(f"结果：PASS {len(ok)}   FAIL {len(bad)}")
for b in bad:
    print("  -", b)
print("=" * 78)
sys.exit(1 if bad else 0)
