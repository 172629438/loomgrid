# -*- coding: utf-8 -*-
"""文件名与标题生成、字符串清洗。

本模块从 Fusion v2.3.9 单文件原样搬出，函数体一字未改。

注意 _sanitize_name 的两层防护：
  1. _CLEAN_RE（控制字符）—— 来自 core.timegrid
  2. _PATH_ILLEGAL_CHARS_RE + Windows 保留设备名 —— 本模块内
两层都不能省：第一层防日志/报告被控制字符打断，第二层防写盘失败。
"""
from __future__ import annotations

import os
import re

import pandas as pd
from loguru import logger as log

from core.timegrid import _CLEAN_RE, _clean_plot

__all__ = ["_sanitize_name", "_clean_series", "generate_filename",
           "generate_default_title", "parse_title_template"]


MAX_FILENAME_LEN = 50


MAX_FILENAME_ATTEMPTS = 1000


_PATH_ILLEGAL_CHARS_RE = re.compile(r'[<>:"/\\|?*]')


_PATH_DOT_RE = re.compile(r'\.{2,}')


_WINDOWS_RESERVED_NAMES = {
    'CON', 'PRN', 'AUX', 'NUL',
    'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9',
    'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9'
}


def _sanitize_name(s, max_len=MAX_FILENAME_LEN):
    if not s:
        s = "未命名"
    s = _CLEAN_RE.sub('', str(s)).strip()
    s = _PATH_ILLEGAL_CHARS_RE.sub('_', s)
    s = _PATH_DOT_RE.sub('_', s)
    s = s.lstrip('.')
    if not s:
        s = "未命名"
    base, ext = os.path.splitext(s)
    if base.upper() in _WINDOWS_RESERVED_NAMES:
        s = base + '_' + ext
    return s[:max_len-3]+"..." if len(s) > max_len else s


def _clean_series(s):
    """清理 pandas Series 中的控制字符"""
    if isinstance(s, pd.DataFrame):
        if s.shape[1] > 0:
            s = s.iloc[:, 0]
        else:
            return pd.Series(dtype=str)
    return s.astype(str).str.replace(_CLEAN_RE, '', regex=True).str.strip()


def generate_filename(template, ctx, ext='.png'):
    raw = parse_title_template(template, ctx)
    return _sanitize_name(raw) + ext


def generate_default_title(ctx):
    parts = []
    if ctx.get('table_no'):
        parts.append(f"表号{ctx['table_no']}")
    if ctx.get('asset'):
        parts.append(str(ctx['asset']))
    parts.append(ctx.get('year_month',''))
    return '_'.join(parts)


def parse_title_template(template, ctx):
    for k,v in {'year_month':ctx.get('year_month',''),'table_no':ctx.get('table_no',''),
                'asset':ctx.get('asset',''),'sheet':ctx.get('sheet','')}.items():
        template = template.replace(f'{{{k}}}',str(v))
    return _clean_plot(template.strip())

