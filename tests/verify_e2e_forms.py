# -*- coding: utf-8 -*-
"""端到端 · 单形态：每种数据形态走完整链路。

链路：读文件 → 扫描分组 → 取数 → 算充放电空间 → 出图
断言：分组数、日期数、时段数、数据形状、关键数值、图片真实落盘且非空

为什么不能只测函数
------------------
过去四个严重 bug（C9 转置误判、示数表画成累计值、24:00 丢点、
「2026年7月份」整表消失）**全都是数据形态触发的**，不是某个函数算错。
点状测试只能锁住「已经修过的那一个形态」，换形态又来一遍。

跑法：python tests/verify_e2e_forms.py
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
from core.models import GroupSummary, PlotOptions  # noqa: E402
from core.spaces import compute_segmented_spaces  # noqa: E402
from services.export import plot_group  # noqa: E402
from services.provider import DataProvider  # noqa: E402

PASS = FAIL = 0
TMP = tempfile.mkdtemp(prefix="e2e-forms-")
OUT = os.path.join(TMP, "out")
os.makedirs(OUT, exist_ok=True)


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}  {detail}")


def scan(sample, sheet="Sheet1"):
    args = (sample["path"], sheet, sample["mapping"], "未命名", threading.Event(), None)
    return _scan_file_worker(args)


def to_summaries(res):
    return [GroupSummary.from_dict(d) for d in res] if isinstance(res, list) else []


def run_chain(label, sample, sheet="Sheet1", expect_groups=None,
              expect_points=None, expect_raise=False):
    """一条完整链路。返回 (summaries, data) 供后续断言。"""
    res = scan(sample, sheet)
    if expect_raise:
        check(f"{label}：应报错而不是静默出图",
              isinstance(res, dict) and "error" in res, f"实际返回 {str(res)[:120]}")
        return None, None
    check(f"{label}：扫描返回分组列表", isinstance(res, list), f"实际 {type(res).__name__} {str(res)[:100]}")
    if not isinstance(res, list):
        return None, None
    if expect_groups is not None:
        check(f"{label}：分组数 {expect_groups}", len(res) == expect_groups, f"实际 {len(res)}")
    if not res:
        return None, None

    summaries = to_summaries(res)
    provider = DataProvider()
    data = provider.get_group_data(summaries[0])
    check(f"{label}：取数成功", data is not None, "get_group_data 返回 None")
    if not data:
        return summaries, None

    load = np.asarray(data["load_array"], dtype=float)
    check(f"{label}：load_array 是二维且非空",
          load.ndim == 2 and load.size > 0, f"shape={load.shape}")
    if expect_points is not None:
        check(f"{label}：时段数 {expect_points}", load.shape[1] == expect_points,
              f"实际 {load.shape[1]}")
    check(f"{label}：负荷无全 NaN 行", not np.all(np.isnan(load), axis=1).any())

    # 算充放电空间：给它一个简单时段（0-8 充、18-22 放）
    segs = [
        {"type": "charge", "start": 0.0, "end": 8.0, "label": "充电1", "sub_periods": []},
        {"type": "discharge", "start": 18.0, "end": 22.0, "label": "放电1", "sub_periods": []},
    ]
    try:
        c_segs, d_segs, c_lab, d_lab = compute_segmented_spaces(
            np.nan_to_num(load, nan=0.0), list(data["time_labels"]), segs,
            billing={"value": 800.0, "type": "kva"}, mode="A", rated_power=500.0)
        check(f"{label}：空间计算不崩且非负",
              all(np.nanmin(a) >= 0 for a in c_segs + d_segs if a.size))
        check(f"{label}：四个列表等长（N4 约束）",
              len(c_segs) == len(d_segs) == len(c_lab) == len(d_lab),
              f"{len(c_segs)}/{len(d_segs)}/{len(c_lab)}/{len(d_lab)}")
    except Exception as e:
        check(f"{label}：空间计算", False, f"{type(e).__name__}: {e}")
        return summaries, data

    # 出图
    try:
        # plot_group 内部直接转给 _plot_curves(opts=...)，要传 dict 不是数据类
        png = plot_group(summaries[0], data, OUT, plot_options=PlotOptions().to_dict())
        size = os.path.getsize(png) if os.path.exists(png) else 0
        check(f"{label}：出图落盘且非空（>1KB）", size > 1024, f"{png} size={size}")
    except Exception as e:
        check(f"{label}：出图", False, f"{type(e).__name__}: {e}")

    return summaries, data


# ══════════════════════════════════════════════════════════
print("=" * 78)
print("形态 1/2：长表")
print("=" * 78)

s, d = run_chain("长表·单资产", F.make_long(TMP), expect_groups=1, expect_points=96)
if d:
    check("长表·单资产：日期数 3（同月三日归一组）", d["load_array"].shape[0] == 3,
          str(d["load_array"].shape))
    check("长表·单资产：首日首点 = 0", abs(float(d["load_array"][0][0])) < 1e-9,
          str(d["load_array"][0][0]))
    check("长表·单资产：首日末点 = 95", abs(float(d["load_array"][0][-1]) - 95.0) < 1e-9,
          str(d["load_array"][0][-1]))

run_chain("长表·双资产", F.make_long_two_assets(TMP), expect_groups=2, expect_points=96)

s, d = run_chain("宽表示数表·日重置", F.make_wide_accumulated(TMP), expect_groups=1)
if d:
    check("示数表：日期数 2", d["load_array"].shape[0] == 2, str(d["load_array"].shape))
    check("示数表：96 点差分后 95 点", d["load_array"].shape[1] == 95,
          str(d["load_array"].shape))
    check("示数表：差分结果恒为 16 kW（4kWh/15min）",
          abs(float(np.nanmean(d["load_array"])) - 16.0) < 0.5,
          str(np.nanmean(d["load_array"])))

# 宽表示数表：横向差分只看行内递增，跨天回绕体现在行与行之间，不影响结果。
# 所以宽表形态下「日重置」与「月累计」的横向差分结果一样，不需要额外检测。
# （长表示数表才需要，因为那里是纵向差分、跨天回绕会被当成负功率。）

# 长表示数表：后端支持但 UI 无入口，用于锁住「后端能力不被删掉」
s, d = run_chain("长表示数表·后端能力", F.make_long_accumulated(TMP), expect_groups=1)
if d:
    arr = np.asarray(d["load_array"], dtype=float)
    check("长表示数表：95 列（末列两天均无值，pivot 丢列）", arr.shape[1] == 95, str(arr.shape))
    valid = np.sum(~np.isnan(arr), axis=1)
    check("长表示数表：每天 95 个有效点（首列无前一点可差）",
          list(valid) == [95, 95], str(list(valid)))
    check("长表示数表：有效值恒为 16 kW",
          abs(np.nanmean(arr) - 16.0) < 0.5, str(np.nanmean(arr)))
    check("长表示数表：不存在负功率尖刺（跨天伪差值已置 NaN）",
          float(np.nanmin(arr)) >= 0.0, str(float(np.nanmin(arr))))

print()
print("=" * 78)
print("形态 3/4/5：宽表")
print("=" * 78)

s, d = run_chain("宽表·96时刻", F.make_wide(TMP), expect_groups=1, expect_points=96)
if d:
    check("宽表·96时刻：日期数 3", d["load_array"].shape[0] == 3, str(d["load_array"].shape))
    check("宽表·96时刻：首末点对得上真值",
          abs(float(d["load_array"][0][0])) < 1e-9 and abs(float(d["load_array"][0][-1]) - 95.0) < 1e-9,
          f"{d['load_array'][0][0]} / {d['load_array'][0][-1]}")

s, d = run_chain("宽表·点序号1..96", F.make_wide_pointindex(TMP), expect_groups=1, expect_points=96)
if d:
    labels = list(d["time_labels"])
    check("点序号：标签首 = 00:00", str(labels[0]) == "00:00", str(labels[0]))
    check("点序号：标签末 = 23:45", str(labels[-1]) == "23:45", str(labels[-1]))
    check("点序号：数值与真值一致",
          abs(float(d["load_array"][0][-1]) - 95.0) < 1e-9, str(d["load_array"][0][-1]))

run_chain("宽表·转置", F.make_wide_transposed(TMP), expect_groups=1)
run_chain("宽表·无年份日期", F.make_wide_yearless(TMP), expect_groups=1, expect_points=96)
run_chain("宽表·中文日期", F.make_wide_cn(TMP), expect_groups=2, expect_points=96)
run_chain("宽表·中文日期带份", F.make_wide_cn(TMP, with_fen=True), expect_groups=2, expect_points=96)

print()
print("=" * 78)
print("坏数据：必须给出可识别的跳过/错误，不能静默产出")
print("=" * 78)

r = scan(F.make_bad_empty(TMP), "CSV文件")
check("空文件：不返回分组", isinstance(r, dict) and ("error" in r or r.get("skipped")),
      str(r)[:120])

r = scan(F.make_bad_header_only(TMP), "CSV文件")
check("只有表头：明确 skip", isinstance(r, dict) and r.get("skipped") == "empty_sheet",
      str(r)[:120])

r = scan(F.make_bad_garbage(TMP), "CSV文件")
check("全乱码：skip=no_valid_rows 且带丢弃计数",
      isinstance(r, dict) and r.get("skipped") == "no_valid_rows" and r.get("dropped"),
      str(r)[:120])

print()
print("=" * 78)
print(f"结果：PASS {PASS}   FAIL {FAIL}")
print(f"（临时目录 {TMP}）")
print("=" * 78)
sys.exit(1 if FAIL else 0)
