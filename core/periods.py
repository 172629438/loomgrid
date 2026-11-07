# -*- coding: utf-8 -*-
"""时段切分：把原始充放时段整理成「一个段一组」的可算结构。

本模块从 Fusion v2.3.9 单文件原样搬出，函数体一字未改。
搬移依据：`tools/analyze_deps.py --group periods` 实测闭包 1 个符号 / 75 行。

为什么这里只有一个函数
----------------------
`_segment_periods` 的两个搭档 `_expand_overnight` 与 `_valid_periods`
搬进了 core/timegrid.py —— 它们与时刻解析同住（`_valid_periods` 要拼错误消息、
`_expand_overnight` 直接在 0~24 的时钟轴上展开），放在一起读更顺。
`_segment_periods` 反过来依赖 `_expand_overnight`，所以本模块 import timegrid。

依赖方向：core.models（PeriodType）+ core.timegrid（_expand_overnight）。
"""
from __future__ import annotations

from core.models import PeriodType
from core.timegrid import _expand_overnight

__all__ = ["_segment_periods"]


def _segment_periods(periods):
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
        elif ptype == current_type and p['start'] <= current_end + 0.01:
            # v2.3.15fix: 原来只合并「相邻」（abs(start-end)<0.01），重叠时段会
            # 变成两个独立段。而 compute_segmented_spaces 是逐段积分再相加的，
            # 重叠区间会被算两次——实测 Mode A 下 0-8 充 + 6-12 充，充电空间
            # 从 800 涨到 1400，其中 200 是 6-8 这两小时的重复量。
            # 同方向重叠语义上就是「这些时间都算充电」，合并是对的；
            # end 取较大值，防止被更短的段截短。
            current_end = max(current_end, p['end'])
            current_sub.append(p)
        else:
            # v2.3.15fix: 不同方向重叠是矛盾配置（同一时刻既充又放），
            # 不能静默合并。界面保存时有 _valid_periods 拦，但 api/routes_calc
            # 与程序内构造的时段不过那条路，所以在这里兜底。
            if p['start'] < current_end - 0.01:
                raise ValueError(
                    f"时段重叠且方向不同：{current_start}-{current_end} 与 "
                    f"{p['start']}-{p['end']}，请修改电价策略时段")
            label = ''
            if current_type in (PeriodType.CHARGE, PeriodType.CHARGE_LABEL):
                charge_idx += 1
                label = f'充电{charge_idx}'
            else:
                discharge_idx += 1
                label = f'放电{discharge_idx}'
            segments.append({
                'type': current_type,
                'start': current_start,
                'end': current_end,
                'label': label,
                'sub_periods': current_sub
            })
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
        segments.append({
            'type': current_type,
            'start': current_start,
            'end': current_end,
            'label': label,
            'sub_periods': current_sub
        })

    # --- 跨午夜合并：首尾同类型且存在原始跨天时段时，合并回一个段 ---
    if len(segments) >= 2:
        first, last = segments[0], segments[-1]
        has_overnight = any(p['end'] < p['start'] for p in periods)
        if (has_overnight and
                first['type'] == last['type'] and
                abs(first['start'] - 0.0) < 0.01 and
                abs(last['end'] - 24.0) < 0.01):
            segments[0]['start'] = last['start']
            segments[0]['sub_periods'] = last['sub_periods'] + first['sub_periods']
            segments.pop()

    return segments
