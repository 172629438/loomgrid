# -*- coding: utf-8 -*-
"""
Charge / Discharge Space
========================

给定负荷曲线与时段策略，计算每个时段可充、可放的电量空间。

本文件由 LoomGrid 主程序剥离而来，保留原始逻辑，仅去除 GUI 依赖。
原始出处：负荷曲线批量生成Ultra.py v3.2

依赖：numpy
"""

from __future__ import annotations
import math
import numpy as np
import sys
import os
import re
import json
import copy
import time
import io
import logging
import atexit
import subprocess
import traceback
import gc
import random
import ctypes
from typing import List, Any
import urllib.request
import urllib.parse
import calendar
import logging.handlers
import multiprocessing as mp
import threading
import shutil
import tempfile
import csv
from datetime import datetime, time as dt_time, date, timedelta
from collections import OrderedDict, defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
import threading as _threading
import hashlib, functools, threading
import json as _json
from contextlib import contextmanager, suppress
from enum import Enum
from loguru import logger as log
from functools import lru_cache
import types as _types
import sys as _sys
from dataclasses import dataclass
import pandas as pd
import sys as __sys
from enum import Enum
from collections import OrderedDict
import logging
import datetime

log = logging.getLogger(__name__)


BESS_EFF_CHARGE = 0.93


CHARGING_EFFICIENCY = BESS_EFF_CHARGE


BESS_EFF_DISCHARGE = 0.93


DISCHARGING_EFFICIENCY = BESS_EFF_DISCHARGE


INVALID_MINUTES = 9999


MINUTES_PER_HOUR = 60


MINUTES_PER_DAY = 1440


TIME_CACHE_MAXSIZE = 20000


class PeriodType(str, Enum):
    CHARGE = 'charge'
    DISCHARGE = 'discharge'
    CHARGE_LABEL = '充电(低谷)'
    DISCHARGE_LABEL = '放电(高峰)'


class LRUCache:
    """线程安全 LRU 缓存,含命中率统计"""
    __slots__ = ('cache', 'maxsize', '_lock', 'hits', 'misses', 'evictions')
    def __init__(self, maxsize=20000):
        self.cache = OrderedDict()
        self.maxsize = maxsize
        self._lock = threading.RLock()
        self.hits = 0
        self.misses = 0
        self.evictions = 0
    def get(self, key):
        with self._lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                self.hits += 1
                return self.cache[key]
            self.misses += 1
            return None
    def put(self, key, value):
        with self._lock:
            # v2.23: 重复key先更新热key顺序,防止高频热key被误驱逐
            if key in self.cache:
                self.cache.move_to_end(key)
            self.cache[key] = value
            while len(self.cache) > self.maxsize:
                self.cache.popitem(last=False)
                self.evictions += 1
    def clear(self):
        with self._lock:
            self.cache.clear()
    @property
    def hit_rate(self):
        total = self.hits + self.misses
        return self.hits / total if total else 0.0
    def stats(self):
        return {'size': len(self.cache), 'maxsize': self.maxsize,
                'hits': self.hits, 'misses': self.misses, 'evictions': self.evictions,
                'hit_rate': f'{self.hit_rate:.1%}'}


TIME_CACHE = LRUCache(maxsize=TIME_CACHE_MAXSIZE)


_TIME_HHMM_RE = re.compile(r'(\d{1,2}:\d{2})')


_TIME_T_SLOT_RE = re.compile(r'[TtPp](\d{1,2})')


_FULLWIDTH_TRANS_TABLE = str.maketrans(
    '０１２３４５６７８９：，　',
    '0123456789:, '
)


_RE_TIME_CN_COLON = re.compile(r'^(\d{1,2})\s*[时点](?:钟)?$')


_RE_TIME_CN_HM = re.compile(r'^(\d{1,2})\s*时\s*(\d{1,2})\s*分$')


_RE_TIME_COLON_SUFFIX = re.compile(r'^(\d{1,2})[:：](\d{2})(?:\s*[时分秒])?$')


def _normalize_time_key(key: str) -> str:
    """合并全角转换+时间列规范化,一次字符串遍历 (OPT: 1.2 times faster than two-pass)"""
    k = key.strip()
    k = k.translate(_FULLWIDTH_TRANS_TABLE)
    m = _RE_TIME_CN_COLON.match(k)
    if m:
        return f"{int(m.group(1))}:00"
    m = _RE_TIME_CN_HM.match(k)
    if m:
        return f"{int(m.group(1))}:{int(m.group(2)):02d}"
    # v2.23: 补充"X点Y分"格式匹配
    _pt_match = re.match(r'^(\d{1,2})\s*点\s*(\d{1,2})\s*分$', k)
    if _pt_match:
        return f"{int(_pt_match.group(1))}:{int(_pt_match.group(2)):02d}"
    m = _RE_TIME_COLON_SUFFIX.match(k)
    if m:
        return f"{m.group(1)}:{m.group(2)}"
    return k


_HHMM_RE_CACHED = re.compile(r'^(\d{1,2}):(\d{2})$')


_RE_AMPM = re.compile(r'(\d{1,2}):(\d{2})(?:\s*)(AM|PM|am|pm)', re.IGNORECASE)


def _parse_min(t) -> int:
    """解析时间点为分钟数。
    解析优先级(6条路径,由上至下):
      PATH-1 datetime.time 对象 → 直接取 hour*60+minute
      PATH-2 ASCII HH:MM → str.split(':') 快速路径(覆盖95%+)
      PATH-3 全角/中文格式 → _normalize_time_key 归一化后 regex
      PATH-4 浮点数(0~24) → 小数转分钟
      PATH-5 AM/PM 格式 → 正则+12h制转换
      PATH-6 T/P 槽位格式 → T1=00:00, T96=23:45
    
    注意: Settlement模块有独立的 _parse_min_stl() 简化版,仅支持 HH:MM.
    如需修改时间解析逻辑,请确保两处保持行为一致."""
    # ═══ PATH-1: datetime.time 对象 ═══
    if isinstance(t, dt_time):
        minutes = t.hour * MINUTES_PER_HOUR + t.minute
        TIME_CACHE.put(str(t), (minutes, f"{t.hour:02d}:{t.minute:02d}"))
        return minutes
    key = str(t).strip()
    # ═══ PATH-2: ASCII HH:MM 纯 Python str.split(覆盖95%+调用) ═══
    if key.isascii():
        parts = key.split(':')
        if len(parts) == 2:
            try:
                h, minute = int(parts[0]), int(parts[1])
                if h == 24 and minute == 0:
                    return MINUTES_PER_DAY
                if 0 <= h < 24 and 0 <= minute < 60:
                    return h * MINUTES_PER_HOUR + minute
            except ValueError:
                pass
    # ═══ PATH-3: SLOW PATH 全角转半角+时间列规范化 ═══
    key = _normalize_time_key(key)
    # 快速路径:标准 HH:MM 格式直接解析
    m = _HHMM_RE_CACHED.match(key)
    if m:
        h, minute = int(m.group(1)), int(m.group(2))
        if h == 24 and minute == 0:
            minutes, lbl = MINUTES_PER_DAY, '24:00'
        elif 0 <= h < 24 and 0 <= minute < 60:
            minutes, lbl = h * MINUTES_PER_HOUR + minute, key
        else:
            minutes, lbl = INVALID_MINUTES, key
        TIME_CACHE.put(key, (minutes, lbl))
        return minutes
    # 缓存查询
    cached = TIME_CACHE.get(key)
    if cached is not None:
        return cached[0]
    if key == '24:00':
        TIME_CACHE.put(key, (MINUTES_PER_DAY, '24:00'))
        return MINUTES_PER_DAY
    # ═══ PATH-4: 浮点数格式(如14.5=14:30) ═══
    try:
        val = float(key)
        if 0 <= val <= 24:
            h = int(val)
            m = int(round((val - h) * 60))
            if h == 24:
                minutes, lbl = MINUTES_PER_DAY, '24:00'
            else:
                minutes, lbl = h * MINUTES_PER_HOUR + m, f"{h:02d}:{m:02d}"
            TIME_CACHE.put(key, (minutes, lbl))
            return minutes
        TIME_CACHE.put(key, (INVALID_MINUTES, key))
        return INVALID_MINUTES
    except ValueError:
        pass
    # ═══ PATH-5: AM/PM 格式 ═══
    amp_match = _RE_AMPM.search(key)
    if amp_match:
        h = int(amp_match.group(1))
        m_val = int(amp_match.group(2))
        is_pm = amp_match.group(3).upper() == 'PM'
        if is_pm and h != 12:
            h += 12
        elif not is_pm and h == 12:
            h = 0
        if 0 <= h < 24 and 0 <= m_val < 60:
            minutes = h * MINUTES_PER_HOUR + m_val
            TIME_CACHE.put(key, (minutes, f"{h:02d}:{m_val:02d}"))
            return minutes
    # v2.28fix: 负号前缀保护——防止"-1:00"被_TiME_HHMM_RE误匹配为"1:00"
    if key.startswith('-'):
        TIME_CACHE.put(key, (INVALID_MINUTES, key))
        return INVALID_MINUTES
    # HH:MM 含其他字符
    time_match = _TIME_HHMM_RE.search(key)
    if time_match:
        parts = time_match.group(1).split(':')
        try:
            h, m_val = int(parts[0]), int(parts[1])
            if h == 24:
                minutes, lbl = MINUTES_PER_DAY, '24:00'
            else:
                minutes, lbl = h * MINUTES_PER_HOUR + m_val, f"{h:02d}:{m_val:02d}"
            TIME_CACHE.put(key, (minutes, lbl))
            return minutes
        except (ValueError, IndexError):
            pass
    # T/P 槽位格式
    # ═══ PATH-6: T/P 槽位格式(T1=00:00..T96=23:45) ═══
    slot_match = _TIME_T_SLOT_RE.fullmatch(key)
    if slot_match:
        try:
            slot = int(slot_match.group(1))
            if 1 <= slot <= 96:
                minutes = (slot - 1) * 15
                lbl = f"{minutes // MINUTES_PER_HOUR:02d}:{minutes % MINUTES_PER_HOUR:02d}"
                TIME_CACHE.put(key, (minutes, lbl))
                return minutes
        except ValueError:
            pass
    TIME_CACHE.put(key, (INVALID_MINUTES, key))
    return INVALID_MINUTES


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


def _canon_period_type(t):
    """v3.2fix(P-32): 把一个时段类型词归一化到 PeriodType 词汇表。

    charge 系：charge / valley / deep_valley / 低谷 / 深谷 / 充电(低谷) / 充电
    discharge 系：discharge / peak / sharp_peak / 高峰 / 尖峰 / 放电(高峰) / 放电
    flat 及其他：保留原值（平段不参与充放，由下游跳过）
    """
    if t is None:
        return None
    s = str(t).strip()
    low = s.lower()
    if low in ('charge', 'valley', 'deep_valley') or s in ('低谷', '深谷', '充电', '充电(低谷)'):
        return PeriodType.CHARGE.value
    if low in ('discharge', 'peak', 'sharp_peak') or s in ('高峰', '尖峰', '放电', '放电(高峰)'):
        return PeriodType.DISCHARGE.value
    return t


def _normalize_periods(periods):
    """v3.2fix(P-30/P-32): 时段格式与类型词汇表归一化。

    格式三态（系统内并存）：
      ① dict  [{'start':8,'end':11,'type':'flat'}, ...]   ← 核心函数原本只吃这种
      ② 元组  [(8, 11, 'flat'), ...]
      ③ 字符串端点 [('08:00','11:00','flat'), ...]

    类型词表两套（同一个字段两种语义）：
      A. 充放电策略词表：charge / discharge / 充电(低谷) / 放电(高峰)
      B. 电价时段词表：valley / flat / peak / 低谷 / 高峰
    核心函数（_segment_periods / compute_*_spaces）只认 A，而省份电价库存 B。
    传 B 进去时充放判断恒假 → 充电空间和放电空间都是 0，不报错、静默算零。
    这里把 B 统一翻成 A（valley→charge, peak→discharge），flat 保留原样
    （平段既不属于充也不属于放，由下游显式跳过）。
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
        out.append({'start': _to_hour(s), 'end': _to_hour(e), 'type': _canon_period_type(t)})
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


def _normalize_time_column_name(col_name: str) -> str:
    """v2.23: 增强列名识别------补全X点Y分/HH:MM:SS/T槽位等格式"""
    m = _RE_TIME_CN_COLON.match(col_name)
    if m:
        hour = int(m.group(1))
        return f"{hour}:00"
    m = _RE_TIME_CN_HM.match(col_name)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        return f"{hour}:{minute:02d}"
    # v2.23: X点Y分格式
    _pt = re.match(r'^(\d{1,2})\s*点\s*(\d{1,2})\s*分$', col_name)
    if _pt:
        return f"{int(_pt.group(1))}:{int(_pt.group(2)):02d}"
    # v2.23: HH:MM:SS -> 取前两部分
    _hms = re.match(r'^(\d{1,2})[::](\d{2}):\d{2}$', col_name)
    if _hms:
        return f"{_hms.group(1)}:{_hms.group(2)}"
    m = _RE_TIME_COLON_SUFFIX.match(col_name)
    if m:
        return f"{m.group(1)}:{m.group(2)}"
    # v2.23: T/P槽位格式 (T0->00:00, T1->00:15, P48->12:00等)
    _slot = re.match(r'^[TtPp](\d{1,2})$', col_name)
    if _slot:
        slot = int(_slot.group(1))
        if 1 <= slot <= 96:
            minutes = (slot - 1) * 15
            return f"{minutes // 60}:{minutes % 60:02d}"
    return col_name


def _compute_time_dt(hours, start_hour=None, end_hour=None):
    """v2.9a: 统一的时间步长计算,供所有空间计算函数共用.
    对内部点使用中点法,对边界点(如果提供 start/end)使用段端点补偿."""
    n = len(hours)
    dt = np.zeros(n)
    if n == 1:
        span = (end_hour or 24.0) - (start_hour or 0.0)
        dt[0] = max(span, 0.0)
        return dt
    # 边界处理
    if start_hour is not None:
        dt[0] = max(0.0, hours[0] - start_hour + (hours[1] - hours[0]) / 2.0)
    else:
        dt[0] = max(0.0, hours[1] - hours[0])
    if end_hour is not None:
        dt[-1] = max(0.0, end_hour - hours[-1] + (hours[-1] - hours[-2]) / 2.0)
    else:
        dt[-1] = max(0.0, hours[-1] - hours[-2])
    # 内部点: 中点法
    for i in range(1, n - 1):
        dt[i] = (hours[i + 1] - hours[i - 1]) / 2.0
    return np.maximum(dt, 0.0)


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
            elif p['type'] in (PeriodType.DISCHARGE, PeriodType.DISCHARGE_LABEL):
                # v3.2fix(P-32): 明确只让放电类型置放电 mask；
                # 平段（flat）不属于充也不属于放，原 else 分支会把它当放电段，导致放电空间虚高。
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
                elif seg['type'] in (PeriodType.DISCHARGE, PeriodType.DISCHARGE_LABEL):
                    # v3.2fix(P-32): 原为 else，会把平段当放电段
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
                else:
                    # v3.2fix(P-32): 平段既不充也不放。
                    # 四个列表必须同步追加（segs 与 labels 必须一一对应），否则 zip 会错位。
                    charge_segments.append(np.zeros(n_days))
                    discharge_segments.append(np.zeros(n_days))
                    charge_labels.append(seg['label'])
                    discharge_labels.append(seg['label'])
    return charge_segments, discharge_segments, charge_labels, discharge_labels
