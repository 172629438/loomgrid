# -*- coding: utf-8 -*-
"""分组缓存键必须包含列映射。

缺陷（v2.3.11 之前）
--------------------
`get_group_data` / `get_load_error` 的键是

    (file_path, sheet_name, month_str, asset_value)

不含 mapping。用户改过列映射后再取同一分组，命中旧缓存——
**列换了、图不变、测算不变**，而且没有任何提示。
用户只会觉得「改了映射没生效」或「这两列数据一样」。

正确做法是复用已有的 `_cache_key`（它已含
format_type / date_col / load_col / time_col / mtime / header_row / delimiter）。

跑法：python tests/verify_cache_key_mapping.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

from core.models import GroupSummary  # noqa: E402
from services.provider import DataProvider  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}  {detail}")


def mk(mapping, fp=r"C:\fake\a.xlsx", asset="1", month="2026-07"):
    return GroupSummary(
        file_path=fp, sheet_name="Sheet1", asset_value=asset, month_str=month,
        date_count=1, time_points=96, min_val=0.0, max_val=0.0, mapping=mapping,
    )


p = DataProvider()
base = {"load_col": "A负荷", "date_col": "日期", "format_type": "wide",
        "time_col": None, "header_row": 0, "delimiter": ","}

k_a = p._group_key(mk(base))
k_b = p._group_key(mk({**base, "load_col": "B负荷"}))
check("不同 load_col → 不同缓存键", k_a != k_b, f"{k_a} vs {k_b}")

k_a2 = p._group_key(mk(dict(base)))
check("相同 mapping → 相同缓存键", k_a == k_a2)

k_date = p._group_key(mk({**base, "date_col": "日期2"}))
check("不同 date_col → 不同缓存键", k_a != k_date)

k_hrow = p._group_key(mk({**base, "header_row": 2}))
check("不同 header_row → 不同缓存键", k_a != k_hrow)

k_fmt = p._group_key(mk({**base, "format_type": "long"}))
check("不同 format_type → 不同缓存键", k_a != k_fmt)

# 键里必须带 mapping 的痕迹：旧键长度是 4，现在应显著更长
check("缓存键不再是 4 元组（旧键形状）", len(k_a) != 4, f"len={len(k_a)}")

# 同一 summary 对象重复取键应稳定（mtime 不变时）
s = mk(base)
check("同一 summary 两次取键一致", p._group_key(s) == p._group_key(s))

# get_load_error 走的也是同一个键函数
import inspect  # noqa: E402
src = inspect.getsource(DataProvider.get_load_error)
check("get_load_error 使用 _group_key", "_group_key" in src, src.strip().splitlines()[-1:])
src2 = inspect.getsource(DataProvider.get_group_data)
check("get_group_data 使用 _group_key", "_group_key" in src2)

print()
print(f"结果：PASS {PASS}   FAIL {FAIL}")
sys.exit(1 if FAIL else 0)
