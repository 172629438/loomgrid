# -*- coding: utf-8 -*-
"""缓存键完整性：_load_group_data 读的每个 mapping 字段都必须进键。

这条是 v2.3.8 那次修复的补完。当时修了 header_row / delimiter 两个，
但漏了 asset_col / convert_accumulated / transpose 三个——
勾上「累计示数差分」后仍拿到未差分的旧数据。

反向证明：先断言漏字段的键确实不变（bug 存在），再断言现在会变。
"""
import os
import re
import sys

import numpy as np
import pandas as pd

REPO = r"C:\Users\LENOVO\.hanako\repos\172629438__loomgrid"
P = os.path.join(REPO, "services", "provider.py")
sys.path.insert(0, REPO)
sys.stdout.reconfigure(encoding="utf-8")

from services.provider import DataProvider
from core.models import GroupSummary

ok, bad = [], []


def check(name, cond, detail=""):
    if cond:
        ok.append(name)
        print(f"  [PASS] {name}")
    else:
        bad.append(name)
        print(f"  [FAIL] {name}   {detail}")


lines = open(P, encoding="utf-8").read().splitlines()


def body_of(name):
    st = next(i for i, l in enumerate(lines) if l.strip().startswith(f"def {name}"))
    end = next((k for k in range(st + 1, len(lines))
                if re.match(r"^    (def |@)", lines[k])), len(lines))
    return "\n".join(lines[st:end])


KEY_RE = re.compile(r"""mapping(?:\.get\(|\[)['"](\w+)['"]""")
loaded = set(KEY_RE.findall(body_of("_load_group_data")))
cached = set(KEY_RE.findall(body_of("_cache_key")))

print("=" * 76)
print("① _load_group_data 读的字段是否全进缓存键")
print("=" * 76)
print(f"  _load_group_data 读 {len(loaded)} 个: {sorted(loaded)}")
print(f"  _cache_key 含 {len(cached)} 个: {sorted(cached)}")
missing = sorted(loaded - cached)
print(f"  漏掉的: {missing if missing else '无'}")
check("读完的字段全部进键", not missing, str(missing))

print()
print("=" * 76)
print("② 逐个字段：改它必须让键变")
print("=" * 76)
base = {"format_type": "long", "date_col": "日期", "load_col": "负荷",
        "time_col": "时间", "header_row": 0, "delimiter": ","}
fp = __file__
k0 = DataProvider._cache_key(fp, "Sheet1", dict(base))
for field, val in (("asset_col", "表号"),
                   ("convert_accumulated", True),
                   ("transpose", True),
                   ("date_col", "日期2"),
                   ("time_col", "时刻"),
                   ("header_row", 1),
                   ("delimiter", "\t"),
                   ("format_type", "wide")):
    m = dict(base)
    m[field] = val
    k = DataProvider._cache_key(fp, "Sheet1", m)
    print(f"  {field:22s} → {'键变了' if k != k0 else '键没变 ← 问题'}")
    check(f"改 {field} 让键变", k != k0)

# load_col 传列表也要能区分
# 注意：_cache_key 内部做了 sorted()，顺序无关是**有意设计**（选哪几列
# 做负荷不影响结果）。但选的列集合不同必须能区分。
m1 = dict(base); m1["load_col"] = ["A", "B"]
m2 = dict(base); m2["load_col"] = ["B", "A"]
m3 = dict(base); m3["load_col"] = ["A"]
k1 = DataProvider._cache_key(fp, "S", m1)
k2 = DataProvider._cache_key(fp, "S", m2)
k3 = DataProvider._cache_key(fp, "S", m3)
print(f"  load_col [A,B] vs [B,A] → {'键相同（顺序无关，有意）' if k1 == k2 else '键不同'}")
check("load_col 顺序无关（sorted 归一化）", k1 == k2)
print(f"  load_col [A,B] vs [A]   → {'键变了' if k1 != k3 else '键相同 ← 问题'}")
check("load_col 列集合不同时键不同", k1 != k3)

print()
print("=" * 76)
print("③ 端到端：勾上差分必须真的改变返回数据")
print("=" * 76)
tmp = os.path.join(os.environ.get("TEMP", "."), "vcc_probe.csv")
rows = []
for d in ("2026-01-01", "2026-01-02"):
    for i in range(96):
        rows.append({"日期": d, "时间": f"{i//4:02d}:{(i%4)*15:02d}",
                     "负荷": 1000 + i * 4})
pd.DataFrame(rows).to_csv(tmp, index=False, encoding="utf-8-sig")

M_PLAIN = {"format_type": "long", "date_col": "日期", "load_col": "负荷",
           "time_col": "时间", "header_row": 0, "delimiter": ",",
           "convert_accumulated": False}
M_ACC = dict(M_PLAIN)
M_ACC["convert_accumulated"] = True


def gs(mapping):
    return GroupSummary(file_path=tmp, sheet_name="Sheet1", asset_value="A1",
                        month_str="2026-01", date_count=2, time_points=96,
                        min_val=0.0, max_val=0.0, mapping=mapping)


prov = DataProvider()
d_plain = prov.get_group_data(gs(M_PLAIN))
d_acc = prov.get_group_data(gs(M_ACC))
print(f"  不差分: type={type(d_plain).__name__}")
print(f"  勾差分: type={type(d_acc).__name__}")


def same(a, b):
    """逐 key 比较，数值数组与字符串数组分开处理。"""
    if not isinstance(a, dict) or not isinstance(b, dict):
        return a == b
    if list(a.keys()) != list(b.keys()):
        return False
    for k in a:
        va, vb = np.asarray(a[k]), np.asarray(b[k])
        if va.shape != vb.shape:
            return False
        if va.dtype.kind in "fc" and vb.dtype.kind in "fc":
            if not np.array_equal(va, vb, equal_nan=True):
                return False
        else:
            if not np.array_equal(va, vb):
                return False
    return True


identical = same(d_plain, d_acc)
print(f"  两次结果完全相同: {identical}")
check("勾上差分后数据确实变了（不再命中旧缓存）", not identical,
      "数值全相等 = 拿到的是旧缓存")

# 值域应该明显不同：累计从 1000 单调增到 1380，差分成 16 kW/15min
if isinstance(d_acc, dict) and "load_array" in d_acc:
    arr = np.asarray(d_acc["load_array"], dtype=float)
    mx = float(np.nanmax(arr))
    print(f"  差分后最大值: {mx:.1f}")
    check("差分后量级明显变小（不再是累计示数）", mx < 100, f"{mx}")

prov.clear()
print("  清缓存后重取（勾差分）：与第一次是否相同 →", end=" ")
d3 = prov.get_group_data(gs(M_ACC))
print(same(d_plain, d3))

try:
    os.remove(tmp)
except Exception:
    pass

print()
print("=" * 76)
print(f"结果：PASS {len(ok)}   FAIL {len(bad)}")
for b in bad:
    print("  -", b)
print("=" * 76)
sys.exit(1 if bad else 0)
