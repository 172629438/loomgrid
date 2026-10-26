# -*- coding: utf-8 -*-
"""
Charge / Discharge Space
========================

给定负荷曲线与时段策略，计算每个时段可充、可放的电量空间。

本文件由 Load Curve Studio (LoomGrid) 主程序剥离而来，保留原始逻辑，仅去除 GUI 依赖。
原始出处：负荷曲线批量生成Ultra.py v3.2

依赖：numpy

"""

import math
import numpy as np


def compute_daily_spaces(load_array, time_labels, periods, billing=None, mode='A',
                         rated_power=None, ignore_negative=False):
    """v3.2fix: Mode A/B/C 每日充放电空间快速计算."""
    n_days, n_points = load_array.shape
    if n_points < 2:
        return np.zeros(n_days), np.zeros(n_days)
    hours = np.array([_parse_min(t)/60.0 for t in time_labels])
    dt = np.zeros(n_points)
    dt[0] = hours[1] - hours[0] if n_points > 1 else 1.0
    dt[-1] = hours[-1] - hours[-2] if n_points > 1 else 1.0
    for i in range(1, n_points-1):
        dt[i] = (hours[i+1] - hours[i-1]) / 2.0
    dt = np.maximum(dt, 0.0)
    work_load = load_array.copy()
    if ignore_negative:
        work_load = np.maximum(work_load, 0.0)
    charge = np.zeros(n_days)
    discharge = np.zeros(n_days)
    if mode == 'A':
        if not periods or not billing or not billing.get('value'):
            return charge, discharge
        cap = billing['value']
        expanded = _expand_overnight(periods)
        charge_mask = np.zeros(n_points, dtype=bool)
        discharge_mask = np.zeros(n_points, dtype=bool)
        for p in expanded:
            start_idx = np.searchsorted(hours, p['start'], side='left')
            end_idx = np.searchsorted(hours, p['end'], side='right')
            if p['type'] in (PeriodType.CHARGE, PeriodType.CHARGE_LABEL):
                charge_mask[start_idx:end_idx] = True
            else:
                discharge_mask[start_idx:end_idx] = True
        charge = np.sum(np.maximum(cap - work_load, 0) * charge_mask * dt, axis=1) * CHARGING_EFFICIENCY
        pos_load = np.maximum(work_load, 0)
        discharge = np.sum(np.minimum(pos_load, cap) * discharge_mask * dt, axis=1) * DISCHARGING_EFFICIENCY
    elif mode == 'B':
        if rated_power is None or rated_power <= 0:
            return charge, discharge
        neg_load = np.maximum(-work_load, 0)
        charge = np.sum(np.minimum(rated_power, neg_load) * dt, axis=1) * CHARGING_EFFICIENCY
        pos_load = np.maximum(work_load, 0)
        discharge = np.sum(np.minimum(rated_power, pos_load) * dt, axis=1) * DISCHARGING_EFFICIENCY
    elif mode == 'C':
        if rated_power is None or rated_power <= 0:
            return charge, discharge
        discharge_mask = np.zeros(n_points, dtype=bool)
        if periods:
            expanded = _expand_overnight(periods)
            for p in expanded:
                if p['type'] in (PeriodType.DISCHARGE, PeriodType.DISCHARGE_LABEL):
                    start_idx = np.searchsorted(hours, p['start'], side='left')
                    end_idx = np.searchsorted(hours, p['end'], side='right')
                    discharge_mask[start_idx:end_idx] = True
        else:
            discharge_mask[:] = True
        pos_load = np.maximum(work_load, 0)
        discharge = np.sum(np.minimum(rated_power, pos_load) * discharge_mask * dt, axis=1) * DISCHARGING_EFFICIENCY
        charge_mask = np.zeros(n_points, dtype=bool)
        charge_mask_2d = (work_load < 0)
        if periods:
            for p in expanded:
                if p['type'] in (PeriodType.CHARGE, PeriodType.CHARGE_LABEL):
                    start_idx = np.searchsorted(hours, p['start'], side='left')
                    end_idx = np.searchsorted(hours, p['end'], side='right')
                    charge_mask[start_idx:end_idx] = True
        neg_load = np.maximum(-work_load, 0)
        charge = np.sum(np.minimum(rated_power, neg_load) * np.maximum(charge_mask, charge_mask_2d) * dt, axis=1) * CHARGING_EFFICIENCY
        if billing and billing.get('value'):
            cap = billing['value']
            charge = np.minimum(charge, np.sum(np.maximum(cap - work_load, 0) * charge_mask * dt, axis=1) * CHARGING_EFFICIENCY)
            discharge = np.minimum(discharge, np.sum(np.minimum(pos_load, cap) * discharge_mask * dt, axis=1) * DISCHARGING_EFFICIENCY)
    return charge, discharge


def compute_segmented_spaces(load_array, time_labels, segments, billing=None, mode='A',
                             rated_power=None, ignore_negative=False):
    """v2.27fix: 分充放时段计算每日能量空间. 从备份恢复.
    ⚠️ v3.2: 本函数 Ultra(200行) 与 Pro(95行) 独立实现, 修改任一侧需同步更新."""
    n_days, n_points = load_array.shape
    if n_points != len(time_labels):
        log.error(f"compute_segmented_spaces 形状不匹配: load_array={load_array.shape}, time_labels={len(time_labels)}")
        return [], [], [], []
    hours = np.array([_parse_min(_normalize_time_column_name(t)) / 60.0 for t in time_labels])
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
        # v2.27fix: 跨午夜时段(如22→2)需拆分为[22,24)和[0,2)两段
        if seg_start > seg_end:
            sub_ranges = [(seg_start, 24.0), (0.0, seg_end)]
        else:
            sub_ranges = [(seg_start, seg_end)]
        for sub_start, sub_end in sub_ranges:
            left = np.searchsorted(hours, sub_start, side='left')
            right = np.searchsorted(hours, sub_end, side='right')
            if left == right:
                space = np.zeros(n_days)
            else:
                sub_hours = hours[left:right]
                sub_load = work_load[:, left:right]
                seg_dt = _compute_time_dt(sub_hours, start_hour=sub_start, end_hour=sub_end)
                seg_dt = np.maximum(seg_dt, 0.0)
                if seg_dt.ndim == 1 and len(seg_dt) != sub_load.shape[1]:
                    log.error(f"seg_dt形状不匹配: seg_dt={seg_dt.shape}, sub_load[:,{left}:{right}]={sub_load.shape}")
                    if seg['type'] in (PeriodType.CHARGE, PeriodType.CHARGE_LABEL):
                        charge_segments.append(np.zeros(n_days))
                    else:
                        discharge_segments.append(np.zeros(n_days))
                    continue
                if seg['type'] in (PeriodType.CHARGE, PeriodType.CHARGE_LABEL):
                    if mode == 'A':
                        if cap is None:
                            space = np.zeros(n_days)
                        else:
                            headroom = np.maximum(cap - sub_load, 0)
                            # v2.29fix: Mode A充电空间加功率约束——电池C-rate限制
                            if rated_power and rated_power > 0:
                                headroom = np.minimum(rated_power, headroom)
                            space = np.sum(headroom * seg_dt, axis=1)
                    elif mode in ('B', 'C'):
                        if rated_power is None or rated_power <= 0:
                            space = np.zeros(n_days)
                        else:
                            neg_load = np.maximum(-sub_load, 0)
                            space = np.sum(np.minimum(rated_power, neg_load) * seg_dt, axis=1) * CHARGING_EFFICIENCY
                            # v3.0fix: Mode B/C 充电同时受容量线约束
                            if cap and cap > 0:
                                cap_space = np.sum(np.maximum(cap - sub_load, 0) * seg_dt, axis=1) * CHARGING_EFFICIENCY
                                space = np.minimum(space, cap_space)
                    else:
                        space = np.zeros(n_days)
                    space = np.nan_to_num(space, nan=0.0)
                    charge_segments.append(space)
                    charge_labels.append(seg['label'])
                else:
                    pos_sub_load = np.maximum(sub_load, 0)
                    if mode == 'A':
                        space = np.zeros(n_days) if cap is None else np.sum(np.minimum(pos_sub_load, cap) * seg_dt, axis=1) * DISCHARGING_EFFICIENCY
                    elif mode in ('B', 'C'):
                        if rated_power is None or rated_power <= 0:
                            space = np.zeros(n_days)
                        else:
                            space = np.sum(np.minimum(rated_power, pos_sub_load) * seg_dt, axis=1) * DISCHARGING_EFFICIENCY
                            # v3.0fix: Mode B/C 放电同时受容量线约束
                            if cap and cap > 0:
                                space = np.minimum(space, np.sum(np.minimum(cap, pos_sub_load) * seg_dt, axis=1))
                    else:
                        space = np.zeros(n_days)
                    space = np.nan_to_num(space, nan=0.0)
                    discharge_segments.append(space)
                    discharge_labels.append(seg['label'])
    return charge_segments, discharge_segments, charge_labels, discharge_labels
