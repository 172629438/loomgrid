# -*- coding: utf-8 -*-
"""DataProvider 与 MemoryLimitedCache 的行为验证。

为什么必须有这一套
------------------
DataProvider 原来住在主程序里，几乎没有独立测试——它总是被 UI 顺带跑到。
搬进 services 后它是 API 的取数层，任何缓存 bug 都会表现成「预览数据不对」，
而那种错误不会崩，只会静默给出旧数据或错数据。

覆盖
----
A 注入路径        inject_raw_file 落缓存、取数命中、不跨实例串用
B 缓存层次        raw_file_cache 与 group_cache 互不污染
C 失效语义        clear 后两个缓存都清空、随后能回落读盘
D 错误隔离        非法分组返回错误、不影响正常分组
E 内存淘汰        MemoryLimitedCache 按字节而不是条数淘汰

不依赖主程序，也不依赖 Qt —— 只 import services.provider 与 core。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from core.ingest import _read_file_full, _scan_file_worker, _smart_classify_columns  # noqa: E402
from core.models import GroupSummary  # noqa: E402
from services.provider import DataProvider, MemoryLimitedCache  # noqa: E402

VERBOSE = "-v" in sys.argv
PASS = FAIL = 0
FAILS = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        if VERBOSE:
            print(f"[PASS] {name}")
    else:
        FAIL += 1
        FAILS.append((name, detail))
        print(f"[FAIL] {name}  {detail}")


TMP = tempfile.mkdtemp(prefix="lg_prov_")


def make_wide_file(days=3, n_pts=96, base=400.0, name="w.xlsx"):
    """造一个标准宽表文件：日期列 + 96 个时刻列。"""
    step = 1440 // n_pts
    labels = [f"{(i * step) // 60:02d}:{(i * step) % 60:02d}" for i in range(n_pts)]
    dates = pd.date_range("2026-01-15", periods=days, freq="D").strftime("%Y-%m-%d").tolist()
    data = {"日期": dates}
    for i, lab in enumerate(labels):
        data[lab] = [base + i + d for d in range(days)]
    p = os.path.join(TMP, name)
    pd.DataFrame(data).to_excel(p, index=False)
    return p, dates, labels


def summary_for(fp, sheet="Sheet1"):
    """走真实链路拿一个 GroupSummary。"""
    df = _read_file_full(fp, sheet)
    mapping = _smart_classify_columns(list(df.columns), df)
    mapping["format_type"] = "wide"
    mapping["header_row"] = 0
    res = _scan_file_worker((fp, sheet, mapping, None, threading.Event(), None))
    return GroupSummary.from_dict(res[0]), df


# ────────────────────────────────────────────────────────────
print("A 注入路径")
fp, dates, labels = make_wide_file()
gs, df = summary_for(fp)

p1 = DataProvider()
p1.inject_raw_file(fp, "Sheet1", df)
check("A1 注入键落在 raw_file_cache", (fp, "Sheet1") in p1._raw_file_cache,
      f"键数={len(p1._raw_file_cache)}")

data = p1.get_group_data(gs)
check("A2 注入后能取到宽表", bool(data) and data.get("load_array") is not None)
check("A3 取到的形状正确", np.asarray(data["load_array"]).shape == (3, 96),
      f"{np.asarray(data['load_array']).shape}")

p_other = DataProvider()
check("A4 新实例不共享注入缓存", (fp, "Sheet1") not in p_other._raw_file_cache)

# ────────────────────────────────────────────────────────────
print("B 缓存层次")
p2 = DataProvider()
p2.inject_raw_file(fp, "Sheet1", df)
check("B1 注入不提前填充 group_cache", len(p2._group_cache) == 0,
      f"group_cache={len(p2._group_cache)}")
p2.get_group_data(gs)
check("B2 取数后 group_cache 有条目", len(p2._group_cache) > 0)
check("B3 两个缓存独立存在",
      len(p2._raw_file_cache) > 0 and len(p2._group_cache) > 0)

# ────────────────────────────────────────────────────────────
print("C 失效语义")
p2.clear()
check("C1 clear 后 raw_file_cache 清空", len(p2._raw_file_cache) == 0)
check("C2 clear 后 group_cache 清空", len(p2._group_cache) == 0)
data2 = p2.get_group_data(gs)
check("C3 clear 后回落读盘仍可取数", bool(data2) and data2.get("load_array") is not None)

# ────────────────────────────────────────────────────────────
print("D 错误隔离")
p3 = DataProvider()
bad = GroupSummary(file_path=os.path.join(TMP, "__missing__.xlsx"), sheet_name="Sheet1",
                   asset_value="X", month_str="2026-01", date_count=1,
                   time_points=96, min_val=0, max_val=1, mapping={})
p3.get_group_data(bad)
err = p3.get_load_error(bad)
check("D1 非法分组返回错误信息", bool(err), f"{str(err)[:60]}")
check("D2 非法分组不影响正常分组", bool(p3.get_group_data(gs)))

# ────────────────────────────────────────────────────────────
print("E 内存淘汰")
small = MemoryLimitedCache(max_memory_bytes=1024)
big = np.zeros(4096, dtype=np.float64)      # 32 KB，远超上限
small.put("k", big)
check("E1 超限对象不会把缓存撑爆", len(small.cache) <= 1,
      f"cache={len(small.cache)}")
small.put("k2", "tiny")
check("E2 小对象能正常放入", "k2" in small.cache)

shutil.rmtree(TMP, ignore_errors=True)

# ────────────────────────────────────────────────────────────
print()
if FAILS:
    print("失败项：")
    for n, d in FAILS:
        print(f"  - {n}  {d}")
print(f"结果：PASS {PASS}   FAIL {FAIL}")
sys.exit(1 if FAIL else 0)
