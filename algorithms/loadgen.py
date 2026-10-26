# -*- coding: utf-8 -*-
"""
Synthetic Load Generation
=========================

三层叠加负荷生成：省份基线 × 班制 × 设备 Markov × 噪声平滑。

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
from datetime import datetime, timedelta
from collections import deque


INDUSTRY_PARAMS = {
    '机械加工':   {'name': '机械加工',   'par': 2.2, 'lf': 0.50, 'cv': 0.15, 'base_load': 0.15, 'shift': '两班倒'},
    '化工':       {'name': '化工',       'par': 1.4, 'lf': 0.82, 'cv': 0.08, 'base_load': 0.60, 'shift': '三班倒'},
    '电子制造':   {'name': '电子制造',   'par': 1.8, 'lf': 0.62, 'cv': 0.12, 'base_load': 0.20, 'shift': '两班倒'},
    '纺织':       {'name': '纺织',       'par': 2.0, 'lf': 0.68, 'cv': 0.14, 'base_load': 0.25, 'shift': '三班倒'},
    '食品加工':   {'name': '食品加工',   'par': 1.9, 'lf': 0.55, 'cv': 0.16, 'base_load': 0.20, 'shift': '白班制'},
    '商业楼宇':   {'name': '商业楼宇',   'par': 2.5, 'lf': 0.40, 'cv': 0.18, 'base_load': 0.08, 'shift': '白班制'},
    '数据中心':   {'name': '数据中心',   'par': 1.1, 'lf': 0.92, 'cv': 0.05, 'base_load': 0.85, 'shift': '三班倒'},
    '塑料制品':   {'name': '塑料制品',   'par': 2.0, 'lf': 0.58, 'cv': 0.13, 'base_load': 0.22, 'shift': '两班倒'},
    '汽车零部件': {'name': '汽车零部件', 'par': 2.1, 'lf': 0.52, 'cv': 0.14, 'base_load': 0.18, 'shift': '两班倒'},
    '居民':       {'name': '居民',       'par': 3.0, 'lf': 0.35, 'cv': 0.25, 'base_load': 0.05, 'shift': '全天候'},
}


SHIFT_PROFILES_24H = {
    '白班制':  [0.05,0.04,0.03,0.03,0.03,0.05,0.15,0.40,0.70,1.00,1.30,1.60,
                1.80,1.90,2.00,2.10,2.20,1.80,1.20,0.50,0.15,0.08,0.06,0.05],
    '两班倒':  [0.08,0.07,0.06,0.05,0.05,0.10,0.30,0.60,1.00,1.40,1.80,2.00,
                1.60,1.30,1.50,1.80,2.10,2.30,2.50,2.00,1.40,1.00,0.40,0.10],
    '三班倒':  [0.60,0.55,0.50,0.45,0.40,0.40,0.70,1.10,1.40,1.50,1.60,1.55,
                1.50,1.40,1.50,1.60,1.70,1.80,1.90,2.00,1.80,1.40,1.00,0.80],
    '全天候':  [0.50,0.40,0.35,0.30,0.30,0.40,0.70,1.00,0.80,0.60,0.60,0.80,
                1.00,0.80,0.70,0.80,1.00,1.30,1.60,2.00,2.20,2.00,1.40,1.00],
}


SEASON_FACTORS = {'spring': 1.00, 'summer': 1.12, 'autumn': 0.95, 'winter': 0.88}


PROVINCE_BASELINES = {
    # 华东
    '浙江': [0.55,0.50,0.48,0.45,0.45,0.50,0.70,0.95,1.20,1.40,1.55,1.60,1.35,1.40,1.55,1.75,1.90,2.00,1.85,1.60,1.30,1.10,0.85,0.65],
    '江苏': [0.50,0.48,0.45,0.43,0.43,0.50,0.72,0.98,1.25,1.42,1.58,1.62,1.38,1.42,1.58,1.78,1.92,2.05,1.88,1.62,1.28,1.08,0.82,0.62],
    '上海': [0.50,0.48,0.45,0.43,0.43,0.50,0.72,0.98,1.28,1.48,1.62,1.68,1.40,1.45,1.62,1.82,1.98,2.10,1.92,1.65,1.32,1.10,0.85,0.65],
    '山东': [0.48,0.45,0.43,0.40,0.40,0.48,0.70,0.98,1.28,1.50,1.65,1.68,1.40,1.45,1.60,1.75,1.90,2.00,1.85,1.60,1.28,1.08,0.82,0.60],
    '安徽': [0.50,0.47,0.44,0.42,0.42,0.50,0.72,0.95,1.22,1.42,1.55,1.58,1.35,1.40,1.55,1.72,1.88,1.98,1.82,1.58,1.28,1.08,0.82,0.62],
    '福建': [0.52,0.48,0.45,0.43,0.43,0.50,0.75,1.00,1.30,1.50,1.65,1.70,1.45,1.50,1.72,1.92,2.08,2.18,2.00,1.70,1.35,1.10,0.85,0.68],
    # 华北
    '北京': [0.45,0.42,0.40,0.38,0.40,0.48,0.70,0.95,1.20,1.40,1.50,1.48,1.25,1.30,1.45,1.65,1.85,1.95,1.80,1.55,1.25,1.05,0.80,0.58],
    '天津': [0.46,0.43,0.40,0.38,0.40,0.48,0.72,0.95,1.22,1.40,1.52,1.50,1.28,1.32,1.48,1.68,1.88,1.98,1.82,1.58,1.28,1.08,0.82,0.60],
    '河北': [0.50,0.45,0.42,0.40,0.40,0.48,0.72,1.00,1.25,1.45,1.55,1.55,1.30,1.35,1.50,1.70,1.88,1.98,1.82,1.58,1.28,1.08,0.82,0.60],
    '山西': [0.48,0.45,0.42,0.40,0.40,0.48,0.72,0.98,1.25,1.42,1.52,1.52,1.28,1.32,1.50,1.70,1.88,1.98,1.82,1.58,1.28,1.05,0.80,0.60],
    '内蒙古':[0.50,0.48,0.45,0.42,0.42,0.50,0.72,0.98,1.22,1.40,1.50,1.50,1.28,1.32,1.48,1.68,1.85,1.95,1.78,1.55,1.25,1.05,0.82,0.62],
    # 华中
    '河南': [0.48,0.45,0.42,0.40,0.40,0.48,0.72,1.00,1.28,1.48,1.60,1.62,1.38,1.42,1.58,1.78,1.92,2.05,1.88,1.62,1.30,1.10,0.85,0.65],
    '湖北': [0.50,0.47,0.44,0.42,0.42,0.50,0.72,0.98,1.25,1.45,1.58,1.60,1.35,1.40,1.55,1.75,1.90,2.02,1.85,1.60,1.30,1.10,0.85,0.65],
    '湖南': [0.52,0.48,0.45,0.43,0.43,0.50,0.75,1.02,1.30,1.50,1.65,1.68,1.42,1.48,1.65,1.85,2.00,2.12,1.95,1.68,1.35,1.12,0.88,0.68],
    '江西': [0.52,0.48,0.45,0.42,0.43,0.50,0.75,1.00,1.28,1.48,1.62,1.65,1.40,1.45,1.62,1.82,1.98,2.10,1.92,1.65,1.32,1.10,0.85,0.65],
    # 华南
    '广东': [0.52,0.48,0.45,0.43,0.43,0.50,0.75,1.00,1.30,1.50,1.65,1.70,1.45,1.50,1.72,1.95,2.10,2.20,2.00,1.70,1.35,1.10,0.85,0.68],
    '广西': [0.52,0.48,0.45,0.43,0.43,0.50,0.75,1.00,1.30,1.48,1.62,1.68,1.42,1.48,1.65,1.85,2.00,2.12,1.92,1.65,1.32,1.08,0.85,0.65],
    '海南': [0.55,0.50,0.48,0.45,0.45,0.52,0.78,1.05,1.35,1.55,1.72,1.75,1.50,1.55,1.75,1.98,2.15,2.28,2.05,1.75,1.38,1.12,0.88,0.68],
    # 西南
    '四川': [0.48,0.45,0.43,0.40,0.42,0.50,0.70,0.92,1.15,1.35,1.48,1.50,1.30,1.35,1.50,1.70,1.88,1.95,1.80,1.55,1.25,1.05,0.80,0.60],
    '重庆': [0.50,0.47,0.44,0.42,0.42,0.50,0.72,0.95,1.20,1.40,1.55,1.58,1.35,1.40,1.55,1.75,1.92,2.02,1.85,1.62,1.30,1.10,0.85,0.65],
    '云南': [0.48,0.45,0.42,0.40,0.40,0.48,0.70,0.92,1.15,1.35,1.48,1.50,1.28,1.32,1.48,1.68,1.85,1.95,1.80,1.55,1.25,1.05,0.80,0.60],
    '贵州': [0.48,0.45,0.42,0.40,0.40,0.48,0.70,0.92,1.15,1.35,1.48,1.50,1.28,1.32,1.48,1.68,1.85,1.95,1.78,1.55,1.25,1.05,0.80,0.60],
    # 西北
    '陕西': [0.48,0.45,0.42,0.40,0.40,0.48,0.72,0.95,1.22,1.42,1.55,1.55,1.32,1.38,1.55,1.75,1.90,2.00,1.85,1.60,1.30,1.08,0.82,0.62],
    '甘肃': [0.48,0.45,0.42,0.40,0.40,0.48,0.70,0.92,1.18,1.38,1.50,1.52,1.28,1.32,1.50,1.70,1.88,1.98,1.80,1.58,1.25,1.05,0.80,0.60],
    '青海': [0.50,0.48,0.45,0.42,0.42,0.50,0.70,0.92,1.15,1.35,1.48,1.50,1.28,1.32,1.48,1.65,1.82,1.92,1.75,1.52,1.22,1.02,0.80,0.62],
    '宁夏': [0.48,0.45,0.42,0.40,0.40,0.48,0.70,0.92,1.18,1.38,1.50,1.52,1.28,1.32,1.50,1.70,1.88,1.98,1.80,1.58,1.25,1.05,0.80,0.60],
    '新疆': [0.55,0.52,0.50,0.48,0.48,0.55,0.78,1.00,1.22,1.40,1.52,1.55,1.35,1.38,1.55,1.75,1.90,2.00,1.85,1.60,1.35,1.15,0.90,0.70],
    # 东北
    '辽宁': [0.48,0.45,0.42,0.40,0.40,0.48,0.72,1.00,1.28,1.48,1.58,1.58,1.35,1.40,1.58,1.78,1.92,2.05,1.88,1.62,1.30,1.10,0.85,0.65],
    '吉林': [0.48,0.45,0.42,0.40,0.40,0.48,0.70,0.98,1.25,1.45,1.58,1.58,1.32,1.38,1.55,1.75,1.90,2.02,1.85,1.60,1.28,1.08,0.82,0.62],
    '黑龙江':[0.50,0.48,0.45,0.42,0.42,0.50,0.72,0.98,1.22,1.42,1.55,1.58,1.35,1.38,1.55,1.75,1.90,2.02,1.85,1.60,1.30,1.10,0.88,0.68],
    # 回退: 全国平均
    '_default': [0.50,0.47,0.44,0.42,0.42,0.50,0.72,0.97,1.23,1.43,1.57,1.61,1.35,1.40,1.57,1.77,1.92,2.03,1.85,1.61,1.29,1.09,0.83,0.62],
}


def _generate_load_profile(industry, daily_kwh, peak_kw, n_days, n_points,
                           shift_start_h=8, shift_end_h=17,
                           lunch_start_h=12, lunch_end_h=13,
                           weekend_atten=0.40, cv=0.12,
                           equipment=None, seed=None,
                           province=None, use_season=True):
    """v3.0: 三层叠加负荷模拟引擎（从备份程序 v2.29+ 回填，修复 v3.2 的特性丢失）。

    Args:
        industry: 行业名称(INDUSTRY_PARAMS 的 key)
        daily_kwh: 日均用电量(kWh)
        peak_kw: 峰值功率估算(kW)
        n_days: 模拟天数
        n_points: 每日时间点数(96=15min, 48=30min, 24=1h)
        shift_start_h: 上班起始小时
        shift_end_h: 下班小时
        lunch_start_h: 午休起始
        lunch_end_h: 午休结束
        weekend_atten: 周末衰减系数(0~1, 0=完全不工作)
        cv: 日间变异系数
        equipment: 设备列表 [{'power':kW, 'count':n, 'simul':0.8, 'run_prob':0.85}, ...]
        seed: 随机种子(可复现)
        province: 省份(使用该省典型基线曲线, None=纯班制)
        use_season: 是否启用季节修正

    Returns:
        (load_array, date_labels, time_labels)
    """
    rng = np.random.RandomState(seed) if seed is not None else np.random
    params = INDUSTRY_PARAMS.get(industry, INDUSTRY_PARAMS['机械加工'])

    # ===== 第一层: 省份基线 + 班制叠加(24h → n_points 插值) =====
    hours_24 = np.arange(0.5, 24.0, 1.0)
    target_hours = np.linspace(0, 24, n_points, endpoint=False)

    if province and province in PROVINCE_BASELINES:
        province_base = np.array(PROVINCE_BASELINES[province], dtype=np.float64)
    else:
        province_base = np.ones(24, dtype=np.float64)

    shift_24 = np.array(SHIFT_PROFILES_24H.get(params['shift'], SHIFT_PROFILES_24H['两班倒']), dtype=np.float64)
    for h in range(24):
        if lunch_start_h <= h < lunch_end_h:
            shift_24[h] *= 0.30
    for h in range(24):
        if h < shift_start_h or h >= shift_end_h:
            shift_24[h] = shift_24[h] * 0.08 + params['base_load'] * 0.08 * 24

    base_24 = province_base * shift_24
    base_24 = base_24 / max(np.mean(base_24), 1e-9)
    base_profile = np.interp(target_hours, hours_24, base_24, left=base_24[0], right=base_24[-1])
    base_profile = base_profile / max(np.mean(base_profile), 1e-9)

    # ===== 第二层: 设备 Markov 叠加 =====
    equipment_profile = np.zeros(n_points)
    if equipment:
        for eq in equipment:
            eq_power = eq.get('power', 0) * eq.get('count', 0)
            if eq_power <= 0:
                continue
            state = 2
            p_fault = 1.0 - eq.get('run_prob', 0.85)
            p_restart = 0.3
            p_off = 0.05
            for t in range(n_points):
                hour = t / n_points * 24
                in_shift = (shift_start_h <= hour < shift_end_h)
                if in_shift:
                    r = rng.random()
                    if state == 2:
                        equipment_profile[t] += eq_power * eq.get('simul', 0.8)
                        if r < p_fault:
                            state = 1
                    elif state == 1:
                        equipment_profile[t] += eq_power * 0.15
                        if r < p_restart:
                            state = 2
                        elif r < p_off:
                            state = 0
                    else:
                        if r < p_restart * 0.5:
                            state = 1
                else:
                    equipment_profile[t] += eq_power * 0.03

    # ===== 第三层: 日间随机噪声(Gaussian平滑) =====
    daily_noise = rng.normal(1.0, cv, size=n_days)
    daily_noise = np.clip(daily_noise, 0.5, 1.5)
    try:
        from scipy.ndimage import gaussian_filter1d
        smoothed = gaussian_filter1d(daily_noise, sigma=2.0)
    except ImportError:
        from collections import deque
        smoothed = np.zeros(n_days)
        window = deque(maxlen=5)
        for i in range(n_days):
            window.append(daily_noise[i])
            smoothed[i] = np.mean(window)

    # ===== 合成 =====
    load_array = np.zeros((n_days, n_points))
    dt_hours = 24.0 / n_points
    start_date = datetime(2025, 1, 1)
    date_labels = []

    for d in range(n_days):
        current_date = start_date + timedelta(days=d)
        date_labels.append(current_date.strftime('%Y-%m-%d'))
        wd = current_date.weekday()
        is_weekend = (wd >= 5)
        atten = weekend_atten if is_weekend else 1.0
        if use_season:
            month = current_date.month
            if month in (6, 7, 8):
                season_factor = SEASON_FACTORS['summer']
            elif month in (12, 1, 2):
                season_factor = SEASON_FACTORS['winter']
            elif month in (9, 10, 11):
                season_factor = SEASON_FACTORS['autumn']
            else:
                season_factor = SEASON_FACTORS['spring']
        else:
            season_factor = 1.0
        profile = base_profile * smoothed[d] * atten * season_factor + equipment_profile / max(peak_kw, 1)
        actual_daily = np.sum(np.maximum(profile, 0)) * dt_hours
        scale = daily_kwh / max(actual_daily, 1e-9)
        load_array[d] = profile * scale

    time_labels = [f"{int(h):02d}:{int((h%1)*60):02d}" for h in target_hours]
    return load_array, date_labels, time_labels
