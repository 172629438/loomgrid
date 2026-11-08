# -*- coding: utf-8 -*-
"""充放电空间与容量换算。

本模块从 Fusion v2.3.9 单文件原样搬出，**函数体一字未改**。
搬移依据：`tools/analyze_deps.py --group spaces` 实测闭包 6 个符号 / 145 行、零 UI 依赖。

这一块是容量测算的核心
----------------------
`compute_segmented_spaces` 承载三次历史修复，搬移时逐字保留其注释，勿删：

  C1  无法识别的时间点不再静默丢弃（invalid_mask 显式检出并告警）
  N4  四个返回列表必须同长（充放标签一一对应，避免下游按索引错位）
  P-32 「非充非放」的类型不参与任何一侧（原实现落进 else 被当放电统计）

⚠️ Ultra 侧同名函数是独立实现（约 200 行），改这里不会自动同步过去。

依赖方向：本模块只依赖 core.models 与 core.timegrid，不反向依赖单文件。
"""
from __future__ import annotations

import logging
import numpy as np
log = logging.getLogger(__name__)

from core.models import PeriodType
from core.timegrid import _normalize_time_column_name, _parse_min

__all__ = [
    "CHARGING_EFFICIENCY",
    "DISCHARGING_EFFICIENCY",
    "compute_segmented_spaces",
    "calc_capacity_for_days",
    "calc_days_for_capacity",
]

# v3.2fix: 充放电效率(单向93%),用于容量测算
CHARGING_EFFICIENCY = 0.93
DISCHARGING_EFFICIENCY = 0.93


def compute_segmented_spaces(load_array, time_labels, segments, billing=None, mode='A',
                             rated_power=None, ignore_negative=False):
    """v3.2fix: 分充放时段计算每日能量空间.
    ⚠️ 本函数 Pro(95行) 与 Ultra(200行) 独立实现, 修改任一侧需同步更新."""
    n_days, n_points = load_array.shape
    # v2.3.15fix: 全 NaN 输入必须报错，不能静默返回全 0。
    # 原实现下，整列选错 / 差分后全无效的数据会安静地算出「容量空间为 0」，
    # 用户看到的是「这个月没有充放空间」，而不是「数据有问题」。
    # 实测：2 天 96 点全 NaN → 四个返回列表全是 0，没有任何提示。
    if load_array.size and not np.isfinite(load_array).any():
        raise ValueError(
            "负荷数据全部无效（全为 NaN 或空），无法测算。"
            "请检查列映射是否选错、或该文件是否有有效数据")
    hours = np.array([_parse_min(_normalize_time_column_name(t)) / 60.0 for t in time_labels])
    # v2.3.5fix(C1): 无法识别的时间点原本会被静默丢弃。
    # _parse_min 解析失败返回 INVALID_MINUTES(9999)，/60 = 166.65；
    # 后面 np.diff 非单调触发 argsort 把它排到末尾，而所有时段都在 0~24 内，
    # searchsorted 永远找不到它 → 该点连同负荷数据一起静默消失，无任何提示。
    # 96 点里丢 1 点，曲线看不出缺口，但日均值已经偏了。
    # 改为：显式检出、计数、警告、从 hours 与 load_array 同步剔除。
    # ⚠️ Ultra 侧同名函数是独立实现（200 行），本次未同步，需人工对齐。
    invalid_mask = hours > 24.0
    if invalid_mask.any():
        n_invalid = int(invalid_mask.sum())
        log.warning(f"有 {n_invalid} 个时刻无法识别，这些点不参与测算（可能影响日均值）")
        keep_idx = np.where(~invalid_mask)[0]
        hours = hours[keep_idx]
        load_array = load_array[:, keep_idx]
        if hours.size == 0:
            log.error("所有时刻均无法识别，无法进行测算")
            empty = np.zeros(n_days)
            return [empty], [empty], [], []
    if not np.all(np.diff(hours) >= 0):
        sort_idx = np.argsort(hours)
        hours = hours[sort_idx]
        load_array = load_array[:, sort_idx]

    work_load = load_array.copy()
    if ignore_negative:
        work_load = np.maximum(work_load, 0.0)

    charge_segments = []
    discharge_segments = []
    charge_labels = []
    discharge_labels = []

    cap = billing.get('value') if billing and billing.get('value') else None

    for seg in segments:
        seg_start = seg['start']
        seg_end = seg['end']
        overnight = seg_start > seg_end
        sub_ranges = [(seg_start, 24.0), (0.0, seg_end)] if overnight else [(seg_start, seg_end)]
        space = np.zeros(n_days)

        for s_start, s_end in sub_ranges:
            left = np.searchsorted(hours, s_start, side='left')
            right = np.searchsorted(hours, s_end, side='right')
            if left >= right:
                continue
            sub_hours = hours[left:right]
            sub_load = work_load[:, left:right]
            seg_dt = np.zeros_like(sub_hours)
            if len(sub_hours) == 1:
                seg_dt[0] = s_end - s_start
            else:
                seg_dt[0] = (sub_hours[0] + sub_hours[1]) / 2 - s_start
                seg_dt[-1] = s_end - (sub_hours[-1] + sub_hours[-2]) / 2
                for i in range(1, len(sub_hours) - 1):
                    seg_dt[i] = (sub_hours[i + 1] - sub_hours[i - 1]) / 2.0
            seg_dt = np.maximum(seg_dt, 0.0)

            if seg['type'] in (PeriodType.CHARGE, PeriodType.CHARGE_LABEL):
                if mode == 'A':
                    if cap is None:
                        space += np.zeros(n_days)
                    else:
                        space += np.sum(np.maximum(cap - sub_load, 0) * seg_dt, axis=1)
                elif mode in ('B', 'C'):
                    if rated_power is None or rated_power <= 0:
                        space += np.zeros(n_days)
                    else:
                        neg_load = np.maximum(-sub_load, 0)
                        # v2.3.16fix: 空间不再乘效率。
                        # 业务口径：空间就是「负荷曲线里还剩多少能量可搬」——
                        # 纯物理量，能搬多少就显示多少。效率是另一层的事，
                        # 在专业模式窗口里以 DOD（可自定义 50%~100%）引入，
                        # 作用于容量（可用容量 = 安装容量 × DOD），不作用于空间。
                        charge_space = np.sum(np.minimum(rated_power, neg_load) * seg_dt, axis=1)
                        # v3.0fix: Mode B/C 充电同时受容量线约束
                        if cap and cap > 0:
                            cap_space = np.sum(np.maximum(cap - sub_load, 0) * seg_dt, axis=1)
                            charge_space = np.minimum(charge_space, cap_space)
                        space += charge_space
                else:
                    space += np.zeros(n_days)
            elif seg['type'] in (PeriodType.DISCHARGE, PeriodType.DISCHARGE_LABEL):
                pos_sub_load = np.maximum(sub_load, 0)
                if mode == 'A':
                    if cap is None:
                        space += np.zeros(n_days)
                    else:
                        space += np.sum(np.minimum(pos_sub_load, cap) * seg_dt, axis=1)
                elif mode in ('B', 'C'):
                    if rated_power is None or rated_power <= 0:
                        space += np.zeros(n_days)
                    else:
                        discharge_space = np.sum(np.minimum(rated_power, pos_sub_load) * seg_dt, axis=1)
                        # v3.0fix: Mode B/C 放电同时受容量线约束
                        if cap and cap > 0:
                            cap_space = np.sum(np.minimum(pos_sub_load, cap) * seg_dt, axis=1)
                            discharge_space = np.minimum(discharge_space, cap_space)
                        space += discharge_space
                else:
                    space += np.zeros(n_days)
            else:
                # v2.3.1fix: 原为 else，非充非放的类型被当成放电段统计。
                # 与 Ultra 的 P-32 修复对齐：它既不属于充电也不属于放电。
                #
                # v2.3.9fix: 这个分支在当前枚举下不会执行——PeriodType 只有
                # charge / discharge（+两个标签别名），没有 flat；
                # _segment_periods 也不会把未配置时段填成平段。
                # 保留它是为了满足 N4「四个返回列表必须同长」，不是因为它能跑到。
                # 之前叫它「平段」，导致 C4 那次修复被误记成「平段不再占列」——
                # 实际修的是「非本方向的占位零不该显示成列」。
                space += np.zeros(n_days)

        space = np.nan_to_num(space, nan=0.0)
        # v2.3.1fix(N4): 四个列表必须同长（segs 与 labels 一一对应），
        # 原实现只向单侧追加，导致 charge/discharge 列表不等长，下游按索引取值错位。
        if seg['type'] in (PeriodType.CHARGE, PeriodType.CHARGE_LABEL):
            charge_segments.append(space)
            charge_labels.append(seg['label'])
            discharge_segments.append(np.zeros(n_days))
            discharge_labels.append(seg['label'])
        elif seg['type'] in (PeriodType.DISCHARGE, PeriodType.DISCHARGE_LABEL):
            discharge_segments.append(space)
            discharge_labels.append(seg['label'])
            charge_segments.append(np.zeros(n_days))
            charge_labels.append(seg['label'])
        else:
            charge_segments.append(np.zeros(n_days))
            discharge_segments.append(np.zeros(n_days))
            charge_labels.append(seg['label'])
            discharge_labels.append(seg['label'])

    return charge_segments, discharge_segments, charge_labels, discharge_labels


def calc_capacity_for_days(target_days, charge_kwh, discharge_kwh=None):
    """容量反推：要满足 target_days 天，电池得装多大。

    ★ 业务语义（改这个函数前必须读）
    --------------------------------
    **容量越小越容易满足。** 电池装得比当天的充放空间大，就会充不满、放不完，
    那一天就不算「满足」。所以：

        容量 c 满足某天  ⟺  c ≤ 当天的最小容量（四个空间里最小的那个）
        若 c = 第 m 小的值（无重复）→ 满足天数 = len - m + 1
        要满足 N 天 → 取第 (len-N+1) 小 → sorted[len-N]

    例：365 天要满足 330 天 → sorted[365-330] = sorted[35]（第 36 小）

    v2.3.14fix：v2.3.13 曾把它改成 sorted[target_days-1]（第 target 小），
    方向正好反了——「满足 330 天」会变成「满足 36 天」。这不只是数字错，
    是业务语义理解反了：误把「满足」当成「容量 ≥ 需求」，而实际是「容量 ≤ 空间」。
    """
    if discharge_kwh is None:
        mins = np.asarray(charge_kwh)
    else:
        mins = np.minimum(np.asarray(charge_kwh), np.asarray(discharge_kwh))
    if target_days > len(mins) or target_days <= 0:
        return 0.0
    sorted_mins = np.sort(mins)  # 升序
    # 取第 (len - target_days + 1) 小，即索引 len-target_days
    idx = max(0, len(sorted_mins) - target_days)
    return sorted_mins[idx]


def calc_days_for_capacity(capacity, charge_kwh, discharge_kwh):
    satisfied = (charge_kwh >= capacity) & (discharge_kwh >= capacity)
    return np.sum(satisfied)
