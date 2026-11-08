# -*- coding: utf-8 -*-
"""数据摄入：文件读取、编码探测、表型识别、列分类、扫描。

本模块从 Fusion v2.3.9 单文件原样搬出，**函数体一字未改**。
搬移依据：`tools/analyze_deps.py --group ingest` 实测闭包 34 符号 / 956 行、零 UI 依赖；
搬前用 `_tmp/scripts/precheck_ingest.py` 做过依赖预检，13 个缺失名全部可解析。

这一块是本层最重的一块，承载三次历史修复，搬移时勿动其判定顺序与注释：

  C1  无法识别的时间点不静默丢弃（与 core.spaces 同一处修复）
  C2  报告 HTML 转义（已随报告层迁出，本模块只留下文件名清洗相关部分）
  N6  数值列不当日期列 —— _detect_transposed_wide 里那条 is_numeric_dtype 检查，
      缺了它，多值列长表会被整表误判成转置宽表
  C9  时刻列必须像「一天里的时刻序列」（数量 / 唯一率 / 跨度上下限三条同时成立）

表的形态判定顺序是有意为之，不要「优化」：
  先 _detect_transposed_wide（J1 修复：避免纯数字递增列被宽表判定抢走）
  再 _detect_wide_matrix
  最后才落到长表分支

依赖方向：只依赖 core.timegrid，不依赖 plotting（_clean_series 已归位到 core）。
"""
from __future__ import annotations

import csv
import logging
import os
import re
import threading
from datetime import datetime
from functools import lru_cache

import numpy as np
import pandas as pd
log = logging.getLogger(__name__)

from core.timegrid import (
    INVALID_MINUTES, _clean_series, _fmt_time, _normalize_fullwidth_chars,
    _normalize_time_column_name, _parse_min,
)

__all__ = [
    "_read_file_full", "_scan_file_worker", "_scan_wide_chunk", "_scan_long_chunk",
    "_smart_classify_columns", "_detect_wide_matrix", "_detect_transposed_wide",
    "_wide_row_accumulated_to_power", "_detect_accumulated_pattern",
    "_convert_accumulated_to_power", "_parse_long_to_standard",
    "_detect_csv_delimiter", "_detect_enc", "_detect_file_format_type",
    "_parse_file_meta_worker", "_extract_asset_from_name", "_mapping_is_long_shape",
    "_normalize_mapping", "_safe_read_excel", "_safe_excel_sheets",
    "get_columns_fast", "_valid_time_cols", "_is_point_sequence", "_time_labels_for_cols",
]

DEFAULT_ENCODING_LIST = ['utf-8-sig', 'utf-8', 'gb18030', 'gbk', 'gb2312', 'latin1']

LARGE_FILE_SIZE = 50 * 1024 * 1024

log = logging.getLogger(__name__)

_warned_dup_cols = set()

_warned_dup_lock = threading.Lock()

def _resolve_col(df, col_name):
    """返回 df 里与 col_name 对应的真实列名；找不到返回 None。

    v2.3.15fix: 只做「完全等价」的归一化匹配（strip + 全角转半角），
    不做模糊匹配——模糊匹配留给 _safe_get_col。

    为什么需要它：前置检查原本用 `c not in df.columns` 精确匹配，于是列名带
    前后空格（" 采集时间 "）或全角括号（"负荷（kW）"）时，整个文件被判为
    「未找到所需列」——而 _safe_get_col 本来有能力处理这两种情况，只是
    永远跑不到。实测：列名 " 采集时间 " + mapping "采集时间" → 直接报错。
    """
    if col_name in df.columns:
        return col_name
    # 常见全角标点折叠：_normalize_fullwidth_chars 不转括号，
    # 而「负荷（kW）」与「负荷(kW)」在中文输入法下经常混用。
    _PUNCT = str.maketrans({'（': '(', '）': ')', '：': ':', '，': ',',
                            '；': ';', '、': ',', '／': '/', '　': ' '})

    def _fold(s):
        return _normalize_fullwidth_chars(str(s)).translate(_PUNCT)

    target = _fold(col_name)
    for c in df.columns:
        if _fold(c) == target:
            return c
    return None


def _safe_get_col(df, col_name):
    """安全获取列，支持近似匹配和全角字符处理"""
    from difflib import SequenceMatcher
    normalized_col_name = _normalize_fullwidth_chars(str(col_name))
    if normalized_col_name in df.columns:
        return df[normalized_col_name]
    if col_name in df.columns:
        return df[col_name]
    best_score = 0
    best_col = None
    for c in df.columns:
        nc = _normalize_fullwidth_chars(str(c))
        score = SequenceMatcher(None, normalized_col_name.lower(), nc.lower()).ratio()
        if score > best_score:
            best_score = score
            best_col = c
    if best_score >= 0.85:
        # v2.3.9fix(P2-14): 阈值从 0.6 提到 0.85。中文列名两个字相同就能过 0.6，
        # 「负荷1 找不到 → 自动用负荷2」，算出的是另一列数据，只在日志里留一条 info。
        log.warning(f"列名 '{col_name}' 不存在，自动替换为最相似列 '{best_col}' (相似度 {best_score:.2f})")
        col_name = best_col
    else:
        raise KeyError(f"列 '{col_name}' 不存在，可用列: {list(df.columns)}")
    series = df[col_name]
    if isinstance(series, pd.DataFrame):
        warn_key = (id(df), col_name)
        with _warned_dup_lock:
            if warn_key not in _warned_dup_cols:
                _warned_dup_cols.add(warn_key)
        series = series.iloc[:, 0]
    return series

# 月份解析失败时的统一返回值。
# 用它而不是 None/NaN：_extract_ym 的返回值会被多处消费，
# 允许返回字符串就必须同时提供一个判断谓词，否则消费方会误用
# .notna()（字符串恒为 True）。
UNKNOWN_MONTH = "未知月份"


def _is_valid_ym(v) -> bool:
    """_extract_ym 的返回值是否是一个真实可用的年月。"""
    if v is None:
        return False
    try:
        if pd.isna(v):
            return False
    except (TypeError, ValueError):
        pass
    return str(v).strip() not in (UNKNOWN_MONTH, "", "nan", "NaT")


def _ym_valid_ratio(series, sample_n: int = 20) -> float:
    """一列值里能解析出真实年月的比例（抽样前 sample_n 个）。"""
    if series is None or len(series) == 0:
        return 0.0
    vals = series.head(sample_n)
    hit = sum(1 for v in vals if _is_valid_ym(_extract_ym(v)))
    return hit / max(len(vals), 1)


def _looks_yearless(raw) -> bool:
    """这个日期字符串是不是「没有年份」的形式（会被补成今年）。

    只认明确无年份的两段式：M月D日 / M月 / M-D / M/D。
    带年份的（2026-01-15、1/15/2026）不算 —— 它们不会被补年份。
    """
    t = str(raw).strip()
    if not t:
        return False
    if re.fullmatch(r"\d{1,2}\s*月(?:\s*\d{1,2}\s*日?)?", t):
        return True
    m = re.fullmatch(r"(\d{1,2})[-/](\d{1,2})", t)
    if m:
        try:
            return 1 <= int(m.group(1)) <= 12 and 1 <= int(m.group(2)) <= 31
        except Exception:
            return False
    return False

def _extract_ym(d):
    """从任意日期表示里取 YYYY-MM。

    v2.3.8fix: 数字要先判 Excel 序列号，不能直接丢给 pd.to_datetime ——
    后者把数字当纳秒，任何数字都会落到 1970-01（epoch）。
    用户看到的「未找到月份 '1970-01'」就是这么来的。

    序列号有效区间取 20000~80000（1954-10 ~ 2119-01），超出即视为无效值。
    0 与空值一律返回「未知月份」，不再伪造出一个 1970-01。
    """
    raw = str(d).strip()
    # v2.3.8fix: 原来是 \d{5}，4 位数（如户号 1002）会漏到 pd.to_datetime，
    # 被宽松解析成「公元 1002 年 1 月」。改为任意纯数字都先走区间判断：
    # 只有 20000~80000 才是合法 Excel 序列号，其余一律未知月份。
    if re.fullmatch(r"\d+(?:\.\d+)?", raw):
        try:
            days = float(raw)
            if 20000 <= days <= 80000:
                # Excel 的序列号原点：1899-12-30（含 1900 闰年 bug 修正）
                dt = pd.Timestamp("1899-12-30") + pd.Timedelta(days=days)
                return dt.strftime("%Y-%m")
        except Exception:
            pass
        return "未知月份"

    # M/D 与 M-D 同义的无年份日期。必须在 pd.to_datetime 之前处理：
    # '1/32' 会被 pd.to_datetime 当成「1 月 / 32 年」→ 2032-01，
    # 年份 >= 1900 就跑不进任何合理性检查。这里先接下并校验日号。
    _md3 = re.match(r"^(\d{1,2})[-/](\d{1,2})$", raw)
    if _md3:
        _mm, _dd = int(_md3.group(1)), int(_md3.group(2))
        if 1 <= _mm <= 12 and 1 <= _dd <= 31:
            import datetime as _dt3
            _y3 = _dt3.date.today().year
            log.warning(f"日期 '{raw}' 无年份，按 {_y3} 年处理；如属往年请指定年份")
            return "%s-%02d" % (_y3, _mm)
        return UNKNOWN_MONTH

    try:
        # 用 raw（已转字符串）而不是 d：pd.to_datetime(0) 会得到 1970-01，
        # 而 pd.to_datetime('0') 是 NaT。数字一律只能走上面的序列号分支。
        dt = pd.to_datetime(raw, errors="coerce")
        if pd.notna(dt):
            # 年份 < 1900 一律视为解析失败：0001-01 是 pd.to_datetime 对
            # 无法识别的格式给出的「像日期的东西」，比报错更难发现。
            if dt.year < 1900:
                log.warning(f"日期 '{raw}' 解析出年份 {dt.year}，视为无法识别")
                return UNKNOWN_MONTH
            return dt.strftime("%Y-%m")
    except Exception:
        pass
    # C8：中文年月日 / 无年份日期。
    # 这三种格式此前全部落到 pd.to_datetime 并被解析成公元 1 年（0001-01），
    # 看起来像个日期，比直接报错更糟。
    # v2.3.8fix: 「份」是中文里最常见的月份写法（“2026 年 7 月份”），
    # 原正则只认「月」不认「月份」，整表会返回「未知月份」→ 扫描层 continue →
    # 整月数据静默消失（实测：同样五组日期写法，只有带「份」的被丢）。
    _cn = re.match(r"^(\d{4})\s*年\s*(\d{1,2})\s*月\s*(?:份)?\s*(?:\d{1,2}\s*日?)?$", raw)
    if _cn:
        return "%s-%02d" % (_cn.group(1), int(_cn.group(2)))

    _md = re.match(r"^(\d{1,2})\s*月\s*(?:份)?\s*(?:\d{1,2}\s*日?)?$", raw)
    if _md:
        import datetime as _dt
        _y = _dt.date.today().year
        log.warning(f"日期 '{raw}' 无年份，按 {_y} 年处理；如属往年请指定年份")
        return "%s-%02d" % (_y, int(_md.group(1)))

    _dash = re.match(r"^(\d{1,2})-(\d{1,2})$", raw)
    if _dash and 1 <= int(_dash.group(1)) <= 12:
        import datetime as _dt
        _y = _dt.date.today().year
        log.warning(f"日期 '{raw}' 无年份，按 {_y} 年处理；如属往年请指定年份")
        return "%s-%02d" % (_y, int(_dash.group(1)))

    s = str(d).strip()
    m = re.match(r'^(\d{4}-\d{2})$', s)
    if m:
        return m.group(1)
    m2 = re.match(r'^(\d{4})-(\d{1,2})$', s)
    if m2:
        return f"{m2.group(1)}-{int(m2.group(2)):02d}"
    try:
        dt2 = pd.to_datetime(s, errors='coerce', dayfirst=False)
        if pd.notna(dt2):
            return dt2.strftime('%Y-%m')
    except Exception:
        pass
    log.warning(f"无法从 '{s}' 提取有效年月，标记为未知月份")
    return '未知月份'

def _normalize_asset(val, fallback='未命名'):
    if val is None:
        return fallback
    if isinstance(val, float) and np.isnan(val):
        return fallback
    s = str(val).strip()
    if s.lower() in ('', 'nan', '<na>', 'null', 'none'):
        return fallback
    return s

def _detect_accumulated_pattern(df, load_col, date_col, time_col=None, sample_rows=200):
    try:
        sub = df.head(sample_rows).copy()
        if load_col not in sub.columns:
            return False
        if date_col and date_col in sub.columns:
            sub['__ts__'] = pd.to_datetime(sub[date_col].astype(str), errors='coerce')
            if time_col and time_col in sub.columns:
                sub['__ts__'] = pd.to_datetime(
                    sub[date_col].astype(str) + ' ' + sub[time_col].astype(str),
                    errors='coerce'
                )
            sub = sub.dropna(subset=['__ts__']).sort_values('__ts__')
        else:
            sub = sub.sort_index()
        vals = pd.to_numeric(sub[load_col], errors='coerce').dropna().values
        if len(vals) < 10:
            return False
        diffs = np.diff(vals)
        non_neg_ratio = np.sum(diffs >= 0) / len(diffs)
        if np.max(vals) > 0 and np.min(vals) >= 0:
            span_ratio = np.max(vals) / (np.min(vals) + 1e-9)
        else:
            span_ratio = 1
        return non_neg_ratio >= 0.85 and (span_ratio > 20 or np.max(vals) > 1000)
    except Exception:
        log.debug("累积示数检测异常", exc_info=True)
        return False

def _convert_accumulated_to_power(df, date_col, time_col, load_col, asset_col=None, cancel_event=None):
    needed = [date_col, load_col]
    if time_col:
        needed.append(time_col)
    if asset_col:
        needed.append(asset_col)
    if any(c not in df.columns for c in needed):
        return df

    try:
        dt_series = df[date_col].astype(str)
        if time_col:
            dt_series = dt_series + ' ' + df[time_col].astype(str)
        df['__ts__'] = pd.to_datetime(dt_series, errors='coerce')
        df = df.dropna(subset=['__ts__']).sort_values(['__ts__'])

        # v2.3.3fix(I3): 移植 Ultra v2.9c 的日重置/月累计判别。
        # 原 Fusion 无此保护：月度累计表会被直接差分，跨天跳变污染整条曲线。
        load_num = pd.to_numeric(df[load_col], errors='coerce')
        df['__date__'] = df['__ts__'].dt.date
        daily_min = df.groupby('__date__')[load_col].transform('min')
        # 原判据：某日读数全部远离全表中位数*0.3 → 判为非日重置
        non_reset_days = (daily_min > load_num.median() * 0.3)
        if non_reset_days.mean() > 0.5:
            # v2.3.3fix(I5): 补本质判据——跨天是否回落。
            # 日累计表即便日初基准非 0（如每天从 1000 起），跨天也会回落；
            # 月度累计表跨天是连续递增，不会回落。原判据只认「日初读数接近 0」，
            # 会把基准非 0 的日累计表误判成月度累计而拒绝转换。
            _reset_like = False
            try:
                _first = pd.to_numeric(df.groupby('__date__')[load_col].first(), errors='coerce')
                _last = pd.to_numeric(df.groupby('__date__')[load_col].last(), errors='coerce')
                if len(_first) >= 2:
                    _cross = _first.values[1:] - _last.values[:-1]
                    _cross = _cross[~np.isnan(_cross)]
                    if len(_cross) and (np.sum(_cross < 0) / len(_cross)) > 0.5:
                        _reset_like = True
                else:
                    # v2.3.3fix(I9): 单日数据无跨天回落，差分必然正确（常见：只导一天）
                    _reset_like = True
            except Exception:
                _reset_like = False
            if not _reset_like:
                log.error('示数表检测:数据可能为非日重置累积表(月度累计),无法通过差分计算功率.请改用「长表」格式.')
                df = df.drop(columns=['__ts__', '__date__'])
                # v2.3.9fix(P1-8): 打标记，由 _parse_long_to_standard 转成明确错误。
                # 原实现静默返回原始示数，下游把它当功率画，曲线单调上升且无任何提示。
                df.attrs['accumulated_error'] = '数据可能是月度累计表（跨天不回落），无法通过差分计算功率'
                return df
        # v2.3.3fix(I7): 标记跨天边界——日重置表 23:45→次日 00:00 的回落是正常现象。
        # shift(-1)：示数差归属到「起始时间」，所以跨天回落落在当天最后一个点上。
        df['__crossday__'] = df['__date__'].ne(df['__date__'].shift(-1))
        df = df.drop(columns=['__date__'])

        group_cols = [asset_col] if asset_col and asset_col in df.columns else []
        new_load = pd.Series(index=df.index, dtype=float)

        for _, grp in (df.groupby(group_cols) if group_cols else [((), df)]):
            if cancel_event and cancel_event.is_set():
                return df
            grp = grp.sort_values('__ts__')
            loads = pd.to_numeric(grp[load_col], errors='coerce')
            times = grp['__ts__']
            delta_hours = times.diff().dt.total_seconds() / 3600.0
            valid_delta = delta_hours[delta_hours > 0]
            if len(valid_delta) > 2:
                mean_delta = valid_delta.mean()
                std_delta = valid_delta.std()
                if std_delta / mean_delta > 0.5:
                    log.warning("累计示数时间间隔不均匀，转换功率可能失真。建议检查数据完整性。")
            # v2.3.3fix(I4): 移植 Ultra v2.28 的 diff 前移。
            # 原实现 loads.diff() 把差值记在后一个时间点上，整条曲线右偏一个采样步长。
            raw_diff = loads.diff().shift(-1)
            delta_hours = times.diff().dt.total_seconds() / 3600.0
            delta_hours_shifted = delta_hours.shift(-1)
            safe_delta = delta_hours_shifted.clip(lower=0.05, upper=3.0)
            power = raw_diff / safe_delta
            # v2.3.12fix: 跨天边界的点是伪差值（次日首点 − 当日本末点），
            # 日重置表里必然是大负数，必须置 NaN。
            # 原实现（v2.3.3fix I7）只把它从「换表/清零报警」名单里摘出来，
            # 值本身仍保留——于是曲线上出现 -1520 这种尖刺，还把均值拉低
            # （实测 96 点日重置表均值从 16 掉到 7.96）。
            if '__crossday__' in grp.columns:
                _cross = grp['__crossday__'].fillna(False).astype(bool)
                if _cross.any():
                    power[_cross] = np.nan
            # v2.3.3fix(I4): 换表/清零导致的负跳变标记为 NaN（同 Ultra v2.9c）
            if len(power.dropna()) > 1:
                avg_step = max(power.dropna().abs().median(), 0.001)
                neg_spikes = power[power < -5.0 * avg_step]
                if len(neg_spikes) > 0:
                    log.warning(f'检测到 {len(neg_spikes)} 处可能的换表/清零导致的负跳变,已标记为 NaN')
                    power[neg_spikes.index] = np.nan
            new_load[grp.index] = power

        df = df.dropna(subset=['__ts__']).copy()
        df[load_col] = new_load
        df = df.dropna(subset=[load_col])
        df = df.drop(columns=['__ts__', '__crossday__'], errors='ignore')
        log.info("累计示数已自动转换为平均功率（差值/时间间隔）")
        return df
    except Exception as e:
        log.warning(f"累计示数转换失败: {e}")
        for _c in ('__ts__', '__date__', '__crossday__'):
            if _c in df.columns:
                df = df.drop(columns=[_c])
        # v2.3.9fix(P1-8): 不再静默回退原始示数
        df.attrs['accumulated_error'] = str(e)
        return df

def _parse_long_to_standard(df, date_col, time_col, load_col, multiplier=1.0,
                            asset_col=None, cancel_event=None, convert_accumulated=False,
                            fallback_asset='未命名'):
    needed_cols = [date_col, load_col]
    if time_col and time_col != date_col:
        needed_cols.append(time_col)
    if asset_col:
        needed_cols.append(asset_col)
    resolved = {}
    for c in needed_cols:
        r = _resolve_col(df, c)
        if r is None:
            resolved[c] = None
        else:
            resolved[c] = r
    if any(v is None for v in resolved.values()):
        missing = [k for k, v in resolved.items() if v is None]
        raise KeyError(f"缺少列: {missing}")
    # 按真实列名选列，再改名回规范名，下游代码不用改
    df = df[[resolved[c] for c in needed_cols]].copy()
    df = df.rename(columns={v: k for k, v in resolved.items()})
    if convert_accumulated:
        df = _convert_accumulated_to_power(df, date_col, time_col, load_col, asset_col, cancel_event)
        # v2.3.9fix(P1-8): 差分失败必须报错，不能拿累计示数当功率画
        _acc_err = df.attrs.get('accumulated_error')
        if _acc_err:
            raise ValueError(f"累计示数转换失败：{_acc_err}")

    date_series = _safe_get_col(df, date_col)
    if date_series.empty:
        return pd.DataFrame(), 1.0
    df[date_col] = _clean_series(date_series)
    if time_col and time_col in df.columns:
        time_series = _safe_get_col(df, time_col)
        df[time_col] = _clean_series(time_series)
    combined = df[date_col].astype(str)
    if time_col and time_col != date_col:
        combined = combined + ' ' + df[time_col].astype(str)
    if isinstance(combined, pd.DataFrame):
        combined = combined.iloc[:, 0]
    df['__dt__'] = _to_dt(combined)
    load_series = _safe_get_col(df, load_col)
    before = len(df)
    # v2.3.15fix: 千分位数字（"1,234.5"）会被 to_numeric 变成 NaN，整行丢掉。
    # 真实场景：Excel 单元格存成文本 + 千分位格式，或 CSV 里带引号的 "1,234.5"。
    # 只剥离「逗号后正好三位数字」的情况，不动欧洲小数逗号（"1,5" → 仍是 1.5 的意思，不剥）。
    load_text = _clean_series(load_series)
    if load_text.str.contains(',', na=False).any():
        load_text = load_text.str.replace(r'(?<=\d),(?=\d{3}(?:\D|$))', '', regex=True)
    df['__load__'] = pd.to_numeric(load_text, errors='coerce') * multiplier
    df_clean = df.dropna(subset=['__dt__', '__load__'])
    dropped = before - len(df_clean)
    ratio = dropped / before if before > 0 else 0.0
    if dropped > 0:
        log.info(f"长表解析：{before}行，丢弃{dropped}行 ({ratio:.1%})")

    df_clean['__ym__'] = df_clean['__dt__'].dt.strftime('%Y-%m')
    df_clean['__date__'] = df_clean['__dt__'].dt.date

    if asset_col and asset_col in df_clean.columns:
        df_clean['__asset__'] = df_clean[asset_col].apply(lambda x: _normalize_asset(x))
    else:
        # v2.3.5fix(K2): 必须与 _scan_long_chunk 的兜底同源。
        # 原实现写死 '未命名'，而扫描阶段用的是 _extract_asset_from_name。
        # 文件名带表号（表号123.xlsx）且无资产列时两边对不上，整表被筛空，
        # 报「资产号 '123' 在月份 'xxx' 无数据」。这是 v2.3.2 修复在长表分支的遗漏。
        df_clean['__asset__'] = fallback_asset or '未命名'

    return df_clean, ratio

def _detect_csv_delimiter(filepath, sample_bytes=8192):
    try:
        with open(filepath,'r',encoding='utf-8-sig',errors='replace') as f:
            sample = f.read(sample_bytes)
        dialect = csv.Sniffer().sniff(sample, delimiters=[',',';','\t','|'])
        return dialect.delimiter
    except Exception:
        log.debug("CSV分隔符检测失败，使用默认逗号。", exc_info=True)
    return ','

def _detect_enc(fp,size=50000):
    try:
        with open(fp,'rb') as f:
            if f.read(3)==b'\xef\xbb\xbf':
                return 'utf-8-sig'
        try:
            import chardet
            with open(fp,'rb') as f:
                raw = f.read(size)
            detected = chardet.detect(raw)
            if detected and detected.get('encoding'):
                enc = detected['encoding'].lower()
                if enc in ('gb2312','gbk','gb18030'):
                    return 'gb18030'
                if enc == 'big5':
                    return 'big5'
                return enc
        except Exception:
            log.debug("chardet 检测失败，回退顺序尝试。", exc_info=True)
    except Exception:
        log.debug("文件编码检测异常", exc_info=True)
    for e in DEFAULT_ENCODING_LIST:
        try:
            with open(fp,'r',encoding=e) as f:
                f.read(4096)
            # v2.3.5fix(C5): latin1 对任何字节都能解码，永不抛异常。
            # 落到它意味着「前面的编码全部识别失败」，读出来会是满表错字
            # 但看起来正常——这比直接打不开更危险，必须留下痕迹。
            if e == 'latin1':
                log.warning(f"编码无法确定，回退到 latin1（可能整表乱码）: {fp}")
            return e
        except Exception:
            continue
    return 'utf-8'

def _get_excel_engine(ext):
    """返回可用的 Excel 读取引擎。

    v2.3.4fix(J5): 原实现用 `'python-calamine'.split('-')[0]` 取模块名得到 'python'，
    永远 import 失败——calamine 装了也不会被选中。且 .xlsb 会被归到 openpyxl 分支
    （openpyxl 读不了 xlsb）。现改为显式（引擎, 模块名）表 + 扩展名约束。
    """
    if ext == '.csv':
        return None
    for engine, modname in (
        ('calamine', 'python_calamine'),
        ('pyxlsb', 'pyxlsb'),
        ('xlrd', 'xlrd'),
        ('openpyxl', 'openpyxl'),
    ):
        try:
            __import__(modname)
        except ImportError:
            continue
        if engine == 'pyxlsb' and ext != '.xlsb':
            continue
        if engine == 'xlrd' and ext != '.xls':
            continue
        if engine == 'openpyxl' and ext in ('.xls', '.xlsb'):
            continue
        return engine
    log.warning("Excel 引擎自动检测失败，使用 pandas 默认引擎")
    return None

def _safe_read_excel(fp, sheet=None, nrows=None, header=0):
    """安全的Excel读取，自动选择可用引擎"""
    ext = os.path.splitext(fp)[1].lower()
    engine = _get_excel_engine(ext)
    kwargs = {'header': header}
    if sheet:
        kwargs['sheet_name'] = sheet
    if nrows:
        kwargs['nrows'] = nrows
    if engine:
        kwargs['engine'] = engine
    return pd.read_excel(fp, **kwargs)

def _safe_excel_sheets(fp):
    """安全的Excel sheet列表获取"""
    ext = os.path.splitext(fp)[1].lower()
    engine = _get_excel_engine(ext)
    try:
        if engine:
            xl = pd.ExcelFile(fp, engine=engine)
        else:
            xl = pd.ExcelFile(fp)
        return xl.sheet_names
    except Exception as _e:
        log.warning(f"Excel文件解析失败({fp}): {_e}")
        return []

def get_columns_fast(fp, sheet=None, hrow=0, enc=None, delimiter=','):
    ext = os.path.splitext(fp)[1].lower()
    if ext=='.csv':
        e = enc if enc and enc!='auto' else _detect_enc(fp)
        try:
            df = pd.read_csv(fp, encoding=e, nrows=hrow+1, header=hrow, sep=delimiter, engine='python')
        except Exception:
            # v2.3.8fix: 回退分支原来没有 try。空文件在 sep=None 时招 pandas
            # `_csv.Error: Could not determine delimiter`，直接逃到 UI 的「无法读取列名」。
            # 现在统一返回空列表，让调用方走「未能识别任何列名」的明确分支。
            try:
                df = pd.read_csv(fp, encoding=e, nrows=hrow+1, header=hrow, sep=None, engine='python')
            except Exception as _e2:
                log.error(f"CSV列名读取失败({fp}): {_e2}")
                return []
    else:
        # v2.3fix: 引擎自动检测 + 空DataFrame保护
        try:
            df = _safe_read_excel(fp, sheet=sheet, nrows=hrow+1, header=hrow)
        except Exception as _e:
            log.error(f"Excel列名读取失败({fp}): {_e}")
            return [f"列{i+1}" for i in range(10)]  # 兜底列名
    # v2.3.8fix: 0 行 ≠ 没有列名。
    # 原判据是 `df is None or df.empty`，而「只有表头、没有数据行」的表格
    # df.empty 为 True 但列名完全有效——旧代码会把真实列名丢掉、伪造「列1..列10」，
    # 用户在映射界面看到 10 个不存在的列，手选后必然对不上。
    if df is None:
        log.error(f"文件读取失败({fp})，返回占位列名")
        return [f"列{i+1}" for i in range(10)]
    cols = [str(c) for c in df.columns]
    if not cols:
        log.warning(f"文件无任何列({fp})，返回占位列名")
        return [f"列{i+1}" for i in range(10)]
    if all(c.startswith('Unnamed:') for c in cols):
        cols = [f"列{i+1}" for i in range(len(cols))]
    return cols

def _read_file_full(fp, sheet=None, hrow=0, enc=None, delimiter=',', force_chunk=False):
    ext = os.path.splitext(fp)[1].lower()
    if ext == '.csv':
        e = enc if enc and enc != 'auto' else _detect_enc(fp)
        try:
            fsize = os.path.getsize(fp)
        except Exception:
            fsize = -1  # v2.3fix: -1表示未知大小，强制分块保护内存
        base_kwargs = {
            'encoding': e,
            'header': hrow,
            'sep': delimiter,
        }
        # v2.3.5fix(C5): latin1 是「编码识别失败」的兜底，坏字节用替换符而不是抛异常
        if e == 'latin1':
            base_kwargs['encoding_errors'] = 'replace'
        # v2.3.5fix(C6): 坏行跳过而不是让整表失败。
        # 注意：on_bad_lines 传 callable 只有 python / pyarrow 引擎支持，
        # C 引擎会抛 ValueError（pandas 实测）。C 引擎比 Python 引擎快数倍，
        # 为记行号降级整条链路代价不成比例，故主路径用 'skip'，
        # 仅在回退到 Python 引擎时（见下方 except 分支）挂 callable 记录行号。
        base_kwargs['on_bad_lines'] = 'skip'
        _bad_lines = []
        if fsize < 0 or fsize > LARGE_FILE_SIZE or force_chunk:
            chunks = []
            reader_kwargs = base_kwargs.copy()
            reader_kwargs.update({'chunksize': 100000, 'engine': 'c', 'low_memory': False})
            try:
                reader = pd.read_csv(fp, **reader_kwargs)
            except Exception:
                log.warning(f"C 引擎读取失败，回退至 Python 引擎: {fp}")
                reader_kwargs.pop('low_memory', None)
                reader_kwargs['engine'] = 'python'

                # v2.3.5fix(C6): Python 引擎支持 callable，此时尽量把坏行记下来
                def _on_bad_line(line):
                    _bad_lines.append(line)
                    return None
                reader_kwargs['on_bad_lines'] = _on_bad_line
                reader = pd.read_csv(fp, **reader_kwargs)
            for chunk in reader:
                chunk.columns = [str(c) for c in chunk.columns]
                seen = {}
                new_cols = []
                for c in chunk.columns:
                    if c in seen:
                        seen[c] += 1
                        new_cols.append(f"{c}_{seen[c]}")
                    else:
                        seen[c] = 0
                        new_cols.append(c)
                chunk.columns = new_cols
                for c in chunk.select_dtypes(include=['object', 'string']).columns:
                    try:
                        chunk[c] = _clean_series(chunk[c])
                    except Exception as _cle:
                        # v2.3.7fix: 原来是裸 pass，清洗失败连痕迹都没留。
                        # 整块路径那边一直有 log.debug，分块路径反而漏了——
                        # 同一个动作两处实现，只给一处加日志就是这种结果。
                        log.debug(f"分块列清洗失败 [{c}]: {_cle}")
                chunks.append(chunk)
            df = pd.concat(chunks, ignore_index=True)
            if _bad_lines:
                log.warning(f"CSV 有 {len(_bad_lines)} 行列数不匹配，已跳过: {fp}")
            else:
                log.debug(f"CSV 读取完成（C 引擎，坏行若存在已被 skip，无法定位行号）: {fp}")
            return df
        else:
            read_kwargs = base_kwargs.copy()
            read_kwargs.update({'low_memory': False, 'engine': 'c'})
            try:
                df = pd.read_csv(fp, **read_kwargs)
            except Exception:
                log.warning(f"C 引擎读取失败，回退至 Python 引擎: {fp}")
                read_kwargs.pop('low_memory', None)
                read_kwargs['engine'] = 'python'

                # v2.3.5fix(C6): 同上，Python 引擎下记录坏行
                def _on_bad_line(line):
                    _bad_lines.append(line)
                    return None
                read_kwargs['on_bad_lines'] = _on_bad_line
                df = pd.read_csv(fp, **read_kwargs)
            if _bad_lines:
                log.warning(f"CSV 有 {len(_bad_lines)} 行列数不匹配，已跳过: {fp}")
    else:
        # v2.3fix: 引擎自动检测
        df = _safe_read_excel(fp, sheet=sheet, header=hrow)
    # v2.3fix: 空DataFrame保护
    df.columns = [str(c) for c in df.columns]
    seen = {}
    new_cols = []
    for c in df.columns:
        if c in seen:
            seen[c] += 1
            new_cols.append(f"{c}_{seen[c]}")
        else:
            seen[c] = 0
            new_cols.append(c)
    df.columns = new_cols
    for c in df.select_dtypes(include=['object', 'string']).columns:
        try:
            df[c] = _clean_series(df[c])
        except Exception as _cle:
            log.debug(f"列清洗失败 [{c}]: {_cle}")
    return df

def _to_dt(series, fmt=None):
    # v3.2fix: 日期列无年份时补今年
    if isinstance(series, pd.Series):
        s_str = series.astype(str)
        # v2.3.15fix: 中文日期先归一。Excel 里显示为「2026年7月1日」的列，
        # 如果被读成文本（常见于 CSV / 合并单元格 / 文本格式单元格），
        # pd.to_datetime 直接给 NaT，整批行被静默丢掉。
        # 顺序：先带年的，再处理无年的（无年的会接下面的补今年逻辑）。
        if s_str.str.contains('年', na=False).any():
            s_str = s_str.str.replace(
                r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?', r'\1-\2-\3', regex=True)
            s_str = s_str.str.replace(
                r'(\d{4})\s*年\s*(\d{1,2})\s*月', r'\1-\2', regex=True)
        if s_str.str.contains('月', na=False).any():
            s_str = s_str.str.replace(
                r'(\d{1,2})\s*月\s*(\d{1,2})\s*日', r'\1-\2', regex=True)
        # v2.3.15fix: 6 位纯数字 YYMMDD（"260701"）原实现被 pandas 当杂格式，
        # 实测解成 2001-07-26——不报错但整批日期错位 25 年。
        # 只在整体都是 6 位数字时才按 YYMMDD 处理，不掺到其他格式里。
        _m6 = s_str.str.fullmatch(r'\d{6}', na=False)
        if _m6.any() and _m6.all():
            s_str = ('20' + s_str.str[:2] + '-' + s_str.str[2:4] + '-' + s_str.str[4:6])
        series = s_str if not s_str.equals(series.astype(str)) else series
        s_str = series.astype(str)
        # 检测月-日格式 (如"01-01", "01-01 00:00", "6/20 14:30", 无年份)
        needs_year = s_str.str.match(r'^\d{1,2}[-/.]\d{1,2}(\s+\d{1,2}:\d{2}(:\d{2})?)?$', na=False)
        if needs_year.any():
            series = series.copy()
            # v2.3.8fix: 补年份时把分隔符统一成 '-'。
            # 原来直接拼 f"{year}/" + '01-01 24:00' 得到 '2026/01-01 24:00'，
            # 下面的 24:00 分支用 \d{4}-\d{2}-\d{2} 提取日期，混合分隔符匹配不上
            # → NaT → 该行被 dropna 丢掉。每天最后一个采样点静默消失（实测确认）。
            _patched = s_str[needs_year].str.replace(r'[/.]', '-', regex=True)
            series[needs_year] = str(datetime.now().year) + '-' + _patched
    dt_series = pd.to_datetime(series, format=fmt, errors='coerce') if fmt else pd.to_datetime(series, errors='coerce')
    if isinstance(series, pd.Series):
        mask_24 = series.astype(str).str.contains(r'24:00', na=False)
        if mask_24.any():
            # v2.3.8fix: 月份/日可能不补零（2026-1-1），正则要允许 1~2 位。
            date_part = series.astype(str).str.extract(r'(\d{4}-\d{1,2}-\d{1,2})')[0]
            dt_24 = pd.to_datetime(date_part, errors='coerce') + pd.Timedelta(days=1)
            dt_series[mask_24] = dt_24[mask_24]
    return dt_series

def _is_point_sequence(cols):
    """整组列名都是纯数字且最大 >24：判定为采样点序号（1..N）。

    v2.3.9fix(P2-15): 一天不可能有超过 24 个「小时」，所以 1..96 / 1..288
    这类列名必然是点位序号，不是时刻。判定放在一处，
    `_valid_time_cols` / `_time_labels_for_cols` / 累积示数差分共用。
    """
    nums = []
    for c in cols:
        s = str(c).strip()
        if not re.fullmatch(r'\d+', s):
            return False
        nums.append(int(s))
    return len(nums) >= 2 and max(nums) > 24


def _time_labels_for_cols(cols):
    """把时刻列名转成 HH:MM 标签，返回 (labels, error)。

    v2.3.9fix(P2-15): 采样点序号（1..96 / 1..48 / 1..288）按 24h/点数 推算粒度；
    除不尽时返回明确错误，不再让下游报「日期无法识别」——那个文案会把人
    引去查日期格式，而真正的问题在列名。
    """
    if _is_point_sequence(cols):
        n = len(cols)
        lo = min(int(str(c).strip()) for c in cols)
        hi = max(int(str(c).strip()) for c in cols)
        if n == 0 or 1440 % n != 0:
            return None, (
                f"列名看起来是采样点序号（{lo}..{hi}），但 {n} 点无法均分 24 小时，"
                f"请改用 HH:MM 格式的列名")
        step = 1440 // n
        return [f"{(i * step) // 60:02d}:{(i * step) % 60:02d}" for i in range(n)], None
    return [_fmt_time(t) for t in cols], None


def _valid_time_cols(cols):
    # v2.3.9fix(P2-15): 整组纯数字且最大 >24 → 采样点序号（1..96）。
    # 按数值排序返回，让数据能全部取到；标签由 _time_labels_for_cols 推算。
    # 注意：不是剔除。剔除会让「以前能出图」的表变成完全不能用，
    # 而且错误会被归到「日期无法识别」上。
    if _is_point_sequence(cols):
        return sorted(cols, key=lambda c: int(str(c).strip()))
    valid = []
    for c in cols:
        normalized = _normalize_time_column_name(str(c))
        m = _parse_min(normalized)
        if m!=INVALID_MINUTES:
            valid.append((m,c))
    valid.sort(key=lambda x: x[0])
    return [c for _,c in valid]

def _detect_wide_matrix(df: pd.DataFrame, cols, max_sample=100):
    if df.empty:
        return None, None
    sample = df.head(max_sample)
    numeric_flags = {}
    for c in cols:
        s = sample[c].dropna()
        if len(s) == 0:
            numeric_flags[c] = 0.0
            continue
        try:
            ratio = pd.to_numeric(s, errors='coerce').notna().mean()
        except Exception:
            ratio = 0.0
        numeric_flags[c] = ratio
    time_cols = []
    for c in cols:
        if numeric_flags.get(c, 0) >= 0.9:
            normalized = _normalize_time_column_name(c)
            mins = _parse_min(normalized)
            if mins != INVALID_MINUTES:
                time_cols.append((c, mins))
    if len(time_cols) < 2:
        return None, None
    best_start = 0
    best_len = 1
    cur_start = 0
    for i in range(1, len(time_cols)):
        if time_cols[i][1] >= time_cols[i-1][1]:
            if i - cur_start + 1 > best_len:
                best_len = i - cur_start + 1
                best_start = cur_start
        else:
            cur_start = i
    if best_len < 2:
        return None, None
    load_candidates = [tc[0] for tc in time_cols[best_start:best_start+best_len]]
    left_idx = cols.index(load_candidates[0]) - 1
    date_col = None
    # v2.3.8fix: 原先用 .apply(_extract_ym).notna().any() 判断。
    # _extract_ym 现在对无效值返回字符串「未知月份」而不是 NaN，
    # 字符串的 .notna() 恒为 True —— 任何列都会通过，包括「户号」。
    # 改为「抽样里至少 60% 能解析出真实年月」。
    if left_idx >= 0:
        if _ym_valid_ratio(sample[cols[left_idx]]) >= 0.6:
            date_col = cols[left_idx]
    if date_col is None:
        date_col = next((c for c in cols if '日期' in c or 'date' in c.lower()), None)
    return date_col, load_candidates

def _detect_transposed_wide(df: pd.DataFrame, cols, max_sample=100):
    if len(cols) < 3:
        return None
    sample = df.head(max_sample)
    for time_idx in range(min(3, len(cols))):
        time_col = cols[time_idx]
        time_vals = sample[time_col].dropna().apply(lambda x: _parse_min(_normalize_time_column_name(str(x))))
        if len(time_vals) == 0:
            continue
        valid_ratio = (time_vals != INVALID_MINUTES).mean()
        if valid_ratio >= 0.8:
            # v2.3.9fix(C9): 时刻列必须真的像「一天里的时刻序列」。
            # 原判据只看「值能否解析成时间」，而 _parse_min('0') 是成功的——
            # 宽表行累计表的 00:00 列每天重置为 0，几个 0 全被当成时刻 00:00，
            # 整表因此被判成转置宽表，输出 [95,1] 垃圾且不报错。
            # 真正的转置表时刻列有三个硬特征：数量足够、互不重复、覆盖一天。
            valid = time_vals[time_vals != INVALID_MINUTES]
            if len(valid) < 2:
                continue
            _uniq_ratio = valid.nunique() / float(len(valid))
            _span = float(valid.max() - valid.min())
            # 下限 12 小时：时刻列要覆盖半天以上（真实转置表的 96 点跨 23.75 小时）
            # 上限 24 小时：时刻不可能跨过一天；无上限时「跳天累计」的数值列
            # （如 0 / 86400 / 172800）会被当成时刻序列。
            if _uniq_ratio < 0.8 or _span < 720 or _span > 1440:
                log.debug(f"转置判定跳过列 {time_col}: 唯一率={_uniq_ratio:.2f} 跨度={_span:.0f}分钟")
                continue
            date_cols = []
            for c in cols[time_idx+1:]:
                try:
                    pd.to_datetime(c, errors='raise')
                    date_cols.append(c)
                    continue
                except Exception:
                    pass
                top_vals = sample[c].dropna().head(5)
                # v2.3.2fix(N6): 数值列不当日期列——pd.to_datetime(512.3) 会把数值按纳秒解释，
                # 导致浮荷列被认成日期列，多值列长表被整表误判成转置宽表。
                try:
                    if pd.api.types.is_numeric_dtype(top_vals):
                        continue
                except Exception:
                    pass
                if _ym_valid_ratio(top_vals) >= 0.6:
                    date_cols.append(c)
            if len(date_cols) >= 2:
                return {'date_cols': date_cols, 'time_col': time_col, 'transpose': True}
    return None

def _wide_row_accumulated_to_power(df, time_cols):
    col_times = {}
    if _is_point_sequence(time_cols):
        # v2.3.9fix(P2-15): 点序号列名（1..96）按 24h/点数 推算每个点的小时位置。
        # 否则 _parse_min("25") 以后全是 INVALID，累加示数只能差出前 24 列。
        n = len(time_cols)
        if n and 1440 % n == 0:
            step_h = 24.0 / n
            for i, c in enumerate(sorted(time_cols, key=lambda x: int(str(x).strip()))):
                col_times[c] = i * step_h
    else:
        for col in time_cols:
            norm_col = _normalize_time_column_name(str(col))
            mins = _parse_min(norm_col)
            if mins != INVALID_MINUTES:
                col_times[col] = mins / 60.0
    if len(col_times) < 2:
        return pd.DataFrame(index=df.index)
    sorted_cols = sorted(col_times.keys(), key=lambda c: col_times[c])
    vals = df[sorted_cols].apply(pd.to_numeric, errors='coerce')
    hours_arr = np.array([col_times[c] for c in sorted_cols])
    diff_hours = np.diff(hours_arr)
    if len(diff_hours) > 1:
        mean_diff = np.mean(diff_hours)
        std_diff = np.std(diff_hours)
        if std_diff / mean_diff > 0.2:
            log.warning("宽表累积示数时间列间隔不均匀，功率计算可能偏差。请检查列是否完整。")
    diff_vals = vals.diff(axis=1).iloc[:, 1:]
    power = diff_vals / diff_hours
    result_cols = sorted_cols[1:]
    result = pd.DataFrame(power.values, index=df.index, columns=result_cols)
    return result

def _smart_classify_columns(cols, sample_df=None, max_sample=100):
    result = {}
    date_kw = ['日期','date','时间戳','timestamp']
    # v2.3.5fix(K1): 从时间词表删掉单字 '点'。time_col 用 setdefault，
    # 「测点编号」「节点」会同时命中 asset_kw 和 time_kw('点')，
    # 若排在真「时间」列之前就会抢占 time_col，导致整表时刻解析失败。
    # '点' 原本想匹配的「时间点」已被 '时间' 覆盖，删掉不丢能力。
    time_kw = ['时间','time','时刻','时分','时']
    # v2.3.15fix: 原词表只有「资产/户号/编号/表号/id/用户/meter」，而工商业项目
    # 里资产列常叫「回路名称」「支路」「出线」「设备名称」——识别不出就意味着
    # 多个回路的数据混在一起算，出一张图而不是 N 张。
    # 注意：加的全是双字以上的词，不单用「线」「名」——否则「1#线总有功」
    # 这种负荷列会被误判成资产列。
    asset_kw = ['资产','户号','编号','表号','id','用户','meter',
                '回路','支路','线路','出线','干线','馈线','进线',
                '设备名称','测点名称','站点名称','变压器名称','开关名称']
    # v2.3.2fix(N9): 原词表把「无功 / 电流 / 电压」与有功并列为负荷列，
    # 且累计词表里混进了 '点' / '时' 这两个时间词。后果：
    #   · 电表表里「四象限无功功率」可能先于「正向有功功率」被选中作负荷列；
    #   · 列名带「点」「时」的列（如「测点1」）被误置 accumulated_hint，整表判成示数表。
    # 现改为强/弱分层：有功类强匹配，无功/电流/电压仅作候补。
    load_kw_strong = ['负荷','负载','功率','load','有功','需量','demand','kw','power']
    load_kw_weak = ['无功','电流','电压','amp']
    accumulated_kw = ['示数','有功电能','总电能','电能','累积','累计','kwh','mwh','电度','读数','read','反向有功','总电量','电量']

    for c in cols:
        cl = c.lower().replace(' ', '').replace('_', '').replace('-', '')
        # v2.3.2fix(N6): 同 Ultra——「正向有功功率」不该被 '正向' 这类累计词踢出负荷列；
        # 以主体词定夺：含「功率」且不含「电量/电能/kwh」的，按功率处理。
        _is_power_word = ('功率' in cl) or ('kw' in cl and 'kwh' not in cl)
        _is_energy_word = any(k in cl for k in ('电量', '电能', 'kwh', 'mwh', '示数', '电度', '读数', '示值'))
        # v2.3.3fix(I1): 「四象限无功功率」「A相电流」也含 '功率' 强词，会先于有功列抢占 load_col。
        # 无功/电流/电压/功率因数不是负荷，命中强词也降级候补；无有功列时仍能兜底选中。
        _is_reactive_word = any(k in cl for k in ('无功', '电流', '电压', 'amp', 'var', '功率因数', '频率', 'pf'))
        if any(k in cl for k in date_kw):
            result.setdefault('date_col', c)
        if any(k in cl for k in time_kw):
            result.setdefault('time_col', c)
        # v2.3.15fix: 命中资产词之外还要排除负荷强词。
        # 否则「2#进线有功功率」会因为带「进线」被判成资产列，
        # 结果一列同时当 asset_col 和 load_col。
        if any(k in cl for k in asset_kw) and not any(k in cl for k in load_kw_strong):
            result.setdefault('asset_col', c)
        if any(k in cl for k in load_kw_strong) and not (_is_energy_word and not _is_power_word):
            if _is_reactive_word:
                if not result.get('load_col'):
                    result.setdefault('load_col_fallback', c)
            else:
                result.setdefault('load_col', c)
        elif any(k in cl for k in load_kw_weak) and not result.get('load_col'):
            result.setdefault('load_col_fallback', c)
        if any(k in cl for k in accumulated_kw) and not (_is_power_word and not _is_energy_word):
            result.setdefault('accumulated_hint', True)
            # v2.3.4fix(J2): 记下第一个累计类列——「总电量」这类不含「功率」的列
            # 原本拿不到 load_col，扫描阶段报「未找到列 load_col」。
            result.setdefault('accumulated_col', c)

    if not result.get('load_col') and result.get('load_col_fallback'):
        result['load_col'] = result.pop('load_col_fallback')
    result.pop('load_col_fallback', None)
    # v2.3.4fix(J2): 无功率列时累计列充当 load_col（示数表标准形态）
    if not result.get('load_col') and result.get('accumulated_col'):
        result['load_col'] = result['accumulated_col']

    if sample_df is not None and not sample_df.empty:
        # v2.3.4fix(J1): 先判转置宽表——_detect_wide_matrix 的「纯数字递增列当时刻列」兜底
        # 会把转置表的日期列（值严格递增）误当时刻列，两者对同一张表同时命中。
        trans = _detect_transposed_wide(sample_df, cols, max_sample)
        if trans:
            result['date_col'] = trans['date_cols'][0]
            result['load_col'] = trans['date_cols']
            result['format_type'] = 'wide_transposed'
            result['transpose'] = True
            result['time_col'] = trans['time_col']
            result['date_cols_full'] = trans['date_cols']
        else:
            wdate, wload = _detect_wide_matrix(sample_df, cols, max_sample)
            if wdate and wload:
                # v2.3.8fix: 抽样验证 wdate 真的是日期列再覆盖。
                # 词表已正确识别出「日期」时，宽表检测仍可能把「户号」这种
                # 能解析成日期的数值列覆上来（1002 曾被解析成公元 1002 年 1 月），
                # 而且 confidence 还给 1.0 —— 错得很有把握，最难发现。
                probe = sample_df[wdate].head(20) if wdate in sample_df.columns else None
                if probe is None:
                    log.info(f"宽表检测候选日期列 {wdate!r} 不在样本里，不覆盖词表结果")
                else:
                    hit = sum(1 for v in probe if _extract_ym(v) != "未知月份")
                    ratio = hit / max(len(probe), 1)
                    if ratio >= 0.6:
                        result['date_col'] = wdate
                        result['load_col'] = wload
                        result['format_type'] = 'wide'
                    else:
                        log.info(
                            f"宽表检测候选日期列 {wdate!r} 仅 {ratio:.0%} 可解析，不覆盖词表结果"
                        )

    if sample_df is not None and not sample_df.empty and result.get('format_type') not in ('wide', 'wide_transposed', 'accumulated'):
        # v2.3.3fix(I8): 列名是强信号，数值只是弱信号。列名已明确是功率
        # （含「功率」或 kw 且非 kwh）时，不因数值单调性改判示数表。
        _lc = result.get('load_col')
        _lc_s = _lc if isinstance(_lc, str) else ''
        _load_named_power = ('功率' in _lc_s) or ('kw' in _lc_s.lower() and 'kwh' not in _lc_s.lower())
        if (not _load_named_power) and (result.get('accumulated_hint') or (_lc and _detect_accumulated_pattern(
            sample_df, _lc, result.get('date_col', None), result.get('time_col', None)))):
            result['format_type'] = 'accumulated'
        else:
            result['format_type'] = 'long'

    return result

def _detect_file_format_type(fp, sheet, hrow=0, enc='auto', delimiter=','):
    try:
        ext = os.path.splitext(fp)[1].lower()
        if ext == '.csv':
            e = enc if enc != 'auto' else _detect_enc(fp)
            sample_df = pd.read_csv(fp, encoding=e, nrows=200, header=hrow, sep=delimiter)
        else:
            sample_df = _safe_read_excel(fp, sheet=sheet, nrows=200, header=hrow)
        sample_df.columns = [str(c) for c in sample_df.columns]
    except Exception:
        log.debug("文件格式检测失败", exc_info=True)
        # v2.3.9fix(P2-1): 返回 None 而不是 'long'。
        # 返回 'long' 会让 MappingDialog 默认勾选长表，宽表被直接当长表解析，
        # 报「缺少列」而用户以为是自己选错了列。None 表示「没检测出来」。
        return None
    cols = list(sample_df.columns)
    smart = _smart_classify_columns(cols, sample_df)
    # 可能为 None：调用方需要区分「检测失败」与「确实是长表」
    return smart.get('format_type')

def _extract_asset_from_name(file_path, sheet_name):
    basename = os.path.splitext(os.path.basename(file_path))[0]
    m = re.search(r'表号[：:\s]*(\d+)', basename)
    if m:
        return m.group(1)
    m = re.search(r'表号[：:\s]*(\d+)', sheet_name)
    if m:
        return m.group(1)
    # v2.3.1fix: 纯 4 位数字且落在年份区间时，当作年份而不是表号。
    # 旧逻辑对「2026年1月.xlsx」这类文件名会把年份 2026 当成表号，
    # 导致扫描阶段记下的资产名（'2026'）与加载阶段的兜底（'未命名'）对不上，
    # 整表报「未找到月份…且资产…的数据」。
    m = re.search(r'(\d{4,})', basename)
    if m:
        cand = m.group(1)
        is_year_like = len(cand) == 4 and 1900 <= int(cand) <= 2100
        # v2.3.9fix(P2-6): 8 位纯数字日期（20261101）不是表号。
        # 「20261101_负荷.xlsx」原来会被当成表号 '20261101'，图表标题与报告都跟着错。
        is_date_like = (len(cand) == 8 and cand.isdigit()
                        and 19000101 <= int(cand) <= 21001231)
        if not is_year_like and not is_date_like:
            return cand
    return '未命名'

def _parse_file_meta_worker(fp):
    ext = os.path.splitext(fp)[1].lower()
    if ext == '.csv':
        return fp, ["CSV文件"]
    # v2.3fix: 自动引擎检测 + 降级日志
    sheets = _safe_excel_sheets(fp)
    if not sheets:
        log.warning(f"无法解析Excel文件工作表: {fp}，请确认已安装python-calamine或xlrd")
    return fp, sheets if sheets else ["默认工作表"]

def _scan_wide_chunk(fp, sheet, mapping, df, fallback, monthly_stats, cancel_event=None):
    date_col = mapping['date_col']
    load_cols = mapping['load_col']
    asset_col = mapping.get('asset_col')
    if mapping.get('transpose'):
        time_col = mapping.get('time_col')
        if time_col not in df.columns:
            log.warning(f"转置宽表时间列 {time_col} 不存在，跳过扫描。文件: {fp}, 工作表: {sheet}")
            return
        df_temp = df.copy(deep=True).set_index(time_col).transpose()
        # v2.3.4fix(J3): 转置后 index 是日期、columns 是时间。load_cols 存的是转置前的
        # 日期列名，转置后它们变成了 index，不在 columns 里——原逻辑恒为空，整表被跳过。
        existing = [c for c in load_cols if c in df_temp.index] or list(df_temp.index)
        if not existing:
            log.warning(f"转置宽表负荷列 {load_cols} 在转置后未匹配到任何日期行，跳过扫描。文件: {fp}, 工作表: {sheet}")
            return
        df_temp = df_temp.loc[existing]
        ldf = df_temp.apply(pd.to_numeric, errors='coerce')
        if mapping.get('format_type') == 'accumulated':
            ldf = _wide_row_accumulated_to_power(ldf, list(ldf.columns))
        yms = pd.Series(df_temp.index).apply(_extract_ym)
        # 转置表的日期在 index 里（没有 date_col 变量）——
        # 统一成 date_values，下游循环不必区分分支。
        # v2.3.9fix(P2-5): index 必须与 yms 同为 RangeIndex。原写法
        # `pd.Series(df_temp.index, index=df_temp.index)` 把 index 设成了日期字符串，
        # 而循环里 idx 是整数，date_values.get(idx) 永远取不到，
        # yearless_samples 恒为空，“有 N 个日期没年份”的提示永远不出现。
        date_values = pd.Series(df_temp.index)
        raw_vals = None
    else:
        existing = [c for c in load_cols if c in df.columns]
        if not existing:
            log.warning(f"宽表负荷列 {load_cols} 在文件中均未找到，跳过扫描。文件: {fp}, 工作表: {sheet}")
            return
        ldf = df[existing].apply(pd.to_numeric, errors='coerce')
        if mapping.get('format_type') == 'accumulated':
            ldf = _wide_row_accumulated_to_power(ldf, existing)
            existing = list(ldf.columns)
        yms = df[date_col].apply(_extract_ym)
        date_values = df[date_col]
        raw_vals = df[asset_col] if asset_col and asset_col in df.columns else None

    for idx, ym in yms.items():
        if cancel_event and cancel_event.is_set():
            return
        # v2.3.8fix: 丢弃的行必须可计数。
        # 原来这三处 continue 把无法识别月份 / 无有效时间列 / 空数组的行
        # 默默丢掉，调用方拿到的只是「少了几个月」，不知道丢了多少。
        if pd.isna(ym) or ym == '' or ym == '未知月份':
            monthly_stats['__dropped__'] = monthly_stats.get('__dropped__', 0) + 1
            continue
        if raw_vals is not None:
            asset_val = _normalize_asset(raw_vals[idx], fallback=_extract_asset_from_name(fp, sheet))
        else:
            asset_val = _extract_asset_from_name(fp, sheet)

        row = ldf.loc[idx] if not mapping.get('transpose') else ldf.iloc[idx]
        vcols = _valid_time_cols(row.index)
        if not vcols:
            monthly_stats['__dropped__'] = monthly_stats.get('__dropped__', 0) + 1
            continue
        arr = row[vcols].values
        if arr.size == 0:
            monthly_stats['__dropped__'] = monthly_stats.get('__dropped__', 0) + 1
            continue
        key = (ym, asset_val)
        monthly_stats.setdefault(key, {'date_count': 0, 'min_val': np.inf,
                                       'max_val': -np.inf, 'time_set': set(),
                                       'dates': set(), 'yearless': []})
        # 记下前几个「无年份」的原始值 —— 前端要把它们摆给用户看，
        # 否则「有 3 个日期没年份」这句话没法落地（用户还得自己翻表）
        if len(monthly_stats[key]['yearless']) < 5:
            _raw = date_values.get(idx, '')
            if _looks_yearless(_raw):
                _sv = str(_raw).strip()
                if _sv not in monthly_stats[key]['yearless']:
                    monthly_stats[key]['yearless'].append(_sv)
        monthly_stats[key]['date_count'] += 1
        # v2.3.12: 记下具体日期，用于前后端判断「哪几天缺数据」。
        # 原来只存 count，界面上只能显示「31 天里缺 1 天」却说不出是哪天。
        try:
            _dv = pd.to_datetime(str(date_values.get(idx, "")), errors='coerce')
            if pd.notna(_dv):
                _ds = _dv.strftime('%Y-%m-%d')
                # 无年份的日期会被 pd.to_datetime 解成公元 1 年（Y1 场景实测 0001-01-15）。
                # ym 已经经过了 _extract_ym 的年份处理（含 C8 默认今年），
                # 用它的年月做前缀，保证 dates 与 month_str 同源。
                if not _ds.startswith(ym):
                    _ds = ym + _ds[7:]
                monthly_stats[key]['dates'].add(_ds)
        except Exception:
            pass
        monthly_stats[key]['min_val'] = min(monthly_stats[key]['min_val'], np.nanmin(arr))
        monthly_stats[key]['max_val'] = max(monthly_stats[key]['max_val'], np.nanmax(arr))
        monthly_stats[key]['time_set'].update(vcols)

def _scan_long_chunk(fp, sheet, mapping, df, fallback, monthly_stats, cancel_event=None):
    date_col = mapping['date_col']
    time_col = mapping.get('time_col')
    asset_col = mapping.get('asset_col')
    load_col = mapping['load_col']
    if isinstance(load_col, list):
        # v2.3fix: 多负荷列取第一个 + 日志警告
        if len(load_col) > 1:
            log.warning(f"长表模式仅支持单负荷列，已自动选择'{load_col[0]}'，忽略列: {load_col[1:]}。文件: {fp}, 工作表: {sheet}")
        load_col = load_col[0]
    # v2.3.15fix: 不能静默 return。原实现直接 return，导致 monthly_stats 为空，
    # 上层返回 {'skipped': 'no_valid_rows'}，用户看到的文案是
    # 「所有行的日期都无法识别」——而真正的问题是负荷列不存在，
    # 他会去查日期格式，查到天亮也查不出来。
    if _resolve_col(df, load_col) is None:
        raise KeyError(f"负荷列不存在: {load_col}，可用列: {list(df.columns)[:12]}")

    convert_accumulated = mapping.get('convert_accumulated', False) or mapping.get('format_type') == 'accumulated'
    # v2.3.15fix: 选列必须走 _resolve_col，否则列名带前后空格或全角括号时
    # 这里直接 KeyError（"None of ['负荷(kW)', '采集时间'] are in the columns"），
    # 前面几处检查就算放行也白搭。
    _need = list(set([date_col, load_col] + ([time_col] if time_col else [])
                     + ([asset_col] if asset_col else [])))
    _resolved = {}
    for _c in _need:
        _r = _resolve_col(df, _c)
        if _r is None:
            raise KeyError(f"列不存在: {_c}，可用列: {list(df.columns)[:12]}")
        _resolved[_c] = _r
    _sub = df[[_resolved[c] for c in _need]].copy()
    _sub = _sub.rename(columns={v: k for k, v in _resolved.items()})
    df_clean, dropped_ratio = _parse_long_to_standard(
        _sub,
        date_col, time_col, load_col, 1.0, asset_col, cancel_event, convert_accumulated,
        fallback_asset=_extract_asset_from_name(fp, sheet)
    )
    if df_clean.empty:
        return
    if dropped_ratio > 0.2:
        log.info(f"扫描阶段长表解析丢弃比例 {dropped_ratio:.1%}，文件: {fp}/{sheet}，请检查日期格式")

    g = ['__ym__']
    if asset_col and '__asset__' in df_clean.columns:
        g.insert(0, '__asset__')
    for keys, grp in df_clean.groupby(g):
        if cancel_event and cancel_event.is_set():
            return
        # v2.3fix: GroupBy keys安全解包
        if isinstance(keys, tuple):
            if len(keys) < 1:
                continue
            ym_val = keys[-1]
            asset_val = str(keys[0]) if asset_col and len(keys) > 1 else _extract_asset_from_name(fp, sheet) or '未命名'
        else:
            ym_val = keys
            asset_val = _extract_asset_from_name(fp, sheet) or '未命名'
        ym = str(ym_val)
        if ym == '未知月份':
            monthly_stats['__dropped__'] = monthly_stats.get('__dropped__', 0) + 1
            continue
        key = (ym, asset_val)
        grp_clean = grp[['__dt__', '__load__']].copy()
        grp_clean['__d__'] = grp_clean['__dt__'].dt.date
        grp_clean['__t__'] = grp_clean['__dt__'].dt.floor('min').dt.time
        # v2.3.9fix(P2-8): 同一分钟多条记录按均值聚合，与加载阶段
        # （provider._load_group_data 的 groupby(...).mean()）保持同一口径。
        # 原实现 drop_duplicates 只留首条：扫描显示的最小/最大值与图上的点
        # 对不上，而且丢了多少条没有任何提示。
        _before = len(grp_clean)
        grp_clean = grp_clean.groupby(['__d__', '__t__'], as_index=False)['__load__'].mean()
        _dup = _before - len(grp_clean)
        if _dup > 0:
            log.info(f"长表同一分钟存在 {_dup} 条重复记录，已按均值聚合: {fp}/{sheet}")
        if grp_clean.empty:
            monthly_stats['__dropped__'] = monthly_stats.get('__dropped__', 0) + 1
            continue
        dates = grp_clean['__d__'].unique()
        min_v = grp_clean['__load__'].min()
        max_v = grp_clean['__load__'].max()
        times = grp_clean['__t__'].unique()
        monthly_stats.setdefault(key, {'date_count': 0, 'min_val': np.inf, 'max_val': -np.inf, 'time_set': set()})
        monthly_stats[key]['date_count'] += len(dates)
        monthly_stats[key]['min_val'] = min(monthly_stats[key]['min_val'], min_v)
        monthly_stats[key]['max_val'] = max(monthly_stats[key]['max_val'], max_v)
        monthly_stats[key]['time_set'].update(times)

_ERROR_TRANSLATION = {
    "KeyError": "未找到所需列，请检查列名是否有多余空格或全角符号",
    "ValueError": "数值转换失败，请确认负荷列不含文本或无效符号",
    "MemoryError": "系统内存不足，请关闭其他应用程序或减少批量处理数量",
    "PermissionError": "文件写入权限不足，请检查输出目录是否可写",
    "FileNotFoundError": "文件未找到，请检查路径是否正确",
    "UnicodeDecodeError": "文件编码错误，请尝试切换编码格式",
    "IndexError": "数据索引越界，可能日期与时间点数量不匹配",
    "TypeError": "数据类型错误，请检查列是否为数值",
    "OSError": "系统 I/O 错误，可能磁盘已满或路径无效",
}

def _translate_error(exc_type, exc_msg):
    # v2.3.9fix(P1-8): 示数转换失败的原因要原样透出，
    # 否则被 ValueError 的通用文案（"数值转换失败…"）盖掉，用户不知道该改什么
    if "累计示数转换失败" in str(exc_msg):
        return str(exc_msg)
    str_type = str(exc_type)
    for tech, template in _ERROR_TRANSLATION.items():
        if tech in str_type:
            return template
    if "KeyError" in str_type:
        match = re.search(r"'([^']*)'", str(exc_msg))
        if match:
            return f"未找到列“{match.group(1)}”，请检查列名是否存在多余空格或全角字符"
    if "could not convert" in str(exc_msg).lower():
        return "数据格式错误：无法将文本转换为数值，请检查数据中是否包含非数字字符"
    if "cannot reindex" in str(exc_msg).lower():
        return "数据索引不匹配，可能是日期或时间列存在重复值"
    if "no numeric data" in str(exc_msg).lower():
        return "未找到可绘制的数值数据，请检查负荷列"
    return f"发生错误：{exc_msg}"

def _mapping_is_long_shape(mapping):
    """判断 accumulated 映射的实际结构是长表还是宽表（同 Ultra）。"""
    tc = mapping.get('time_col')
    return bool(tc) and tc != mapping.get('date_col') and not mapping.get('transpose')

def _scan_file_worker(args):
    fp, sheet, mapping_dict, fallback, cancel_event, shared_cache = args
    if not isinstance(mapping_dict, dict) or 'date_col' not in mapping_dict:
        return {'error': _translate_error(ValueError, f'{fp}/{sheet}: invalid mapping')}
    mapping = mapping_dict
    try:
        ext = os.path.splitext(fp)[1].lower()
        hrow = mapping.get('header_row', 0)
        delimiter = mapping.get('delimiter', ',')
        df = shared_cache.get((fp, sheet)) if shared_cache else None
        if df is None:
            df = _read_file_full(fp, sheet if sheet != "CSV文件" else None, hrow,
                                 _detect_enc(fp) if ext == '.csv' else None, delimiter)
            if shared_cache is not None:
                shared_cache[(fp, sheet)] = df
        if cancel_event.is_set():
            return {'cancelled': True}
        # v3.0fix: 空工作表跳过——0行数据无需扫描,节省列映射时间
        # v2.3.8fix: 但「跳过」不能等于「静默」。返回可识别的 skipped，
        # 由调用方向用户说明，否则用户只知道「少了这个 sheet」。
        if df is not None and len(df) == 0:
            return {'skipped': 'empty_sheet'}
        if _resolve_col(df, mapping['date_col']) is None and not mapping.get('transpose'):
            return {'error': _translate_error(KeyError, f"日期列不存在，可用列: {list(df.columns)}")}
        monthly_stats = {}
        normative = mapping.copy()
        # v2.3.4fix(J4): accumulated 长表形（日期+时间+示数）不该走宽表分支
        _fmt = mapping.get('format_type')
        if _fmt in ('wide', 'wide_transposed') or (_fmt == 'accumulated' and not _mapping_is_long_shape(mapping)):
            _scan_wide_chunk(fp, sheet, normative, df, fallback, monthly_stats, cancel_event)
        else:
            load_col = mapping['load_col']
            if isinstance(load_col, list):
                if not load_col:
                    return {'error': _translate_error(ValueError, "长表 load_col 列表为空")}
                load_col = load_col[0]
            normative['load_col'] = load_col
            _scan_long_chunk(fp, sheet, normative, df, fallback, monthly_stats, cancel_event)
        dropped = monthly_stats.pop('__dropped__', 0)
        if not monthly_stats:
            # 有行但全部无法归类（日期列全乱码就是这种）——以前返回 []，
            # 界面表现与「这个 sheet 是空的」完全一样。
            return {'skipped': 'no_valid_rows', 'dropped': dropped}
        summaries = []
        for (ym, asset), stats in monthly_stats.items():
            summaries.append({
                'file_path': fp,
                'sheet_name': sheet,
                'asset_value': asset,
                'month_str': ym,
                'date_count': stats['date_count'],
                'time_points': len(stats['time_set']),
                'min_val': stats['min_val'],
                'max_val': stats['max_val'],
                'dates': sorted(stats.get('dates') or []),
                'yearless_samples': stats.get('yearless') or [],
                # GroupSummary.from_dict 只取白名单字段，这个额外字段不会进对象，
                # 但调用方能在构造前读到它并给出提示。
                'dropped_rows': dropped,
                'mapping': normative
            })
        return summaries
    except Exception as e:
        log.debug(f"扫描异常: {fp}/{sheet}", exc_info=True)
        return {'error': _translate_error(type(e), str(e))}

def _normalize_mapping(fp, raw_result, sheets):
    if raw_result is None:
        return None
    if isinstance(raw_result, dict) and 'date_col' in raw_result:
        if raw_result.get('__apply_to_all_sheets'):
            base = raw_result.get('__base_mapping', raw_result)
            return {'sheets': {s: base.copy() for s in sheets}, 'is_single': False, 'apply_all': True}
        if os.path.splitext(fp)[1].lower() == '.csv':
            return {'sheets': {'CSV文件': raw_result}, 'is_single': True, 'apply_all': False}
        else:
            return {'sheets': {sheets[0] if sheets else 'Sheet1': raw_result}, 'is_single': True, 'apply_all': False}
    if isinstance(raw_result, dict) and raw_result.get('__apply_to_all_sheets'):
        base = raw_result.get('__base_mapping')
        if not base:
            return None
        return {'sheets': {s: base.copy() for s in sheets}, 'is_single': False, 'apply_all': True}
    if isinstance(raw_result, dict):
        return {'sheets': {s: m for s, m in raw_result.items() if not s.startswith('__')}, 'is_single': False, 'apply_all': False}
    return None

