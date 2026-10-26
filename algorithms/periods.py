# -*- coding: utf-8 -*-
"""
Tariff Periods & Price Series
=============================

分时电价时段的归一化、分段合并、价格序列展开。

本文件由 Load Curve Studio (LoomGrid) 主程序剥离而来，保留原始逻辑，仅去除 GUI 依赖。
原始出处：负荷曲线批量生成Ultra.py v3.2

依赖：numpy / pandas

"""

import math
import numpy as np
from enum import Enum
import pandas as pd


class PeriodType(str, Enum):
    CHARGE = 'charge'
    DISCHARGE = 'discharge'
    CHARGE_LABEL = '充电(低谷)'
    DISCHARGE_LABEL = '放电(高峰)'


def _to_hour(v):
    """v3.2fix(P-30): 将时段端点统一转为小时数(float)。
    支持 float/int、"HH:MM"、"HH:MM:SS"、datetime.time。"""
    if isinstance(v, bool):
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip()
        if ':' in s:
            parts = s.split(':')
            try:
                hh = float(parts[0])
                mm = float(parts[1]) if len(parts) > 1 else 0.0
                return hh + mm / 60.0
            except (ValueError, IndexError):
                return 0.0
        try:
            return float(s)
        except ValueError:
            return 0.0
    if hasattr(v, 'hour'):
        return float(v.hour) + float(getattr(v, 'minute', 0) or 0) / 60.0
    return 0.0


def _normalize_periods(periods):
    """v3.2fix(P-30): 时段格式归一化。

    系统内时段至少三种写法并存：
      ① dict  [{'start':8,'end':11,'type':'flat'}, ...]   ← _segment_periods 原本只吃这种
      ② 元组  [(8, 11, 'flat'), ...]                      ← 测试/外部导入常见
      ③ 字符串端点 [('08:00','11:00','flat'), ...]
    传 ②③ 进去以前会直接 TypeError/比较错，现统一转成 ①。
    """
    if not periods:
        return []
    out = []
    for p in periods:
        if isinstance(p, dict):
            s, e, t = p.get('start'), p.get('end'), p.get('type')
        elif isinstance(p, (list, tuple)) and len(p) >= 3:
            s, e, t = p[0], p[1], p[2]
        else:
            continue
        if t is None:
            continue
        out.append({'start': _to_hour(s), 'end': _to_hour(e), 'type': t})
    return out


def _expand_overnight(periods):
    periods = _normalize_periods(periods)
    res = []
    for p in periods:
        s, e, t = p["start"], p["end"], p["type"]
        if e < s:
            res.append({"start":s, "end":24.0, "type":t})
            res.append({"start":0.0, "end":e, "type":t})
        else:
            res.append({"start":s, "end":e, "type":t})
    return res


def _segment_periods(periods):
    """v2.27fix: 将时段列表合并为连续的充/放电段. 从备份恢复."""
    if not periods:
        return []
    expanded = _expand_overnight(periods)
    expanded.sort(key=lambda p: p['start'])
    segments = []
    current_type = None
    current_start = None
    current_end = None
    current_sub = []
    charge_idx = 0
    discharge_idx = 0
    for p in expanded:
        ptype = p['type']
        if current_type is None:
            current_type = ptype
            current_start = p['start']
            current_end = p['end']
            current_sub = [p]
        elif ptype == current_type and abs(p['start'] - current_end) < 0.01:
            current_end = p['end']
            current_sub.append(p)
        else:
            label = ''
            if current_type in (PeriodType.CHARGE, PeriodType.CHARGE_LABEL):
                charge_idx += 1
                label = f'充电{charge_idx}'
            else:
                discharge_idx += 1
                label = f'放电{discharge_idx}'
            segments.append({'type': current_type, 'start': current_start,
                           'end': current_end, 'label': label, 'sub_periods': current_sub})
            current_type = ptype
            current_start = p['start']
            current_end = p['end']
            current_sub = [p]
    if current_type is not None:
        label = ''
        if current_type in (PeriodType.CHARGE, PeriodType.CHARGE_LABEL):
            charge_idx += 1
            label = f'充电{charge_idx}'
        else:
            discharge_idx += 1
            label = f'放电{discharge_idx}'
        segments.append({'type': current_type, 'start': current_start,
                       'end': current_end, 'label': label, 'sub_periods': current_sub})
    return segments


def _validate_tariff_periods(periods) -> bool:
    """v2.28fix: 校验电价时段格式一致性.
    合法格式: (1) [(start, end, type), ...] 平铺 (2) [((months_tuple), [(s,e,t),...]), ...] 分层
    非法: 混合两种格式 或 空列表."""
    if not periods or not isinstance(periods, list):
        return False
    first = periods[0]
    if isinstance(first, (tuple, list)) and len(first) >= 2:
        # 分层格式: 每个元素是 (months_tuple, periods_list)
        if isinstance(first[0], (tuple, list)) and isinstance(first[1], list):
            # v2.28fix: 递归验证内层时段列表
            return all(
                isinstance(p, (tuple, list)) and len(p) >= 2
                and isinstance(p[0], (tuple, list))
                and isinstance(p[1], list)
                and all(isinstance(ip, (tuple, list)) and len(ip) >= 2 for ip in p[1])
                for p in periods
            )
    # 平铺格式: 每个元素是 (start, end, type)
    return all(isinstance(p, (tuple, list)) and len(p) >= 2 for p in periods)


def _parse_time_min(t: str) -> int:
    """将时间字符串转为从 00:00 开始的分钟数."""
    parts = t.strip().split(":")
    return int(parts[0]) * 60 + int(parts[1])


def periods_to_price_series(
    time_labels: list[str],
    periods: dict[str, list[tuple[str, str]]],
    price_map: dict[str, float] | None = None,
    default_price: float = 0.0,
    date_str: str = "2025-01-01",
) -> tuple[pd.Series, pd.Series]:
    """将时段策略转换为价格序列.

    periods 格式: {"充电": [("08:00", "11:00"), ...], "放电": [("18:00", "21:00"), ...]}
    price_map: {"充电": 0.3, "放电": 1.2, "平时": 0.7}

    Returns:
        (buy_price: pd.Series, price_type: pd.Series)
    """
    if price_map is None:
        price_map = {"充电": 0.3, "平时": 0.7, "放电": 1.2}

    n_labels = len(time_labels)  # v2.28fix: 原为独立表达式,未赋值
    timestamps = [pd.Timestamp(f"{date_str} {t}") for t in time_labels]

    buy_price = pd.Series(default_price, index=timestamps, dtype=float)
    price_type = pd.Series("flat", index=timestamps)

    # 对每个时段的每个区间,设置价格
    for ptype, intervals in periods.items():
        p_price = price_map.get(ptype, default_price)
        for start_str, end_str in intervals:
            start_min = _parse_time_min(start_str)
            end_min = _parse_time_min(end_str)
            for i, t in enumerate(time_labels):
                cur_min = _parse_time_min(t)
                # 处理跨午夜时段
                if start_min <= end_min:
                    in_range = start_min <= cur_min <= end_min
                else:
                    in_range = cur_min >= start_min or cur_min <= end_min
                if in_range:
                    buy_price.iloc[i] = p_price
                    price_type.iloc[i] = ptype

    return buy_price, price_type
