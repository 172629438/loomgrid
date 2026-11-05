# -*- coding: utf-8 -*-
"""端到端测试的样本真值库（下划线前缀，不会被 verify_*.py 的 glob 收录）。

设计原则
--------
**同一个真值，用不同数据形态表达，结果必须一致。**

所以这里先造一份「真值」（日期 × 时刻 × 负荷），再派生出各种文件形态。
这样跨形态一致性测试才有基准，而不是各造各的数据各断言各的。

真值特性（刻意设计成能区分假设）：
- 负荷值按 `day*1000 + point` 生成，**每天的最后一个点与其他天不同**，
  这样 32 点 / 33 点的端点约定差异、24:00 归哪一天，都能被区分出来。
- 支持单日与多日、96 点与 48 点两种粒度。

形态清单
--------
长表系：long_basic / long_two_assets / long_accumulated / long_accumulated_monthly
宽表系：wide_96 / wide_pointindex / wide_transposed / wide_yearless / wide_cn / wide_cn_fen
坏数系：bad_empty / bad_header_only / bad_garbage
时段系：long_midnight（跨午夜时段用）
"""
import io
import os

import numpy as np
import pandas as pd

TIMES_96 = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 15, 30, 45)]
DATES_3 = ["2026-07-01", "2026-07-02", "2026-07-03"]


def make_truth(dates=None, n_points=96):
    """真值矩阵：load[d][p] = d*1000 + p。

    第一个点是 0，最后一个点是 d*1000+n_points-1——与其他点都不一样，
    所以丢点、错位、端点约定变化都能被检出。
    """
    dates = dates or DATES_3
    times = TIMES_96[:n_points] if n_points <= 96 else TIMES_96
    load = np.array([[d * 1000 + p for p in range(n_points)] for d in range(len(dates))],
                    dtype=float)
    return dates, times, load


def truth_stats(load):
    return {
        "days": int(load.shape[0]),
        "points": int(load.shape[1]),
        "min": float(np.nanmin(load)),
        "max": float(np.nanmax(load)),
        "sum": float(np.nansum(load)),
        "first_row_sum": float(np.nansum(load[0])),
        "last_point_of_day0": float(load[0, -1]),
    }


# ── 写文件的公共入口 ──────────────────────────────────────
def _write_csv(tmpdir, name, text):
    p = os.path.join(tmpdir, name)
    io.open(p, "w", encoding="utf-8", newline="").write(text)
    return p


def _write_xlsx(tmpdir, name, df):
    p = os.path.join(tmpdir, name)
    df.to_excel(p, index=False)
    return p


# ── 1 长表：采集时间 + 有功功率（用户的真实形态）──────────
def make_long(tmpdir, dates=None, n_points=96, asset="赵钢1#线十总降213#总有功"):
    dates, times, load = make_truth(dates, n_points)
    rows = []
    for d, date in enumerate(dates):
        for p, t in enumerate(times):
            rows.append({"采集时间": f"{date} {t}", asset: load[d, p]})
    df = pd.DataFrame(rows)
    return {
        "path": _write_xlsx(tmpdir, "long_basic.xlsx", df),
        "mapping": {"date_col": "采集时间", "time_col": "采集时间",
                    "load_col": [asset], "asset_col": None,
                    "format_type": "long", "header_row": 0, "delimiter": ",",
                    "convert_accumulated": False, "transpose": False},
        "truth": truth_stats(load),
        "dates": dates, "times": times, "load": load,
        "expect": {"groups": len(dates), "date_count": 1, "time_points": n_points},
    }


# ── 2 长表：两个资产 ──────────────────────────────────────
def make_long_two_assets(tmpdir, dates=None, n_points=96):
    dates, times, load = make_truth(dates, n_points)
    rows = []
    for name, offset in (("A线总有功", 0.0), ("B线总有功", 10000.0)):
        for d, date in enumerate(dates):
            for p, t in enumerate(times):
                rows.append({"采集时间": f"{date} {t}", "资产编号": name,
                             "有功功率": load[d, p] + offset})
    df = pd.DataFrame(rows)
    return {
        "path": _write_xlsx(tmpdir, "long_two_assets.xlsx", df),
        "mapping": {"date_col": "采集时间", "time_col": "采集时间",
                    "load_col": ["有功功率"], "asset_col": "资产编号",
                    "format_type": "long", "header_row": 0, "delimiter": ",",
                    "convert_accumulated": False, "transpose": False},
        "truth": truth_stats(load),
        "dates": dates, "times": times, "load": load,
        "expect": {"groups": len(dates) * 2},
    }


# ── 3 宽表示数表：日期行 + 96 列累计读数（日重置，应能转成功率）──
# 为什么用宽表形态：UI 里「示数表」是宽表模式下的选项（见 MappingDialog._ok），
# 每行一天、每列一个时刻。长表形态的示数表后端也支持（format_type='long' +
# convert_accumulated=True），但 UI 没有入口——那是另一个缺口，另立用例验证。
def make_wide_accumulated(tmpdir, dates=None, n_points=96, step=4.0):
    """每行一天，96 列累计读数，每天从 100 重新累加。

    差分后应得到恒定的 step kWh/15min → 功率 = step / 0.25 h。
    首列无前一点可差，会被丢掉（96 → 95）。
    """
    dates = dates or ["2026-07-01", "2026-07-02"]
    times = TIMES_96[:n_points]
    rows = []
    for date in dates:
        row = {"日期": date}
        acc = 100.0
        for t in times:
            row[t] = acc
            acc += step
        rows.append(row)
    df = pd.DataFrame(rows)
    return {
        "path": _write_xlsx(tmpdir, "wide_accumulated.xlsx", df),
        "mapping": {"date_col": "日期", "load_col": list(times), "asset_col": None,
                    "format_type": "accumulated", "header_row": 0, "delimiter": ",",
                    "transpose": False},
        "step": step,
        "expect": {"groups": len(dates), "days": len(dates),
                   "points_after_diff": n_points - 1,
                   "power_expected": step / 0.25},
    }


# ── 4 宽表示数表：整月累计（跨天不回落，应报错而不是静默出图）──
def make_wide_accumulated_monthly(tmpdir, dates=None, n_points=96, step=4.0):
    dates = dates or ["2026-07-01", "2026-07-02", "2026-07-03"]
    times = TIMES_96[:n_points]
    rows = []
    acc = 100.0
    for date in dates:
        row = {"日期": date}
        for t in times:
            row[t] = acc
            acc += step
        rows.append(row)
    df = pd.DataFrame(rows)
    return {
        "path": _write_xlsx(tmpdir, "wide_accumulated_monthly.xlsx", df),
        "mapping": {"date_col": "日期", "load_col": list(times), "asset_col": None,
                    "format_type": "accumulated", "header_row": 0, "delimiter": ",",
                    "transpose": False},
        "expect": {"should_raise": True},
    }


# ── 4b 长表示数表：后端支持但 UI 无入口（验证能力边界，不是验证用户路径）──
def make_long_accumulated(tmpdir, dates=None, n_points=96, step=4.0):
    """采集日期 + 采集时间两列，一行一个时刻，示数日重置。

    为什么用两列而不是合并的「采集时间」：_convert_accumulated_to_power
    需要分开的 date_col / time_col 才能做差分。合并成一列时整个转换会失败，
    dropped_ratio=1.0、整表丢弃（实测过）。
    另外 _mapping_is_long_shape 要求 time_col != date_col 才认定为长表。

    需要 format_type='long' + convert_accumulated=True 才会转换。
    UI 里选长表时 convert_accumulated 恒为 False——所以这条路径用户走不到，
    但后端 _scan_long_chunk 是支持的。用来锁住「后端能力不被删掉」。
    """
    dates = dates or ["2026-07-01", "2026-07-02"]
    times = TIMES_96[:n_points]
    rows = []
    for date in dates:
        acc = 100.0
        for t in times:
            rows.append({"采集日期": date, "采集时间": t, "正向有功电度": acc})
            acc += step
    df = pd.DataFrame(rows)
    return {
        "path": _write_xlsx(tmpdir, "long_accumulated.xlsx", df),
        "mapping": {"date_col": "采集日期", "time_col": "采集时间",
                    "load_col": ["正向有功电度"], "asset_col": None,
                    "format_type": "long", "header_row": 0, "delimiter": ",",
                    "convert_accumulated": True, "transpose": False},
        "step": step,
        "expect": {"groups": len(dates), "points_after_diff": n_points - 1,
                   "power_expected": step / 0.25},
    }


# ── 5 宽表：日期行 + 96 个时刻列 ──────────────────────────
def make_wide(tmpdir, dates=None, n_points=96):
    dates, times, load = make_truth(dates, n_points)
    df = pd.DataFrame(load, columns=times)
    df.insert(0, "日期", dates)
    return {
        "path": _write_xlsx(tmpdir, "wide_96.xlsx", df),
        "mapping": {"date_col": "日期", "load_col": list(times), "asset_col": None,
                    "format_type": "wide", "header_row": 0, "delimiter": ",",
                    "transpose": False},
        "truth": truth_stats(load),
        "dates": dates, "times": times, "load": load,
        "expect": {"groups": len(dates), "time_points": n_points},
    }


# ── 6 宽表：列名是点序号 1..96 ────────────────────────────
def make_wide_pointindex(tmpdir, dates=None, n_points=96):
    dates, times, load = make_truth(dates, n_points)
    cols = [str(i) for i in range(1, n_points + 1)]
    df = pd.DataFrame(load, columns=cols)
    df.insert(0, "日期", dates)
    return {
        "path": _write_xlsx(tmpdir, "wide_pointindex.xlsx", df),
        "mapping": {"date_col": "日期", "load_col": cols, "asset_col": None,
                    "format_type": "wide", "header_row": 0, "delimiter": ",",
                    "transpose": False},
        "truth": truth_stats(load),
        "dates": dates, "times": times, "load": load,
        "expect": {"groups": len(dates), "time_points": n_points,
                   "labels_head": "00:00", "labels_tail": "23:45"},
    }


# ── 7 转置宽表：日期在行、时刻在列，但日期是"多余"的那一列 ──
def make_wide_transposed(tmpdir, dates=None, n_points=48):
    """转置形态：第一列是时刻，其余列名是日期。"""
    dates = dates or ["2026-07-01", "2026-07-02"]
    times = TIMES_96[:n_points]
    data = {"时刻": times}
    for d, date in enumerate(dates):
        data[date] = [d * 1000 + p for p in range(n_points)]
    df = pd.DataFrame(data)
    load = np.array([[data[date][p] for p in range(n_points)] for date in dates], dtype=float)
    return {
        "path": _write_xlsx(tmpdir, "wide_transposed.xlsx", df),
        "mapping": {"date_col": dates[0], "time_col": "时刻",
                    "load_col": list(dates), "asset_col": None,
                    "format_type": "wide_transposed", "header_row": 0,
                    "delimiter": ",", "transpose": True},
        "truth": truth_stats(load),
        "dates": dates, "times": times, "load": load,
        "expect": {"groups": len(dates)},
    }


# ── 8 宽表：无年份日期 ────────────────────────────────────
def make_wide_yearless(tmpdir, n_points=96):
    dates = ["7月1日", "7月2日"]
    times = TIMES_96[:n_points]
    load = np.array([[d * 1000 + p for p in range(n_points)] for d in range(len(dates))],
                    dtype=float)
    df = pd.DataFrame(load, columns=times)
    df.insert(0, "日期", dates)
    return {
        "path": _write_xlsx(tmpdir, "wide_yearless.xlsx", df),
        "mapping": {"date_col": "日期", "load_col": list(times), "asset_col": None,
                    "format_type": "wide", "header_row": 0, "delimiter": ",",
                    "transpose": False},
        "truth": truth_stats(load),
        "dates": dates, "times": times, "load": load,
        "expect": {"groups": len(dates), "year_prefix": True},
    }


# ── 9/10 宽表：中文日期，带与不带「份」──────────────────
def make_wide_cn(tmpdir, with_fen=False, n_points=96):
    dates = ["2026年7月份" if with_fen else "2026年7月",
             "2026年8月份" if with_fen else "2026年8月"]
    times = TIMES_96[:n_points]
    load = np.array([[d * 1000 + p for p in range(n_points)] for d in range(len(dates))],
                    dtype=float)
    df = pd.DataFrame(load, columns=times)
    df.insert(0, "日期", dates)
    name = "wide_cn_fen.xlsx" if with_fen else "wide_cn.xlsx"
    return {
        "path": _write_xlsx(tmpdir, name, df),
        "mapping": {"date_col": "日期", "load_col": list(times), "asset_col": None,
                    "format_type": "wide", "header_row": 0, "delimiter": ",",
                    "transpose": False},
        "truth": truth_stats(load),
        "dates": dates, "times": times, "load": load,
        "expect": {"groups": len(dates)},
    }


# ── 11/12/13 坏数据 ───────────────────────────────────────
_BAD_MAPPING = {"date_col": "日期", "load_col": ["00:00"], "asset_col": None,
                "format_type": "wide", "header_row": 0, "delimiter": ",",
                "transpose": False}


def make_bad_empty(tmpdir):
    return {"path": _write_csv(tmpdir, "bad_empty.csv", ""),
            "mapping": dict(_BAD_MAPPING),
            "expect": {"should_not_scan": True}}


def make_bad_header_only(tmpdir):
    return {"path": _write_csv(tmpdir, "bad_header_only.csv",
                               "日期,00:00,00:15,00:30,00:45\n"),
            "mapping": dict(_BAD_MAPPING),
            "expect": {"should_skip_empty": True}}


def make_bad_garbage(tmpdir):
    lines = ["日期,00:00,00:15,00:30,00:45"] + ["乱码,1,2,3,4"] * 3
    return {"path": _write_csv(tmpdir, "bad_garbage.csv", "\n".join(lines) + "\n"),
            "mapping": dict(_BAD_MAPPING),
            "expect": {"should_skip_no_valid_rows": True}}


# ── 14 跨午夜时段用数据（22:00-06:00）────────────────────
def make_midnight(tmpdir, n_points=96):
    dates, times, load = make_truth(["2026-07-01"], n_points)
    df = pd.DataFrame(load, columns=times)
    df.insert(0, "日期", dates[0])
    return {
        "path": _write_xlsx(tmpdir, "midnight.xlsx", df),
        "mapping": {"date_col": "日期", "load_col": list(times), "asset_col": None,
                    "format_type": "wide", "header_row": 0, "delimiter": ",",
                    "transpose": False},
        "truth": truth_stats(load),
        "dates": dates, "times": times, "load": load,
        "expect": {"groups": 1},
    }
