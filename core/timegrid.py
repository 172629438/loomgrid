# -*- coding: utf-8 -*-
"""时间网格：时间解析、日期解析、节假日判定、时段校验。

本模块从 Fusion v2.3.9 单文件原样搬出，**函数体一字未改**。
搬移依据：`tools/analyze_deps.py --group timegrid` 实测依赖闭包 24 个符号 /
302 行、零 Qt 引用。

依赖闭包说明（这解释了为什么这里还带着一个缓存类）
--------------------------------------------------
`_parse_min` 是热路径，每次解析结果进 `TIME_CACHE`；而 `TIME_CACHE` 是
`LRUCache` 实例。所以搬 `_parse_min` 必然带出 `LRUCache` 与 `TIME_CACHE_MAXSIZE`。
按入口函数行数估算会漏掉这一层——这是 `analyze_deps.py` 存在的理由。

可变全局的处理
--------------
`_HOLIDAY_AVAILABLE` 与 `_24H_BELONGS_TO_CURRENT_MONTH` 都是模块级可变状态。
主程序只通过 `_is_holiday_available()` / `_set_holiday_available()` 访问前者，
从不直接读变量；后者在主程序里没有任何读取（即 C7 记录的「配了个不生效的开关」），
所以搬走不会造成「两边各持一份状态」的失联。

铁律：core/ 不得 import 任何 UI 框架 / 绘图库 / Web 框架
（用 tools/check_layering.py 校验，那是 AST 级检查，不会误伤文档）。
本模块只用标准库 + pandas + chinese_calendar（可选），日志走 logging。
"""
from __future__ import annotations

import logging
import re
import threading
from collections import OrderedDict
from datetime import date, datetime, time as dt_time
from functools import lru_cache

import pandas as pd
log = logging.getLogger(__name__)

__all__ = [
    "INVALID_MINUTES",
    "TIME_CACHE_MAXSIZE",
    "TIME_CACHE",
    "LRUCache",
    "parse_flexible_time",
    "_parse_min",
    "_fmt_time",
    "_fmt_hour",
    "_fmt_date",
    "_get_day_type",
    "_set_24h_belong",
    "_set_holiday_available",
    "_is_holiday_available",
    "_expand_overnight",
    "_valid_periods",
    "_hour_x",
    "_parse_date_flexible",
    "_normalize_time_column_name",
    "_normalize_fullwidth_chars",
    "_clean_plot",
    "_clean_series",
    "invalidate_day_type_cache",
]


# ── 常量 ──────────────────────────────────────────────────────
INVALID_MINUTES = 9999
TIME_CACHE_MAXSIZE = 5000


# ── 缓存 ──────────────────────────────────────────────────────
class LRUCache:
    def __init__(self, maxsize=5000):
        self.cache = OrderedDict()
        self.maxsize = maxsize
        self._lock = threading.RLock()

    def get(self, key):
        with self._lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
            return None

    def put(self, key, value):
        with self._lock:
            self.cache[key] = value
            while len(self.cache) > self.maxsize:
                self.cache.popitem(last=False)

    def clear(self):
        with self._lock:
            self.cache.clear()


TIME_CACHE = LRUCache(maxsize=TIME_CACHE_MAXSIZE)

# 预编译正则（性能优化）
_CLEAN_RE = re.compile(r'[\x00-\x1f\x7f]')
_TIME_HHMM_RE = re.compile(r'(\d{1,2}:\d{2})')
_TIME_T_SLOT_RE = re.compile(r'[TtPp](\d{1,2})')


def _clean_plot(s):
    """清理字符串中的控制字符，用于图表标签"""
    return _CLEAN_RE.sub('', str(s)) if isinstance(s, str) else str(s)


def _clean_series(s):
    """清理 pandas Series 中的控制字符

    原住在 plotting/naming.py，但 ingest 需要它——若它留在 plotting，
    core.ingest 就会反向依赖 plotting（而 plotting 允许 matplotlib），
    破坏分层单向性。它只用到 pandas + _CLEAN_RE，与绘图无关，归 core 才对。
    """
    if isinstance(s, pd.DataFrame):
        if s.shape[1] > 0:
            s = s.iloc[:, 0]
        else:
            return pd.Series(dtype=str)
    return s.astype(str).str.replace(_CLEAN_RE, '', regex=True).str.strip()


def _normalize_fullwidth_chars(s: str) -> str:
    """全角字符转半角，统一标点"""
    if not isinstance(s, str):
        return s
    s = s.strip()
    table = str.maketrans(
        '０１２３４５６７８９：，　',
        '0123456789:, '
    )
    s = s.translate(table)
    s = s.replace('：', ':').replace('，', ',').replace('　', ' ')
    return s


# ── 24:00 归属开关（C7：主程序无读取点，保留原行为）──────────────
_24H_BELONGS_TO_CURRENT_MONTH = False


def _set_24h_belong(flag: bool):
    global _24H_BELONGS_TO_CURRENT_MONTH
    _24H_BELONGS_TO_CURRENT_MONTH = flag


# ══════════════════ 节假日检测线程安全封装 ══════════════════
_HOLIDAY_AVAILABLE = False
_HOLIDAY_LOCK = threading.Lock()
try:
    from chinese_calendar import is_holiday, is_workday
    with _HOLIDAY_LOCK:
        _HOLIDAY_AVAILABLE = True
except ImportError:
    log.debug("chinese_calendar 未安装，节假日识别不可用。", exc_info=True)


def _is_holiday_available():
    with _HOLIDAY_LOCK:
        return _HOLIDAY_AVAILABLE


def _set_holiday_available(flag: bool):
    global _HOLIDAY_AVAILABLE
    with _HOLIDAY_LOCK:
        _HOLIDAY_AVAILABLE = flag
    if flag:
        _get_day_type.cache_clear()


# ══════════════════ 时刻解析 ══════════════════
def _parse_min(t) -> int:
    if isinstance(t, dt_time):
        minutes = t.hour * 60 + t.minute
        TIME_CACHE.put(str(t), (minutes, f"{minutes//60:02d}:{minutes%60:02d}"))
        return minutes

    key = str(t).strip()
    key = _normalize_time_column_name(key)
    key = _normalize_fullwidth_chars(key)

    cached = TIME_CACHE.get(key)
    if cached is not None:
        return cached[0]

    if re.fullmatch(r'\d{1,2}:\d{2}', key):
        parts = key.split(':')
        h, m = int(parts[0]), int(parts[1])
        minutes = h * 60 + m
        lbl = f"{h:02d}:{m:02d}"
        TIME_CACHE.put(key, (minutes, lbl))
        return minutes

    time_match = _TIME_HHMM_RE.search(key)
    if time_match:
        key = time_match.group(1)
        if ':' in key:
            parts = key.split(':')
            try:
                h, m = int(parts[0]), int(parts[1]) if len(parts) >= 2 else 0
                minutes = h * 60 + m
                lbl = f"{h:02d}:{m:02d}"
                TIME_CACHE.put(key, (minutes, lbl))
                return minutes
            except Exception:
                minutes = INVALID_MINUTES
                lbl = key
                TIME_CACHE.put(key, (minutes, lbl))
                return minutes

    if ':' in key:
        parts = key.split(':')
        try:
            h, m = int(parts[0]), int(parts[1]) if len(parts) >= 2 else 0
            minutes = h * 60 + m
            lbl = f"{h:02d}:{m:02d}"
            TIME_CACHE.put(key, (minutes, lbl))
            return minutes
        except Exception:
            minutes = INVALID_MINUTES
            lbl = key
            TIME_CACHE.put(key, (minutes, lbl))
            return minutes

    try:
        val = float(key)
        if 0 <= val <= 24:
            h = int(val)
            m = int(round((val - h) * 60))
            # v2.3.9fix(P2-4): 23.999 → h=23, m=round(59.94)=60，原实现不处理进位，
            # 得到 1440 分钟与 "23:60" 标签。补进位，与 datetime 语义一致。
            if m == 60:
                h += 1
                m = 0
            minutes = h * 60 + m
            lbl = f"{h:02d}:{m:02d}"
            TIME_CACHE.put(key, (minutes, lbl))
            return minutes
        else:
            minutes = INVALID_MINUTES
            lbl = key
            TIME_CACHE.put(key, (minutes, lbl))
            return minutes
    except Exception:
        pass

    pattern_match = _TIME_T_SLOT_RE.fullmatch(key)
    if pattern_match:
        try:
            slot = int(pattern_match.group(1))
            if 1 <= slot <= 96:
                minutes = (slot - 1) * 15
                lbl = f"{minutes//60:02d}:{minutes%60:02d}"
                TIME_CACHE.put(key, (minutes, lbl))
                return minutes
        except Exception:
            pass

    minutes = INVALID_MINUTES
    lbl = key
    TIME_CACHE.put(key, (minutes, lbl))
    return minutes


def _fmt_time(t) -> str:
    # v2.3.9fix(P2-4): _parse_min 存缓存用的 key 是规范化之后的（"8时" → "8:00"），
    # 而 _fmt_time 查的是 str(t) 原样，永远查不到，直接返回原字符串。
    # 图表 X 轴于是显示 "8时" 而不是 "08:00"。这里用同一套规范化生成 key。
    raw = str(t).strip()
    key = _normalize_fullwidth_chars(_normalize_time_column_name(raw))
    cached = TIME_CACHE.get(key)
    if not cached:
        _parse_min(t)
        cached = TIME_CACHE.get(key)
    return _clean_plot(cached[1] if cached and cached[0] != INVALID_MINUTES else raw)


def _fmt_date(d):
    try:
        dt = pd.to_datetime(d, errors='coerce')
        if pd.notna(dt):
            return dt.strftime('%Y-%m-%d')
    except Exception:
        pass
    s = str(d).strip()
    m = re.search(r'(\d{4}-\d{2}-\d{2})', s)
    if m:
        return m.group(1)
    return s.split()[0] if ' ' in s else s


def parse_flexible_time(s):
    s = s.strip().replace('：', ':')
    if ':' in s:
        parts = s.split(':')
        try:
            h, m = int(parts[0]), int(parts[1]) if len(parts) >= 2 else 0
        except Exception:
            raise ValueError(f"无法解析 '{s}'")
        if 0 <= h <= 24 and 0 <= m < 60:
            return h + m / 60.0
    if ' ' in s:
        parts = s.split()
        if len(parts) == 2:
            try:
                h, m = int(parts[0]), int(parts[1])
            except Exception:
                pass
            else:
                if 0 <= h <= 24 and 0 <= m < 60:
                    return h + m / 60.0
    try:
        v = float(s)
        if 0 <= v <= 24:
            return v
    except Exception:
        pass
    if s.isdigit() and len(s) <= 4:
        val = int(s)
        h = val // 100
        m = val % 100
        if 0 <= h <= 24 and 0 <= m < 60:
            return h + m / 60.0
    raise ValueError(f"无法解析 '{s}'，请使用 HH:MM、8.5 或 8 30 格式")


def _fmt_hour(h):
    hh = int(h)
    mm = int(round((h - hh) * 60))
    if mm == 60:
        hh += 1
        mm = 0
    return f"{hh:02d}:{mm:02d}"


def _expand_overnight(periods):
    res = []
    for p in periods:
        s, e, t = p["start"], p["end"], p["type"]
        if e < s:
            res.append({"start": s, "end": 24.0, "type": t})
            res.append({"start": 0.0, "end": e, "type": t})
        else:
            res.append({"start": s, "end": e, "type": t})
    return res


def _valid_periods(periods):
    if not periods:
        return True, "", []
    errors = []
    for p in periods:
        if not (0 <= p["start"] <= 24 and 0 <= p["end"] <= 24):
            errors.append(f"时段越界: {p}")
        if p["start"] == p["end"]:
            errors.append(f"时段开始与结束相同: {p}")
    expanded = _expand_overnight(periods)
    expanded.sort(key=lambda x: x["start"])
    conflicts = []
    for i in range(len(expanded) - 1):
        if expanded[i]["end"] > expanded[i + 1]["start"]:
            conflicts.append((expanded[i], expanded[i + 1]))
    if conflicts:
        msg = "时段重叠:"
        for a, b in conflicts:
            msg += f"\n  {a['start']}-{a['end']} 与 {b['start']}-{b['end']}"
        return False, msg, conflicts
    if errors:
        return False, "; ".join(errors), []
    return True, "", []


def _hour_x(h, pp):
    return h * pp


# ══════════════════ 日期解析与日类型 ══════════════════
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
            elif len(parts) == 2:
                # v3.2fix: 日期列无年份时默认今年（C8：业务决策，非缺陷）
                try:
                    m, d = int(parts[0]), int(parts[1])
                    if 1 <= m <= 12 and 1 <= d <= 31:
                        return date(datetime.now().year, m, d)
                except (ValueError, TypeError):
                    pass
    _s8 = str(date_str).strip()
    # v2.3.3fix(I2): 8 位纯数字日期（20261001）此前解析为 None。
    if len(_s8) == 8 and _s8.isdigit():
        try:
            _d = date(int(_s8[:4]), int(_s8[4:6]), int(_s8[6:8]))
            return _d
        except (ValueError, TypeError):
            pass
    nums = re.findall(r'\d+', date_str)
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


@lru_cache(maxsize=2048)
def _get_day_type(date_str: str, enable_holiday: bool = False) -> str:
    dt = _parse_date_flexible(date_str)
    if dt is None:
        log.debug(f"日期解析失败，按工作日处理: '{date_str}'")
        return 'weekday'

    if enable_holiday and _is_holiday_available():
        try:
            if is_workday(dt):
                return 'weekday'
            if is_holiday(dt):
                return 'holiday'
        except Exception:
            log.debug(f"节假日判断异常，按普通周末处理: {date_str}", exc_info=True)
            pass

    if dt.weekday() >= 5:
        return 'weekend'
    return 'weekday'


def invalidate_day_type_cache():
    _get_day_type.cache_clear()


def _normalize_time_column_name(col_name: str) -> str:
    m = re.match(r'^(\d{1,2})\s*[时点](?:钟)?$', col_name)
    if m:
        hour = int(m.group(1))
        return f"{hour}:00"
    m = re.match(r'^(\d{1,2})\s*时\s*(\d{1,2})\s*分$', col_name)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        return f"{hour}:{minute:02d}"
    return col_name
