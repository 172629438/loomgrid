# -*- coding: utf-8 -*-
"""端到端 · 跨形态一致性：同一份真值，不同文件形态，结果必须一样。

为什么这是最强的一组断言
------------------------
单形态测试只能证明「这个形态能跑通」，不能证明「跑对了」。
而同一份负荷数据用长表、宽表、点序号列名三种方式表达时，
解析出来的 load_array 必须在数值上逐元素相等——任何形态的解析错位、
丢点、单位错、轴顺序错，都会在这里暴露。

跑法：python tests/verify_e2e_consistency.py
"""
import os
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
sys.path.insert(0, HERE)
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np  # noqa: E402
import _e2e_fixtures as F  # noqa: E402
from core.ingest import _scan_file_worker  # noqa: E402
from core.models import GroupSummary  # noqa: E402
from services.provider import DataProvider  # noqa: E402

PASS = FAIL = 0
TMP = tempfile.mkdtemp(prefix="e2e-cons-")
DATES = ["2026-07-01", "2026-07-02"]
N_POINTS = 96


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}  {detail}")


def load_of(maker, label):
    """走完整链路取出 load_array / 标签。"""
    s = maker(TMP, dates=DATES) if maker is not F.make_wide_pointindex else maker(TMP, dates=DATES)
    res = _scan_file_worker((s["path"], "Sheet1", s["mapping"], "未命名",
                             threading.Event(), None))
    if not isinstance(res, list) or not res:
        print(f"  [{label}] 扫描失败: {str(res)[:120]}")
        return None, None, None
    summary = GroupSummary.from_dict(res[0])
    data = DataProvider().get_group_data(summary)
    if not data:
        print(f"  [{label}] 取数失败")
        return None, None, None
    return (np.asarray(data["load_array"], dtype=float),
            list(data["date_labels"]), list(data["time_labels"]))


print("=" * 78)
print("同一真值 · 三种形态")
print("=" * 78)

variants = [
    ("长表", F.make_long),
    ("宽表", F.make_wide),
    ("点序号列名", F.make_wide_pointindex),
]

results = {}
for label, maker in variants:
    arr, dates, times = load_of(maker, label)
    results[label] = (arr, dates, times)
    if arr is not None:
        print(f"  {label:<12} shape={arr.shape}  dates={len(dates)}  times={len(times)}")

base_label = "长表"
base_arr, base_dates, base_times = results[base_label]
check("基准形态（长表）取数成功", base_arr is not None)

if base_arr is not None:
    for label in ("宽表", "点序号列名"):
        arr, dates, times = results[label]
        check(f"{label}：取数成功", arr is not None)
        if arr is None:
            continue
        check(f"{label}：形状与长表一致 {base_arr.shape}", arr.shape == base_arr.shape,
              f"实际 {arr.shape}")
        if arr.shape == base_arr.shape:
            same = bool(np.allclose(base_arr, arr, equal_nan=True))
            diff = float(np.nanmax(np.abs(base_arr - arr))) if not same else 0.0
            check(f"{label}：数值逐元素相等", same, f"最大差值 {diff}")
        check(f"{label}：日期标签一致", dates == base_dates, f"{dates} vs {base_dates}")
        check(f"{label}：时刻标签一致", times == base_times, f"前3 {times[:3]} vs {base_times[:3]}")

print()
print("=" * 78)
print("转置宽表：结构不同，单独校验")
print("=" * 78)

s = F.make_wide_transposed(TMP)
res = _scan_file_worker((s["path"], "Sheet1", s["mapping"], "未命名", threading.Event(), None))
check("转置宽表：扫描返回分组", isinstance(res, list) and len(res) > 0, str(res)[:120])
if isinstance(res, list) and res:
    summary = GroupSummary.from_dict(res[0])
    data = DataProvider().get_group_data(summary)
    check("转置宽表：取数成功", data is not None)
    if data:
        arr = np.asarray(data["load_array"], dtype=float)
        check("转置宽表：二维且非空", arr.ndim == 2 and arr.size > 0, str(arr.shape))
        check("转置宽表：无负值（真值均 ≥ 0）", float(np.nanmin(arr)) >= 0.0,
              str(float(np.nanmin(arr))))
        check("转置宽表：首日首点 = 0", abs(float(arr[0][0])) < 1e-9, str(arr[0][0]))

print()
print("=" * 78)
print("月度边界：跨月文件必须分成两组，不能合并")
print("=" * 78)

s = F.make_wide(TMP, dates=["2026-07-31", "2026-08-01"])
res = _scan_file_worker((s["path"], "Sheet1", s["mapping"], "未命名", threading.Event(), None))
check("跨月两天 → 2 个分组", isinstance(res, list) and len(res) == 2,
      f"实际 {len(res) if isinstance(res, list) else res}")
if isinstance(res, list) and len(res) == 2:
    months = sorted(x["month_str"] for x in res)
    check("跨月月份正确 2026-07 / 2026-08", months == ["2026-07", "2026-08"], str(months))

print()
print("=" * 78)
print(f"结果：PASS {PASS}   FAIL {FAIL}")
print("=" * 78)
sys.exit(1 if FAIL else 0)
