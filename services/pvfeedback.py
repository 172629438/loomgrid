# -*- coding: utf-8 -*-
"""光伏返送详情：逐日返送电量与最大返送功率。

**来源**：从 Fusion 主程序的 ``PVFeedbackDialog._refresh`` 原样搬入，
逻辑未改。搬入原因与 ``services/loadsim.py`` 相同：算法只能有一份实现，
进度条上的数、界面上列的数、将来报告里的数必须是同一套。

⚠️ **时间权重与 spaces.seg_dt 不是同一套，勿混用**
--------------------------------------------------
这里用的是**整格差分**（照 Fusion）：

    dt[0]   = h[1] - h[0]
    dt[-1]  = h[-1] - h[-2]
    dt[i]   = (h[i+1] - h[i-1]) / 2

而 ``core/spaces.py`` 的 ``seg_dt`` 是**首尾靠时段边界补齐**：

    seg_dt[0]  = (h0 + h1)/2 - s_start
    seg_dt[-1] = s_end - (h[-1] + h[-2])/2

**准确的差异在哪里**（实测，见 tests/verify_pv_feedback.py）：
两者对同一条时段的**总和都对齐**（0–8 点都是 8.0 h），
差的是**单点分配**：pv 首点 0.25 h，seg 首点 0.125 h。

所以：
- 算「整段总量」时两者结果相同，看不出错；
- 算「部分区间」（比如只有几个点倒送）时，首尾点的能量会算得不一样，
  **而两个数字都“看起来合理”** —— 这类差异不会报错，只能靠口径文档拦住。

**结论**：返送电量必须用本文件的 dt；也不要把本文件的 dt 拿去替代 seg_dt。
两个量在业务上相关（用户会拿返送量和充电空间对比），口径必须各自照抄各自的来源。

名词：**返送 = 负荷为负 = 电往电网倒送**。本模块统计的是「倒送出去多少」，
不是「能存下多少」—— Fusion 里也没有「可存电量」这个字段。
"""
from __future__ import annotations

import logging

import numpy as np

from core.timegrid import _normalize_time_column_name, _parse_min

log = logging.getLogger(__name__)


def _dt_weights(time_labels: list[str]) -> np.ndarray:
    """每个采样点代表的时长（小时）。整格差分，与 Fusion 一致。"""
    hours = np.array([_parse_min(_normalize_time_column_name(str(t))) / 60.0
                      for t in time_labels], dtype=float)
    n = hours.size
    dt = np.zeros(n, dtype=float)
    if n >= 2:
        dt[0] = hours[1] - hours[0]
        dt[-1] = hours[-1] - hours[-2]
        for i in range(1, n - 1):
            dt[i] = (hours[i + 1] - hours[i - 1]) / 2.0
    dt = np.maximum(dt, 0.0)
    return dt


def _is_sorted_hours(time_labels: list[str]) -> bool:
    """时刻是否单调递增。"""
    hours = [_parse_min(_normalize_time_column_name(str(t))) / 60.0
             for t in time_labels]
    return all(hours[i] <= hours[i + 1] for i in range(len(hours) - 1))


def pv_feedback(load_array, time_labels, date_labels, multiplier: float = 1.0):
    """逐日返送统计。

    Args:
        load_array: (n_days, n_points) 原始负荷（kW）
        time_labels: 采样点时刻标签
        date_labels: 每日日期标签
        multiplier: 倍率。Fusion 是**先乘倍率再算**——用户把倍率从 1.0 调到 2.0，
            负荷翻倍，返送量也该翻倍。漏乘会让数字停在旧值。

    Returns:
        dict：total_energy_kwh / max_power_kw / daily[] / negative_points /
              has_negative
    """
    arr = np.asarray(load_array, dtype=float)
    if arr.ndim != 2:
        raise ValueError("load_array 必须是二维数组")
    arr = arr * float(multiplier)

    # v2.3.35：时刻乱序时 dt 会被 clip 成 0，返送量静默少算。
    # 实测（96 点全程倒送 100 kW）：正常 2400 kWh，乱序 2325 kWh，**少 3.12%**。
    # 原因是 _dt_weights 的 `np.maximum(dt, 0.0)`：负的差分被抹成 0。
    # 这里先按时刻排序（load_array 列同步排），从根上避免。
    _hours = [_parse_min(_normalize_time_column_name(str(t))) / 60.0
              for t in time_labels]
    if any(_hours[i] > _hours[i + 1] for i in range(len(_hours) - 1)):
        _idx = sorted(range(len(_hours)), key=lambda i: _hours[i])
        log.warning("时刻不是递增顺序，已按时间重排后统计返送（原顺序会让 dt 被截成 0）")
        arr = arr[:, _idx]
        time_labels = [time_labels[i] for i in _idx]

    dt = _dt_weights(time_labels)
    n_days, n_pts = arr.shape
    if dt.size != n_pts:
        raise ValueError(f"time_labels 长度 {dt.size} 与列数 {n_pts} 不一致")

    daily = []
    total_energy = 0.0
    max_power = 0.0
    neg_points = 0

    for d in range(n_days):
        row = arr[d]
        neg = row < 0
        cnt = int(np.count_nonzero(neg))
        neg_points += cnt
        if cnt == 0:
            energy = 0.0
            peak = 0.0
        else:
            energy = float(np.sum(np.abs(row[neg]) * dt[neg]))
            peak = float(np.max(np.abs(row[neg])))
        total_energy += energy
        max_power = max(max_power, peak)
        daily.append({
            "date": date_labels[d] if d < len(date_labels) else f"第 {d + 1} 天",
            "energy_kwh": round(energy, 3),
            "max_power_kw": round(peak, 3),
        })

    return {
        "total_energy_kwh": round(total_energy, 3),
        "max_power_kw": round(max_power, 3),
        "negative_points": neg_points,
        "has_negative": neg_points > 0,
        "daily": daily,
    }
