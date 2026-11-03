# -*- coding: utf-8 -*-
"""数据提供：把扫描结果变成可取用的宽表，并做两层缓存。

本模块从 Fusion v2.3.9 单文件原样搬出，逻辑一字未改。
搬移依据：tools/analyze_class.py DataProvider —— 基类空、无 Qt 引用，
8 个实例属性全是缓存（file_cache / file_mtimes / group_cache / load_errors
/ raw_file_cache / enc / lock / max_group_cache），跨方法共享，整类搬不移拆。

缓存分两层，别混：
  _raw_file_cache  MemoryLimitedCache  按内存占用淘汰，存原始 DataFrame
  _group_cache     OrderedDict LRU     按条数淘汰，存加工后的宽表

inject_raw_file 是外部注入入口（ProcessingThread 预加载后把 DataFrame 塞进来，
避免重复读盘）。它只写 _raw_file_cache，与 _group_cache 无关——
两个调用方之间不该有状态串用，这条在搬移后要单独验（见阶段报告）。
"""
from __future__ import annotations

import logging
import os
import sys
import threading
from collections import OrderedDict

import numpy as np
import pandas as pd

from core.ingest import (
    _extract_asset_from_name, _extract_ym, _mapping_is_long_shape, _normalize_asset,
    _parse_long_to_standard, _read_file_full, _valid_time_cols,
    _wide_row_accumulated_to_power,
)
from core.models import GroupSummary
from core.timegrid import _clean_series, _fmt_date, _fmt_time, _parse_min

log = logging.getLogger(__name__)

__all__ = ["DataProvider", "MemoryLimitedCache"]


DATA_CACHE_MAXSIZE = 60

FILE_CACHE_MAX_MEMORY = 512 * 1024 * 1024

class MemoryLimitedCache:
    """基于内存占用的 LRU 缓存，用于存储 DataFrame 等大对象"""
    def __init__(self, max_memory_bytes=FILE_CACHE_MAX_MEMORY):
        self.cache = OrderedDict()
        self.max_memory = max_memory_bytes
        self.current_memory = 0
        self._lock = threading.RLock()

    @staticmethod
    def _sizeof(obj):
        if isinstance(obj, pd.DataFrame):
            return obj.memory_usage(deep=True).sum()
        return sys.getsizeof(obj)

    def get(self, key):
        with self._lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
            return None

    def put(self, key, value):
        with self._lock:
            size = self._sizeof(value)
            while self.current_memory + size > self.max_memory and self.cache:
                oldest_key = next(iter(self.cache))
                old_val = self.cache.pop(oldest_key)
                self.current_memory -= self._sizeof(old_val)
            self.cache[key] = value
            self.current_memory += size

    def remove(self, key):
        """安全移除缓存项，维护内存计数"""
        with self._lock:
            if key in self.cache:
                old_val = self.cache.pop(key)
                self.current_memory -= self._sizeof(old_val)

    def clear(self):
        with self._lock:
            self.cache.clear()
            self.current_memory = 0

    def __contains__(self, key):
        with self._lock:
            return key in self.cache

    def items(self):
        with self._lock:
            return list(self.cache.items())

class DataProvider:
    def __init__(self, enc='auto'):
        self.enc = enc
        self._group_cache = OrderedDict()
        self._max_group_cache = DATA_CACHE_MAXSIZE
        self._file_cache = MemoryLimitedCache(max_memory_bytes=FILE_CACHE_MAX_MEMORY)
        self._lock = threading.RLock()
        self._file_mtimes = {}
        self._load_errors = {}
        self._raw_file_cache = {}

    @staticmethod
    def _cache_key(fp, sheet, mapping):
        fmt = mapping.get('format_type', 'long')
        date_col = mapping.get('date_col', '')
        load = mapping.get('load_col', '')
        time_col = mapping.get('time_col') or ''
        hrow = mapping.get('hrow', 0)  # v2.3fix: 表头行加入缓存键
        delimiter = mapping.get('delimiter', ',')  # v2.3fix: 分隔符加入缓存键
        if isinstance(load, list):
            load = tuple(sorted(load))
        mtime = 0
        try:
            mtime = os.path.getmtime(fp)
        except Exception:
            pass
        return (fp, sheet, fmt, date_col, load, time_col, mtime, hrow, delimiter)

    def get_group_data(self, summary: GroupSummary):
        key = (summary.file_path, summary.sheet_name, summary.month_str, summary.asset_value)
        with self._lock:
            if key in self._group_cache:
                self._group_cache.move_to_end(key)
                return self._group_cache[key]
        data, error_msg = self._load_group_data(summary)
        if data:
            with self._lock:
                while len(self._group_cache) >= self._max_group_cache:
                    self._group_cache.popitem(last=False)
                self._group_cache[key] = data
            if key in self._load_errors:
                del self._load_errors[key]
            return data
        else:
            if error_msg:
                with self._lock:
                    self._load_errors[key] = error_msg
            return None

    def get_load_error(self, summary: GroupSummary):
        key = (summary.file_path, summary.sheet_name, summary.month_str, summary.asset_value)
        with self._lock:
            return self._load_errors.get(key, "未知加载错误")

    def preload_file(self, file_path, sheet_name, mapping, df=None):
        fkey = self._cache_key(file_path, sheet_name, mapping)
        with self._lock:
            if fkey in self._file_cache and self._file_mtimes.get(fkey) == fkey[-1]:
                return
            if fkey in self._file_cache:
                self._file_cache.remove(fkey)
                self._file_mtimes.pop(fkey, None)
        if df is not None:
            # v2.3fix: 写入缓存操作移入锁内
            with self._lock:
                self._file_cache.put(fkey, df)
                self._file_mtimes[fkey] = fkey[-1]
            return
        try:
            df_read = _read_file_full(file_path, sheet_name if sheet_name != "CSV文件" else None,
                                      mapping.get('header_row', 0), self.enc, mapping.get('delimiter', ','))
            with self._lock:  # v2.3fix: 写入缓存操作加锁
                self._file_cache.put(fkey, df_read)
                self._file_mtimes[fkey] = fkey[-1]
        except Exception as e:
            log.error(f"预加载文件失败 {file_path}/{sheet_name}: {e}")

    def preload_all_groups(self, summaries, preloaded_dfs=None):
        unique = {}
        for s in summaries:
            fkey = self._cache_key(s.file_path, s.sheet_name, s.mapping)
            if fkey not in unique:
                unique[fkey] = (s.file_path, s.sheet_name, s.mapping)
        for fkey, (fp, sname, mapping) in unique.items():
            key = (fp, sname)
            if preloaded_dfs and key in preloaded_dfs:
                self.preload_file(fp, sname, mapping, df=preloaded_dfs[key])
            else:
                self.preload_file(fp, sname, mapping, df=None)

    def inject_raw_file(self, fp, sheet, df):
        with self._lock:
            self._raw_file_cache[(fp, sheet)] = df

    def _load_group_data(self, s: GroupSummary, cancel_event=None):
        mapping = s.mapping
        fkey = self._cache_key(s.file_path, s.sheet_name, mapping)
        with self._lock:
            df = self._file_cache.get(fkey)
            if df is not None and self._file_mtimes.get(fkey) != fkey[-1]:
                self._file_cache.remove(fkey)
                df = None
        if df is None:
            raw_key = (s.file_path, s.sheet_name)
            with self._lock:
                cached_df = self._raw_file_cache.get(raw_key)
            if cached_df is not None:
                df = cached_df
                self._file_cache.put(fkey, df)
                self._file_mtimes[fkey] = fkey[-1]
            else:
                try:
                    df = _read_file_full(
                        s.file_path,
                        s.sheet_name if s.sheet_name != "CSV文件" else None,
                        mapping.get('header_row', 0),
                        self.enc,
                        mapping.get('delimiter', ',')
                    )
                    # v2.3fix: 缓存写入加锁
                    with self._lock:
                        self._file_cache.put(fkey, df)
                        self._file_mtimes[fkey] = fkey[-1]
                except Exception as e:
                    return None, f"读取文件失败: {e}"
        if cancel_event and cancel_event.is_set():
            return None, None
        try:
            date_col = mapping['date_col']
            time_col = mapping.get('time_col')
            asset_col = mapping.get('asset_col')
            if mapping.get('format_type') in ('wide', 'wide_transposed', 'accumulated') and not (
                    mapping.get('format_type') == 'accumulated' and _mapping_is_long_shape(mapping)):
                load_cols = mapping['load_col']
                if not isinstance(load_cols, list):
                    load_cols = [str(load_cols)]
                if mapping.get('transpose'):
                    trans_time_col = mapping.get('time_col')
                    if trans_time_col not in df.columns:
                        return None, f"缺少时间列 {trans_time_col}"
                    df_for_trans = df.copy(deep=True)
                    if df_for_trans[trans_time_col].duplicated().any():
                        log.warning(f"转置宽表时间列 {trans_time_col} 存在重复值，将聚合取均值。文件: {s.file_path}, 工作表: {s.sheet_name}")
                        num_cols = df_for_trans.select_dtypes(include=np.number).columns.tolist()
                        group_cols = [trans_time_col]
                        df_for_trans = df_for_trans.groupby(group_cols, as_index=False)[num_cols].mean()
                    df_trans = df_for_trans.set_index(trans_time_col).transpose()
                    yms = pd.Series(df_trans.index).apply(_extract_ym)
                    vcols = _valid_time_cols(df_trans.columns)
                    if not vcols:
                        return None, "转置后未找到有效时间列"
                    idxs = yms.groupby(yms).groups.get(s.month_str)
                    if idxs is None:
                        return None, f"未找到月份 '{s.month_str}' 的数据"
                    dates = list(df_trans.index[idxs])
                    ldf = df_trans[vcols].apply(pd.to_numeric, errors='coerce').values[idxs]
                    if mapping.get('format_type') == 'accumulated':
                        ldf_df = pd.DataFrame(ldf, index=dates, columns=vcols)
                        ldf_df = _wide_row_accumulated_to_power(ldf_df, vcols)
                        vcols = list(ldf_df.columns)
                        ldf = ldf_df.values
                    time_labels = [_fmt_time(t) for t in vcols]
                    return {'date_labels': dates, 'load_array': ldf, 'time_labels': time_labels,
                            'month_str': s.month_str, 'table_no': s.asset_value or '未命名',
                            'asset': s.asset_value, 'sheet': s.sheet_name}, None
                else:
                    needed = [date_col] + ([time_col] if time_col else []) + ([asset_col] if asset_col else []) + load_cols
                    if any(c not in df.columns for c in needed):
                        missing = [c for c in needed if c not in df.columns]
                        return None, f"缺少列: {missing}"
                    dser = _clean_series(df[date_col])
                    sub_df = df[load_cols].copy().apply(pd.to_numeric, errors='coerce')
                    if mapping.get('format_type') == 'accumulated':
                        sub_df = _wide_row_accumulated_to_power(sub_df, load_cols)
                        load_cols = list(sub_df.columns)
                    yms = dser.apply(_extract_ym)
                    if asset_col and asset_col in df.columns:
                        asset_ser = df[asset_col].apply(lambda x: _normalize_asset(x))
                    else:
                        # v2.3.1fix: 必须与 _scan_wide_chunk 的兜底保持同源。
                        # 扫描阶段用的是 _extract_asset_from_name，这里旧代码写死 '未命名'，
                        # 两边对不上就会整表筛空，报「未找到月份…且资产…的数据」。
                        asset_ser = pd.Series(
                            _extract_asset_from_name(s.file_path, s.sheet_name),
                            index=df.index)
                    mask = (yms == s.month_str) & (asset_ser == s.asset_value)
                    idxs = df.index[mask]
                    if not len(idxs):
                        return None, f"未找到月份 '{s.month_str}' 且资产 '{s.asset_value}' 的数据"
                    dates = [_fmt_date(dser.iloc[i]) for i in idxs]
                    sub = sub_df.iloc[idxs]
                    vcols = _valid_time_cols(sub.columns)
                    if not vcols:
                        return None, "没有有效的时间列（无法识别 HH:MM 格式的列头）"
                    time_labels = [_fmt_time(t) for t in vcols]
                    arr = sub[vcols].values
                    return {'date_labels': dates, 'load_array': arr, 'time_labels': time_labels,
                            'month_str': s.month_str, 'table_no': s.asset_value or '未命名',
                            'asset': s.asset_value, 'sheet': s.sheet_name}, None
            else:
                load_col = mapping['load_col']
                if isinstance(load_col, list):
                    if not load_col:
                        return None, "负荷列列表为空"
                    load_col = load_col[0]
                needed = [date_col, load_col] + ([time_col] if time_col else []) + ([asset_col] if asset_col else [])
                if any(c not in df.columns for c in needed):
                    missing = [c for c in needed if c not in df.columns]
                    return None, f"缺少列: {missing}"
                sub_df = df[needed].copy()
                convert_accumulated = mapping.get('convert_accumulated', False) or mapping.get('format_type') == 'accumulated'
                df_clean, dropped_ratio = _parse_long_to_standard(
                    sub_df, date_col, time_col, load_col, 1.0, asset_col, cancel_event, convert_accumulated,
                    fallback_asset=_extract_asset_from_name(s.file_path, s.sheet_name)
                )
                if df_clean.empty:
                    return None, "解析后无有效数据（可能日期或负荷列格式不正确）"
                if dropped_ratio > 0.2:
                    log.info(f"长表解析丢弃 {dropped_ratio:.1%} 行，请检查日期格式：{s.file_path}/{s.sheet_name}")

                if asset_col and '__asset__' in df_clean.columns:
                    asset_series = df_clean['__asset__']
                    grp = df_clean[(df_clean['__ym__'] == s.month_str) & (asset_series == s.asset_value)] if s.asset_value and s.asset_value != '未命名' else df_clean[df_clean['__ym__'] == s.month_str]
                    if grp.empty and s.asset_value != '未命名':
                        return None, f"资产号 '{s.asset_value}' 在月份 '{s.month_str}' 无数据"
                else:
                    grp = df_clean[df_clean['__ym__'] == s.month_str]
                if grp.empty:
                    return None, f"月份 '{s.month_str}' 无数据"
                grp_clean = grp[['__dt__', '__load__']].copy()
                grp_clean['__d__'] = grp_clean['__dt__'].dt.date
                grp_clean['__t__'] = grp_clean['__dt__'].dt.floor('min').dt.time
                grp_agg = grp_clean.groupby(['__d__', '__t__'])['__load__'].mean().reset_index()
                piv = grp_agg.pivot(index='__d__', columns='__t__', values='__load__')
                dates = [d.strftime('%Y-%m-%d') if hasattr(d, 'strftime') else _fmt_date(d) for d in piv.index]
                scols = sorted(piv.columns, key=_parse_min)
                time_labels = [_fmt_time(x) for x in scols]
                arr = piv[scols].values
                return {'date_labels': dates, 'load_array': arr, 'time_labels': time_labels,
                        'month_str': s.month_str, 'table_no': s.asset_value or '未命名',
                        'asset': s.asset_value, 'sheet': s.sheet_name}, None
        except Exception as e:
            log.error(f"提取分组数据失败 {s.file_path}/{s.sheet_name}/{s.month_str}: {e}", exc_info=True)
            return None, str(e)

    def clear(self):
        with self._lock:
            self._group_cache.clear()
            self._file_cache.clear()
            self._file_mtimes.clear()
            self._load_errors.clear()
            self._raw_file_cache.clear()

