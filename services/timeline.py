# -*- coding: utf-8 -*-
"""时间分辨率归一：把所有月份插值到点数最多的那个粒度。

来源
----
搬自 Fusion 主程序 `ProfessionalWindow._unify_time_resolution`（v2.3.9 L4464）。
**逻辑一字不改**，只把 `self.monthly_data` 换成入参，`self` 去掉。

为什么放在 services/ 而不是继续留在 UI
--------------------------------------
三个消费者：
  · V4 月度表 —— 要在同一粒度下比较各月
  · V4 每日表 —— 同上
  · V5 导出   —— 导出的图也要同一粒度，否则一张图 96 点一张图 48 点

留在 UI 层意味着 WinUI 迁移时要在 C# 重写一遍 —— 那是第三份实现。
搬到 services/ 之后，api 层可以直接调用它。

⚠️ NaN 语义（搬的时候特意保留）
-------------------------------
`np.interp(..., left=np.nan, right=np.nan)`：插值区间之外填 NaN。
这是原实现的意图——「原来没有的时刻，宁可标缺失，也不要凭空造一个值」。

**具体位置**：从 48 点（末点 23:30）上采样到 96 点（末点 23:45）时，
**每天最后一格 23:45 超出右边界 → NaN**。所以一个 n_days 天的月份有
**n_days 个** NaN 格，不是 1 个。（占全部格子仍是 1/96，但缺失格数随天数增长。）
若上层统计「缺失率」，分子是 n_days × 1，不是 1。

数据不满一天时更明显：0:00–12:00 的组上采样后，13:00 之后全是 NaN。

下游若对该数组做 `np.nanmin` / `np.mean` / `np.sum`，NaN 会传播。
调用方要么先 `np.nan_to_num`，要么用 nan 感知的聚合函数。
（Fusion 原实现就是这么干的：`np.nan_to_num(_c[i], nan=0.0)`。）
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
            new_arr[i, :] = np.interp(
                new_hours, old_hours, arr[i, :], left=np.nan, right=np.nan)
        data["load_array"] = new_arr
        data["time_labels"] = ref_labels
        log.info("月份 %s 时间分辨率已统一为 %d 点", month, max_points)

    return monthly_data
