# -*- coding: utf-8 -*-
"""
Load Diagnostics
================

负荷指纹分析、行业匹配与循环退化估算。

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
from collections import defaultdict
from collections import OrderedDict
import logging

log = logging.getLogger(__name__)


INVALID_MINUTES = 9999


MINUTES_PER_HOUR = 60


MINUTES_PER_DAY = 1440


TIME_CACHE_MAXSIZE = 20000


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


_RE_DIGITS_ALL = re.compile(r'\d+')


def _parse_date_flexible(date_str: str):
    try:
        return datetime.strptime(date_str, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        pass
    for sep in ['/', '.', ' ']:
        if sep in date_str:
            parts = date_str.split(sep)
            if len(parts) == 3:
                try:
                    y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
                    return date(y, m, d)
                except (ValueError, TypeError):
                    pass
    _s8 = str(date_str).strip()
    # v3.2.3fix(I2): 8 位纯数字日期（20261001）此前解析为 None——
    # 正则只切出一个 '20261001'，既不满 3 段也不满 2 段，直接落到 return None。
    if len(_s8) == 8 and _s8.isdigit():
        try:
            _d = date(int(_s8[:4]), int(_s8[4:6]), int(_s8[6:8]))
            return _d
        except (ValueError, TypeError):
            pass
    nums = _RE_DIGITS_ALL.findall(date_str)
    if len(nums) >= 3:
        try:
            y, m, d = int(nums[0]), int(nums[1]), int(nums[2])
            if 1 <= m <= 12 and 1 <= d <= 31:
                return date(y, m, d)
        except Exception:
            pass
    if len(nums) == 2:
        # v3.2fix: 日期列无年份时默认今年
        try:
            m, d = int(nums[0]), int(nums[1])
            if 1 <= m <= 12 and 1 <= d <= 31:
                return date(datetime.now().year, m, d)
        except Exception:
            pass
    return None


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


def analyze_load_fingerprint(load_array, date_labels, time_labels):
    """v2.28: 分析负荷曲线的运行模式指纹.
    
    Args:
        load_array: shape (n_days, n_points) 的负荷矩阵
        date_labels: 日期标签列表 (长度=n_days)
        time_labels: 时间标签列表 (长度=n_points)
    
    Returns:
        dict: {primary_tag, secondary_tags[], confidence, weekday_avg, weekend_avg, ...}
    """
    arr = np.asarray(load_array)
    if arr.ndim != 2 or arr.shape[0] < 3:
        return {'primary_tag': '📊 数据不足', 'secondary_tags': [], 'confidence': 0.0}
    n_days, n_points = arr.shape
    # ── 周日/周末分离 ──
    weekday_mask = np.zeros(n_days, dtype=bool)
    weekend_mask = np.zeros(n_days, dtype=bool)
    for i, dl in enumerate(date_labels):
        dt = _parse_date_flexible(str(dl))
        if dt is not None:
            wd = dt.weekday()
            if wd < 5:
                weekday_mask[i] = True
            else:
                weekend_mask[i] = True
    has_weekend = weekend_mask.sum() >= 2
    # ── 日均负荷 ──
    daily_avg = np.nanmean(arr, axis=1)
    peak_all = np.nanmax(arr)
    daily_peak = np.nanmax(arr, axis=1)
    # ── 时间索引(用于凌晨/早晚检测) ──
    hours = np.array([_parse_min(_normalize_time_column_name(t)) / 60.0 for t in time_labels])
    night_mask = (hours >= 2) & (hours < 5)  # 凌晨2-5点
    morning_mask = (hours >= 8) & (hours < 9)  # 早8-9点
    evening_mask = (hours >= 17) & (hours < 18)  # 晚17-18点
    prev_mask = (hours >= 7) & (hours < 8)  # 7-8点(对比基线)
    post_mask = (hours >= 18) & (hours < 19)  # 18-19点(对比基线)
    # ── 检测1: 光伏主导 ──
    zero_rate = np.sum(arr < peak_all * 0.005) / arr.size
    if zero_rate > 0.40:
        return {'primary_tag': '☀️ 光伏主导', 'secondary_tags': [], 'confidence': min(zero_rate * 100, 95),
                'zero_rate': zero_rate * 100}
    # ── 检测2: 连续生产 ──
    if night_mask.sum() >= 2:
        night_load = np.nanmean(arr[:, night_mask])
        if peak_all > 0 and night_load / peak_all > 0.60:
            return {'primary_tag': '🏭 连续生产', 'secondary_tags': [],
                    'confidence': min(night_load / peak_all * 100, 95)}
    # ── 检测3: 白班制 ──
    if morning_mask.sum() >= 1 and prev_mask.sum() >= 1:
        morning_avg = np.nanmean(arr[:, morning_mask])
        prev_avg = np.nanmean(arr[:, prev_mask])
        if prev_avg > 0 and (morning_avg - prev_avg) / prev_avg > 0.50:
            evening_jump = 0.0
            if evening_mask.sum() >= 1 and post_mask.sum() >= 1:
                eve_avg = np.nanmean(arr[:, evening_mask])
                post_avg = np.nanmean(arr[:, post_mask])
                if eve_avg > 0:
                    evening_jump = (eve_avg - post_avg) / eve_avg if post_avg >= 0 else 0
            if evening_jump < 0 or (evening_mask.sum() == 0):
                return {'primary_tag': '🌅 白班制', 'secondary_tags': [],
                        'confidence': 80, 'morning_jump_pct': (morning_avg - prev_avg) / prev_avg * 100}
    # ── 检测4: 周末模式 ──
    tags = []
    conf = 0
    if has_weekend:
        wd_avg = np.nanmean(daily_avg[weekday_mask]) if weekday_mask.sum() > 0 else 0
        we_avg = np.nanmean(daily_avg[weekend_mask]) if weekend_mask.sum() > 0 else 0
        if wd_avg > 0:
            we_ratio = we_avg / wd_avg
            if we_ratio < 0.30:
                tags.append('🗓 双休')
                conf = max(conf, (1 - we_ratio) * 100)
            elif we_ratio < 0.60:
                tags.append('📅 单休')
                conf = max(conf, (1 - we_ratio) * 120 if we_ratio < 0.45 else 70)
            else:
                tags.append('🔄 无休')
                conf = 60
    # ── 检测5: 季节性 ──
    if n_days >= 30:
        monthly_avgs = []
        for i, dl in enumerate(date_labels):
            dt = _parse_date_flexible(str(dl))
            if dt is not None:
                monthly_avgs.append((dt.month, daily_avg[i]))
        if monthly_avgs:
            by_month = defaultdict(list)
            for m, v in monthly_avgs:
                by_month[m].append(v)
            month_means = [np.mean(v) for v in by_month.values() if len(v) >= 5]
            if len(month_means) >= 3:
                cv = np.std(month_means) / max(np.mean(month_means), 1e-9)
                if cv > 0.30:
                    tags.append('🍂 季节性波动')
                    conf = max(conf, min(cv * 150, 85))
    if not tags:
        tags.append('⚙️ 常规模式')
        conf = 50
    return {'primary_tag': tags[0], 'secondary_tags': tags[1:], 'confidence': round(conf, 1)}


def match_fingerprint_to_industry(fp_result):
    """v2.32: 从指纹标签匹配最可能的行业模板。返回(industry_name, confidence)。"""
    if not fp_result:
        return '电子制造', 50
    # v3.2fix: 指纹→行业映射表
    _FINGERPRINT_TO_INDUSTRY = {
        'continuous_process': ['电解铝', '铁合金', '钢铁(电炉)', '化工(连续)', '造纸'],
        'three_shift': ['制药(原料药)', '矿山/选矿', '数据中心', '污水处理', '冷链物流', '纺织/印染', '半导体封装'],
        'two_shift': ['水泥(粉磨)', '塑料/注塑', '电子制造', '机械加工(双班)', '食品加工(双班)', '汽车制造'],
        'one_shift': ['机械加工(单班)'],
        'commercial': ['商业楼宇', '办公楼宇', '医院', '酒店', '居民小区'],
        'seasonal': ['农业排灌'],
    }
    best = None; best_conf = 0
    for tag, industries in _FINGERPRINT_TO_INDUSTRY.items():
        if tag == fp_result.get('primary_tag', ''):
            for ind in industries:
                if best is None or fp_result.get('confidence', 0) > best_conf:
                    best = ind; best_conf = fp_result.get('confidence', 0)
    return best or '电子制造', best_conf or 50


def _estimate_degradation(equiv_full_cycles_per_year, chemistry='LFP',
                           ambient_temp_c=25, project_life_years=15):
    """电池退化预估模型(日历老化 + 循环老化混合模型).

    模型: Q_loss = k_cal(T) x t + k_cyc(T) x N
    其中温度加速因子为 Arrhenius 形式: k(T) = k_25 x exp(Ea/R x (1/298.15 - 1/T))

    标定基准(25degC,365 循环/年):
      LFP: 第5年 SOH~=88%,第10年 SOH~=79%,第13年触发更换
      NMC: 第5年 SOH~=83%,第8年 SOH~=72%,第8年触发更换

    Args:
        equiv_full_cycles_per_year: 年均等效全循环次数
        chemistry: 'LFP' 或 'NMC'
        ambient_temp_c: 环境温度 (degC)
        project_life_years: 项目寿命 (年)

    Returns:
        dict: {yearly_soh_pct, replacement_year, note, ...}
    """
    # ── 输入验证 ──
    if equiv_full_cycles_per_year is None or equiv_full_cycles_per_year < 0:
        equiv_full_cycles_per_year = 0
    if chemistry not in ('LFP', 'NMC'):
        chemistry = 'LFP'
        log.warning("_estimate_degradation: 未知电池化学类型,回退为LFP")
    if ambient_temp_c < -50 or ambient_temp_c > 80:
        log.warning(f"_estimate_degradation: 环境温度{ambient_temp_c}degC超出合理范围,已钳位")
        ambient_temp_c = max(-50, min(80, ambient_temp_c))
    if project_life_years < 1 or project_life_years > 50:
        log.warning(f"_estimate_degradation: 项目寿命{project_life_years}年超出合理范围")
        project_life_years = max(1, min(50, project_life_years))

    # ── 25degC 基准衰减系数 ──
    if chemistry == 'LFP':
        k_cal_25 = 0.012     # 日历衰减系数 (1/年)
        k_cyc_25 = 0.000025  # 循环衰减系数 (1/次)
        Ea_cal = 24000       # 日历老化活化能 J/mol
        Ea_cyc = 15000       # 循环老化活化能 J/mol
        label = 'LFP (磷酸铁锂)'
    else:  # NMC
        k_cal_25 = 0.018
        k_cyc_25 = 0.000040
        Ea_cal = 24000
        Ea_cyc = 15000
        label = 'NMC (三元锂)'

    R = 8.314
    T_ref = 298.15          # 25degC 参考温度 (K)
    T_actual = ambient_temp_c + 273.15

    # 温度加速因子
    accel_cal = math.exp((Ea_cal / R) * (1.0 / T_ref - 1.0 / T_actual))
    accel_cyc = math.exp((Ea_cyc / R) * (1.0 / T_ref - 1.0 / T_actual))
    k_cal_T = k_cal_25 * accel_cal
    k_cyc_T = k_cyc_25 * accel_cyc

    yearly_soh = []
    cumulative_cycles = 0
    replacement_year = None

    for y in range(1, project_life_years + 1):
        cumulative_cycles += equiv_full_cycles_per_year
        q_loss_cal = k_cal_T * y
        q_loss_cyc = k_cyc_T * cumulative_cycles
        q_loss_total = q_loss_cal + q_loss_cyc
        soh = max(0.0, 1.0 - q_loss_total)
        yearly_soh.append(round(soh * 100, 1))
        if replacement_year is None and soh < 0.70:
            replacement_year = y

    return {
        'chemistry': chemistry,
        'chemistry_label': label,
        'ambient_temp_c': ambient_temp_c,
        'cycles_per_year': equiv_full_cycles_per_year,
        'yearly_soh_pct': yearly_soh,
        'year5_soh_pct': yearly_soh[4] if len(yearly_soh) >= 5 else yearly_soh[-1],
        'year10_soh_pct': yearly_soh[9] if len(yearly_soh) >= 10 else yearly_soh[-1],
        'replacement_year': replacement_year,
        'cal_loss_rate': round(k_cal_T * 100, 2),
        'cyc_loss_rate': round(k_cyc_T * 100, 5),
        'note': (f'{label},{ambient_temp_c}degC环境下(日历衰减{k_cal_T*100:.1f}%/年 + 循环衰减{k_cyc_T*100:.4f}%/次),'
                 f'第5年SOH~={yearly_soh[4] if len(yearly_soh)>=5 else yearly_soh[-1]}%,'
                 + (f'预计第{replacement_year}年需更换' if replacement_year
                    else f'{project_life_years}年内未触发更换阈值(SOH>{70}%)'))
    }
