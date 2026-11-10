# -*- coding: utf-8 -*-
"""时间分辨率归一：把所有月份插值到点数最多的那个粒度。

来源
----
搬自 Fusion 主程序 `ProfessionalWindow._unify_time_resolution`（v2.3.9 L4464）。

⚠️ 两份实现必须同步（2026-11 补记）
--------------------------------------
v2.3.23 主程序侧改过一次：区间外从 `left/right=nan` 改成「最近有效值填充」。
理由：那些 NaN 是「插值区间外」造成的**假缺失**，不是真数据缺失；
而 `compute_segmented_spaces` 用 `np.nansum`，NaN 被当 0——
落在那个区间的充放时段空间会算成 0，容量建议跟着偏小。

本文件当时没同步，还停在旧写法（docstring 也还写着「特意保留 NaN 语义」）。
**现在与主程序对齐**。以后改任一侧，两边一起改。

为什么放在 services/ 而不是继续留在 UI
--------------------------------------
三个消费者：V4 月度表、V4 每日表、V5 导出。
留在 UI 层意味着 WinUI 迁移时要在 C# 重写一遍——那是第三份实现。
"""
from __future__ import annotations

import logging

import numpy as np

from core.timegrid import _parse_min

log = logging.getLogger(__name__)

__all__ = ["unify_time_resolution"]


def unify_time_resolution(monthly_data: dict) -> dict:
    """把所有月份插值到点数最多的那个粒度。**原地修改并返回**。

    monthly_data: {month_str: {"load_array": ndarray(天×点), "time_labels": [str], ...}}
    返回同一个 dict（便于链式调用）。
    """
    if not monthly_data:
        return monthly_data

    max_points = max(v["load_array"].shape[1] for v in monthly_data.values())
    ref_labels = None
    for v in monthly_data.values():
        if v["load_array"].shape[1] == max_points:
            ref_labels = v["time_labels"]
            break
    if ref_labels is None or max_points < 2:
        return monthly_data

    new_hours = np.array([_parse_min(t) / 60.0 for t in ref_labels])
    for month, data in monthly_data.items():
        arr = data["load_array"]
        if arr.shape[1] == max_points:
            continue
        old_hours = np.array([_parse_min(t) / 60.0 for t in data["time_labels"]])
        new_arr = np.zeros((arr.shape[0], max_points))
        for i in range(arr.shape[0]):
            row = arr[i, :]
            valid = row[~np.isnan(row)]
            if valid.size == 0:
                # 整行都是 NaN（源数据真有缺失）——保留 NaN，不伪造
                new_arr[i, :] = np.nan
                continue
            # 与主程序 v2.3.23 对齐：区间外用「最近的有效值」填充。
            # 原来的 left/right=nan 会让参考点数更密的那一侧边缘变 NaN，
            # 而 compute_segmented_spaces 用 nansum，NaN 被当 0——
            # 落在那个区间的充放时段空间会算成 0，容量建议跟着偏小。
            new_arr[i, :] = np.interp(
                new_hours, old_hours, row,
                left=float(valid[0]), right=float(valid[-1]))
        data["load_array"] = new_arr
        data["time_labels"] = ref_labels
        log.info("月份 %s 时间分辨率已统一为 %d 点（边缘用最近有效值）",
                 month, max_points)

    return monthly_data
