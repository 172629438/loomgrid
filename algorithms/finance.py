# -*- coding: utf-8 -*-
"""
Financial Metrics
=================

NPV / IRR / 静态与动态回收期 / LCOE。

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
import logging

log = logging.getLogger(__name__)


class FinanceEngine:
    """统一财务引擎。所有模块通过此类计算NPV/IRR/回收期,确保一致。"""

    @staticmethod
    def npv(investment, annual_cashflow, project_life=10, discount_rate=0.06,
            degradation=0.0, salvage_rate=0.1):
        """净现值。支持逐年衰减和残值。"""
        import math
        if math.isnan(annual_cashflow) or math.isinf(annual_cashflow) or math.isnan(investment):
            return float('-inf')
        npv_val = -investment
        for y in range(1, project_life + 1):
            cf = annual_cashflow * max(0, (1 - degradation) ** (y - 1))
            npv_val += cf / ((1 + discount_rate) ** y)
        npv_val += investment * salvage_rate / ((1 + discount_rate) ** project_life)
        return round(npv_val, 0)

    @staticmethod
    def irr(investment, annual_cashflow, project_life=10, degradation=0.0, guess=0.1):
        """Newton-Raphson法IRR。"""
        import math
        if math.isnan(investment) or math.isinf(investment): return 0.0
        if math.isnan(annual_cashflow) or annual_cashflow <= 0: return 0.0
        try:
            rate = guess
            for _ in range(200):
                npv_val = 0.0; dnpv = 0.0
                npv_val = -investment
                for y in range(1, project_life + 1):
                    cf = annual_cashflow * max(0, (1 - degradation) ** (y - 1))
                    npv_val += cf / ((1 + rate) ** y)
                    dnpv += -y * cf / ((1 + rate) ** (y + 1))
                if abs(dnpv) < 1e-12 or np.isnan(dnpv): break
                new_rate = rate - npv_val / dnpv
                if np.isnan(new_rate) or abs(new_rate - rate) < 1e-6:
                    rate = new_rate; break
                rate = new_rate
            rate = max(-0.99, min(0.99, rate))
            return round(rate * 100, 1)
        except Exception:
            return 0.0

    @staticmethod
    def payback(investment, annual_cashflow, degradation=0.0):
        """简单回收期(年)。"""
        if annual_cashflow <= 0: return 99.0
        cum = 0.0
        for y in range(1, 50):
            cum += annual_cashflow * max(0, (1 - degradation) ** (y - 1))
            if cum >= investment:
                return round(y - 1 + (investment - (cum - annual_cashflow * max(0, (1-degradation)**(y-2)))) / max(annual_cashflow * max(0, (1-degradation)**(y-1)), 1), 1) if y > 1 else round(investment / annual_cashflow, 1)
        return 99.0

    @staticmethod
    def discounted_payback(investment, annual_cashflow, discount_rate=0.06, degradation=0.0):
        """折现回收期——累计折现现金流转正年份。"""
        if annual_cashflow <= 0: return 99.0
        cum = -investment
        for y in range(1, 31):
            cf = annual_cashflow * max(0, (1 - degradation) ** (y - 1))
            cum += cf / ((1 + discount_rate) ** y)
            if cum >= 0:
                prev_cf = annual_cashflow * max(0, (1 - degradation) ** (y - 2)) if y > 1 else 0
                prev_cum = cum - cf / ((1 + discount_rate) ** y)
                return round(y - 1 + (-prev_cum) / (cf / ((1 + discount_rate) ** y)), 1)
        return 99.0


def calc_npv(annual_cashflow: float, investment: float,
             project_life: int = 15, discount_rate: float = 0.07,
             degradation: float = 0.02) -> float:
    """净现值(考虑容量衰减). v2.27fix: NaN/Inf 输入防护."""
    if math.isnan(annual_cashflow) or math.isinf(annual_cashflow):
        return float('-inf')
    if math.isnan(investment) or math.isinf(investment):
        return float('-inf')
    npv = -investment
    for year in range(1, project_life + 1):
        cf = annual_cashflow * ((1 - degradation) ** (year - 1))
        npv += cf / ((1 + discount_rate) ** year)
    return npv


def calc_irr(investment: float, annual_cashflow: float,
             project_life: int = 15, degradation: float = 0.02,
             guess: float = 0.1, tolerance: float = 1e-6) -> float:
    """Newton-Raphson 法计算 IRR(%).
    v2.29fix: 修复Newton更新误置于for-else分支的退化bug,恢复为每次迭代更新.
    v2.29fix: 增加 NaN/Inf 输入防护,对齐 calc_npv/calc_lcos."""
    if math.isnan(investment) or math.isinf(investment):
        return None
    if math.isnan(annual_cashflow) or math.isinf(annual_cashflow):
        return None
    if annual_cashflow <= 0:
        return None
    try:
        rate = guess
        for _ in range(200):
            npv = -investment
            dnpv = 0.0
            for y in range(1, project_life + 1):
                cf = annual_cashflow * ((1 - degradation) ** (y - 1))
                d = (1 + rate) ** y
                npv += cf / d
                dnpv += -y * cf / ((1 + rate) ** (y + 1))
            if abs(dnpv) < 1e-12:
                break
            new_rate = rate - npv / dnpv
            if abs(new_rate - rate) < tolerance:
                return new_rate * 100
            rate = new_rate
        else:
            log.warning(f"calc_irr: Newton法未在200次迭代内收敛 (investment={investment}, cashflow={annual_cashflow})")
    except (ZeroDivisionError, OverflowError):
        pass
    # v2.15: 不收敛时返回 None 而非 nan,避免下游排序/比较出现不可预测行为
    return None


def calc_simple_payback(investment: float, annual_savings: float) -> float:
    """简单回收期 (年)."""
    if annual_savings <= 0:
        return float('inf')
    return investment / annual_savings


def calc_payback_timeline(investment: float, monthly_savings: list[float]) -> dict:
    """计算投资回收时间线.

    Returns:
        {
            "payback_month": int (0-indexed),
            "cumulative_savings": [...],
            "remaining_investment": [...],
        }
    """
    cumulative = []
    cum_sum = 0.0
    payback_month = -1

    for i, saving in enumerate(monthly_savings):
        cum_sum += saving
        cumulative.append(cum_sum)
        if payback_month < 0 and cum_sum >= investment:
            payback_month = i

    return {
        "payback_month": payback_month,
        "cumulative_savings": cumulative,
        "remaining_investment": [investment - s for s in cumulative],
    }
