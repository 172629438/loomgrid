# -*- coding: utf-8 -*-
"""
Dispatch Engines
================

三种调度求解：贪心、逐日仿真、线性规划。

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
import datetime


INVALID_MINUTES = 9999


MINUTES_PER_HOUR = 60


MINUTES_PER_DAY = 1440


TIME_CACHE_MAXSIZE = 20000


BESS_SOC_MIN = 0.05


BESS_SOC_MAX = 0.95


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


def simulate_daily_dispatch(load_array, time_labels, periods, capacity_kwh, power_kw,
                             efficiency=0.9, initial_soc=0.2, soc_min=BESS_SOC_MIN, soc_max=BESS_SOC_MAX,
                             transformer_capacity=None):
    """v2.16: 逐日充放电调度仿真 - 含 SOC 上下限保护 + 变压器负载校验.
    默认 DOD=90%, SOC范围10%~90%, 初始SOC=20%.
    v2.16: 新增 transformer_capacity 参数,充电时校验 当前负荷+充电功率 <= 变压器容量x0.9,
    超限则降功率或暂停充电,防止变压器过载.
    """
    n_days, n_points = load_array.shape
    # 零容量保护
    if capacity_kwh <= 0 or power_kw <= 0:
        return {'soc_trajectory': np.zeros((n_days, max(n_points, 2))), 'charge_kwh': np.zeros(n_days),
                'discharge_kwh': np.zeros(n_days), 'final_soc': 0.0,
                'transformer_limit_events': 0}
    if n_points < 2:
        return {'soc_trajectory': np.zeros((n_days, 2)), 'charge_kwh': np.zeros(n_days),
                'discharge_kwh': np.zeros(n_days), 'final_soc': 0.0,
                'transformer_limit_events': 0}
    hours = np.array([_parse_min(_normalize_time_column_name(t)) / 60.0 for t in time_labels])
    soc_trajectory = np.zeros((n_days, n_points + 1))
    charge_kwh = np.zeros(n_days)
    discharge_kwh = np.zeros(n_days)
    soc = initial_soc * capacity_kwh
    soc_low = soc_min * capacity_kwh
    soc_high = soc_max * capacity_kwh
    # v2.16: 变压器安全上限
    xfmr_limit = (transformer_capacity * 0.9) if (transformer_capacity and transformer_capacity > 0) else None
    xfmr_events = 0

    expanded = _expand_overnight(periods) if periods else []
    for d in range(n_days):
        soc_trajectory[d, 0] = soc / capacity_kwh * 100
        for t in range(n_points):
            # 判断当前时刻的充放电类型
            hour = hours[t]
            seg_type = None
            for p in expanded:
                if p['start'] <= hour < p['end'] or (p['start'] > p['end'] and (hour >= p['start'] or hour < p['end'])):
                    seg_type = p['type']
                    break
            load = load_array[d, t]
            if np.isnan(load): continue
            dt = 1.0 / n_points * 24  # 时间步长(小时)
            if seg_type in (PeriodType.CHARGE, PeriodType.CHARGE_LABEL):
                charge_power = min(power_kw, (soc_high - soc) / dt) if dt > 0 and soc < soc_high else 0
                # v2.16: 变压器负载校验 - 当前负荷+充电功率不得超过变压器安全上限
                if xfmr_limit is not None and charge_power > 0:
                    available_headroom = max(0, xfmr_limit - load)
                    if charge_power > available_headroom:
                        charge_power = 0.0 if available_headroom <= 0.01 else min(charge_power, available_headroom)
                        xfmr_events += 1
                soc += charge_power * dt * efficiency
                charge_kwh[d] += charge_power * dt
            elif seg_type in (PeriodType.DISCHARGE, PeriodType.DISCHARGE_LABEL):
                discharge_power = min(power_kw, (soc - soc_low) / dt) if dt > 0 and soc > soc_low else 0
                soc -= discharge_power * dt / efficiency
                discharge_kwh[d] += discharge_power * dt
            soc = max(soc_low, min(soc_high, soc))
            soc_trajectory[d, t + 1] = soc / capacity_kwh * 100

    return {'soc_trajectory': soc_trajectory, 'charge_kwh': charge_kwh,
            'discharge_kwh': discharge_kwh, 'final_soc': soc / capacity_kwh * 100,
            'transformer_limit_events': xfmr_events}


def greedy_dispatch_annual(
    load_kw: np.ndarray,
    pv_kw: np.ndarray | None = None,
    wind_kw: np.ndarray | None = None,
    batt_kwh: float = 500.0,
    c_rate: float = 0.5,
    eta_roundtrip: float = 0.90,
    soc_init_frac: float = 0.5,
    soc_min_frac: float = 0.1,
    soc_max_frac: float = 0.95,
    dt_hours: float = 0.25,
) -> dict:
    """v2.19 SSS: 纯Python贪心调度引擎 - 逐时遍历,新能源优先.

    调度逻辑(每时步按序执行):
        1) 新能源直供负荷(direct = min(renewable, load))
        2) 多余电优先充电(受SOC上限和Pmax约束)
        3) 缺额优先放电(受SOC下限和Pmax约束)
        4) 未消纳新能源计入弃电
        5) 剩余缺额从电网购电

    Args:
        load_kw: 逐时负荷(kW), shape (n_timesteps,)
        pv_kw: 逐时光伏出力(kW), shape (n_timesteps,), None->全零
        wind_kw: 逐时风电出力(kW), shape (n_timesteps,), None->全零
        batt_kwh: 电池容量(kWh)
        c_rate: 充放电倍率(C-rate), 0.5=2小时充满
        eta_roundtrip: 往返效率, 默认0.90
        soc_init_frac: 初始SOC比例
        soc_min_frac: SOC下限比例
        soc_max_frac: SOC上限比例
        dt_hours: 时间步长(h), 默认0.25=15分钟

    Returns:
        {soc, grid_import, bess_charge, bess_discharge, curtailment,
         direct_use, total_gen, total_load, total_import, total_curtail}

    Note:
        - 仅适配新能源消纳优先场景,峰谷套利请使用 optimal_dispatch_lp()
        - dt_hours=0时自动回退0.25h(v2.19安全修复)
        - SOC约束: 充放电后夹具 soc = max(soc_min, min(soc_max, soc))
    """
    # v2.29fix: dt_hours入口校验,防止SOC更新与功率限制使用不同步长
    if dt_hours <= 0:
        raise ValueError(f"dt_hours 必须 > 0, 当前值 {dt_hours}")
    n = len(load_kw)
    if pv_kw is None:
        pv_kw = np.zeros(n)
    if wind_kw is None:
        wind_kw = np.zeros(n)

    renewable = pv_kw + wind_kw
    E = batt_kwh
    Pmax = c_rate * E

    soc_min = soc_min_frac * E
    soc_max = soc_max_frac * E
    soc = soc_init_frac * E
    eta_c = eta_roundtrip ** 0.5
    eta_d = eta_roundtrip ** 0.5

    soc_arr = np.zeros(n)
    grid_import = np.zeros(n)
    bess_ch = np.zeros(n)
    bess_dis = np.zeros(n)
    curtail = np.zeros(n)

    total_gen = 0.0
    total_load = 0.0
    total_import = 0.0
    total_curtail = 0.0
    direct_use = 0.0

    for t in range(n):
        ld = load_kw[t]
        ren = renewable[t]
        total_load += ld * dt_hours
        total_gen += ren * dt_hours

        # 1) 新能源直供
        direct = min(ren, ld)
        direct_use += direct * dt_hours
        surplus = ren - direct
        deficit = ld - direct

        # 2) 多余电充电
        ch_power = 0.0
        if surplus > 0 and soc < soc_max:
            max_ch_energy = (soc_max - soc) / eta_c
            max_ch_power = min(Pmax, max_ch_energy / dt_hours)
            ch_power = min(surplus, max_ch_power)
            soc += ch_power * eta_c * dt_hours
            bess_ch[t] = ch_power
            if surplus > ch_power:
                curtail[t] = surplus - ch_power
                total_curtail += curtail[t] * dt_hours

        # 3) 缺额放电
        dis_power = 0.0
        if deficit > 0 and soc > soc_min:
            max_dis_energy = (soc - soc_min) * eta_d
            max_dis_power = min(Pmax, max_dis_energy / dt_hours)
            dis_power = min(deficit, max_dis_power)
            soc -= dis_power * dt_hours / eta_d
            bess_dis[t] = dis_power

        # 4) 剩余缺额从电网购电
        net_deficit = deficit - dis_power
        if net_deficit > 0:
            grid_import[t] = net_deficit
            total_import += net_deficit * dt_hours

        # 夹具
        soc = max(soc_min, min(soc_max, soc))
        soc_arr[t] = soc

    return {
        "soc": soc_arr,
        "grid_import": grid_import,
        "bess_charge": bess_ch,
        "bess_discharge": bess_dis,
        "curtailment": curtail,
        "direct_use": direct_use,
        "total_gen": total_gen,
        "total_load": total_load,
        "total_import": total_import,
        "total_curtail": total_curtail,
    }


def optimal_dispatch_lp(load_kw, buy_price, sell_price, batt_kwh=500, Pmax=250,
                         eta_c=0.95, eta_d=0.95, soc_min=0.1, soc_max=0.95,
                         soc_init=0.5, dt=0.25, demand_limit=None, demand_rate=0):
    """v2.19 SSS: 线性规划全局最优储能调度.

    使用 scipy.optimize.linprog 求解考虑分时电价+需量约束的最优充放电策略.
    相比贪心算法,LP能保证全局最优解(在连续松弛下).

    决策变量 (3T+1个, T=时间步数):
        ch[t]: 充电功率(kW), dis[t]: 放电功率(kW), soc[t]: 电池SOC(kWh)
        peak: 周期内峰值需量(kW)

    目标: max sum((dis[t]*sell_price[t] - ch[t]*buy_price[t]) * dt) - peak * demand_rate

    约束:
        - 0 <= ch[t], dis[t] <= Pmax
        - soc[t+1] = soc[t] + ch[t]*eta_c*dt - dis[t]*eta_d_inv*dt
        - soc_min*batt_kwh <= soc[t] <= soc_max*batt_kwh
        - soc[0] = soc_init*batt_kwh
        - load[t] - dis[t] + ch[t] <= peak (需量约束, 可选)

    Args:
        load_kw: 逐时负荷(kW), shape (T,)
        buy_price: 购电价格(元/kWh), shape (T,)
        sell_price: 售电/节电价格(元/kWh), shape (T,)
        batt_kwh: 电池容量(kWh)
        Pmax: 最大充放电功率(kW)
        eta_c, eta_d: 充放电效率
        soc_min, soc_max: SOC比例限制
        soc_init: 初始SOC比例
        dt: 时间步长(h)
        demand_limit: 需量上限(kW), None=不约束
        demand_rate: 需量费率(元/kW/周期)

    Returns:
        {ch, dis, soc, revenue, peak_demand, feasible, message}
        若scipy不可用或无可行解,返回 feasible=False
    """
    try:
        from scipy.optimize import linprog
    except ImportError:
        return {'feasible': False, 'message': 'scipy未安装,无法使用MILP调度'}

    import numpy as np
    T = len(load_kw)
    E = batt_kwh

    # 变量: ch[0..T-1], dis[0..T-1], soc[1..T], peak(可选)
    n_demand = 1 if demand_limit is not None else 0
    n_vars = 2*T + T + n_demand  # ch + dis + soc(1..T) + peak

    # 目标系数 (linprog最小化 -> 取负最大化收益)
    # v3.2fix(2026-10-27): 原实现放电收益只按 sell_price 算。
    # sell_price=0（纯自用场景）时放电不产生任何收益，LP 就完全不碰电池——
    # 但物理上放电是替代购电，省的是 buy_price。现按「自用优先」取较高者：
    #   放电能量要么自己用掉（省 buy_price），要么卖给电网（得 sell_price）。
    c = np.zeros(n_vars)
    for t in range(T):
        c[t] = buy_price[t] * dt        # ch成本
        benefit = max(float(buy_price[t]), float(sell_price[t]))
        c[T+t] = -benefit * dt          # dis收益(负=最大化)
    if n_demand:
        c[-1] = demand_rate  # 需量成本

    # 约束: A_ub @ x <= b_ub
    A_rows, b_rows = [], []

    # SOC递推: soc[t] 定义为"第t个时段结束后的电量"
    # v3.2fix: 原实现 for t in range(T-1) 只约束到 soc[T-2], 末时段 ch[T-1]/dis[T-1]
    # 未进入任何方程, 可在最后一步凭空满功率放电而不付 SOC 代价(收益虚增);
    # 且初值绑在 soc[0] 上造成变量语义错位。现改为覆盖全部 T 个时段。
    inv_eta_d = 1.0 / eta_d if eta_d > 0 else 1.0
    for t in range(T):
        row = np.zeros(n_vars)
        row[t] = -eta_c * dt
        row[T + t] = inv_eta_d * dt
        row[2 * T + t] = 1.0
        if t > 0:
            row[2 * T + t - 1] = -1.0
        base_b = E * soc_init if t == 0 else 0.0
        # 上界: soc[t] - soc[t-1] - eta_c*dt*ch[t] + inv_eta_d*dt*dis[t] <= base_b
        A_rows.append(row.copy()); b_rows.append(base_b)
        # 下界(取负): -soc[t] + soc[t-1] + eta_c*dt*ch[t] - inv_eta_d*dt*dis[t] <= -base_b
        A_rows.append(-row); b_rows.append(-base_b)

    # SOC上下限
    for t in range(T):
        idx = 2*T + t
        # soc <= soc_max * E
        row_ub = np.zeros(n_vars); row_ub[idx] = 1.0
        A_rows.append(row_ub); b_rows.append(soc_max * E)
        # -soc <= -soc_min * E -> soc >= soc_min * E
        row_lb = np.zeros(n_vars); row_lb[idx] = -1.0
        A_rows.append(row_lb); b_rows.append(-soc_min * E)

    # 需量约束: load[t] - dis[t] + ch[t] <= peak
    if n_demand:
        peak_idx = n_vars - 1
        # v2.19fix: 峰值变量上限约束 peak <= demand_limit
        row = np.zeros(n_vars); row[peak_idx] = 1.0
        A_rows.append(row); b_rows.append(demand_limit)
        for t in range(T):
            row = np.zeros(n_vars)
            row[t] = 1.0; row[T+t] = -1.0; row[peak_idx] = -1.0
            A_rows.append(row); b_rows.append(-load_kw[t])

    # 变量上下界
    bounds = [(0, Pmax)] * (2*T)  # ch, dis
    bounds += [(soc_min*E, soc_max*E)] * T  # soc
    if n_demand:
        # v2.19fix: peak上界使用demand_limit而非硬编码
        bounds += [(0, demand_limit)]  # peak

    A = np.array(A_rows) if A_rows else None
    b = np.array(b_rows) if b_rows else None

    result = linprog(c, A_ub=A, b_ub=b, bounds=bounds, method='highs',
                     options={'time_limit': 120})  # v2.22fix: 防止大问题挂死GUI

    if result.success:
        x = result.x
        ch = x[:T]; dis = x[T:2*T]; soc = x[2*T:2*T+T]
        revenue = float(-result.fun)
        peak = x[-1] if n_demand else max(load_kw)
        return {'ch': ch, 'dis': dis, 'soc': soc, 'revenue': round(revenue, 2),
                'peak_demand': round(peak, 1), 'feasible': True,
                'message': f'LP最优解 (收益: {revenue:.2f}元)'}
    return {'feasible': False, 'message': result.message}
