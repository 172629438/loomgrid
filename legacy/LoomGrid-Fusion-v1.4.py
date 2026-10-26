# -*- coding: utf-8 -*-
"""
负荷曲线批量生成工具 Pro 融合版 (智能增强版 v2.1)
第一部分：基础模块、配置、映射、数据读取与扫描

增强记录（v2.1）：
- 对话框最小尺寸自适应优化，防止控件显示不全
- 新增第三种数据格式：“正向有功示数表”，独立于长表/宽表
- 示数表智能识别（关键词+数值单调递增检测）
- 映射对话框中移除“累计示数转功率”复选框，改为格式单选
- 配置迁移：旧版映射自动升级格式
- 长表扫描与数据提取自动适配示数表差分转换
- 智能分类函数增强，返回推荐格式类型
"""

import sys, os, re, json, time, io, logging, atexit, warnings, subprocess, traceback, gc
import logging.handlers
import multiprocessing as mp
import threading
import csv
import shutil
import tempfile
from datetime import datetime, time as dt_time, date, timedelta
from collections import OrderedDict, defaultdict
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
import numpy as np
import pandas as pd

# -------------------- PySide6 统一导入 --------------------
from PySide6.QtCore import (
    QObject, Qt, Signal, Slot, QTimer, QThreadPool, QRunnable, QSemaphore, QByteArray,
    QSize, QPoint, QRect, QEvent, QMutex, QMutexLocker,
    QPropertyAnimation, QEasingCurve, QUrl, QProcess
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QListWidget, QListWidgetItem, QPushButton, QLabel, QProgressBar,
    QColorDialog,
    QFileDialog, QMessageBox, QDialog, QComboBox,
    QLineEdit, QSpinBox, QDoubleSpinBox, QCheckBox, QRadioButton, QButtonGroup,
    QGroupBox, QSplitter, QScrollArea, QGridLayout, QStatusBar,
    QToolBar, QTreeWidget, QTreeWidgetItem, QDialogButtonBox, QPlainTextEdit, QFrame,
    QGraphicsScene, QGraphicsView, QMenu, QInputDialog, QSizeGrip, QStyle,
    QSizePolicy, QHeaderView, QMenuBar, QGraphicsDropShadowEffect,
    QToolTip, QScrollBar, QTableWidget, QTableWidgetItem, QAbstractItemView,
    QTextEdit
)
from PySide6.QtGui import (
    QAction, QColor, QPixmap, QDragEnterEvent, QDropEvent,
    QBrush, QPen, QFont, QPainter, QPalette, QIcon, QMouseEvent,
    QLinearGradient
)

# 可选 Windows 任务栏进度条支持
try:
    from PySide6.QtWinExtras import QWinTaskbarButton
except ImportError:
    QWinTaskbarButton = None

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")

# ==================== 依赖检查与自动安装 ====================
def _check_and_install_dependencies():
    """检查可选依赖，缺失时尝试自动安装（仅在交互式终端下询问，GUI环境跳过）"""
    missing = []
    try:
        from scipy import interpolate, ndimage
    except ImportError:
        missing.append('scipy')
    try:
        import chardet
    except ImportError:
        missing.append('chardet')
    # 节假日判断库 (可选)
    try:
        import chinese_calendar  # noqa: F401
    except ImportError:
        missing.append('chinese_calendar')

    if not missing:
        return

    # 仅在交互式终端（tty）环境下询问用户，避免 GUI 启动时卡死
    if not sys.stdin.isatty():
        print("检测到非交互式环境，跳过自动安装。以下可选依赖缺失，部分功能可能受限：")
        for pkg in missing:
            print(f"  - {pkg}")
        return

    msg = "检测到以下可选依赖未安装，可能影响部分功能（如曲线平滑、编码检测、节假日识别）：\n"
    msg += "\n".join(f"  - {pkg}" for pkg in missing)
    msg += "\n\n是否尝试自动安装？（需要网络连接，约几秒钟）"
    try:
        user_input = input(f"{msg}\n输入 y 安装，其他键跳过：").strip().lower()
    except EOFError:
        user_input = 'n'
    if user_input.startswith('y'):
        for pkg in missing:
            print(f"正在安装 {pkg}...")
            install_cmd = [sys.executable, "-m", "pip", "install", pkg]
            try:
                subprocess.check_call(
                    install_cmd + ["--user"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT
                )
                print(f"{pkg} 安装成功 (--user)")
            except subprocess.CalledProcessError:
                try:
                    subprocess.check_call(
                        install_cmd,
                        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT
                    )
                    print(f"{pkg} 安装成功 (全局)")
                except subprocess.CalledProcessError as e:
                    print(f"安装 {pkg} 失败: {e}，请手动执行 pip install {pkg}")
    else:
        print("已跳过自动安装，部分功能可能受限。")

# ======================= 常量 =======================
INVALID_MINUTES = 9999
MAX_FILENAME_LEN = 50
DEFAULT_ENCODING_LIST = ['utf-8-sig', 'utf-8', 'gb18030', 'gbk', 'gb2312', 'latin1']
SUPPORTED_EXTS = ('.csv', '.xlsx', '.xls')

# 缓存配置
TIME_CACHE_MAXSIZE = 5000
DATA_CACHE_MAXSIZE = 60
FILE_CACHE_MAXSIZE = 20
PREVIEW_CACHE_MAXSIZE = 100
MAX_FILENAME_ATTEMPTS = 1000

# 文件大小阈值 (字节)
LARGE_FILE_SIZE = 50 * 1024 * 1024   # 50 MB，触发分块读取
HUGE_FILE_SIZE = 200 * 1024 * 1024  # 200 MB，提示并降低并行度

# 内存保护阈值 (字节)
MEMORY_LOW_THRESHOLD = 1 * 1024 * 1024 * 1024  # 1 GB

# 图形/颜色常量（可被用户配置覆盖）
DEFAULT_WEEKDAY_COLOR = '#1f77b4'
DEFAULT_WEEKEND_COLOR = '#FF8C00'
DEFAULT_HOLIDAY_COLOR = '#DC143C'
DEFAULT_WEEKEND_LINE_WIDTH = 1.8
DEFAULT_HOLIDAY_LINE_WIDTH = 2.0
WEEKEND_BG_ALPHA = 0.08

from enum import Enum

class PeriodType(str, Enum):
    CHARGE = 'charge'
    DISCHARGE = 'discharge'
    CHARGE_LABEL = '充电(低谷)'
    DISCHARGE_LABEL = '放电(高峰)'

class CurveLabel:
    AVERAGE = '平均负荷'
    MAX_DEMAND = '当月最大需量'
    CAPACITY_LINE = '容量线'

class ColorConst:
    CHARGE_BG = '#90EE90'
    DISCHARGE_BG = '#FFA07A'
    CUSTOM_PARAM_BG = '#E3F2FD'
    READY_BG = '#e0ffe0'
    READY_FG = 'green'
    DISCHARGE_BG_ITEM = '#ffe0d0'
    DISCHARGE_FG = 'darkorange'
    HIGHLIGHT_BG = '#FFF9C4'

def _default_scan_workers():
    return min(4, max(1, mp.cpu_count() - 1))

def _default_plot_processes():
    return min(4, max(1, mp.cpu_count() - 1))

MAX_PRELOAD_THREADS = min(4, max(1, mp.cpu_count()))

os.makedirs("logs", exist_ok=True)
log_formatter = logging.Formatter('%(asctime)s - %(threadName)s - %(levelname)s - %(message)s')
log_handler = logging.handlers.RotatingFileHandler(
    os.path.join("logs", "app.log"), maxBytes=5*1024*1024, backupCount=5, encoding='utf-8'
)
log_handler.setFormatter(log_formatter)
log = logging.getLogger(__name__)
log.setLevel(logging.INFO)
log.addHandler(log_handler)
log.propagate = False

logging.captureWarnings(True)
warnings_logger = logging.getLogger("py.warnings")
warnings_logger.addHandler(log_handler)

_warned_dup_cols = set()
_warned_dup_lock = threading.Lock()

# ======================= 字体管理 =======================
try:
    from matplotlib.font_manager import fontManager
except ImportError:
    print("matplotlib 未安装，请先安装 matplotlib")
    sys.exit(1)

_CJK_FONT_CANDIDATES = [
    'SimHei', 'Microsoft YaHei', 'STHeiti', 'STSong', 'SimSun',
    'FangSong', 'KaiTi', 'YouYuan', 'NSimSun',
    'Noto Sans CJK SC', 'Noto Sans CJK TC', 'WenQuanYi Micro Hei',
    'WenQuanYi Zen Hei', 'Source Han Sans SC', 'Source Han Sans CN',
    'AR PL UMing CN', 'AR PL UKai CN', 'Droid Sans Fallback'
]

_FONT_NAME = None
_FONT_LOCK = threading.Lock()

def _find_cjk_font():
    try:
        fontManager.__init__()
    except Exception:
        log.debug("fontManager 初始化异常，继续尝试查找字体。")
    available = {f.name for f in fontManager.ttflist}
    for name in _CJK_FONT_CANDIDATES:
        if name in available:
            return name
    for f in fontManager.ttflist:
        if any(k in f.name.lower() for k in ['cjk', 'chinese', 'simhei', 'simsun', 'yahei', 'songti', 'heiti']):
            return f.name
    log.warning("未找到任何中文字体！图表中文将显示为方框，请安装中文字体。")
    return 'DejaVu Sans'

def _get_cached_font():
    global _FONT_NAME
    if _FONT_NAME:
        return _FONT_NAME
    with _FONT_LOCK:
        if _FONT_NAME is None:
            _FONT_NAME = _find_cjk_font()
        return _FONT_NAME

def _setup_font():
    font_name = _get_cached_font()
    plt.rcParams['font.sans-serif'] = [font_name]
    plt.rcParams['axes.unicode_minus'] = False
    log.info(f"matplotlib 中文字体设置为: {font_name}")

# ======================= 缓存与工具函数 =======================
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
_CLEAN_RE = re.compile(r'[\x00-\x1f\x7f]')

def _clean_plot(s):
    return _CLEAN_RE.sub('', str(s)) if isinstance(s, str) else str(s)

def _clean_series(s):
    if isinstance(s, pd.DataFrame):
        if s.shape[1] > 0:
            s = s.iloc[:, 0]
        else:
            return pd.Series(dtype=str)
    return s.astype(str).str.replace(_CLEAN_RE, '', regex=True).str.strip()

def _safe_get_col(df, col_name):
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
    if best_score >= 0.6:
        log.info(f"列名 '{col_name}' 不存在，自动替换为最相似列 '{best_col}' (相似度 {best_score:.2f})")
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

def _normalize_fullwidth_chars(s: str) -> str:
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

def _parse_min(t) -> int:
    if isinstance(t, dt_time):
        minutes = t.hour*60 + t.minute
        TIME_CACHE.put(str(t), (minutes, f"{minutes//60:02d}:{minutes%60:02d}"))
        return minutes
    key = str(t).strip()
    key = _normalize_fullwidth_chars(key)
    cached = TIME_CACHE.get(key)
    if cached is not None:
        return cached[0]

    if re.fullmatch(r'\d{1,2}:\d{2}', key):
        parts = key.split(':')
        h, m = int(parts[0]), int(parts[1])
        minutes = h*60+m
        lbl = f"{h:02d}:{m:02d}"
        TIME_CACHE.put(key, (minutes, lbl))
        return minutes

    time_match = re.search(r'(\d{1,2}:\d{2})', key)
    if time_match:
        key = time_match.group(1)
    if ':' in key:
        parts = key.split(':')
        try:
            h, m = int(parts[0]), int(parts[1]) if len(parts)>=2 else 0
            minutes = h*60+m
        except:
            minutes = INVALID_MINUTES
    else:
        try:
            val = float(key)
            if 0 <= val <= 24:
                h = int(val)
                m = int(round((val-h)*60))
                minutes = h*60+m
            else:
                minutes = INVALID_MINUTES
        except:
            pattern_match = re.fullmatch(r'[TtPp](\d{1,2})', key)
            if pattern_match:
                try:
                    slot = int(pattern_match.group(1))
                    if 1 <= slot <= 96:
                        minutes = (slot - 1) * 15
                    else:
                        minutes = INVALID_MINUTES
                except:
                    minutes = INVALID_MINUTES
            else:
                minutes = INVALID_MINUTES
    lbl = f"{minutes//60:02d}:{minutes%60:02d}" if minutes != INVALID_MINUTES else key
    TIME_CACHE.put(key, (minutes, lbl))
    return minutes

def _fmt_time(t) -> str:
    key = str(t).strip()
    cached = TIME_CACHE.get(key)
    if not cached:
        _parse_min(t)
        cached = TIME_CACHE.get(key)
    return _clean_plot(cached[1] if cached and cached[0] != INVALID_MINUTES else key)

def _sanitize_name(s, max_len=MAX_FILENAME_LEN):
    if not s:
        s = "未命名"
    s = re.sub(r'[<>:"/\\|?*]', '_', str(s))
    s = _CLEAN_RE.sub('', s).strip()
    return s[:max_len-3]+"..." if len(s) > max_len else s

def _extract_ym(d):
    try:
        dt = pd.to_datetime(d, errors='coerce')
        if pd.notna(dt):
            return dt.strftime('%Y-%m')
    except:
        pass
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
    except:
        pass
    log.warning(f"无法从 '{s}' 提取有效年月，标记为未知月份")
    return '未知月份'

def _fmt_date(d):
    try:
        dt = pd.to_datetime(d, errors='coerce')
        if pd.notna(dt):
            return dt.strftime('%Y-%m-%d')
    except:
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
            h, m = int(parts[0]), int(parts[1]) if len(parts)>=2 else 0
        except:
            raise ValueError(f"无法解析 '{s}'")
        if 0<=h<=24 and 0<=m<60:
            return h + m/60.0
    if ' ' in s:
        parts = s.split()
        if len(parts)==2:
            try:
                h, m = int(parts[0]), int(parts[1])
            except:
                pass
            else:
                if 0<=h<=24 and 0<=m<60:
                    return h + m/60.0
    try:
        v = float(s)
        if 0<=v<=24:
            return v
    except:
        pass
    if s.isdigit() and len(s)<=4:
        val = int(s)
        h = val//100
        m = val%100
        if 0<=h<=24 and 0<=m<60:
            return h + m/60.0
    raise ValueError(f"无法解析 '{s}'，请使用 HH:MM、8.5 或 8 30 格式")

def _fmt_hour(h):
    hh = int(h)
    mm = int(round((h-hh)*60))
    if mm==60:
        hh+=1
        mm=0
    return f"{hh:02d}:{mm:02d}"

def _expand_overnight(periods):
    res = []
    for p in periods:
        s, e, t = p["start"], p["end"], p["type"]
        if e < s:
            res.append({"start":s, "end":24.0, "type":t})
            res.append({"start":0.0, "end":e, "type":t})
        else:
            res.append({"start":s, "end":e, "type":t})
    return res

def _valid_periods(periods):
    if not periods:
        return True, "", []
    errors = []
    for p in periods:
        if not (0<=p["start"]<=24 and 0<=p["end"]<=24):
            errors.append(f"时段越界: {p}")
        if p["start"] == p["end"]:
            errors.append(f"时段开始与结束相同: {p}")
    expanded = _expand_overnight(periods)
    expanded.sort(key=lambda x: x["start"])
    conflicts = []
    for i in range(len(expanded)-1):
        if expanded[i]["end"] > expanded[i+1]["start"]:
            conflicts.append((expanded[i], expanded[i+1]))
    if conflicts:
        msg = "时段重叠:"
        for a,b in conflicts:
            msg += f"\n  {a['start']}-{a['end']} 与 {b['start']}-{b['end']}"
        return False, msg, conflicts
    if errors:
        return False, "; ".join(errors), []
    return True, "", []

def _hour_x(h, pp):
    return h*pp

def _downsample(x,y,target=500):
    if len(x)<=target:
        return x,y
    idx = np.linspace(0,len(x)-1,target,dtype=int)
    return x[idx], y[idx]

def _compute_fits(x,y,types):
    res = {}
    avg = np.nanmean(y,axis=0)
    n = y.shape[1]
    try:
        from scipy import interpolate, ndimage
        SCIPY = True
    except ImportError:
        SCIPY = False
    if 'poly2' in types and n>=3:
        v = ~np.isnan(avg)
        if np.sum(v)>=3:
            try:
                c = np.polyfit(x[v],avg[v],2)
                res['poly2'] = (np.poly1d(c)(x),'二次拟合')
            except:
                pass
    if 'poly3' in types and n>=4:
        v = ~np.isnan(avg)
        if np.sum(v)>=4:
            try:
                c = np.polyfit(x[v],avg[v],3)
                res['poly3'] = (np.poly1d(c)(x),'三次拟合')
            except:
                pass
    if 'moving_avg' in types:
        w = min(5,n//4) if n>=5 else 2
        if w>=2:
            res['moving_avg'] = (np.convolve(avg, np.ones(w)/w, mode='same'), f'移动平均({w})')
    if 'gaussian' in types and SCIPY:
        try:
            res['gaussian'] = (ndimage.gaussian_filter1d(avg,1.0),'高斯平滑')
        except:
            pass
    if 'spline' in types and SCIPY and n>=4:
        v = ~np.isnan(avg)
        if np.sum(v)>=4:
            try:
                tck = interpolate.splrep(x[v],avg[v],s=len(x)*0.1)
                res['spline'] = (interpolate.splev(x,tck),'样条插值')
            except:
                pass
    return res

def parse_title_template(template, ctx):
    for k,v in {'year_month':ctx.get('year_month',''),'table_no':ctx.get('table_no',''),
                'asset':ctx.get('asset',''),'sheet':ctx.get('sheet','')}.items():
        template = template.replace(f'{{{k}}}',str(v))
    return _clean_plot(template.strip())

def generate_default_title(ctx):
    parts = []
    if ctx.get('table_no'):
        parts.append(f"表号{ctx['table_no']}")
    if ctx.get('asset'):
        parts.append(str(ctx['asset']))
    parts.append(ctx.get('year_month',''))
    return '_'.join(parts)

def generate_filename(template, ctx, ext='.png'):
    raw = parse_title_template(template, ctx)
    return _sanitize_name(raw) + ext

# ======================= 资产名清洗 =======================
def _normalize_asset(val, fallback='未命名'):
    """统一清洗资产标识：去除空格，将 NaN、空字符串、'nan' 等替换为后备值"""
    if val is None:
        return fallback
    if isinstance(val, float) and np.isnan(val):
        return fallback
    s = str(val).strip()
    if s.lower() in ('', 'nan', '<na>', 'null', 'none'):
        return fallback
    return s

# ======================= 日期类型识别 =======================
_HOLIDAY_AVAILABLE = False
try:
    from chinese_calendar import is_holiday, is_workday
    _HOLIDAY_AVAILABLE = True
except ImportError:
    pass

from functools import lru_cache

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
    nums = re.findall(r'\d+', date_str)
    if len(nums) >= 3:
        try:
            y, m, d = int(nums[0]), int(nums[1]), int(nums[2])
            if 1 <= m <= 12 and 1 <= d <= 31:
                return date(y, m, d)
        except:
            pass
    return None

@lru_cache(maxsize=2048)
def _get_day_type(date_str: str, enable_holiday: bool = False) -> str:
    dt = _parse_date_flexible(date_str)
    if dt is None:
        log.debug(f"日期解析失败，按工作日处理: '{date_str}'")
        return 'weekday'

    if enable_holiday and _HOLIDAY_AVAILABLE:
        try:
            if is_workday(dt):
                return 'weekday'
            if is_holiday(dt):
                return 'holiday'
        except Exception:
            log.debug(f"节假日判断异常，按普通周末处理: {date_str}")
            pass

    if dt.weekday() >= 5:
        return 'weekend'
    return 'weekday'

# ======================= 示数表特征检测 =======================
def _detect_accumulated_pattern(df, load_col, date_col, time_col=None, sample_rows=200):
    """检测负荷列是否呈现累计示数特征（数值大致单调递增）"""
    try:
        sub = df.head(sample_rows).copy()
        if load_col not in sub.columns:
            return False
        # 构造时间列排序
        if date_col and date_col in sub.columns:
            sub['__ts__'] = pd.to_datetime(sub[date_col].astype(str), errors='coerce')
            if time_col and time_col in sub.columns:
                sub['__ts__'] = pd.to_datetime(
                    sub[date_col].astype(str) + ' ' + sub[time_col].astype(str),
                    errors='coerce'
                )
            sub = sub.dropna(subset=['__ts__']).sort_values('__ts__')
        else:
            sub = sub.sort_index()  # 按行顺序
        vals = pd.to_numeric(sub[load_col], errors='coerce').dropna().values
        if len(vals) < 10:
            return False
        # 检查是否单调递增（允许少量噪声）
        diffs = np.diff(vals)
        non_neg_ratio = np.sum(diffs >= 0) / len(diffs)
        # 同时检查最大值/最小值之比，示数通常跨度大
        if np.max(vals) > 0 and np.min(vals) >= 0:
            span_ratio = np.max(vals) / (np.min(vals) + 1e-9)
        else:
            span_ratio = 1
        # 若非负差值占比>85%且跨度过大（或数值较大），认为是示数
        return non_neg_ratio >= 0.85 and (span_ratio > 20 or np.max(vals) > 1000)
    except Exception:
        return False

# ======================= 长表解析（含累计示数差分转换） =======================
def _convert_accumulated_to_power(df, date_col, time_col, load_col, asset_col=None):
    """
    尝试将累计示数转换为功率（按时间差分）。
    返回转换后的 DataFrame（load_col 被替换为计算出的功率），若无法转换则返回原 df。
    """
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

        group_cols = [asset_col] if asset_col and asset_col in df.columns else []
        new_load = pd.Series(index=df.index, dtype=float)

        for _, grp in df.groupby(group_cols):
            grp = grp.sort_values('__ts__')
            loads = pd.to_numeric(grp[load_col], errors='coerce')
            times = grp['__ts__']
            delta_hours = times.diff().dt.total_seconds() / 3600.0
            power = loads.diff() / delta_hours
            power = power.where(delta_hours > 0, np.nan)
            new_load[grp.index] = power

        df = df.dropna(subset=['__ts__']).copy()
        df[load_col] = new_load
        df = df.dropna(subset=[load_col])
        df = df.drop(columns=['__ts__'])
        log.info("累计示数已自动转换为平均功率（差值/时间间隔）")
        return df
    except Exception as e:
        log.warning(f"累计示数转换失败: {e}，将使用原始数据")
        if '__ts__' in df.columns:
            df = df.drop(columns=['__ts__'])
        return df

def _parse_long_to_standard(df, date_col, time_col, load_col, multiplier=1.0, asset_col=None, cancel_event=None, convert_accumulated=False):
    """
    将长表数据解析为标准格式，返回 (clean_df, dropped_ratio)
    clean_df 包含列: __dt__, __load__, [__asset__], __ym__, __date__
    新增 convert_accumulated 参数：若为 True，则先尝试将累计示数转换为功率。
    """
    if convert_accumulated:
        df = _convert_accumulated_to_power(df, date_col, time_col, load_col, asset_col)

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
    df['__load__'] = pd.to_numeric(load_series, errors='coerce') * multiplier
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
        df_clean['__asset__'] = '未命名'

    return df_clean, ratio

# ======================= CSV 工具 =======================
try:
    import chardet
    CHARDET = True
except ImportError:
    CHARDET = False

def _detect_csv_delimiter(filepath, sample_bytes=8192):
    try:
        with open(filepath,'r',encoding='utf-8-sig',errors='replace') as f:
            sample = f.read(sample_bytes)
        dialect = csv.Sniffer().sniff(sample, delimiters=[',',';','\t','|'])
        return dialect.delimiter
    except:
        pass
    return ','

def _detect_enc(fp,size=50000):
    try:
        with open(fp,'rb') as f:
            if f.read(3)==b'\xef\xbb\xbf':
                return 'utf-8-sig'
    except:
        pass
    if CHARDET:
        try:
            with open(fp,'rb') as f:
                raw = f.read(size)
            res = chardet.detect(raw)
            if res and res.get('confidence',0)>=0.7:
                return res['encoding'] or 'utf-8'
        except:
            pass
    for e in DEFAULT_ENCODING_LIST:
        try:
            with open(fp,'r',encoding=e) as f:
                f.read(4096)
            return e
        except:
            pass
    return 'utf-8'

def get_columns_fast(fp, sheet=None, hrow=0, enc=None, delimiter=','):
    ext = os.path.splitext(fp)[1].lower()
    if ext=='.csv':
        e = enc if enc and enc!='auto' else _detect_enc(fp)
        try:
            df = pd.read_csv(fp, encoding=e, nrows=hrow+1, header=hrow, sep=delimiter, engine='python')
        except:
            df = pd.read_csv(fp, encoding=e, nrows=hrow+1, header=hrow, sep=None, engine='python')
    else:
        engine = 'openpyxl' if ext=='.xlsx' else 'xlrd'
        df = pd.read_excel(fp, sheet_name=sheet, nrows=hrow+1, header=hrow, engine=engine)
    cols = [str(c) for c in df.columns]
    if all(c.startswith('Unnamed:') for c in cols):
        cols = [f"列{i+1}" for i in range(len(cols))]
    return cols

def _read_file_full(fp, sheet=None, hrow=0, enc=None, delimiter=',', force_chunk=False):
    ext = os.path.splitext(fp)[1].lower()
    if ext == '.csv':
        e = enc if enc and enc != 'auto' else _detect_enc(fp)
        try:
            fsize = os.path.getsize(fp)
        except:
            fsize = 0
        base_kwargs = {
            'encoding': e,
            'header': hrow,
            'sep': delimiter,
        }
        if fsize > LARGE_FILE_SIZE or force_chunk:
            chunks = []
            reader_kwargs = base_kwargs.copy()
            reader_kwargs.update({'chunksize': 100000, 'engine': 'c', 'low_memory': False})
            try:
                reader = pd.read_csv(fp, **reader_kwargs)
            except Exception:
                log.warning(f"C 引擎读取失败，回退至 Python 引擎: {fp}")
                reader_kwargs.pop('low_memory', None)
                reader_kwargs['engine'] = 'python'
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
                    except:
                        pass
                chunks.append(chunk)
            df = pd.concat(chunks, ignore_index=True)
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
                df = pd.read_csv(fp, **read_kwargs)
    else:
        engine = 'openpyxl' if ext == '.xlsx' else 'xlrd'
        df = pd.read_excel(fp, sheet_name=sheet, header=hrow, engine=engine) if sheet else pd.read_excel(fp, header=hrow, engine=engine)
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
        except:
            pass
    return df

def _to_dt(series, fmt=None):
    dt_series = pd.to_datetime(series, format=fmt, errors='coerce') if fmt else pd.to_datetime(series, errors='coerce')
    if isinstance(series, pd.Series):
        mask_24 = series.astype(str).str.contains(r'24:00', na=False)
        if mask_24.any():
            date_part = series.astype(str).str.extract(r'(\d{4}-\d{2}-\d{2})')[0]
            dt_24 = pd.to_datetime(date_part, errors='coerce') + pd.Timedelta(days=1)
            dt_series[mask_24] = dt_24[mask_24]
    return dt_series

def _valid_time_cols(cols):
    valid = []
    for c in cols:
        m = _parse_min(c)
        if m!=INVALID_MINUTES:
            valid.append((m,c))
    valid.sort(key=lambda x: x[0])
    return [c for _,c in valid]

# ======================= 宽表视觉矩阵检测 =======================
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
        except:
            ratio = 0.0
        numeric_flags[c] = ratio
    time_cols = []
    for c in cols:
        if numeric_flags.get(c, 0) >= 0.9:
            mins = _parse_min(c)
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
    if left_idx >= 0:
        if sample[cols[left_idx]].apply(_extract_ym).notna().any():
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
        time_vals = sample[time_col].dropna().apply(_parse_min)
        if len(time_vals) == 0:
            continue
        valid_ratio = (time_vals != INVALID_MINUTES).mean()
        if valid_ratio >= 0.8:
            date_cols = []
            for c in cols[time_idx+1:]:
                try:
                    pd.to_datetime(c, errors='raise')
                    date_cols.append(c)
                    continue
                except:
                    pass
                top_vals = sample[c].dropna().head(5)
                if top_vals.apply(_extract_ym).notna().any():
                    date_cols.append(c)
            if len(date_cols) >= 2:
                return {'date_cols': date_cols, 'time_col': time_col, 'transpose': True}
    return None

# ======================= 智能列分类（新增示数表检测） =======================
def _smart_classify_columns(cols, sample_df=None, max_sample=100):
    result = {}
    date_kw = ['日期','date','时间戳','timestamp']
    time_kw = ['时间','time','时刻','时分']
    asset_kw = ['资产','户号','编号','表号','id','用户','meter']
    load_kw = ['负荷','负载','功率','load','有功','无功','电流','电压']
    accumulated_kw = ['示数','正向','有功电能','总电能','电能','累积','累计','kwh','mwh']

    for c in cols:
        cl = c.lower()
        if any(k in cl for k in date_kw):
            result.setdefault('date_col', c)
        if any(k in cl for k in time_kw):
            result.setdefault('time_col', c)
        if any(k in cl for k in asset_kw):
            result.setdefault('asset_col', c)
        if any(k in cl for k in load_kw):
            result.setdefault('load_col', c)
        if any(k in cl for k in accumulated_kw):
            result.setdefault('accumulated_hint', True)

    # 宽表检测
    if sample_df is not None and not sample_df.empty:
        wdate, wload = _detect_wide_matrix(sample_df, cols, max_sample)
        if wdate and wload:
            result['date_col'] = wdate
            result['load_col'] = wload
            result['format_type'] = 'wide'
        else:
            trans = _detect_transposed_wide(sample_df, cols, max_sample)
            if trans:
                result['date_col'] = trans['date_cols'][0]
                result['load_col'] = trans['date_cols']
                result['format_type'] = 'wide_transposed'
                result['transpose'] = True
                result['time_col'] = trans['time_col']
                result['date_cols_full'] = trans['date_cols']

    # 若未检测到宽表/转置宽表，进一步判断是否为示数表
    if sample_df is not None and not sample_df.empty and result.get('format_type') not in ('wide', 'wide_transposed'):
        # 如果有关键词提示，或数据特征符合，建议为示数表
        if result.get('accumulated_hint') or (result.get('load_col') and _detect_accumulated_pattern(
            sample_df, result.get('load_col'), result.get('date_col', None), result.get('time_col', None))):
            result['format_type'] = 'accumulated'
        else:
            result['format_type'] = 'long'  # 默认长表

    return result

# ======================= 格式检测函数（新增示数表） =======================
def _detect_file_format_type(fp, sheet, hrow=0, enc='auto', delimiter=','):
    """快速检测文件是长表、宽表还是示数表（含转置），不进行完整列映射推荐"""
    try:
        ext = os.path.splitext(fp)[1].lower()
        if ext == '.csv':
            e = enc if enc != 'auto' else _detect_enc(fp)
            sample_df = pd.read_csv(fp, encoding=e, nrows=200, header=hrow, sep=delimiter)
        else:
            sample_df = pd.read_excel(fp, sheet_name=sheet, nrows=200, header=hrow)
        sample_df.columns = [str(c) for c in sample_df.columns]
    except:
        return 'long'
    cols = list(sample_df.columns)
    wdate, wload = _detect_wide_matrix(sample_df, cols)
    if wdate and wload:
        return 'wide'
    trans = _detect_transposed_wide(sample_df, cols)
    if trans:
        return 'wide_transposed'
    # 示数表检测：利用智能分类结果
    smart = _smart_classify_columns(cols, sample_df)
    if smart.get('format_type') == 'accumulated':
        return 'accumulated'
    return 'long'

# ======================= 配置管理 =======================
class Config:
    FILE = "column_mapping_config.json"
    BACKUP_DIR = "config_backups"
    MAX_BACKUPS = 5
    MAX_WEEKLY_BACKUPS = 3
    CONFIG_VERSION = 13   # v2.1

    DEFAULT_CONFIG = {
        'config_version': CONFIG_VERSION,
        'price_strategies': {},
        'active_strategy': None,
        'preview_templates': {},
        'highlight_templates': {},
        'window_geometry': {},
        'app_settings': {
            'auto_open_dir': True,
            'last_output_dir': '',
            'last_multiplier': 1.0,
            'last_csv_encoding': 'auto',
            'last_title_template': '{table_no}_{year_month}_负荷曲线',
            'last_filename_template': '{table_no}_{year_month}_负荷曲线',
            'show_daily': True,
            'show_avg': True,
            'show_max_demand': False,
            'last_billing_enabled': False,
            'last_billing_value': '',
            'last_strategy_name': None,
            'classify_by_asset': False,
            'quick_mode': False,
            'scan_workers': None,
            'plot_processes': None,
            'highlight_mode': 'none',
            'weekday_color': DEFAULT_WEEKDAY_COLOR,
            'weekend_color': DEFAULT_WEEKEND_COLOR,
            'holiday_color': DEFAULT_HOLIDAY_COLOR,
            'weekend_line_width': DEFAULT_WEEKEND_LINE_WIDTH,
            'holiday_line_width': DEFAULT_HOLIDAY_LINE_WIDTH,
            'show_weekend_bg': False,
            'legend_position': 'right',
            'mark_anomalies': False,
            'highlight_enabled_default': True,
            'dark_theme': False,
            'auto_convert_accumulated': False,
        },
        'last_mapping': None,
        'column_fingerprints': {},
        'feedback_data': {}
    }

    def __init__(self):
        self._lock = threading.RLock()
        self.restored_from_backup = False
        self.cfg = self._load()
        with self._lock:
            for key, default_val in self.DEFAULT_CONFIG.items():
                self.cfg.setdefault(key, default_val)
            os.makedirs(self.BACKUP_DIR, exist_ok=True)
            self._migrate()
            self._validate_highlight_dependency()

    def _migrate(self):
        with self._lock:
            if 'charge_discharge_strategies' in self.cfg:
                if 'price_strategies' not in self.cfg:
                    self.cfg['price_strategies'] = self.cfg.pop('charge_discharge_strategies')
                else:
                    self.cfg['price_strategies'].update(self.cfg.pop('charge_discharge_strategies'))
            old_version = self.cfg.get('config_version', 0)
            if old_version < 13:
                # 升级旧版映射：若 mapping 中 convert_accumulated=True 且 format_type='long'，自动升级为 'accumulated'
                for k, v in list(self.cfg.items()):
                    if k in ('price_strategies','active_strategy','app_settings','last_mapping','preview_templates',
                             'highlight_templates','window_geometry','config_version','charge_discharge_strategies',
                             'column_fingerprints','feedback_data'):
                        continue
                    if '|' not in k:
                        continue
                    if isinstance(v, dict) and v.get('convert_accumulated') and v.get('format_type') == 'long':
                        v['format_type'] = 'accumulated'
                        log.info(f"已自动升级映射 '{k}' 为示数表格式")
                # 确保新配置项存在
                self.cfg.setdefault('app_settings', {})
                app = self.cfg['app_settings']
                app.setdefault('mark_anomalies', False)
                app.setdefault('highlight_enabled_default', True)
                app.setdefault('dark_theme', False)
                app.setdefault('auto_convert_accumulated', False)
            self.cfg['config_version'] = self.CONFIG_VERSION
            self.cfg.setdefault('app_settings', {})
            app = self.cfg['app_settings']
            for key, val in self.DEFAULT_CONFIG['app_settings'].items():
                app.setdefault(key, val)
            self.save()

    def _validate_highlight_dependency(self):
        with self._lock:
            mode = self.cfg.get('app_settings', {}).get('highlight_mode', 'none')
            if mode in ('holiday', 'grouped') and not _HOLIDAY_AVAILABLE:
                new_mode = 'weekend' if mode == 'holiday' else 'weekend'
                log.warning(
                    f"高亮模式 '{mode}' 需要 chinesecalendar 库但未安装，自动降级为 '{new_mode}'")
                self.cfg['app_settings']['highlight_mode'] = new_mode

    def _load(self):
        if os.path.exists(self.FILE):
            try:
                with open(self.FILE,'r',encoding='utf-8') as f:
                    cfg = json.load(f)
                if not isinstance(cfg,dict):
                    raise ValueError("格式错误")
                cleaned = {}
                for k,v in cfg.items():
                    if k in ('price_strategies','active_strategy','preview_templates','highlight_templates',
                             'app_settings','last_mapping','window_geometry','config_version',
                             'charge_discharge_strategies','column_fingerprints','feedback_data'):
                        cleaned[k] = v
                        continue
                    if '|' not in k:
                        continue
                    if isinstance(v,dict) and 'date_col' in v and 'load_col' in v:
                        cleaned[k] = v
                if 'config_version' not in cleaned:
                    cleaned['config_version'] = self.CONFIG_VERSION
                cleaned.setdefault('app_settings', {})
                app = cleaned['app_settings']
                for key, val in self.DEFAULT_CONFIG['app_settings'].items():
                    app.setdefault(key, val)
                return cleaned
            except Exception as e:
                log.error(f"配置加载失败: {e}")
                self.restored_from_backup = True
                return self._restore_backup()
        return {}

    def _restore_backup(self):
        backups = sorted([f for f in os.listdir(self.BACKUP_DIR) if f.endswith('.json')], reverse=True)
        for bk in backups:
            try:
                with open(os.path.join(self.BACKUP_DIR,bk),'r',encoding='utf-8') as f:
                    cfg = json.load(f)
                log.info(f"从备份 {bk} 恢复配置")
                self.restored_from_backup = True
                return cfg
            except:
                continue
        log.critical("无法恢复任何配置，使用硬编码默认配置")
        self.restored_from_backup = True
        return self.DEFAULT_CONFIG.copy()

    def save(self):
        with self._lock:
            try:
                tmp = self.FILE+'.tmp'
                with open(tmp,'w',encoding='utf-8') as f:
                    json.dump(self.cfg,f,ensure_ascii=False,indent=2)
                if os.path.exists(self.FILE):
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
                    bk = os.path.join(self.BACKUP_DIR,f"config_{timestamp}.json")
                    os.replace(self.FILE,bk)
                    self._maintain_backups()
                os.replace(tmp, self.FILE)
                return True
            except Exception as e:
                log.error(f"配置保存失败: {e}")
                return False

    def _maintain_backups(self):
        backups = sorted([f for f in os.listdir(self.BACKUP_DIR) if f.startswith('config_')])
        daily = [f for f in backups if not f.startswith('config_weekly_')]
        weekly = [f for f in backups if f.startswith('config_weekly_')]
        while len(daily) > self.MAX_BACKUPS:
            os.remove(os.path.join(self.BACKUP_DIR,daily.pop(0)))
        while len(weekly) > self.MAX_WEEKLY_BACKUPS:
            os.remove(os.path.join(self.BACKUP_DIR,weekly.pop(0)))

    def add_map(self, pattern, sheet, mapping):
        if not pattern or not sheet or not mapping:
            return False
        fingerprint = self._generate_fingerprint(mapping)
        with self._lock:
            self.cfg[f"{pattern}|{sheet}"] = mapping.copy()
            if fingerprint:
                self.cfg.setdefault('column_fingerprints',{})[fingerprint] = mapping.copy()
            return self.save()

    def _generate_fingerprint(self, mapping):
        if not mapping or 'date_col' not in mapping:
            return None
        load_col = mapping.get('load_col','')
        if isinstance(load_col,list):
            load_col = '|'.join(sorted(load_col))
        return f"{mapping.get('date_col','')}|{mapping.get('time_col','')}|{mapping.get('asset_col','')}|{load_col}|{mapping.get('format_type','')}"

    def recommend(self, fpath, sheet, cols):
        fname = os.path.basename(fpath)
        with self._lock:
            for k,m in self.cfg.items():
                if k in ('price_strategies','active_strategy','app_settings','last_mapping','preview_templates',
                         'highlight_templates','window_geometry','config_version','charge_discharge_strategies',
                         'column_fingerprints','feedback_data'):
                    continue
                if '|' not in k:
                    continue
                fp,sp = k.split('|',1)
                if self._match(fname,fp) and self._match(sheet,sp) and self._valid_map(m,cols):
                    return m.copy()
            for fingerprint,m in self.cfg.get('column_fingerprints',{}).items():
                if self._valid_map(m,cols) and self._generate_fingerprint(m)==fingerprint:
                    return m.copy()
            best,best_m = 0,None
            for k,m in self.cfg.items():
                if k in ('price_strategies','active_strategy','app_settings','last_mapping','preview_templates',
                         'highlight_templates','window_geometry','config_version','charge_discharge_strategies',
                         'column_fingerprints','feedback_data'):
                    continue
                if '|' not in k:
                    continue
                try:
                    sc = self._sim(m,cols)
                    if sc>best:
                        best=sc
                        best_m=m
                except:
                    continue
            if best>=0.7 and best_m:
                return best_m.copy()
        return None

    def _match(self,t,p):
        import fnmatch
        return fnmatch.fnmatch(t.lower(),p.lower())

    def _valid_map(self,m,col):
        if m['date_col'] not in col:
            return False
        if m.get('time_col') and m['time_col'] not in col:
            return False
        if m.get('asset_col') and m['asset_col'] not in col:
            return False
        if m['format_type'] in ('wide', 'wide_transposed'):
            load_cols = m.get('load_col', [])
            if isinstance(load_cols, str):
                load_cols = [load_cols]
            for c in load_cols:
                if c not in col:
                    return False
        else:
            if m['load_col'] not in col:
                return False
        return True

    def _sim(self,m,col):
        import difflib
        ds = self._bsim(m['date_col'],col)
        lc = m['load_col']
        if isinstance(lc, str):
            ls = self._bsim(lc, col)
        else:
            ls = max(self._bsim(c, col) for c in lc) if lc else 0.0
        ts = self._bsim(m.get('time_col',''),col) if m.get('time_col') else 1.0
        ass = self._bsim(m.get('asset_col',''),col) if m.get('asset_col') else 1.0
        return (ds + ls + ts + ass) / 4

    def _bsim(self,tar,cand):
        import difflib
        norm_tar = _normalize_fullwidth_chars(tar)
        if norm_tar in cand:
            return 1.0
        return max(difflib.SequenceMatcher(None, norm_tar.lower(), _normalize_fullwidth_chars(str(c)).lower()).ratio() for c in cand)

    def strat_names(self):
        with self._lock:
            return list(self.cfg.get('price_strategies',{}).keys())
    def get_strat(self,name):
        with self._lock:
            return self.cfg.get('price_strategies',{}).get(name)
    def save_strat(self,name,periods):
        with self._lock:
            self.cfg.setdefault('price_strategies',{})[name] = periods
            return self.save()
    def del_strat(self,name):
        with self._lock:
            if name in self.cfg.get('price_strategies',{}):
                del self.cfg['price_strategies'][name]
                if self.cfg.get('active_strategy')==name:
                    self.cfg['active_strategy']=None
            return self.save()
    def active_strat(self):
        with self._lock:
            return self.cfg.get('active_strategy')
    def set_active(self,name):
        with self._lock:
            self.cfg['active_strategy']=name
            self.save()
    def app_set(self,k,d=None):
        with self._lock:
            val = self.cfg.get('app_settings',{}).get(k,d)
            if isinstance(val, dict):
                return val.copy()
            if isinstance(val, list):
                return val.copy()
            return val
    def set_app(self,k,v):
        with self._lock:
            self.cfg.setdefault('app_settings',{})[k]=v
            self.save()
    def last_map(self):
        with self._lock:
            return self.cfg.get('last_mapping')
    def set_last_map(self,m):
        with self._lock:
            self.cfg['last_mapping']=m
            self.save()
    def get_template_names(self):
        with self._lock:
            return list(self.cfg.get('preview_templates',{}).keys())
    def get_template(self,name):
        with self._lock:
            return self.cfg.get('preview_templates',{}).get(name)
    def save_template(self,name,settings):
        with self._lock:
            self.cfg.setdefault('preview_templates',{})[name]=settings
            self.save()
    def delete_template(self,name):
        with self._lock:
            if name in self.cfg.get('preview_templates',{}):
                del self.cfg['preview_templates'][name]
            self.save()
    def get_window_geometry(self,dialog_id):
        stored = self.cfg.get('window_geometry',{}).get(dialog_id)
        if stored and isinstance(stored,str):
            try:
                return QByteArray.fromBase64(bytes(stored,'ascii'))
            except:
                pass
        return None
    def set_window_geometry(self,dialog_id,geometry):
        with self._lock:
            self.cfg.setdefault('window_geometry',{})[dialog_id]=geometry.toBase64().data().decode()
            self.save()
    def get_splitter_state(self,key):
        stored = self.cfg.get('window_geometry',{}).get(key)
        if stored and isinstance(stored,str):
            try:
                return QByteArray.fromBase64(bytes(stored,'ascii'))
            except:
                pass
        return None
    def set_splitter_state(self,key,state):
        with self._lock:
            self.cfg.setdefault('window_geometry',{})[key]=state.toBase64().data().decode()
            self.save()

    def get_scan_workers(self):
        user_val = self.app_set('scan_workers')
        if user_val is not None:
            return max(1, min(user_val, 8))
        return _default_scan_workers()

    def get_plot_processes(self):
        user_val = self.app_set('plot_processes')
        if user_val is not None:
            return max(1, min(user_val, 8))
        return _default_plot_processes()

# ===================== 文件扫描与验证 =====================
def _extract_asset_from_name(file_path, sheet_name):
    basename = os.path.splitext(os.path.basename(file_path))[0]
    m = re.search(r'表号[：:\s]*(\d+)', basename)
    if m:
        return m.group(1)
    m = re.search(r'表号[：:\s]*(\d+)', sheet_name)
    if m:
        return m.group(1)
    m = re.search(r'(\d{4,})', basename)
    if m:
        return m.group(1)
    return '未命名'

def _parse_file_meta_worker(fp):
    ext = os.path.splitext(fp)[1].lower()
    if ext == '.csv':
        return fp, ["CSV文件"]
    try:
        xl = pd.ExcelFile(fp)
        return fp, xl.sheet_names
    except:
        return fp, []

def _scan_wide_chunk(fp, sheet, mapping, df, fallback, monthly_stats, cancel_event=None):
    date_col = mapping['date_col']
    load_cols = mapping['load_col']
    asset_col = mapping.get('asset_col')
    if mapping.get('transpose'):
        time_col = mapping.get('time_col')
        if time_col not in df.columns:
            log.warning(f"转置宽表时间列 {time_col} 不存在，跳过扫描。文件: {fp}, 工作表: {sheet}")
            return
        df_temp = df.set_index(time_col).transpose()
        existing = [c for c in load_cols if c in df_temp.columns]
        if not existing:
            log.warning(f"转置宽表负荷列 {load_cols} 中未找到有效时间列，跳过扫描。文件: {fp}, 工作表: {sheet}")
            return
        ldf = df_temp[existing].apply(pd.to_numeric, errors='coerce')
        yms = pd.Series(df_temp.index).apply(_extract_ym)
        raw_vals = None
    else:
        existing = [c for c in load_cols if c in df.columns]
        if not existing:
            log.warning(f"宽表负荷列 {load_cols} 在文件中均未找到，跳过扫描。文件: {fp}, 工作表: {sheet}")
            return
        ldf = df[existing].apply(pd.to_numeric, errors='coerce')
        yms = df[date_col].apply(_extract_ym)
        raw_vals = df[asset_col] if asset_col and asset_col in df.columns else None

    for idx, ym in yms.items():
        if cancel_event and cancel_event.is_set():
            return
        if pd.isna(ym) or ym == '' or ym == '未知月份':
            continue
        if raw_vals is not None:
            asset_val = _normalize_asset(raw_vals[idx], fallback=_extract_asset_from_name(fp, sheet))
        else:
            asset_val = _extract_asset_from_name(fp, sheet)

        row = ldf.loc[idx] if not mapping.get('transpose') else ldf.iloc[idx]
        vcols = _valid_time_cols(row.index)
        if not vcols:
            continue
        arr = row[vcols].values
        if arr.size == 0:
            continue
        key = (ym, asset_val)
        monthly_stats.setdefault(key, {'date_count': 0, 'min_val': np.inf, 'max_val': -np.inf, 'time_set': set()})
        monthly_stats[key]['date_count'] += 1
        monthly_stats[key]['min_val'] = min(monthly_stats[key]['min_val'], np.nanmin(arr))
        monthly_stats[key]['max_val'] = max(monthly_stats[key]['max_val'], np.nanmax(arr))
        monthly_stats[key]['time_set'].update(vcols)

def _scan_long_chunk(fp, sheet, mapping, df, fallback, monthly_stats, cancel_event=None):
    date_col = mapping['date_col']
    time_col = mapping.get('time_col')
    asset_col = mapping.get('asset_col')
    load_col = mapping['load_col']
    if isinstance(load_col, list):
        load_col = load_col[0]
    if load_col not in df.columns:
        return

    # 示数表或手动开启转换时，强制差分
    convert_accumulated = mapping.get('convert_accumulated', False) or mapping.get('format_type') == 'accumulated'
    df_clean, dropped_ratio = _parse_long_to_standard(
        df, date_col, time_col, load_col, 1.0, asset_col, cancel_event, convert_accumulated
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
        ym_val = keys[-1] if isinstance(keys, tuple) else keys
        ym = str(ym_val)
        asset_val = str(keys[0]) if asset_col and len(keys) > 1 else _extract_asset_from_name(fp, sheet) or '未命名'
        if ym == '未知月份':
            continue
        key = (ym, asset_val)
        grp_clean = grp[['__dt__', '__load__']].copy()
        grp_clean['__d__'] = grp_clean['__dt__'].dt.date
        grp_clean['__t__'] = grp_clean['__dt__'].dt.floor('min').dt.time
        grp_clean = grp_clean.drop_duplicates(subset=['__d__', '__t__'])
        if grp_clean.empty:
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

# ======================= 错误信息翻译 =======================
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
        if mapping['date_col'] not in df.columns and not mapping.get('transpose'):
            return {'error': _translate_error(KeyError, f"日期列不存在，可用列: {list(df.columns)}")}
        monthly_stats = {}
        normative = mapping.copy()
        if mapping.get('format_type') in ('wide', 'wide_transposed'):
            _scan_wide_chunk(fp, sheet, normative, df, fallback, monthly_stats, cancel_event)
        else:
            load_col = mapping['load_col']
            if isinstance(load_col, list):
                if not load_col:
                    return {'error': _translate_error(ValueError, "长表 load_col 列表为空")}
                load_col = load_col[0]
            normative['load_col'] = load_col
            _scan_long_chunk(fp, sheet, normative, df, fallback, monthly_stats, cancel_event)
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
                'mapping': normative
            })
        return summaries
    except Exception as e:
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

# ===================== 信号类 =====================
class ProcessingSignals(QObject):
    log = Signal(str)
    progress = Signal(int)
    progress_max = Signal(int)
    status = Signal(str)
    error = Signal(str)
    finished = Signal()
    generation_finished = Signal(list)
    mapping_request = Signal(object)
    preview_request = Signal(object)
    export_confirm_request = Signal(object)
    eta = Signal(str)

# ===================== 列刷新任务 =====================
class ColumnRefreshTask(QObject, QRunnable):
    columns_ready = Signal(str, str, list)
    error_occurred = Signal(str, str)

    def __init__(self, fp, sheet, hrow, enc='auto', delimiter=','):
        QObject.__init__(self)
        QRunnable.__init__(self)
        self.fp = fp
        self.sheet = sheet
        self.hrow = hrow
        self.enc = enc
        self.delimiter = delimiter

    def run(self):
        try:
            cols = get_columns_fast(
                self.fp,
                self.sheet if self.sheet != "CSV文件" else None,
                self.hrow,
                self.enc,
                self.delimiter
            )
            self.columns_ready.emit(self.fp, self.sheet, cols)
        except Exception as e:
            self.error_occurred.emit(self.fp, str(e))

class ThreadSafeCache:
    def __init__(self):
        self._dict = {}
        self._lock = threading.Lock()

    def get(self, key, default=None):
        with self._lock:
            return self._dict.get(key, default)

    def set(self, key, value):
        with self._lock:
            self._dict[key] = value

    def __getitem__(self, key):
        with self._lock:
            return self._dict[key]

    def __setitem__(self, key, value):
        with self._lock:
            self._dict[key] = value

    def __contains__(self, key):
        with self._lock:
            return key in self._dict

    def items(self):
        with self._lock:
            return list(self._dict.items())

# ===================== GroupSummary 与 DataProvider =====================
class GroupSummary:
    __slots__ = ('file_path','sheet_name','asset_value','month_str','date_count','time_points','min_val','max_val','mapping')
    def __init__(self, file_path, sheet_name, asset_value, month_str, date_count, time_points, min_val, max_val, mapping):
        self.file_path = file_path
        self.sheet_name = sheet_name
        self.asset_value = asset_value
        self.month_str = month_str
        self.date_count = date_count
        self.time_points = time_points
        self.min_val = min_val
        self.max_val = max_val
        self.mapping = mapping

class DataProvider:
    def __init__(self, enc='auto'):
        self.enc = enc
        self._group_cache = OrderedDict()
        self._max_group_cache = DATA_CACHE_MAXSIZE
        self._file_cache = OrderedDict()
        self._max_file_cache = FILE_CACHE_MAXSIZE
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
        if isinstance(load, list):
            load = tuple(sorted(load))
        mtime = 0
        try:
            mtime = os.path.getmtime(fp)
        except:
            pass
        return (fp, sheet, fmt, date_col, load, time_col, mtime)

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
                del self._file_cache[fkey]
                self._file_mtimes.pop(fkey, None)
        if df is not None:
            with self._lock:
                while len(self._file_cache) >= self._max_file_cache:
                    self._file_cache.popitem(last=False)
                self._file_cache[fkey] = df
                self._file_mtimes[fkey] = fkey[-1]
            return
        try:
            df_read = _read_file_full(file_path, sheet_name if sheet_name != "CSV文件" else None,
                                      mapping.get('header_row', 0), self.enc, mapping.get('delimiter', ','))
            with self._lock:
                while len(self._file_cache) >= self._max_file_cache:
                    self._file_cache.popitem(last=False)
                self._file_cache[fkey] = df_read
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
                del self._file_cache[fkey]
                del self._file_mtimes[fkey]
                df = None
        if df is None:
            raw_key = (s.file_path, s.sheet_name)
            with self._lock:
                cached_df = self._raw_file_cache.get(raw_key)
            if cached_df is not None:
                df = cached_df
                with self._lock:
                    while len(self._file_cache) >= self._max_file_cache:
                        self._file_cache.popitem(last=False)
                    self._file_cache[fkey] = df
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
                    with self._lock:
                        while len(self._file_cache) >= self._max_file_cache:
                            self._file_cache.popitem(last=False)
                        self._file_cache[fkey] = df
                        self._file_mtimes[fkey] = fkey[-1]
                except Exception as e:
                    return None, f"读取文件失败: {e}"
        if cancel_event and cancel_event.is_set():
            return None, None
        try:
            date_col = mapping['date_col']
            time_col = mapping.get('time_col')
            asset_col = mapping.get('asset_col')
            if mapping.get('format_type') in ('wide', 'wide_transposed'):
                load_cols = mapping['load_col']
                if not isinstance(load_cols, list):
                    load_cols = [str(load_cols)]
                if mapping.get('transpose'):
                    trans_time_col = mapping.get('time_col')
                    if trans_time_col not in df.columns:
                        return None, f"缺少时间列 {trans_time_col}"
                    df_for_trans = df.copy()
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
                    arr = df_trans[vcols].apply(pd.to_numeric, errors='coerce').values[idxs]
                    time_labels = [_fmt_time(t) for t in vcols]
                    return {'date_labels': dates, 'load_array': arr, 'time_labels': time_labels,
                            'month_str': s.month_str, 'table_no': s.asset_value or '未命名',
                            'asset': s.asset_value, 'sheet': s.sheet_name}, None
                else:
                    needed = [date_col] + ([time_col] if time_col else []) + ([asset_col] if asset_col else []) + load_cols
                    if any(c not in df.columns for c in needed):
                        missing = [c for c in needed if c not in df.columns]
                        return None, f"缺少列: {missing}"
                    dser = _clean_series(df[date_col])
                    sub_df = df[load_cols].copy().apply(pd.to_numeric, errors='coerce')
                    yms = dser.apply(_extract_ym)
                    if asset_col and asset_col in df.columns:
                        asset_ser = df[asset_col].apply(lambda x: _normalize_asset(x))
                    else:
                        asset_ser = pd.Series('未命名', index=df.index)
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
                # 示数表格式或手动开启时强制差分转换
                convert_accumulated = mapping.get('convert_accumulated', False) or mapping.get('format_type') == 'accumulated'
                df_clean, dropped_ratio = _parse_long_to_standard(
                    sub_df, date_col, time_col, load_col, 1.0, asset_col, cancel_event, convert_accumulated
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
            log.error(f"提取分组数据失败 {s.file_path}/{s.sheet_name}/{s.month_str}: {e}")
            return None, str(e)

    def clear(self):
        with self._lock:
            self._group_cache.clear()
            self._file_cache.clear()
            self._file_mtimes.clear()
            self._load_errors.clear()
            self._raw_file_cache.clear()
# 第一部分结束
# -*- coding: utf-8 -*-
"""
负荷曲线批量生成工具 Pro 融合版 (智能增强版 v2.1)
第二部分：绘图核心、对话框、预览渲染

增强记录：
- 映射对话框重构：新增“正向有功示数表”格式单选，移除原复选框
- 对话框最小尺寸自适应，防止控件显示不全
- 智能格式检测增加示数表识别
- 预览渲染任务增加取消事件检查点
"""

# ==================== 统一绘图核心函数（增强版） ====================
def _draw_curves_core(ax, dates, load_array, time_labels, title, opts=None, bill=None, periods=None):
    """
    在已有的 Axes 上绘制负荷曲线，不创建 Figure。
    新增 mark_anomalies 选项，标记零值/负值为红色叉号。
    """
    if opts is None:
        opts = {}
    show_daily = opts.get('show_daily', True)
    show_avg = opts.get('show_avg', True)
    show_max_demand = opts.get('show_max_demand', False)
    fit_types = opts.get('fit_types', [])
    highlight_mode = opts.get('highlight_mode', 'none')
    weekday_color = opts.get('weekday_color', DEFAULT_WEEKDAY_COLOR)
    weekend_color = opts.get('weekend_color', DEFAULT_WEEKEND_COLOR)
    holiday_color = opts.get('holiday_color', DEFAULT_HOLIDAY_COLOR)
    weekend_line_width = opts.get('weekend_line_width', DEFAULT_WEEKEND_LINE_WIDTH)
    holiday_line_width = opts.get('holiday_line_width', DEFAULT_HOLIDAY_LINE_WIDTH)
    show_weekend_bg = opts.get('show_weekend_bg', False)
    legend_position = opts.get('legend_position', 'right')
    mark_anomalies = opts.get('mark_anomalies', False)
    max_legend_dates = 5

    n_dates, n_times = load_array.shape
    if n_times < 2:
        raise ValueError("时间点数量小于2，无法绘制有意义曲线")
    x = np.arange(n_times)
    pp = n_times / 24.0
    thresh = 500

    # 充放策略背景
    if periods:
        for p in _expand_overnight(periods):
            xs = _hour_x(p["start"], pp)
            xe = min(_hour_x(p["end"], pp), n_times - 1)
            color = ColorConst.CHARGE_BG if p["type"] in (PeriodType.CHARGE, PeriodType.CHARGE_LABEL) else ColorConst.DISCHARGE_BG
            ax.axvspan(xs, xe, alpha=0.15, color=color, zorder=0)

    # 周末背景阴影
    if show_weekend_bg and highlight_mode != 'none':
        has_weekend = any(
            _get_day_type(d, enable_holiday=(highlight_mode in ('holiday','grouped'))) in ('weekend', 'holiday')
            for d in dates
        )
        if has_weekend:
            ax.axvspan(0, n_times - 1, alpha=WEEKEND_BG_ALPHA, color='gray', zorder=1)

    # 日负荷曲线
    if show_daily:
        too_many = n_dates > max_legend_dates and highlight_mode not in ('grouped', 'none')
        if highlight_mode == 'grouped':
            for i in range(n_dates):
                day_type = _get_day_type(dates[i], enable_holiday=_HOLIDAY_AVAILABLE)
                if day_type == 'holiday':
                    color = holiday_color
                elif day_type == 'weekend':
                    color = weekend_color
                else:
                    color = weekday_color
                lw = holiday_line_width if day_type == 'holiday' else (weekend_line_width if day_type == 'weekend' else 1.5)
                xp, yp = (_downsample(x, load_array[i], thresh) if n_times > thresh else (x, load_array[i]))
                ax.plot(xp, yp, color=color, alpha=0.6, lw=lw, zorder=10, label='_nolegend_')
        else:
            for i in range(n_dates):
                day_type = _get_day_type(dates[i], enable_holiday=(highlight_mode == 'holiday'))
                if highlight_mode != 'none':
                    if day_type == 'holiday':
                        color = holiday_color
                        lw = holiday_line_width
                    elif day_type == 'weekend':
                        color = weekend_color
                        lw = weekend_line_width
                    else:
                        color = weekday_color
                        lw = 1.5
                else:
                    color = plt.cm.tab10(i % 10)
                    lw = 1.5
                if too_many and highlight_mode != 'none':
                    label = '_nolegend_'
                else:
                    label = dates[i]
                xp, yp = (_downsample(x, load_array[i], thresh) if n_times > thresh else (x, load_array[i]))
                ax.plot(xp, yp, label=label, color=color, alpha=0.75, lw=lw, zorder=10)

    # 异常数据标记（零值或负值）
    anomaly_handle = None
    if mark_anomalies:
        for i in range(n_dates):
            y = load_array[i]
            mask = (y <= 0)
            if np.any(mask):
                ax.scatter(x[mask], y[mask], color='red', marker='x', s=30, zorder=50, label='_nolegend_')
                if anomaly_handle is None:
                    anomaly_handle = True  # 只标记一次，用于后面图例添加

    # 平均曲线
    avg = np.nanmean(load_array, axis=0)
    if show_avg:
        xp, yp = (_downsample(x, avg, thresh) if n_times > thresh else (x, avg))
        ax.plot(xp, yp, label=CurveLabel.AVERAGE, color='black', ls='--', lw=2.0, zorder=20)

    # 当月最大需量
    if show_max_demand:
        max_val = np.nanmax(load_array) if load_array.size > 0 else 0
        if max_val > 0:
            ax.axhline(y=max_val, color='blue', ls='--', lw=2.0, label=f'{CurveLabel.MAX_DEMAND}({max_val:.2f})', zorder=25)

    # 拟合曲线
    if fit_types:
        for nm, (curve, lab) in _compute_fits(x, load_array, fit_types).items():
            xp, yp = (_downsample(x, curve, thresh) if n_times > thresh else (x, curve))
            ax.plot(xp, yp, label=lab, ls='-.', lw=2.0, zorder=20)

    # 计费容量线
    if bill and bill.get('type') == 'capacity' and bill.get('value'):
        ax.axhline(y=bill['value'], color='red', ls=':', lw=2.0, label=f'{CurveLabel.CAPACITY_LINE}({bill["value"]:.2f})', zorder=30)

    # 坐标轴设置
    ax.set_title(_clean_plot(title), fontsize=16)
    ax.set_xlabel('时间', fontsize=12)
    ax.set_ylabel('负荷值', fontsize=12)

    # x轴刻度自适应
    if n_times >= 25:
        step = max(1, n_times // 24)
        idxs = list(range(0, n_times, step))
        lbls = [_clean_plot(f"{int(i // pp):02d}:00" if pp > 0 else f"{i}") for i in idxs]
        ax.set_xticks(idxs)
        ax.set_xticklabels(lbls, rotation=45, fontsize=9)
    else:
        ax.set_xticks(range(n_times))
        ax.set_xticklabels([_clean_plot(lbl) for lbl in time_labels], rotation=45, fontsize=9)

    ax.grid(True, alpha=0.3)

    # 图例整理
    handles, labels = ax.get_legend_handles_labels()
    filtered_handles, filtered_labels = [], []
    for h, l in zip(handles, labels):
        if l == '_nolegend_':
            continue
        filtered_handles.append(h)
        filtered_labels.append(l)

    if show_daily and too_many and highlight_mode != 'none':
        filtered_handles.append(mpatches.Patch(color=weekday_color, alpha=0.75, label=f'每日曲线({n_dates}条)'))
        filtered_labels.append(f'每日曲线({n_dates}条)')

    if periods:
        filtered_handles.extend([
            mpatches.Patch(color=ColorConst.CHARGE_BG, alpha=0.3, label=PeriodType.CHARGE_LABEL),
            mpatches.Patch(color=ColorConst.DISCHARGE_BG, alpha=0.3, label=PeriodType.DISCHARGE_LABEL)
        ])
        filtered_labels.extend([PeriodType.CHARGE_LABEL, PeriodType.DISCHARGE_LABEL])

    if highlight_mode == 'grouped':
        filtered_handles.extend([
            mpatches.Patch(color=weekday_color, alpha=0.6, label='工作日'),
            mpatches.Patch(color=weekend_color, alpha=0.6, label='周末'),
        ])
        filtered_labels.extend(['工作日', '周末'])
        if _HOLIDAY_AVAILABLE:
            filtered_handles.append(mpatches.Patch(color=holiday_color, alpha=0.6, label='节假日'))
            filtered_labels.append('节假日')
    elif highlight_mode == 'weekend':
        filtered_handles.extend([
            mpatches.Patch(color=weekday_color, alpha=0.6, label='工作日'),
            mpatches.Patch(color=weekend_color, alpha=0.6, label='周末')
        ])
        filtered_labels.extend(['工作日', '周末'])
    elif highlight_mode == 'holiday':
        filtered_handles.extend([
            mpatches.Patch(color=weekday_color, alpha=0.6, label='工作日'),
            mpatches.Patch(color=weekend_color, alpha=0.6, label='周末'),
            mpatches.Patch(color=holiday_color, alpha=0.6, label='节假日')
        ])
        filtered_labels.extend(['工作日', '周末', '节假日'])

    if show_weekend_bg and highlight_mode != 'none':
        filtered_handles.append(mpatches.Patch(color='gray', alpha=WEEKEND_BG_ALPHA, label='周末背景'))
        filtered_labels.append('周末背景')

    if mark_anomalies and anomaly_handle is not None:
        filtered_handles.append(
            mpatches.Patch(color='red', alpha=0.6, label='异常值(零/负)')
        )
        filtered_labels.append('异常值(零/负)')

    if filtered_handles or mark_anomalies:
        if legend_position == 'bottom':
            ax.legend(handles=filtered_handles, labels=filtered_labels, loc='upper center',
                      bbox_to_anchor=(0.5, -0.15), ncol=4, fontsize=9)
        else:
            ax.legend(handles=filtered_handles, labels=filtered_labels, bbox_to_anchor=(1.02, 1),
                      loc='upper left', fontsize=9)

# ======================= 实际绘图函数（原子写入） =======================
def _plot_curves(dates, load, time_labels, title, outpath, opts=None, bill=None, periods=None, ctitle=None):
    """
    绘制负荷曲线图并保存到文件。
    调用 _draw_curves_core 完成绘制。
    """
    with plt.rc_context({'font.sans-serif': [_get_cached_font()], 'axes.unicode_minus': False}):
        if opts is None:
            opts = {'show_daily': True, 'show_avg': True, 'fit_types': [], 'show_max_demand': False,
                    'highlight_mode': 'none', 'mark_anomalies': False}
        fig, ax = plt.subplots(figsize=(16, 8))
        try:
            _draw_curves_core(ax, dates, load, time_labels, ctitle or title, opts, bill, periods)
            fig.tight_layout()
            os.makedirs(os.path.dirname(outpath), exist_ok=True)
            fig.savefig(outpath, dpi=200, bbox_inches='tight', format='png')
        except np.linalg.LinAlgError:
            raise RuntimeError("数据矩阵运算异常，可能包含无效值")
        except ValueError as e:
            raise RuntimeError(f"数据形状错误: {e}")
        except MemoryError:
            raise RuntimeError("绘图时内存不足，请减少数据量或关闭其他程序")
        except Exception as e:
            raise RuntimeError(f"绘图失败: {e}")
        finally:
            plt.close(fig)
            gc.collect()

# ====================== 格式检测（含示数表） ======================
def _detect_file_format_type(fp, sheet, hrow=0, enc='auto', delimiter=','):
    """快速检测文件是长表、宽表还是示数表（含转置）"""
    try:
        ext = os.path.splitext(fp)[1].lower()
        if ext == '.csv':
            e = enc if enc != 'auto' else _detect_enc(fp)
            sample_df = pd.read_csv(fp, encoding=e, nrows=200, header=hrow, sep=delimiter)
        else:
            sample_df = pd.read_excel(fp, sheet_name=sheet, nrows=200, header=hrow)
        sample_df.columns = [str(c) for c in sample_df.columns]
    except:
        return 'long'
    cols = list(sample_df.columns)
    wdate, wload = _detect_wide_matrix(sample_df, cols)
    if wdate and wload:
        return 'wide'
    trans = _detect_transposed_wide(sample_df, cols)
    if trans:
        return 'wide_transposed'
    smart = _smart_classify_columns(cols, sample_df)
    if smart.get('format_type') == 'accumulated':
        return 'accumulated'
    return 'long'

# ====================== 列映射对话框（重构版） ======================
class MappingDialog(QDialog):
    def __init__(self, parent, fp, sheet, cfg=None, hrow=0, multi_sheet=False, enc=None, delimiter=',',
                 batch_multi_file=False, initial_format_type=None):
        super().__init__(parent)
        self.fp = fp
        self.sheet = sheet
        self.cfg = cfg
        self.result = None
        self.hrow = hrow
        self.enc = enc if enc else 'auto'
        self.delimiter = delimiter
        self._refresh_task = None
        self._highlighted_controls = []
        self._batch_multi_file = batch_multi_file
        self._initial_format_type = initial_format_type
        self._pre_auto_state = None

        fname = os.path.basename(fp)
        self.setWindowTitle(f"配置字段映射 — {fname} [{sheet}]")
        self.setMinimumSize(820, 600)  # 自适应优化

        if self.cfg:
            geom = self.cfg.get_window_geometry("MappingDialog")
            if geom:
                self.restoreGeometry(geom)
            else:
                self.resize(850, 620)
        else:
            self.resize(850, 620)

        try:
            self.cols = get_columns_fast(fp, sheet if sheet != "CSV文件" else None, hrow, self.enc, self.delimiter)
        except Exception as e:
            self._handle_column_read_error(fname, e)
            return

        if not self.cols:
            QMessageBox.warning(self, "警告", "未能识别任何列名。")
            self.result = 'skip'
            self.accept()
            return

        self._sample_df = None
        try:
            if os.path.splitext(fp)[1].lower() == '.csv':
                e = self.enc if self.enc != 'auto' else _detect_enc(fp)
                self._sample_df = pd.read_csv(fp, encoding=e, nrows=100, header=hrow)
            else:
                self._sample_df = pd.read_excel(fp, sheet_name=sheet if sheet != "CSV文件" else 0, nrows=100,
                                                header=hrow)
            self._sample_df.columns = [str(c) for c in self._sample_df.columns]
        except:
            pass

        self._init_ui(multi_sheet)

        self._apply_initial_format()
        smart_rec = _smart_classify_columns(self.cols, self._sample_df)
        if smart_rec and 'date_col' in smart_rec:
            self._apply_smart_rec(smart_rec)
            self.confidence_label.setText("已自动推荐（智能识别）")
        else:
            rec = cfg.recommend(fp, sheet, self.cols) if cfg else None
            if rec:
                self._apply_rec(rec)
                self.confidence_label.setText("已自动推荐（历史配置匹配）")
            else:
                self.confidence_label.setText("未找到推荐配置，请手动选择")

    def _handle_column_read_error(self, fname, error):
        QMessageBox.warning(self, "警告", f"无法读取列名: {error}\n\n您可以选择重试、跳过此文件或取消。")
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("列名读取失败")
        msg_box.setText(f"无法读取文件“{fname}”的列名:\n{error}")
        msg_box.setInformativeText(f"请检查文件是否损坏或编码是否正确。当前标题行设为第{self.hrow+1}行，编码为{self.enc}。")
        retry_btn = msg_box.addButton("重试", QMessageBox.ActionRole)
        skip_btn = msg_box.addButton("跳过此文件", QMessageBox.ActionRole)
        cancel_btn = msg_box.addButton("取消全部", QMessageBox.RejectRole)
        msg_box.exec()
        clicked = msg_box.clickedButton()
        if clicked == retry_btn:
            try:
                self.cols = get_columns_fast(self.fp,
                                            self.sheet if self.sheet != "CSV文件" else None,
                                            self.hrow, self.enc, self.delimiter)
                return
            except Exception as e:
                self.result = 'skip'
                self.accept()
                return
        elif clicked == skip_btn:
            self.result = 'skip'
            self.accept()
            return
        else:
            self.result = 'cancel'
            self.reject()
            return

    def _init_ui(self, multi_sheet):
        layout = QVBoxLayout(self)

        info = QLabel(f"<b>文件:</b> {os.path.basename(self.fp)}<br><b>工作表:</b> {self.sheet}<br><b>结构:</b> {'多工作表' if multi_sheet else '单工作表'}")
        layout.addWidget(info)

        hrow_group = QGroupBox("标题行设置")
        hrow_layout = QHBoxLayout(hrow_group)
        hrow_layout.addWidget(QLabel("标题行(从1开始):"))
        self.header_row_spin = QSpinBox()
        self.header_row_spin.setRange(1, 20)
        self.header_row_spin.setValue(self.hrow + 1)
        hrow_layout.addWidget(self.header_row_spin)

        is_csv = os.path.splitext(self.fp)[1].lower() == '.csv'
        self.enc_combo = QComboBox()
        self.enc_combo.addItems(['auto', 'utf-8', 'utf-8-sig', 'gb18030', 'gbk', 'gb2312', 'latin1'])
        self.enc_combo.setCurrentText(self.enc)
        self.enc_label = QLabel("编码:")
        self.enc_label.setVisible(is_csv)
        self.enc_combo.setVisible(is_csv)
        hrow_layout.addWidget(self.enc_label)
        hrow_layout.addWidget(self.enc_combo)

        self.delim_combo = QComboBox()
        self.delim_combo.addItems([',', ';', '\t', '|', '空格'])
        self.delim_combo.setCurrentText(self.delimiter if self.delimiter != ' ' else '空格')
        self.delim_label = QLabel("分隔符:")
        self.delim_label.setVisible(is_csv)
        self.delim_combo.setVisible(is_csv)
        hrow_layout.addWidget(self.delim_label)
        hrow_layout.addWidget(self.delim_combo)

        self.refresh_btn = QPushButton("刷新列名")
        self.refresh_btn.clicked.connect(self._manual_refresh)
        hrow_layout.addWidget(self.refresh_btn)
        self.refresh_status = QLabel("")
        hrow_layout.addWidget(self.refresh_status)
        hrow_layout.addStretch()
        layout.addWidget(hrow_group)

        # 数据格式：三个单选按钮
        fmt_group = QGroupBox("数据格式")
        fmt_layout = QVBoxLayout(fmt_group)
        self.fmt_long = QRadioButton("长表（单列负荷）")
        self.fmt_accumulated = QRadioButton("正向有功示数表（自动差分计算有功功率）")
        self.fmt_wide = QRadioButton("宽表（多列时间点负荷）")
        # 根据初始格式设置选中
        if self._initial_format_type in ('wide', 'wide_transposed'):
            self.fmt_wide.setChecked(True)
        elif self._initial_format_type == 'accumulated':
            self.fmt_accumulated.setChecked(True)
        else:
            self.fmt_long.setChecked(True)
        fmt_layout.addWidget(self.fmt_long)
        fmt_layout.addWidget(self.fmt_accumulated)
        fmt_layout.addWidget(self.fmt_wide)
        self.fmt_group_btns = QButtonGroup()
        self.fmt_group_btns.addButton(self.fmt_long, 1)
        self.fmt_group_btns.addButton(self.fmt_accumulated, 2)
        self.fmt_group_btns.addButton(self.fmt_wide, 3)
        self.fmt_group_btns.buttonClicked.connect(self._fmt_chg)
        layout.addWidget(fmt_group)

        # 示数表提示标签
        self.accumulated_hint_label = QLabel(
            "<small>程序将自动按时间差分计算平均功率，请确保负荷列为正向有功示数值（累积值）。</small>"
        )
        self.accumulated_hint_label.setWordWrap(True)
        self.accumulated_hint_label.setStyleSheet("color: #2196F3;")
        self.accumulated_hint_label.setVisible(self.fmt_accumulated.isChecked())
        layout.addWidget(self.accumulated_hint_label)

        cols_group = QGroupBox("工作表列名")
        cols_layout = QVBoxLayout(cols_group)
        self.cols_label = QLabel(", ".join(self.cols))
        self.cols_label.setWordWrap(True)
        scroll_cols = QScrollArea()
        scroll_cols.setWidgetResizable(True)
        scroll_cols.setWidget(self.cols_label)
        scroll_cols.setMaximumHeight(80)
        cols_layout.addWidget(scroll_cols)
        layout.addWidget(cols_group)

        select_group = QGroupBox("映射设置")
        grid = QGridLayout(select_group)
        grid.addWidget(QLabel("日期列:"), 0, 0)
        self.date_combo = QComboBox()
        grid.addWidget(self.date_combo, 0, 1)
        self.time_label = QLabel("时间列(可选):")
        grid.addWidget(self.time_label, 1, 0)
        self.time_combo = QComboBox()
        self.time_combo.addItem("")
        self.time_combo.setToolTip("宽表时无需选择时间列，时间点将从列名自动识别")
        grid.addWidget(self.time_combo, 1, 1)
        grid.addWidget(QLabel("资产列(可选):"), 2, 0)
        self.asset_combo = QComboBox()
        self.asset_combo.addItem("")
        grid.addWidget(self.asset_combo, 2, 1)
        grid.addWidget(QLabel("负荷列:"), 3, 0)
        self.load_combo = QComboBox()
        grid.addWidget(self.load_combo, 3, 1)
        self.load_list = QListWidget()
        self.load_list.setSelectionMode(QListWidget.MultiSelection)
        self.load_list.setMaximumHeight(120)
        self.load_list.setVisible(False)
        grid.addWidget(self.load_list, 3, 1)
        layout.addWidget(select_group)

        self.confidence_label = QLabel("")
        self.confidence_label.setStyleSheet("color: #2196F3; font-weight: bold;")
        layout.addWidget(self.confidence_label)

        opt_layout = QHBoxLayout()
        self.save_cb = QCheckBox("保存此配置以便下次自动推荐")
        self.save_cb.setChecked(True)
        opt_layout.addWidget(self.save_cb)
        if multi_sheet:
            self.all_sheets_cb = QCheckBox("应用到所有工作表")
            opt_layout.addWidget(self.all_sheets_cb)
        if self._batch_multi_file:
            self.all_files_cb = QCheckBox("应用到本次所有文件")
            self.all_files_cb.setToolTip("将此映射应用于之后所有相同结构的文件，跳过手动确认")
            opt_layout.addWidget(self.all_files_cb)
        layout.addLayout(opt_layout)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        auto_btn = QPushButton("智能识别")
        auto_btn.clicked.connect(self._auto)
        btn_layout.addWidget(auto_btn)
        last_btn = QPushButton("上次配置")
        last_btn.clicked.connect(self._last)
        btn_layout.addWidget(last_btn)
        self.undo_auto_btn = QPushButton("撤销识别")
        self.undo_auto_btn.clicked.connect(self._undo_auto)
        self.undo_auto_btn.setEnabled(False)
        btn_layout.addWidget(self.undo_auto_btn)
        skip_btn = QPushButton("跳过此文件")
        skip_btn.clicked.connect(self._skip)
        btn_layout.addWidget(skip_btn)
        cancel_btn = QPushButton("取消全部")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)
        ok_btn = QPushButton("确认")
        ok_btn.clicked.connect(self._ok)
        ok_btn.setDefault(True)
        btn_layout.addWidget(ok_btn)
        layout.addLayout(btn_layout)

        self._populate_columns(self.cols)
        self.setLayout(layout)

    def _apply_initial_format(self):
        if self._initial_format_type in ('wide', 'wide_transposed'):
            self.fmt_wide.setChecked(True)
        elif self._initial_format_type == 'accumulated':
            self.fmt_accumulated.setChecked(True)
        else:
            self.fmt_long.setChecked(True)
        self._fmt_chg()

    def _populate_columns(self, cols):
        for ctrl in self._highlighted_controls:
            ctrl.setStyleSheet("")
        self._highlighted_controls.clear()

        self.date_combo.clear()
        self.date_combo.addItems(cols)
        self.time_combo.clear()
        self.time_combo.addItem("")
        self.time_combo.addItems(cols)
        self.asset_combo.clear()
        self.asset_combo.addItem("")
        self.asset_combo.addItems(cols)
        self.load_combo.clear()
        self.load_combo.addItems(cols)
        self.load_list.clear()
        self.load_list.addItems(cols)

    def _highlight_combo(self, combo, text):
        combo.setStyleSheet(f"background-color: {ColorConst.HIGHLIGHT_BG};")
        if combo not in self._highlighted_controls:
            self._highlighted_controls.append(combo)

    def _find_best_match(self, target, cols):
        import difflib
        if target in cols:
            return target, 1.0
        best_score = 0
        best_col = None
        for c in cols:
            score = difflib.SequenceMatcher(None, target.lower(), str(c).lower()).ratio()
            if score > best_score:
                best_score = score
                best_col = c
        if best_score >= 0.6:
            return best_col, best_score
        return target, 0.0

    def _apply_smart_rec(self, rec):
        self._save_pre_auto_state()
        if 'date_col' in rec:
            self.date_combo.setCurrentText(rec['date_col'])
        if rec.get('time_col'):
            self.time_combo.setCurrentText(rec['time_col'])
        if 'asset_col' in rec:
            self.asset_combo.setCurrentText(rec['asset_col'])
        if 'load_col' in rec:
            if rec.get('format_type') in ('wide', 'wide_transposed'):
                self.fmt_wide.setChecked(True)
                self._fmt_chg()
                load_cols = rec.get('load_col', [])
                if isinstance(load_cols, str):
                    load_cols = [load_cols]
                for i in range(self.load_list.count()):
                    self.load_list.item(i).setSelected(self.load_list.item(i).text() in load_cols)
            else:
                lc = rec.get('load_col', '')
                if isinstance(lc, list):
                    lc = lc[0] if lc else ''
                self.load_combo.setCurrentText(lc)
                if rec.get('format_type') == 'accumulated':
                    self.fmt_accumulated.setChecked(True)
                    self._fmt_chg()
        if rec.get('format_type') in ('wide', 'wide_transposed'):
            self.fmt_wide.setChecked(True)
            self._fmt_chg()
        elif rec.get('format_type') == 'accumulated':
            self.fmt_accumulated.setChecked(True)
            self._fmt_chg()

    def _apply_rec(self, m, perform_highlight=True):
        if not m or m.get('date_col') not in self.cols:
            if m and m.get('date_col'):
                matched, sim = self._find_best_match(m['date_col'], self.cols)
                if sim >= 0.6:
                    self.date_combo.setCurrentText(matched)
                    if perform_highlight:
                        self._highlight_combo(self.date_combo, matched)
                        self.confidence_label.setText(
                            f"日期列 '{m['date_col']}' 自动匹配为 '{matched}' (相似度 {sim:.2f})")
                else:
                    return
            else:
                return
        else:
            self.date_combo.setCurrentText(m['date_col'])
        if m.get('time_col'):
            if m['time_col'] not in self.cols:
                matched, sim = self._find_best_match(m['time_col'], self.cols)
                if sim >= 0.6:
                    self.time_combo.setCurrentText(matched)
                    if perform_highlight:
                        self._highlight_combo(self.time_combo, matched)
                        self.confidence_label.setText(
                            f"时间列 '{m['time_col']}' 自动匹配为 '{matched}' (相似度 {sim:.2f})")
            else:
                self.time_combo.setCurrentText(m['time_col'])
        else:
            self.time_combo.setCurrentText('')
        if m.get('asset_col'):
            if m['asset_col'] not in self.cols:
                matched, sim = self._find_best_match(m['asset_col'], self.cols)
                if sim >= 0.6:
                    self.asset_combo.setCurrentText(matched)
                    if perform_highlight:
                        self._highlight_combo(self.asset_combo, matched)
                        self.confidence_label.setText(
                            f"资产列 '{m['asset_col']}' 自动匹配为 '{matched}' (相似度 {sim:.2f})")
            else:
                self.asset_combo.setCurrentText(m['asset_col'])
        else:
            self.asset_combo.setCurrentText('')

        # 根据 format_type 选择格式
        fmt = m.get('format_type', 'long')
        if fmt in ('wide', 'wide_transposed'):
            self.fmt_wide.setChecked(True)
            self._fmt_chg()
            mapped_loads = []
            for lc in m.get('load_col', []):
                if lc in self.cols:
                    mapped_loads.append(lc)
                else:
                    matched, sim = self._find_best_match(lc, self.cols)
                    if sim >= 0.6:
                        mapped_loads.append(matched)
                        if perform_highlight:
                            self._highlight_control_for_load(matched)
                            self.confidence_label.setText(
                                f"负荷列 '{lc}' 自动匹配为 '{matched}' (相似度 {sim:.2f})")
            for i in range(self.load_list.count()):
                self.load_list.item(i).setSelected(False)
            for col in mapped_loads:
                items = self.load_list.findItems(col, Qt.MatchExactly)
                if items:
                    items[0].setSelected(True)
        else:
            # 长表或示数表
            load_col = m['load_col']
            if isinstance(load_col, list):
                load_col = load_col[0] if load_col else ''
            if load_col and load_col not in self.cols:
                matched, sim = self._find_best_match(load_col, self.cols)
                if sim >= 0.6:
                    self.load_combo.setCurrentText(matched)
                    if perform_highlight:
                        self._highlight_combo(self.load_combo, matched)
                        self.confidence_label.setText(
                            f"负荷列 '{load_col}' 自动匹配为 '{matched}' (相似度 {sim:.2f})")
            else:
                self.load_combo.setCurrentText(load_col)
            if fmt == 'accumulated':
                self.fmt_accumulated.setChecked(True)
                self._fmt_chg()
            else:
                self.fmt_long.setChecked(True)
                self._fmt_chg()

        if 'header_row' in m:
            self.header_row_spin.setValue(m['header_row'] + 1)
        if 'delimiter' in m and self.delim_combo.isVisible():
            d = m['delimiter']
            self.delim_combo.setCurrentText(d if d != ' ' else '空格')
        if self.fmt_wide.isChecked():
            self.time_combo.setCurrentText('')
            self.time_combo.setEnabled(False)
        else:
            self.time_combo.setEnabled(True)

    def _highlight_control_for_load(self, col_name):
        items = self.load_list.findItems(col_name, Qt.MatchExactly)
        if items:
            items[0].setBackground(QColor(ColorConst.HIGHLIGHT_BG))
            if self.load_list not in self._highlighted_controls:
                self._highlighted_controls.append(self.load_list)

    def _manual_refresh(self):
        self._start_refresh_task()

    def _start_refresh_task(self):
        new_hrow = self.header_row_spin.value() - 1
        self.refresh_status.setText("刷新中...")
        self.header_row_spin.setEnabled(False)
        self.refresh_btn.setEnabled(False)
        self._set_mapping_enabled(False)
        enc = self.enc_combo.currentText() if self.enc_combo.isVisible() else 'auto'
        delim = self.delim_combo.currentText()
        delim = ' ' if delim == '空格' else delim
        if self._refresh_task is not None:
            try:
                self._refresh_task.columns_ready.disconnect()
                self._refresh_task.error_occurred.disconnect()
            except:
                pass
        task = ColumnRefreshTask(self.fp, self.sheet if self.sheet != "CSV文件" else None, new_hrow, enc, delim)
        task.columns_ready.connect(self._on_columns_ready)
        task.error_occurred.connect(self._on_refresh_error)
        self._refresh_task = task
        QThreadPool.globalInstance().start(task)

    def _on_columns_ready(self, fp, sheet, new_cols):
        if fp == self.fp:
            self.cols = list(new_cols)
            self.hrow = self.header_row_spin.value() - 1
            self.cols_label.setText(", ".join(new_cols))
            self._populate_columns(new_cols)
            self.refresh_status.setText("✓ 已更新")
            if self.cfg:
                rec = self.cfg.recommend(self.fp, self.sheet, new_cols)
                if rec:
                    self._apply_rec(rec, perform_highlight=True)
                    self.confidence_label.setText("已自动推荐（历史配置匹配）")
                else:
                    self.confidence_label.setText("")
        self.header_row_spin.setEnabled(True)
        self.refresh_btn.setEnabled(True)
        self._set_mapping_enabled(True)
        QTimer.singleShot(2000, lambda: self.refresh_status.setText(""))

    def _on_refresh_error(self, fp, error_msg):
        if fp == self.fp:
            QMessageBox.warning(self, "刷新失败", f"无法读取列名:\n{error_msg}\n请检查标题行或编码设置。")
            self.refresh_status.setText("")
        self.header_row_spin.setEnabled(True)
        self.refresh_btn.setEnabled(True)
        self._set_mapping_enabled(True)

    def _set_mapping_enabled(self, enabled):
        self.fmt_long.setEnabled(enabled)
        self.fmt_accumulated.setEnabled(enabled)
        self.fmt_wide.setEnabled(enabled)
        self.date_combo.setEnabled(enabled)
        self.time_combo.setEnabled(enabled)
        self.asset_combo.setEnabled(enabled)
        self.load_combo.setEnabled(enabled)
        self.load_list.setEnabled(enabled)

    def _save_pre_auto_state(self):
        self._pre_auto_state = {
            'date': self.date_combo.currentText(),
            'time': self.time_combo.currentText(),
            'asset': self.asset_combo.currentText(),
            'load': self.load_combo.currentText() if self.load_combo.isVisible() else '',
            'load_list': [self.load_list.item(i).text() for i in range(self.load_list.count()) if self.load_list.item(i).isSelected()],
            'fmt_wide': self.fmt_wide.isChecked(),
            'fmt_accumulated': self.fmt_accumulated.isChecked()
        }
        self.undo_auto_btn.setEnabled(True)

    def _undo_auto(self):
        if not self._pre_auto_state:
            return
        state = self._pre_auto_state
        self.date_combo.setCurrentText(state['date'])
        self.time_combo.setCurrentText(state['time'])
        self.asset_combo.setCurrentText(state['asset'])
        if state['fmt_wide']:
            self.fmt_wide.setChecked(True)
            self._fmt_chg()
            for i in range(self.load_list.count()):
                self.load_list.item(i).setSelected(self.load_list.item(i).text() in state['load_list'])
        elif state['fmt_accumulated']:
            self.fmt_accumulated.setChecked(True)
            self._fmt_chg()
            self.load_combo.setCurrentText(state['load'])
        else:
            self.fmt_long.setChecked(True)
            self._fmt_chg()
            self.load_combo.setCurrentText(state['load'])
        self._pre_auto_state = None
        self.undo_auto_btn.setEnabled(False)
        self.confidence_label.setText("已撤销智能识别")

    def _auto(self):
        self._save_pre_auto_state()
        cand = self.cols
        smart_rec = _smart_classify_columns(cand, self._sample_df)
        if smart_rec and 'date_col' in smart_rec:
            self._apply_smart_rec(smart_rec)
            basis = "根据列名与数据样本推断"
            if smart_rec.get('format_type') == 'wide':
                basis += "（检测到多列时间点负荷，宽表格式）"
            elif smart_rec.get('format_type') == 'wide_transposed':
                basis += "（检测到转置宽表，日期列已自动勾选为负荷数据）"
            elif smart_rec.get('format_type') == 'accumulated':
                basis += "（检测到正向有功示数特征，将自动差分计算功率）"
            else:
                basis += "（长表格式）"
            self.confidence_label.setText(f"已自动推荐（智能识别）: {basis}")
        else:
            dg = next((c for c in cand if '日期' in c or 'date' in c.lower()), cand[0] if cand else '')
            self.date_combo.setCurrentText(dg)
            ag = next((c for c in cand if '资产' in c or '户号' in c or '表号' in c), '')
            self.asset_combo.setCurrentText(ag)
            self.load_combo.setCurrentText(cand[1] if len(cand) > 1 else '')
            self.confidence_label.setText("无法自动识别，已选常用字段，请核对。")
            QMessageBox.information(self, "智能识别", "已自动填写常用字段，请核对。")

    def _last(self):
        self._save_pre_auto_state()
        if self.cfg:
            m = self.cfg.last_map()
            if m:
                self._apply_rec(m, perform_highlight=True)
                self.confidence_label.setText("已应用上次配置")
            else:
                QMessageBox.information(self, "上次配置", "没有找到上次使用的映射配置。")

    def _fmt_chg(self):
        if self.fmt_wide.isChecked():
            self.load_combo.setVisible(False)
            self.load_list.setVisible(True)
            self.time_combo.setCurrentText('')
            self.time_combo.setEnabled(False)
            self.time_label.setToolTip("宽表时无需选择时间列，时间点将从列名中自动识别")
            self.accumulated_hint_label.setVisible(False)
        else:
            self.load_combo.setVisible(True)
            self.load_list.setVisible(False)
            self.time_combo.setEnabled(True)
            self.time_label.setToolTip("")
            self.accumulated_hint_label.setVisible(self.fmt_accumulated.isChecked())

    def _ok(self):
        d = self.date_combo.currentText().strip()
        t = self.time_combo.currentText().strip() or None
        a = self.asset_combo.currentText().strip() or None
        if not d:
            QMessageBox.critical(self, "错误", "必须选择日期列！")
            return
        if self.fmt_wide.isChecked():
            selected = self.load_list.selectedItems()
            if not selected:
                QMessageBox.critical(self, "错误", "宽表格式必须至少勾选一个负荷列！")
                return
            l = [item.text() for item in selected]
            fmt = 'wide'
        else:
            l = [self.load_combo.currentText().strip()]
            if not l[0]:
                QMessageBox.critical(self, "错误", "必须选择负荷列！")
                return
            if self.fmt_accumulated.isChecked():
                fmt = 'accumulated'
            else:
                fmt = 'long'
        delim = self.delim_combo.currentText() if self.delim_combo.isVisible() else ','
        if delim == '空格':
            delim = ' '
        apply_all = self.all_sheets_cb.isChecked() if hasattr(self, 'all_sheets_cb') else False
        apply_all_files = self.all_files_cb.isChecked() if hasattr(self, 'all_files_cb') else False
        self.result = {
            'date_col': d,
            'time_col': t,
            'asset_col': a,
            'load_col': l,
            'format_type': fmt,
            'header_row': self.header_row_spin.value() - 1,
            'delimiter': delim,
            'convert_accumulated': fmt == 'accumulated'  # 自动设置
        }
        if apply_all:
            self.result['__apply_to_all_sheets'] = True
            self.result['__base_mapping'] = self.result.copy()
        if apply_all_files:
            self.result['__apply_to_all_files'] = True
        if self.cfg:
            self.cfg.set_last_map(self.result)
            if self.save_cb.isChecked():
                pat = os.path.basename(self.fp)
                pattern = re.sub(r'\d+', '*', pat)
                if '*' not in pattern:
                    pattern = "*" + pattern + "*"
                if not self.cfg.add_map(pattern, self.sheet, self.result):
                    QMessageBox.warning(self, "保存失败", "配置保存失败，请检查磁盘空间或权限。")
        self.accept()

    def _skip(self):
        self.result = 'skip'
        self.accept()

    def closeEvent(self, event):
        if self._refresh_task is not None:
            try:
                self._refresh_task.columns_ready.disconnect()
                self._refresh_task.error_occurred.disconnect()
            except:
                pass
            self._refresh_task = None
        if self.cfg:
            self.cfg.set_window_geometry("MappingDialog", self.saveGeometry())
        super().closeEvent(event)

# ======================= 时段编辑对话框（微调尺寸） =======================
class PeriodEditDialog(QDialog):
    def __init__(self, parent, period=None, config_manager=None):
        super().__init__(parent)
        self.setWindowTitle("编辑时段")
        self.setMinimumSize(400, 260)
        self.result = None
        self.config_manager = config_manager

        if self.config_manager:
            geom = self.config_manager.get_window_geometry("PeriodEditDialog")
            if geom:
                self.restoreGeometry(geom)
            else:
                self.resize(420, 280)
        else:
            self.resize(420, 280)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("<b>开始时间</b> (例: 08:30 / 8.5 / 8 30 / 830):"))
        self.start_edit = QLineEdit(_fmt_hour(period['start']) if period else "08:00")
        layout.addWidget(self.start_edit)
        layout.addWidget(QLabel("<b>结束时间</b>:"))
        self.end_edit = QLineEdit(_fmt_hour(period['end']) if period else "11:00")
        layout.addWidget(self.end_edit)
        layout.addWidget(QLabel("<b>类型</b>:"))
        self.type_combo = QComboBox()
        self.type_combo.addItems([PeriodType.CHARGE_LABEL, PeriodType.DISCHARGE_LABEL])
        if period and period['type'] in (PeriodType.CHARGE, PeriodType.CHARGE_LABEL):
            self.type_combo.setCurrentIndex(0)
        layout.addWidget(self.type_combo)
        layout.addWidget(QLabel("支持格式: 08:30 / 8.5 / 8 30 / 830"))
        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self._ok)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _ok(self):
        try:
            s = parse_flexible_time(self.start_edit.text())
            e = parse_flexible_time(self.end_edit.text())
            if not (0 <= s <= 24 and 0 <= e <= 24):
                raise ValueError("时间必须在 0~24 之间")
            typ = PeriodType.CHARGE if '充电' in self.type_combo.currentText() else PeriodType.DISCHARGE
            self.result = {'start': s, 'end': e, 'type': typ}
            self.accept()
        except Exception as ex:
            QMessageBox.critical(self, "时间格式错误", f"请输入正确的时间:\n{ex}")

    def closeEvent(self, event):
        if self.config_manager:
            self.config_manager.set_window_geometry("PeriodEditDialog", self.saveGeometry())
        super().closeEvent(event)

# ======================= 充放策略管理对话框（不变） =======================
class ChargeDischargeManagerDialog(QDialog):
    strategy_selected = Signal(str)

    def __init__(self, parent, cfg):
        super().__init__(parent)
        self.cfg = cfg
        self.result = None
        self.setWindowTitle("充放时段策略管理")
        self.setMinimumSize(520, 370)

        if self.cfg:
            geom = self.cfg.get_window_geometry("ChargeDischargeManagerDialog")
            if geom:
                self.restoreGeometry(geom)
            else:
                self.resize(550, 400)
        else:
            self.resize(550, 400)

        layout = QVBoxLayout(self)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['策略名称', '已配置月份', '总时段数'])
        self.tree.itemDoubleClicked.connect(self._edit_strategy)
        layout.addWidget(self.tree)
        btn_layout = QHBoxLayout()
        btn_layout.addWidget(QPushButton("新建策略", clicked=self._new_strategy))
        btn_layout.addWidget(QPushButton("删除策略", clicked=self._delete_strategy))
        btn_layout.addWidget(QPushButton("编辑策略", clicked=self._edit_strategy))
        btn_layout.addStretch()
        layout.addLayout(btn_layout)
        dlg_btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        dlg_btns.accepted.connect(self._accept)
        dlg_btns.rejected.connect(self.reject)
        layout.addWidget(dlg_btns)
        self.refresh_list()

    def refresh_list(self):
        self.tree.clear()
        for name in self.cfg.strat_names():
            strat = self.cfg.get_strat(name)
            months = len(strat)
            total_periods = sum(len(p) for p in strat.values())
            self.tree.addTopLevelItem(QTreeWidgetItem([name, str(months), str(total_periods)]))

    def _new_strategy(self):
        dlg = ChargeDischargeEditDialog(self, self.cfg, None)
        if dlg.exec() == QDialog.Accepted and dlg.result:
            self.refresh_list()

    def _delete_strategy(self):
        items = self.tree.selectedItems()
        if items and QMessageBox.question(self, "确认", f"确定要删除策略 '{items[0].text(0)}' 吗？") == QMessageBox.Yes:
            self.cfg.del_strat(items[0].text(0))
            self.refresh_list()

    def _edit_strategy(self):
        items = self.tree.selectedItems()
        if items:
            dlg = ChargeDischargeEditDialog(self, self.cfg, items[0].text(0))
            if dlg.exec() == QDialog.Accepted:
                self.refresh_list()

    def _accept(self):
        items = self.tree.selectedItems()
        if items:
            self.result = items[0].text(0)
        self.accept()

    def closeEvent(self, event):
        if self.cfg:
            self.cfg.set_window_geometry("ChargeDischargeManagerDialog", self.saveGeometry())
        super().closeEvent(event)

# ======================= 充放策略编辑对话框（不变，仅微调尺寸） =======================
class ChargeDischargeEditDialog(QDialog):
    def __init__(self, parent, cfg, initial_name=None):
        super().__init__(parent)
        self.cfg = cfg
        self.result = None
        self.setWindowTitle("编辑充放时段")
        self.setMinimumSize(820, 620)
        self._original_name = initial_name
        self._session_data = {k: v.copy() for k, v in (cfg.get_strat(initial_name) or {}).items()} if initial_name else {}
        self.selected_month = "1"
        self.current_periods = self._session_data.get(self.selected_month, [])[:]
        self._undo_stack = []
        self._redo_stack = []
        self._max_history = 30
        self._skip_history = False

        if self.cfg:
            geom = self.cfg.get_window_geometry("ChargeDischargeEditDialog")
            if geom:
                self.restoreGeometry(geom)
            else:
                self.resize(850, 620)
        else:
            self.resize(850, 620)

        layout = QVBoxLayout(self)

        name_layout = QHBoxLayout()
        name_layout.addWidget(QLabel("策略名称:"))
        self.name_edit = QLineEdit(initial_name or "")
        name_layout.addWidget(self.name_edit)
        layout.addLayout(name_layout)

        month_layout = QHBoxLayout()
        month_layout.addWidget(QLabel("编辑月份:"))
        self.month_combo = QComboBox()
        self.month_combo.addItems([str(i) for i in range(1, 13)])
        self.month_combo.currentTextChanged.connect(self._switch_month)
        month_layout.addWidget(self.month_combo)
        self.month_status = QLabel("")
        month_layout.addWidget(self.month_status)
        month_layout.addStretch()
        apply_all_btn = QPushButton("应用到全部月份")
        apply_all_btn.clicked.connect(self._apply_to_all_months)
        month_layout.addWidget(apply_all_btn)
        copy_btn = QPushButton("复制到...")
        copy_btn.clicked.connect(self._copy_to_selected_months)
        month_layout.addWidget(copy_btn)
        layout.addLayout(month_layout)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['开始', '结束', '类型'])
        self.tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.tree.setDragDropMode(QTreeWidget.InternalMove)
        self.tree.setDefaultDropAction(Qt.MoveAction)
        self.tree.itemDoubleClicked.connect(self._edit_selected)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.model().rowsMoved.connect(self._on_rows_moved)
        layout.addWidget(self.tree)

        self.conflict_label = QLabel("")
        self.conflict_label.setStyleSheet("color: red; font-weight: bold;")
        layout.addWidget(self.conflict_label)

        tool_layout = QHBoxLayout()

        edit_frame = QFrame()
        edit_frame.setFrameStyle(QFrame.StyledPanel)
        edit_layout = QHBoxLayout(edit_frame)
        edit_layout.setContentsMargins(4, 4, 4, 4)
        edit_layout.addWidget(QLabel("编辑:"))
        edit_layout.addWidget(QPushButton("添加", clicked=self._add_period))
        edit_layout.addWidget(QPushButton("编辑", clicked=self._edit_selected))
        edit_layout.addWidget(QPushButton("删除", clicked=self._delete_selected))
        edit_layout.addWidget(QPushButton("清空本月", clicked=self._clear_month))
        tool_layout.addWidget(edit_frame)

        batch_frame = QFrame()
        batch_frame.setFrameStyle(QFrame.StyledPanel)
        batch_layout = QHBoxLayout(batch_frame)
        batch_layout.setContentsMargins(4, 4, 4, 4)
        batch_layout.addWidget(QLabel("批量:"))
        batch_layout.addWidget(QPushButton("改类型", clicked=self._batch_change_type))
        batch_layout.addWidget(QPushButton("经典模板", clicked=self._preset_classic))
        batch_layout.addWidget(QPushButton("夏季模板", clicked=self._preset_summer))
        tool_layout.addWidget(batch_frame)

        undo_frame = QFrame()
        undo_frame.setFrameStyle(QFrame.StyledPanel)
        undo_layout = QHBoxLayout(undo_frame)
        undo_layout.setContentsMargins(4, 4, 4, 4)
        self.undo_btn = QPushButton("↩ 撤销")
        self.undo_btn.clicked.connect(self._undo)
        self.redo_btn = QPushButton("↪ 重做")
        self.redo_btn.clicked.connect(self._redo)
        undo_layout.addWidget(self.undo_btn)
        undo_layout.addWidget(self.redo_btn)
        tool_layout.addWidget(undo_frame)

        layout.addLayout(tool_layout)

        dlg_btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        dlg_btns.accepted.connect(self._save)
        dlg_btns.rejected.connect(self.reject)
        layout.addWidget(dlg_btns)

        self._populate_list()
        self._update_month_status()
        self._update_undo_redo_buttons()
        self._check_conflicts()

        self.shortcut_undo = QAction("撤销", self)
        self.shortcut_undo.setShortcut("Ctrl+Z")
        self.shortcut_undo.triggered.connect(self._undo)
        self.addAction(self.shortcut_undo)
        self.shortcut_redo = QAction("重做", self)
        self.shortcut_redo.setShortcut("Ctrl+Y")
        self.shortcut_redo.triggered.connect(self._redo)
        self.addAction(self.shortcut_redo)

    def _push_history(self):
        if self._skip_history:
            return
        self._undo_stack.append((self.selected_month, self.current_periods[:]))
        if len(self._undo_stack) > self._max_history:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self._update_undo_redo_buttons()

    def _undo(self):
        if not self._undo_stack:
            return
        self._commit_current_to_session()
        month, periods = self._undo_stack.pop()
        if self.selected_month != month:
            self.month_combo.setCurrentText(month)
        self._redo_stack.append((self.selected_month, self.current_periods[:]))
        self.current_periods = periods[:]
        self._session_data[month] = periods[:]
        self._populate_list()
        self._update_month_status()
        self._update_undo_redo_buttons()
        self._check_conflicts()

    def _redo(self):
        if not self._redo_stack:
            return
        self._commit_current_to_session()
        month, periods = self._redo_stack.pop()
        if self.selected_month != month:
            self.month_combo.setCurrentText(month)
        self._undo_stack.append((self.selected_month, self.current_periods[:]))
        self.current_periods = periods[:]
        self._session_data[month] = periods[:]
        self._populate_list()
        self._update_month_status()
        self._update_undo_redo_buttons()
        self._check_conflicts()

    def _update_undo_redo_buttons(self):
        self.undo_btn.setEnabled(len(self._undo_stack) > 0)
        self.redo_btn.setEnabled(len(self._redo_stack) > 0)

    def _commit_current_to_session(self):
        self._session_data[self.selected_month] = self.current_periods[:]

    def _switch_month(self):
        self._commit_current_to_session()
        new_month = self.month_combo.currentText()
        if new_month == self.selected_month:
            return
        self.selected_month = new_month
        self.current_periods = self._session_data.get(self.selected_month, [])[:]
        self._populate_list()
        self._update_month_status()
        self._check_conflicts()

    def _update_month_status(self):
        cnt = len(self.current_periods)
        self.month_status.setText(f"已配置 {cnt} 个时段" if cnt else "尚未配置")

    def _find_overnight_indices(self):
        indices = set()
        periods = self.current_periods
        for i in range(len(periods)-1):
            p1 = periods[i]
            p2 = periods[i+1]
            if p1['end'] == 24.0 and p2['start'] == 0.0 and p1['type'] == p2['type']:
                indices.add(i)
                indices.add(i+1)
        return indices

    def _populate_list(self):
        self.tree.clear()
        overnight = self._find_overnight_indices()
        for i, p in enumerate(self.current_periods):
            s = _fmt_hour(p['start'])
            e = _fmt_hour(p['end'])
            is_charge = p['type'] in (PeriodType.CHARGE, PeriodType.CHARGE_LABEL)
            t = PeriodType.CHARGE_LABEL if is_charge else PeriodType.DISCHARGE_LABEL
            if i in overnight:
                t += " (跨夜)"
            item = QTreeWidgetItem([s, e, t])
            if is_charge:
                item.setBackground(2, QColor(ColorConst.READY_BG))
                item.setForeground(2, QColor(ColorConst.READY_FG))
            else:
                item.setBackground(2, QColor(ColorConst.DISCHARGE_BG_ITEM))
                item.setForeground(2, QColor(ColorConst.DISCHARGE_FG))
            self.tree.addTopLevelItem(item)

    def _on_rows_moved(self, parent, start, end, destination, row):
        new_periods = []
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            start_text = item.text(0)
            end_text = item.text(1)
            for p in self.current_periods:
                if _fmt_hour(p['start']) == start_text and _fmt_hour(p['end']) == end_text:
                    new_periods.append(p)
                    break
        if len(new_periods) == len(self.current_periods):
            self._push_history()
            self.current_periods = new_periods
        self._check_conflicts()

    def _add_period(self):
        self._push_history()
        dlg = PeriodEditDialog(self, config_manager=self.cfg)
        if dlg.exec() == QDialog.Accepted and dlg.result:
            self.current_periods.append(dlg.result)
            ok, msg, _ = _valid_periods(self.current_periods)
            if not ok:
                self.current_periods.pop()
                QMessageBox.critical(self, "时段冲突", msg)
            else:
                self._populate_list()
                self._update_month_status()
        elif self._undo_stack:
            self._undo_stack.pop()
            self._update_undo_redo_buttons()
        self._check_conflicts()

    def _edit_selected(self):
        items = self.tree.selectedItems()
        if not items:
            return
        self._push_history()
        idx = self.tree.indexOfTopLevelItem(items[0])
        dlg = PeriodEditDialog(self, self.current_periods[idx], config_manager=self.cfg)
        if dlg.exec() == QDialog.Accepted and dlg.result:
            old = self.current_periods[idx]
            self.current_periods[idx] = dlg.result
            ok, msg, _ = _valid_periods(self.current_periods)
            if not ok:
                self.current_periods[idx] = old
                QMessageBox.critical(self, "时段冲突", msg)
            else:
                self._populate_list()
                self._update_month_status()
        elif self._undo_stack:
            self._undo_stack.pop()
            self._update_undo_redo_buttons()
        self._check_conflicts()

    def _delete_selected(self):
        items = self.tree.selectedItems()
        if not items:
            return
        self._push_history()
        if QMessageBox.question(self, "确认", f"删除选中的 {len(items)} 个时段？") == QMessageBox.Yes:
            for item in reversed(items):
                idx = self.tree.indexOfTopLevelItem(item)
                del self.current_periods[idx]
            self._populate_list()
            self._update_month_status()
        elif self._undo_stack:
            self._undo_stack.pop()
            self._update_undo_redo_buttons()
        self._check_conflicts()

    def _clear_month(self):
        if self.current_periods:
            self._push_history()
            if QMessageBox.question(self, "确认", "清空本月全部时段？") == QMessageBox.Yes:
                self.current_periods.clear()
                self._populate_list()
                self._update_month_status()
            elif self._undo_stack:
                self._undo_stack.pop()
                self._update_undo_redo_buttons()
        self._check_conflicts()

    def _batch_change_type(self):
        items = self.tree.selectedItems()
        if not items:
            return
        self._push_history()
        dlg = QDialog(self)
        dlg.setWindowTitle("修改类型")
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel("将选中的时段改为:"))
        combo = QComboBox()
        combo.addItems([PeriodType.CHARGE_LABEL, PeriodType.DISCHARGE_LABEL])
        layout.addWidget(combo)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)
        if dlg.exec() == QDialog.Accepted:
            new_type = PeriodType.CHARGE if '充电' in combo.currentText() else PeriodType.DISCHARGE
            for item in items:
                idx = self.tree.indexOfTopLevelItem(item)
                self.current_periods[idx]['type'] = new_type
            self._populate_list()
        elif self._undo_stack:
            self._undo_stack.pop()
            self._update_undo_redo_buttons()
        self._check_conflicts()

    def _preset_classic(self):
        self._push_history()
        new_periods = [
            {'start': 8, 'end': 11, 'type': PeriodType.DISCHARGE},
            {'start': 18, 'end': 21, 'type': PeriodType.DISCHARGE},
            {'start': 23, 'end': 7, 'type': PeriodType.CHARGE}
        ]
        ok, msg, _ = _valid_periods(new_periods)
        if not ok:
            QMessageBox.critical(self, "快速模板无效", msg)
            self._undo_stack.pop()
            self._update_undo_redo_buttons()
            return
        self.current_periods = new_periods
        self._populate_list()
        self._update_month_status()
        self._check_conflicts()

    def _preset_summer(self):
        self._push_history()
        new_periods = [
            {'start': 10, 'end': 12, 'type': PeriodType.DISCHARGE},
            {'start': 14, 'end': 16, 'type': PeriodType.DISCHARGE},
            {'start': 19, 'end': 22, 'type': PeriodType.DISCHARGE},
            {'start': 22, 'end': 6, 'type': PeriodType.CHARGE}
        ]
        ok, msg, _ = _valid_periods(new_periods)
        if not ok:
            QMessageBox.critical(self, "快速模板无效", msg)
            self._undo_stack.pop()
            self._update_undo_redo_buttons()
            return
        self.current_periods = new_periods
        self._populate_list()
        self._update_month_status()
        self._check_conflicts()

    def _apply_to_all_months(self):
        if not self.current_periods:
            QMessageBox.warning(self, "提示", "当前月份没有时段")
            return
        if QMessageBox.question(self, "确认", "将当前月份的时段复制到所有12个月？") == QMessageBox.Yes:
            self._push_history()
            for m in range(1, 13):
                self._session_data[str(m)] = self.current_periods[:]
            self._update_month_status()
            QMessageBox.information(self, "完成", "已同步到全部月份")

    def _copy_to_selected_months(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("选择目标月份")
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel("勾选要复制到的月份:"))
        checks = {}
        grid = QGridLayout()
        for i in range(1, 13):
            cb = QCheckBox(f"{i}月")
            checks[str(i)] = cb
            grid.addWidget(cb, (i - 1) // 4, (i - 1) % 4)
        layout.addLayout(grid)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)
        if dlg.exec() == QDialog.Accepted:
            targets = [m for m, cb in checks.items() if cb.isChecked()]
            if targets:
                self._push_history()
                for m in targets:
                    self._session_data[m] = self.current_periods[:]
                self._update_month_status()
                QMessageBox.information(self, "完成", "已复制")

    def _context_menu(self, pos):
        item = self.tree.itemAt(pos)
        menu = QMenu()
        if item:
            edit_action = menu.addAction("编辑")
            delete_action = menu.addAction("删除")
            menu.addSeparator()
            move_up_action = menu.addAction("上移")
            move_down_action = menu.addAction("下移")
            menu.addSeparator()
            change_type_action = menu.addAction("更改类型...")

            action = menu.exec(self.tree.viewport().mapToGlobal(pos))
            if action == edit_action:
                self._edit_selected()
            elif action == delete_action:
                self._delete_selected()
            elif action == move_up_action:
                self._move_item_up(item)
            elif action == move_down_action:
                self._move_item_down(item)
            elif action == change_type_action:
                self._batch_change_type()
        else:
            add_action = menu.addAction("添加时段")
            if menu.exec(self.tree.viewport().mapToGlobal(pos)) == add_action:
                self._add_period()

    def _move_item_up(self, item):
        idx = self.tree.indexOfTopLevelItem(item)
        if idx > 0:
            self._push_history()
            self.current_periods[idx], self.current_periods[idx - 1] = self.current_periods[idx - 1], self.current_periods[idx]
            self._populate_list()
            self._check_conflicts()

    def _move_item_down(self, item):
        idx = self.tree.indexOfTopLevelItem(item)
        if idx < self.tree.topLevelItemCount() - 1:
            self._push_history()
            self.current_periods[idx], self.current_periods[idx + 1] = self.current_periods[idx + 1], self.current_periods[idx]
            self._populate_list()
            self._check_conflicts()

    def _check_conflicts(self):
        ok, msg, conflicts = _valid_periods(self.current_periods)
        if not ok:
            self.conflict_label.setText(f"⚠ {msg}")
            for i in range(self.tree.topLevelItemCount()):
                item = self.tree.topLevelItem(i)
                item.setBackground(0, QColor("#FFE4E4"))
        else:
            self.conflict_label.setText("")
            for i in range(self.tree.topLevelItemCount()):
                item = self.tree.topLevelItem(i)
                p = self.current_periods[i] if i < len(self.current_periods) else None
                if p:
                    is_charge = p['type'] in (PeriodType.CHARGE, PeriodType.CHARGE_LABEL)
                    if is_charge:
                        item.setBackground(2, QColor(ColorConst.READY_BG))
                    else:
                        item.setBackground(2, QColor(ColorConst.DISCHARGE_BG_ITEM))
                    item.setBackground(0, QColor("white"))

    def _save(self):
        self._commit_current_to_session()
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "提示", "请输入策略名称")
            return
        if name != self._original_name and name in self.cfg.strat_names():
            if QMessageBox.question(self, "名称已存在", f"策略 '{name}' 已存在，是否覆盖？",
                                    QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
                return
        clean = {k: v for k, v in self._session_data.items() if v}
        if not clean:
            QMessageBox.warning(self, "提示", "所有月份均无时段，无法保存空策略")
            return
        for m in clean:
            if not m.isdigit() or not (1 <= int(m) <= 12):
                QMessageBox.critical(self, "错误", f"非法月份键: {m}")
                return
            ok, msg, _ = _valid_periods(clean[m])
            if not ok:
                QMessageBox.critical(self, f"月份 {m} 校验失败", msg)
                self.month_combo.setCurrentText(m)
                self._switch_month()
                return
        if not self.cfg.save_strat(name, clean):
            QMessageBox.critical(self, "保存失败", "策略保存到配置文件失败，请检查磁盘空间或权限。")
            return
        self.cfg.set_active(name)
        self.result = name
        self.accept()

    def reject(self):
        self.result = None
        super().reject()

    def closeEvent(self, event):
        if self.cfg:
            self.cfg.set_window_geometry("ChargeDischargeEditDialog", self.saveGeometry())
        super().closeEvent(event)

# ======================= 预览渲染任务 =======================
class PreviewRenderTask(QObject, QRunnable):
    preview_ready = Signal(int, QPixmap)
    error_occurred = Signal(int, str)
    task_done = Signal(int)

    def __init__(self, index, summary, provider, plot_options, multiplier, billing, periods, title_tpl):
        QObject.__init__(self)
        QRunnable.__init__(self)
        self.index = index
        self.summary = summary
        self.provider = provider
        self.plot_options = plot_options
        self.multiplier = multiplier
        self.billing = billing
        self.periods = periods
        self.title_tpl = title_tpl
        self._cancel_event = threading.Event()

    def cancel(self):
        self._cancel_event.set()

    def run(self):
        if self._cancel_event.is_set():
            return
        try:
            with plt.rc_context({'font.sans-serif': [_get_cached_font()], 'axes.unicode_minus': False}):
                data = self.provider.get_group_data(self.summary)
                if self._cancel_event.is_set():
                    return
                if not data:
                    error_msg = self.provider.get_load_error(self.summary)
                    self.error_occurred.emit(self.index,
                        f"无法加载数据: {error_msg}\n文件:{self.summary.file_path}\n工作表:{self.summary.sheet_name}\n月份:{self.summary.month_str}")
                    return
                load_array = np.array(data['load_array']) * self.multiplier
                if load_array.ndim != 2 or load_array.size == 0:
                    self.error_occurred.emit(self.index, f"数据形状异常 {load_array.shape}")
                    return

                fig, ax = plt.subplots(figsize=(10, 6), dpi=100)
                if self._cancel_event.is_set():
                    plt.close(fig)
                    return
                ctx = {
                    'year_month': data.get('month_str', ''),
                    'table_no': data.get('table_no', ''),
                    'asset': data.get('asset', ''),
                    'sheet': data.get('sheet', '')
                }
                title = parse_title_template(self.title_tpl, ctx) if self.title_tpl else generate_default_title(ctx)

                _draw_curves_core(ax, data['date_labels'], load_array, data['time_labels'],
                                  title, self.plot_options, self.billing, self.periods)

                fig.tight_layout()
                buf = io.BytesIO()
                fig.savefig(buf, format='png', dpi=100)
                plt.close(fig)
                buf.seek(0)
                if self._cancel_event.is_set():
                    return
                pix = QPixmap()
                pix.loadFromData(buf.read(), "PNG")
                self.preview_ready.emit(self.index, pix)
        except Exception as e:
            log.error(f"预览渲染失败 {self.summary.file_path}: {e}", exc_info=True)
            self.error_occurred.emit(self.index, str(e))
        finally:
            self.task_done.emit(self.index)

# 第二部分结束
# -*- coding: utf-8 -*-
"""
负荷曲线批量生成工具 Pro 融合版 (智能增强版 v2.1)
第三部分：预览对话框辅助控制器、预览对话框

增强记录：
- 预览信息标签增加示数表格式提示
- 对话框最小尺寸保持，内部布局不变
- 渲染调度器保持原有线程安全优化
"""

# ===================== 预览对话框辅助控制器 =====================

class _GroupTreeController(QObject):
    """管理左侧分组树形控件与勾选状态，发射导航和选择变化信号"""
    selectionChanged = Signal(set)      # 当前所有勾选的索引集合
    itemClicked = Signal(int)           # 用户单击某分组（用于导航）

    def __init__(self, tree_widget: QTreeWidget, parent=None):
        super().__init__(parent)
        self.tree = tree_widget
        self.summaries = []
        self.selected_indices = set()
        self.no_data_indices = set()
        self._sort_key = "按表号"
        self._block_signals = False
        self._status_map = {}           # 保存每个索引的状态文本
        self._last_snapshot = None      # 全选/全不选前的快照，用于恢复

        self.tree.itemClicked.connect(self._on_item_clicked)
        self.tree.itemChanged.connect(self._on_item_changed)

    def load(self, summaries, no_data_indices=None):
        self.summaries = summaries
        if no_data_indices is not None:
            self.no_data_indices = no_data_indices
        self._status_map.clear()
        self._rebuild()

    def set_sort_key(self, key):
        self._sort_key = key
        self._rebuild()

    def filter_tree(self, text):
        t = text.strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            asset_item = self.tree.topLevelItem(i)
            asset_visible = False
            for j in range(asset_item.childCount()):
                child = asset_item.child(j)
                match = not t or t in child.text(0).lower() or t in asset_item.text(0).lower()
                child.setHidden(not match)
                if match:
                    asset_visible = True
            asset_item.setHidden(not asset_visible)
            if asset_visible and t:
                self.tree.expandItem(asset_item)

    def set_all_check(self, checked):
        self._last_snapshot = self.selected_indices.copy()
        if checked:
            self.selected_indices = set(range(len(self.summaries))) - self.no_data_indices
        else:
            self.selected_indices.clear()
        self._rebuild()
        QTimer.singleShot(2000, lambda: setattr(self, '_last_snapshot', None))

    def undo_check_change(self):
        if self._last_snapshot is not None:
            self.selected_indices = self._last_snapshot.copy()
            self._last_snapshot = None
            self._rebuild()
            return True
        return False

    def select_by_asset_dialog(self, parent_widget):
        assets = sorted(set(s.asset_value for s in self.summaries if s.asset_value))
        if not assets:
            QMessageBox.information(parent_widget, "提示", "没有识别到任何表号")
            return
        dlg = QDialog(parent_widget)
        dlg.setWindowTitle("按表号多选")
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel("勾选表号:"))
        checks = {}
        for a in assets:
            cb = QCheckBox(a)
            checks[a] = cb
            layout.addWidget(cb)
        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)
        layout.addWidget(btn_box)
        if dlg.exec() == QDialog.Accepted:
            targets = {a for a, cb in checks.items() if cb.isChecked()}
            for i, s in enumerate(self.summaries):
                if s.asset_value in targets and i not in self.no_data_indices:
                    self.selected_indices.add(i)
            self._rebuild()

    def get_checked_indices(self):
        return self.selected_indices.copy()

    def update_status(self, idx, status):
        self._status_map[idx] = status
        for i in range(self.tree.topLevelItemCount()):
            asset_item = self.tree.topLevelItem(i)
            for j in range(asset_item.childCount()):
                child = asset_item.child(j)
                if child.data(0, Qt.UserRole) == idx:
                    child.setText(1, status)
                    return

    def set_current_index(self, idx):
        """高亮树中指定索引的项（不触发信号）"""
        self._block_signals = True
        for i in range(self.tree.topLevelItemCount()):
            asset_item = self.tree.topLevelItem(i)
            for j in range(asset_item.childCount()):
                child = asset_item.child(j)
                if child.data(0, Qt.UserRole) == idx:
                    self.tree.setCurrentItem(child)
                    break
        self._block_signals = False

    def _rebuild(self):
        self._block_signals = True
        self.tree.clear()
        summaries = list(enumerate(self.summaries))
        if self._sort_key == "按月份":
            summaries.sort(key=lambda x: x[1].month_str)
        else:
            summaries.sort(key=lambda x: x[1].asset_value or '')

        asset_dict = OrderedDict()
        for idx, s in summaries:
            asset = s.asset_value or "未命名"
            asset_dict.setdefault(asset, []).append((idx, s))

        for asset, items in asset_dict.items():
            asset_item = QTreeWidgetItem([asset, ""])
            asset_item.setFlags(asset_item.flags() | Qt.ItemIsUserCheckable)
            asset_item.setData(0, Qt.UserRole, -1)
            self.tree.addTopLevelItem(asset_item)

            for idx, s in items:
                status = self._status_map.get(idx, "等待加载...")
                month_item = QTreeWidgetItem([s.month_str, status])
                if idx in self.no_data_indices:
                    month_item.setFlags(month_item.flags() & ~Qt.ItemIsUserCheckable)
                    month_item.setCheckState(0, Qt.Unchecked)
                    month_item.setForeground(0, QColor('gray'))
                    month_item.setToolTip(0, "该分组无有效数据，无法勾选")
                else:
                    month_item.setFlags(month_item.flags() | Qt.ItemIsUserCheckable)
                    checked = Qt.Checked if idx in self.selected_indices else Qt.Unchecked
                    month_item.setCheckState(0, checked)
                month_item.setData(0, Qt.UserRole, idx)
                asset_item.addChild(month_item)

            self._update_parent_check(asset_item)
            self.tree.expandItem(asset_item)

        self._block_signals = False

    def _update_parent_check(self, parent_item):
        all_checked = all(parent_item.child(i).checkState(0) == Qt.Checked for i in range(parent_item.childCount()) if parent_item.child(i).flags() & Qt.ItemIsUserCheckable)
        any_checked = any(parent_item.child(i).checkState(0) == Qt.Checked for i in range(parent_item.childCount()) if parent_item.child(i).flags() & Qt.ItemIsUserCheckable)
        if all_checked:
            parent_item.setCheckState(0, Qt.Checked)
        elif any_checked:
            parent_item.setCheckState(0, Qt.PartiallyChecked)
        else:
            parent_item.setCheckState(0, Qt.Unchecked)

    def _on_item_clicked(self, item, col):
        idx = item.data(0, Qt.UserRole)
        if idx is not None and idx >= 0:
            self.itemClicked.emit(idx)

    def _on_item_changed(self, item, col):
        if self._block_signals:
            return
        idx = item.data(0, Qt.UserRole)
        if idx == -1:
            state = item.checkState(0)
            self._block_signals = True
            for i in range(item.childCount()):
                child = item.child(i)
                if not child.flags() & Qt.ItemIsUserCheckable:
                    continue
                child.setCheckState(0, state)
                cidx = child.data(0, Qt.UserRole)
                if cidx >= 0 and cidx not in self.no_data_indices:
                    if state == Qt.Checked:
                        self.selected_indices.add(cidx)
                    else:
                        self.selected_indices.discard(cidx)
            self._block_signals = False
            self._update_parent_check(item)
            self.selectionChanged.emit(self.selected_indices.copy())
        elif idx >= 0 and idx not in self.no_data_indices:
            if item.checkState(0) == Qt.Checked:
                self.selected_indices.add(idx)
            else:
                self.selected_indices.discard(idx)
            parent = item.parent()
            if parent:
                self._block_signals = True
                self._update_parent_check(parent)
                self._block_signals = False
            self.selectionChanged.emit(self.selected_indices.copy())


class _PerGroupSettings(QObject):
    """管理分组级倍率与容量自定义值，以及资产级容量字典"""
    settingsChanged = Signal()

    def __init__(self, summaries, global_multiplier, billing_dict=None, parent=None):
        super().__init__(parent)
        self.summaries = summaries
        self.global_multiplier = global_multiplier
        self._custom_mult = {}
        self._custom_billing = {}
        self._global_billing = billing_dict or {}

        self._history_mult = {}
        self._history_billing = {}

    def effective_multiplier(self, idx):
        return self._custom_mult.get(idx, self.global_multiplier)

    def effective_billing(self, idx):
        if idx in self._custom_billing:
            return self._custom_billing[idx]
        s = self.summaries[idx]
        return self._global_billing.get(s.asset_value)

    def set_custom_multiplier(self, idx, value):
        self._history_mult[idx] = self._custom_mult.get(idx, self.global_multiplier)
        self._custom_mult[idx] = value
        self.settingsChanged.emit()

    def set_custom_billing(self, idx, billing):
        self._history_billing[idx] = self._custom_billing.get(idx) or self._global_billing.get(self.summaries[idx].asset_value)
        if billing is None:
            self._custom_billing.pop(idx, None)
        else:
            self._custom_billing[idx] = billing
        self.settingsChanged.emit()

    def clear_custom_multiplier(self, idx):
        self._history_mult[idx] = self._custom_mult.get(idx, self.global_multiplier)
        self._custom_mult.pop(idx, None)
        self.settingsChanged.emit()

    def clear_custom_billing(self, idx):
        self._history_billing[idx] = self._custom_billing.get(idx) or self._global_billing.get(self.summaries[idx].asset_value)
        self._custom_billing.pop(idx, None)
        self.settingsChanged.emit()

    def has_custom_mult(self, idx):
        return idx in self._custom_mult

    def has_custom_bill(self, idx):
        return idx in self._custom_billing

    def set_global_multiplier(self, value):
        self.global_multiplier = value
        self.settingsChanged.emit()

    def set_global_billing(self, asset, billing):
        if billing is None:
            self._global_billing.pop(asset, None)
        else:
            self._global_billing[asset] = billing
        self.settingsChanged.emit()

    def get_custom_multipliers(self):
        return self._custom_mult.copy()

    def get_custom_billings(self):
        return self._custom_billing.copy()

    def undo_last_mult(self, idx):
        if idx in self._history_mult:
            old_val = self._history_mult.pop(idx)
            if old_val == self.global_multiplier:
                self._custom_mult.pop(idx, None)
            else:
                self._custom_mult[idx] = old_val
            self.settingsChanged.emit()
            return True
        return False

    def undo_last_bill(self, idx):
        if idx in self._history_billing:
            old_bill = self._history_billing.pop(idx)
            if old_bill is None:
                self._custom_billing.pop(idx, None)
            else:
                self._custom_billing[idx] = old_bill
            self.settingsChanged.emit()
            return True
        return False


class _PreviewRenderScheduler(QObject):
    """管理异步渲染队列、缓存与参数指纹，并负责渲染任务的生命周期"""
    renderReady = Signal(int, QPixmap)
    renderError = Signal(int, str)
    cacheProgress = Signal(str)

    def __init__(self, provider, summaries, settings_model, strategy_getter, title_tpl_getter, parent=None):
        super().__init__(parent)
        self.provider = provider
        self.summaries = summaries
        self.settings_model = settings_model
        self._get_strat = strategy_getter
        self._get_title_tpl = title_tpl_getter
        self._no_data_indices = set()
        self._rendering_set = set()
        self._param_fingerprints = {}
        self._pixmap_cache = OrderedDict()
        self._max_cache = PREVIEW_CACHE_MAXSIZE
        self._thread_pool = QThreadPool()
        self._thread_pool.setMaxThreadCount(2)
        self._render_tasks = {}
        self._render_lock = threading.RLock()
        self.plot_options = {'show_daily': True, 'show_avg': True, 'show_max_demand': False,
                             'fit_types': [], 'highlight_mode': 'none',
                             'weekday_color': DEFAULT_WEEKDAY_COLOR,
                             'weekend_color': DEFAULT_WEEKEND_COLOR,
                             'holiday_color': DEFAULT_HOLIDAY_COLOR,
                             'weekend_line_width': DEFAULT_WEEKEND_LINE_WIDTH,
                             'holiday_line_width': DEFAULT_HOLIDAY_LINE_WIDTH,
                             'show_weekend_bg': False,
                             'legend_position': 'right',
                             'mark_anomalies': False}

    def set_plot_options(self, opts):
        self.plot_options.update(opts)

    def set_no_data_indices(self, indices):
        self._no_data_indices = set(indices)

    def clear_all_caches(self):
        with self._render_lock:
            self._pixmap_cache.clear()
            self._param_fingerprints.clear()
        self.cancel_all()

    def clear_cache_for_index(self, idx):
        with self._render_lock:
            if idx in self._pixmap_cache:
                del self._pixmap_cache[idx]
            if idx in self._param_fingerprints:
                del self._param_fingerprints[idx]
            if idx in self._render_tasks:
                self._render_tasks[idx].cancel()
                del self._render_tasks[idx]
                self._rendering_set.discard(idx)

    def cancel_all(self):
        with self._render_lock:
            tasks = list(self._render_tasks.values())
            for task in tasks:
                task.cancel()
            self._render_tasks.clear()
            self._rendering_set.clear()

    def allow_retry(self, idx):
        self._no_data_indices.discard(idx)

    def schedule_render(self, idx):
        if idx in self._no_data_indices:
            return
        new_fp = self._compute_fingerprint(idx)
        with self._render_lock:
            if idx in self._pixmap_cache and self._param_fingerprints.get(idx) == new_fp:
                pix = self._pixmap_cache[idx]
                self._pixmap_cache.move_to_end(idx)
                self.renderReady.emit(idx, pix)
                return
            if idx in self._rendering_set:
                if idx in self._render_tasks:
                    self._render_tasks[idx].cancel()
                    del self._render_tasks[idx]
                self._rendering_set.discard(idx)

            self._rendering_set.add(idx)

        summary = self.summaries[idx]
        multiplier = self.settings_model.effective_multiplier(idx)
        billing = self.settings_model.effective_billing(idx)
        periods = self._get_strat(idx) if self._get_strat else []

        task = PreviewRenderTask(
            idx, summary, self.provider,
            self.plot_options,
            multiplier,
            billing,
            periods,
            self._get_title_tpl() if self._get_title_tpl else ''
        )
        task.preview_ready.connect(self._on_render_ready)
        task.error_occurred.connect(self._on_render_error)
        task.task_done.connect(self._on_task_done)
        with self._render_lock:
            self._render_tasks[idx] = task
        self._thread_pool.start(task)

    def _compute_fingerprint(self, idx):
        periods = self._get_strat(idx) if self._get_strat else []
        periods_tuple = tuple(sorted(((p['start'], p['end'], p['type']) for p in periods)))
        fp = (
            self.settings_model.effective_multiplier(idx),
            self.settings_model.effective_billing(idx)['value'] if self.settings_model.effective_billing(idx) else None,
            self.plot_options.get('show_daily', True),
            self.plot_options.get('show_avg', True),
            self.plot_options.get('show_max_demand', True),
            self.plot_options.get('highlight_mode', 'none'),
            self.plot_options.get('weekday_color', DEFAULT_WEEKDAY_COLOR),
            self.plot_options.get('weekend_color', DEFAULT_WEEKEND_COLOR),
            self.plot_options.get('holiday_color', DEFAULT_HOLIDAY_COLOR),
            self.plot_options.get('weekend_line_width', DEFAULT_WEEKEND_LINE_WIDTH),
            self.plot_options.get('holiday_line_width', DEFAULT_HOLIDAY_LINE_WIDTH),
            self.plot_options.get('show_weekend_bg', False),
            self.plot_options.get('legend_position', 'right'),
            self.plot_options.get('mark_anomalies', False),
            periods_tuple
        )
        return hash(fp)

    def _on_render_ready(self, idx, pix):
        with self._render_lock:
            self._param_fingerprints[idx] = self._compute_fingerprint(idx)
            if idx in self._pixmap_cache:
                del self._pixmap_cache[idx]
            while len(self._pixmap_cache) >= self._max_cache:
                self._pixmap_cache.popitem(last=False)
            self._pixmap_cache[idx] = pix
            self._rendering_set.discard(idx)
        self._no_data_indices.discard(idx)
        self.renderReady.emit(idx, pix)
        self._update_cache_progress()

    def _on_render_error(self, idx, error_msg):
        with self._render_lock:
            self._rendering_set.discard(idx)
        self.renderError.emit(idx, error_msg)
        self._update_cache_progress()

    def _on_task_done(self, idx):
        with self._render_lock:
            if idx in self._render_tasks:
                del self._render_tasks[idx]

    def _update_cache_progress(self):
        total = len(self.summaries) - len(self._no_data_indices)
        cached = len(self._pixmap_cache)
        self.cacheProgress.emit(f"已缓存: {cached} / {total}")


# ===================== 预览对话框（增加格式提示） =====================

class PreviewDialog(QDialog):
    def __init__(self, parent, all_summaries, config_manager, initial_multiplier=1.0,
                 initial_show_max_demand=False, initial_strategy_name=None, initial_output_dir="", data_provider=None):
        super().__init__(parent)
        self.setWindowFlags(self.windowFlags() | Qt.WindowMinMaxButtonsHint)
        self.all_summaries = all_summaries
        self.config_manager = config_manager
        self.current_index = 0
        self._first_render_done = False
        self._last_strategy_text = ""
        self._zoom_level = 1.0
        self._show_max_demand = initial_show_max_demand if initial_show_max_demand is not None else False
        self._strategy_name = initial_strategy_name

        self.provider = data_provider if data_provider else DataProvider(
            self.config_manager.app_set('last_csv_encoding', 'auto'))

        self.setWindowTitle("负荷曲线预览与设置")
        self.setMinimumSize(1000, 650)
        if self.config_manager:
            geom = self.config_manager.get_window_geometry("PreviewDialog")
            if geom:
                self.restoreGeometry(geom)
            else:
                self.resize(1200, 800)
        else:
            self.resize(1200, 800)

        self.tree_controller = None
        self._global_multiplier = initial_multiplier
        self._global_billing_dict = {}

        self.settings_model = _PerGroupSettings(
            self.all_summaries,
            self._global_multiplier,
            billing_dict=self._global_billing_dict
        )
        self.settings_model.settingsChanged.connect(self._on_settings_changed)

        self.render_scheduler = None

        # 读取上次保存的高亮设置
        self._highlight_mode = self.config_manager.app_set('highlight_mode', 'none')
        self._weekday_color = self.config_manager.app_set('weekday_color', DEFAULT_WEEKDAY_COLOR)
        self._weekend_color = self.config_manager.app_set('weekend_color', DEFAULT_WEEKEND_COLOR)
        self._holiday_color = self.config_manager.app_set('holiday_color', DEFAULT_HOLIDAY_COLOR)
        self._weekend_line_width = self.config_manager.app_set('weekend_line_width', DEFAULT_WEEKEND_LINE_WIDTH)
        self._holiday_line_width = self.config_manager.app_set('holiday_line_width', DEFAULT_HOLIDAY_LINE_WIDTH)
        self._show_weekend_bg = self.config_manager.app_set('show_weekend_bg', False)
        self._legend_position = self.config_manager.app_set('legend_position', 'right')
        self._mark_anomalies = self.config_manager.app_set('mark_anomalies', False)

        self._preload_timer = None
        self._is_closing = False
        self._reusable = True
        self._install_process = None
        self._install_retry_count = 0

        self._init_ui(initial_multiplier, initial_output_dir)

        self.render_scheduler = _PreviewRenderScheduler(
            self.provider,
            self.all_summaries,
            self.settings_model,
            strategy_getter=lambda idx: self._periods_for_idx(idx),
            title_tpl_getter=lambda: self.title_edit.text()
        )
        self._connect_render_signals()
        self._apply_global_display_options()

        self.tree_controller.load(self.all_summaries, set())
        self.tree_controller.itemClicked.connect(self._navigate_to)
        self.tree_controller.selectionChanged.connect(self._on_tree_selection_changed)
        self.tree_controller.set_all_check(True)

        self.strategy_combo.currentTextChanged.connect(self._on_strategy_changed)
        self._last_strategy_text = self.strategy_combo.currentText()
        self._init_periods_fingerprints()

        self._preload_timer = QTimer(self)
        self._preload_timer.timeout.connect(self._preload_step)
        self._preload_timer.setInterval(800)

        if self.all_summaries:
            self._navigate_to(0)

    # ==================== 信号连接 ====================
    def _connect_render_signals(self):
        try:
            self.render_scheduler.renderReady.disconnect()
            self.render_scheduler.renderError.disconnect()
            self.render_scheduler.cacheProgress.disconnect()
        except:
            pass
        self.render_scheduler.renderReady.connect(self._on_render_ready)
        self.render_scheduler.renderError.connect(self._on_render_error)
        self.render_scheduler.cacheProgress.connect(self.cache_progress_label.setText)

    # ==================== 显示事件（修复重用） ====================
    def showEvent(self, event):
        super().showEvent(event)
        if self._reusable and self._is_closing:
            self._is_closing = False
            if self.render_scheduler is not None:
                self._connect_render_signals()
            if self._preload_timer is not None and not self._preload_timer.isActive():
                self._restart_preload_timer()
        if not self._first_render_done and self.all_summaries:
            self._navigate_to(0)
            self._first_render_done = True
        if self._preload_timer is not None and not self._preload_timer.isActive():
            self._restart_preload_timer()

    def _restart_preload_timer(self):
        if self._preload_timer is not None:
            self._preload_timer.stop()
            self._preload_timer.start()

    def _preload_step(self):
        remaining = [i for i in range(len(self.all_summaries))
                     if i not in self.render_scheduler._pixmap_cache
                     and i not in self.render_scheduler._rendering_set
                     and i not in self.tree_controller.no_data_indices]
        if not remaining:
            self._preload_timer.stop()
            return
        cur = self.current_index
        ordered = []
        for offset in range(1, len(self.all_summaries) + 1):
            cand = (cur + offset) % len(self.all_summaries)
            if cand in remaining:
                ordered.append(cand)
        if ordered:
            self._schedule_render(ordered[0])

    # ==================== UI 构建 ====================
    def _init_ui(self, initial_multiplier, initial_output_dir):
        main_layout = QVBoxLayout(self)
        self.splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(self.splitter, 1)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        search_sort_widget = QWidget()
        ss_layout = QHBoxLayout(search_sort_widget)
        ss_layout.addWidget(QLabel("搜索:"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("输入表号或月份关键字...")
        self.search_edit.textChanged.connect(self._filter_tree)
        ss_layout.addWidget(self.search_edit)
        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["按表号", "按月份"])
        self.sort_combo.currentTextChanged.connect(self._on_sort_changed)
        ss_layout.addWidget(QLabel("排序:"))
        ss_layout.addWidget(self.sort_combo)
        left_layout.addWidget(search_sort_widget)

        self.group_tree = QTreeWidget()
        self.group_tree.setHeaderLabels(['分组', '状态'])
        self.group_tree.header().setStretchLastSection(False)
        self.group_tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.group_tree.header().resizeSection(1, 120)
        self.group_tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        left_layout.addWidget(self.group_tree, 3)

        self.tree_controller = _GroupTreeController(self.group_tree)

        sel_btns = QHBoxLayout()
        sel_btns.addWidget(QPushButton("全选", clicked=lambda: self.tree_controller.set_all_check(True)))
        sel_btns.addWidget(QPushButton("全不选", clicked=lambda: self.tree_controller.set_all_check(False)))
        self.undo_check_btn = QPushButton("↩ 恢复勾选")
        self.undo_check_btn.clicked.connect(self._undo_check_selection)
        self.undo_check_btn.setToolTip("恢复到最近一次全选/全不选前的状态")
        sel_btns.addWidget(self.undo_check_btn)
        self.asset_select_btn = QPushButton("按表号多选...")
        self.asset_select_btn.clicked.connect(self._select_by_asset)
        sel_btns.addWidget(self.asset_select_btn)
        self.summary_btn = QPushButton("扫描摘要")
        self.summary_btn.clicked.connect(self._show_scan_summary)
        sel_btns.addWidget(self.summary_btn)
        left_layout.addLayout(sel_btns)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        param_widget = QWidget()
        self.param_layout = QVBoxLayout(param_widget)
        self.param_layout.setContentsMargins(4, 4, 4, 4)

        nav_container = QWidget()
        nav_layout = QHBoxLayout(nav_container)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        self.prev_btn = QPushButton("◀")
        self.next_btn = QPushButton("▶")
        self.index_label = QLabel("0 / 0")
        self.index_label.setAlignment(Qt.AlignCenter)
        self.index_label.setFixedWidth(80)
        nav_layout.addStretch()
        nav_layout.addWidget(self.prev_btn)
        nav_layout.addWidget(self.index_label)
        nav_layout.addWidget(self.next_btn)
        nav_layout.addStretch()
        self.param_layout.addWidget(nav_container)

        self.info_label = QLabel("")
        self.info_label.setWordWrap(True)
        self.param_layout.addWidget(self.info_label)

        self.cache_progress_label = QLabel("已缓存: 0 / 0")
        self.cache_progress_label.setStyleSheet("color: #4CAF50; font-weight: bold;")
        self.param_layout.addWidget(self.cache_progress_label)

        export_mid_btn = QPushButton("导出中间宽表 (检查数据透视)")
        export_mid_btn.clicked.connect(self._export_midwide)
        self.param_layout.addWidget(export_mid_btn)

        template_frame = QHBoxLayout()
        template_frame.addWidget(QLabel("参数模板:"))
        self.template_combo = QComboBox()
        self.template_combo.currentTextChanged.connect(self._load_template)
        template_frame.addWidget(self.template_combo, 1)
        save_tpl_btn = QPushButton("保存为模板")
        save_tpl_btn.clicked.connect(self._save_as_template)
        template_frame.addWidget(save_tpl_btn)
        export_tpl_btn = QPushButton("导出模板...")
        export_tpl_btn.clicked.connect(self._export_template)
        template_frame.addWidget(export_tpl_btn)
        import_tpl_btn = QPushButton("导入模板...")
        import_tpl_btn.clicked.connect(self._import_template)
        template_frame.addWidget(import_tpl_btn)
        self.param_layout.addLayout(template_frame)
        self._refresh_template_list()

        # -------------------- 倍率设置 --------------------
        mult_group = QGroupBox("倍率设置")
        mult_outer = QVBoxLayout(mult_group)
        mult_row1 = QHBoxLayout()
        mult_row1.addWidget(QLabel("倍率:"))
        self.multiplier_spin = QDoubleSpinBox()
        self.multiplier_spin.setRange(0.01, 10000000.0)
        self.multiplier_spin.setDecimals(4)
        self.multiplier_spin.setValue(initial_multiplier)
        self.multiplier_spin.setToolTip("全局倍率，或当前分组的专属倍率")
        mult_row1.addWidget(self.multiplier_spin)
        mult_outer.addLayout(mult_row1)

        mult_btn_layout = QHBoxLayout()
        self.mult_apply_current_btn = QPushButton("应用至当前")
        self.mult_apply_current_btn.clicked.connect(self._apply_mult_to_current)
        mult_btn_layout.addWidget(self.mult_apply_current_btn)
        self.mult_apply_selected_btn = QPushButton("应用至勾选")
        self.mult_apply_selected_btn.clicked.connect(self._apply_mult_to_selected)
        mult_btn_layout.addWidget(self.mult_apply_selected_btn)
        self.mult_apply_all_btn = QPushButton("应用至全部")
        self.mult_apply_all_btn.clicked.connect(self._apply_mult_to_all)
        mult_btn_layout.addWidget(self.mult_apply_all_btn)
        self.mult_undo_btn = QPushButton("撤销")
        self.mult_undo_btn.clicked.connect(self._undo_mult)
        mult_btn_layout.addWidget(self.mult_undo_btn)
        mult_outer.addLayout(mult_btn_layout)

        self.mult_status_label = QLabel("")
        self.mult_status_label.setStyleSheet("color: #FF9800;")
        mult_outer.addWidget(self.mult_status_label)
        self.param_layout.addWidget(mult_group)

        # -------------------- 计费容量线 --------------------
        bill_group = QGroupBox("计费容量线")
        bill_outer = QVBoxLayout(bill_group)
        self.bill_enabled_cb = QCheckBox("启用容量线（仅控制编辑权限）")
        self.bill_enabled_cb.setChecked(False)
        self.bill_enabled_cb.stateChanged.connect(self._on_bill_enabled_changed)
        bill_outer.addWidget(self.bill_enabled_cb)

        bill_val_layout = QHBoxLayout()
        bill_val_layout.addWidget(QLabel("容量值:"))
        self.capacity_spin = QDoubleSpinBox()
        self.capacity_spin.setRange(0.0, 10000000.0)
        self.capacity_spin.setDecimals(2)
        self.capacity_spin.setValue(0.0)
        self.capacity_spin.setReadOnly(True)
        self.capacity_spin.setEnabled(True)
        self.capacity_spin.setToolTip("勾选后可编辑，取消勾选依然可以应用（清除容量线）")
        bill_val_layout.addWidget(self.capacity_spin)
        bill_outer.addLayout(bill_val_layout)

        bill_btn_layout = QHBoxLayout()
        self.bill_apply_current_btn = QPushButton("应用至当前")
        self.bill_apply_current_btn.clicked.connect(self._apply_bill_to_current)
        bill_btn_layout.addWidget(self.bill_apply_current_btn)
        self.bill_apply_selected_btn = QPushButton("应用至勾选")
        self.bill_apply_selected_btn.clicked.connect(self._apply_bill_to_selected)
        bill_btn_layout.addWidget(self.bill_apply_selected_btn)
        self.bill_apply_all_btn = QPushButton("应用至全部")
        self.bill_apply_all_btn.clicked.connect(self._apply_bill_to_all)
        bill_btn_layout.addWidget(self.bill_apply_all_btn)
        self.bill_undo_btn = QPushButton("撤销")
        self.bill_undo_btn.clicked.connect(self._undo_bill)
        bill_btn_layout.addWidget(self.bill_undo_btn)
        bill_outer.addLayout(bill_btn_layout)

        self.bill_status_label = QLabel("")
        self.bill_status_label.setStyleSheet("color: #FF9800;")
        bill_outer.addWidget(self.bill_status_label)
        self.param_layout.addWidget(bill_group)

        # -------------------- 曲线显示选项 --------------------
        disp_group = QGroupBox("曲线显示")
        disp_layout = QVBoxLayout(disp_group)
        self.show_daily_cb = QCheckBox("每日曲线")
        self.show_daily_cb.setChecked(self.config_manager.app_set('show_daily', True))
        self.show_avg_cb = QCheckBox("平均曲线")
        self.show_avg_cb.setChecked(self.config_manager.app_set('show_avg', True))
        self.show_max_demand_cb = QCheckBox(f"显示{CurveLabel.MAX_DEMAND}")
        self.show_max_demand_cb.setChecked(self._show_max_demand)
        self.mark_anomalies_cb = QCheckBox("标记异常值（零/负）")
        self.mark_anomalies_cb.setChecked(self._mark_anomalies)
        disp_layout.addWidget(self.show_daily_cb)
        disp_layout.addWidget(self.show_avg_cb)
        disp_layout.addWidget(self.show_max_demand_cb)
        disp_layout.addWidget(self.mark_anomalies_cb)

        # ---------- 周末/节假日高亮 ----------
        highlight_layout = QHBoxLayout()
        highlight_layout.addWidget(QLabel("日期突出:"))
        self.highlight_combo = QComboBox()
        self.highlight_combo.addItems(["不突出", "突出周末", "突出周末+节假日", "统一分组(三色)"])
        mode_index = {'none': 0, 'weekend': 1, 'holiday': 2, 'grouped': 3}.get(self._highlight_mode, 0)
        self.highlight_combo.setCurrentIndex(mode_index)
        self._holiday_warning_label = QLabel("")
        self._setup_holiday_warning()
        highlight_layout.addWidget(self.highlight_combo)
        disp_layout.addLayout(highlight_layout)
        disp_layout.addWidget(self._holiday_warning_label)

        # 颜色自定义行
        color_layout = QHBoxLayout()
        color_layout.addWidget(QLabel("工作日:"))
        self.weekday_color_btn = QPushButton()
        self.weekday_color_btn.setFixedSize(24, 24)
        self.weekday_color_btn.setStyleSheet(f"background-color: {self._weekday_color}; border: 1px solid gray;")
        self.weekday_color_btn.clicked.connect(self._pick_weekday_color)
        color_layout.addWidget(self.weekday_color_btn)
        color_layout.addStretch()

        color_layout.addWidget(QLabel("周末:"))
        self.weekend_color_btn = QPushButton()
        self.weekend_color_btn.setFixedSize(24, 24)
        self.weekend_color_btn.setStyleSheet(f"background-color: {self._weekend_color}; border: 1px solid gray;")
        self.weekend_color_btn.clicked.connect(self._pick_weekend_color)
        color_layout.addWidget(self.weekend_color_btn)
        color_layout.addStretch()

        color_layout.addWidget(QLabel("节假日:"))
        self.holiday_color_btn = QPushButton()
        self.holiday_color_btn.setFixedSize(24, 24)
        self.holiday_color_btn.setStyleSheet(f"background-color: {self._holiday_color}; border: 1px solid gray;")
        self.holiday_color_btn.clicked.connect(self._pick_holiday_color)
        color_layout.addWidget(self.holiday_color_btn)
        disp_layout.addLayout(color_layout)

        # 线宽行
        lw_layout = QHBoxLayout()
        lw_layout.addWidget(QLabel("周末线宽:"))
        self.weekend_lw_spin = QDoubleSpinBox()
        self.weekend_lw_spin.setRange(0.5, 5.0)
        self.weekend_lw_spin.setSingleStep(0.1)
        self.weekend_lw_spin.setValue(self._weekend_line_width)
        lw_layout.addWidget(self.weekend_lw_spin)
        lw_layout.addStretch()
        lw_layout.addWidget(QLabel("节假日线宽:"))
        self.holiday_lw_spin = QDoubleSpinBox()
        self.holiday_lw_spin.setRange(0.5, 5.0)
        self.holiday_lw_spin.setSingleStep(0.1)
        self.holiday_lw_spin.setValue(self._holiday_line_width)
        lw_layout.addWidget(self.holiday_lw_spin)
        disp_layout.addLayout(lw_layout)

        # 周末背景阴影 & 图例位置
        extra_layout = QHBoxLayout()
        self.show_weekend_bg_cb = QCheckBox("周末浅灰背景")
        self.show_weekend_bg_cb.setChecked(self._show_weekend_bg)
        extra_layout.addWidget(self.show_weekend_bg_cb)
        extra_layout.addStretch()
        extra_layout.addWidget(QLabel("图例位置:"))
        self.legend_pos_combo = QComboBox()
        self.legend_pos_combo.addItems(["右", "底"])
        self.legend_pos_combo.setCurrentIndex(0 if self._legend_position == 'right' else 1)
        extra_layout.addWidget(self.legend_pos_combo)
        disp_layout.addLayout(extra_layout)

        self.param_layout.addWidget(disp_group)

        # -------------------- 充放策略 --------------------
        strat_layout = QHBoxLayout()
        strat_layout.addWidget(QLabel("充放策略:"))
        self.strategy_combo = QComboBox()
        self.strategy_combo.addItem("无")
        self.strategy_combo.addItems(self.config_manager.strat_names())
        self.strategy_combo.setCurrentText("无")
        self._last_strategy_text = self.strategy_combo.currentText()
        strat_layout.addWidget(self.strategy_combo)
        edit_btn = QPushButton("管理策略")
        edit_btn.clicked.connect(self._manage_strategies)
        strat_layout.addWidget(edit_btn)
        self.param_layout.addLayout(strat_layout)

        # -------------------- 输出目录 --------------------
        out_dir_layout = QHBoxLayout()
        out_dir_layout.addWidget(QLabel("输出目录:"))
        self.output_dir_edit = QLineEdit(initial_output_dir)
        self.output_dir_edit.setReadOnly(True)
        out_dir_layout.addWidget(self.output_dir_edit, 1)
        browse_btn = QPushButton("浏览...")
        browse_btn.clicked.connect(self._browse_output_dir)
        out_dir_layout.addWidget(browse_btn)
        new_folder_btn = QPushButton("新建文件夹")
        new_folder_btn.clicked.connect(self._create_new_folder)
        out_dir_layout.addWidget(new_folder_btn)
        open_dir_btn = QPushButton("打开")
        open_dir_btn.clicked.connect(self._open_current_output_dir)
        out_dir_layout.addWidget(open_dir_btn)
        self.param_layout.addLayout(out_dir_layout)

        self.classify_cb = QCheckBox("按表号创建子文件夹")
        self.classify_cb.setChecked(self.config_manager.app_set('classify_by_asset', False))
        self.param_layout.addWidget(self.classify_cb)

        # -------------------- 标题模板 --------------------
        title_layout = QVBoxLayout()
        title_layout.addWidget(QLabel("<b>图表标题模板</b><br><small>变量：{表号} {年月} {资产} {工作表}</small>"))
        self.title_edit = QLineEdit(self.config_manager.app_set('last_title_template', '{table_no}_{year_month}_负荷曲线'))
        title_layout.addWidget(self.title_edit)
        title_quick_btns = QHBoxLayout()
        title_quick_btns.addWidget(QPushButton("表号-年月",
                                               clicked=lambda: self.title_edit.setText("{table_no}_{year_month}_负荷曲线")))
        title_quick_btns.addWidget(QPushButton("年月",
                                               clicked=lambda: self.title_edit.setText("{year_month}_负荷曲线")))
        title_layout.addLayout(title_quick_btns)
        self.title_preview = QLabel("")
        self.title_preview.setStyleSheet("color: gray;")
        title_layout.addWidget(self.title_preview)
        self.param_layout.addLayout(title_layout)

        # -------------------- 文件名模板 --------------------
        fname_layout = QVBoxLayout()
        fname_layout.addWidget(QLabel("<b>输出文件名模板</b><br><small>变量：{表号} {年月} {资产} {工作表}</small>"))
        self.filename_edit = QLineEdit(self.config_manager.app_set('last_filename_template', '{table_no}_{year_month}_负荷曲线'))
        fname_layout.addWidget(self.filename_edit)
        fname_quick_btns = QHBoxLayout()
        fname_quick_btns.addWidget(QPushButton("表号-年月",
                                               clicked=lambda: self.filename_edit.setText("{table_no}_{year_month}_负荷曲线")))
        fname_quick_btns.addWidget(QPushButton("年月",
                                               clicked=lambda: self.filename_edit.setText("{year_month}_负荷曲线")))
        fname_layout.addLayout(fname_quick_btns)
        self.fname_warning = QLabel("")
        self.fname_warning.setStyleSheet("color: #FF9800; font-weight: bold;")
        fname_layout.addWidget(self.fname_warning)
        self.param_layout.addLayout(fname_layout)

        scroll.setWidget(param_widget)
        left_layout.addWidget(scroll, 2)

        # -------------------- 右侧预览区 --------------------
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self.scene = QGraphicsScene()
        self.view = QGraphicsView(self.scene)
        self.view.setDragMode(QGraphicsView.ScrollHandDrag)
        self.view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        zoom_layout = QHBoxLayout()
        self.fit_btn = QPushButton("适应窗口")
        self.fit_btn.clicked.connect(lambda: self.view.fitInView(self.scene.itemsBoundingRect(), Qt.KeepAspectRatio))
        zoom_in_btn = QPushButton("+")
        zoom_in_btn.clicked.connect(lambda: self._zoom(1.2))
        zoom_out_btn = QPushButton("-")
        zoom_out_btn.clicked.connect(lambda: self._zoom(0.8))
        zoom_layout.addWidget(self.fit_btn)
        zoom_layout.addWidget(zoom_in_btn)
        zoom_layout.addWidget(zoom_out_btn)
        right_layout.addLayout(zoom_layout)
        right_layout.addWidget(self.view, 1)

        date_panel_group = QGroupBox("日期状态（当前分组）")
        date_panel_layout = QVBoxLayout(date_panel_group)
        self.date_panel_edit = QTextEdit()
        self.date_panel_edit.setReadOnly(True)
        self.date_panel_edit.setMaximumHeight(150)
        self.date_panel_edit.setStyleSheet("font-family: 'Consolas', 'Microsoft YaHei', monospace; font-size: 11px;")
        date_panel_layout.addWidget(self.date_panel_edit)
        right_layout.addWidget(date_panel_group)

        self.splitter.addWidget(left_panel)
        self.splitter.addWidget(right_widget)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 2)

        if self.config_manager:
            state = self.config_manager.get_splitter_state("preview_splitter")
            if state:
                self.splitter.restoreState(state)

        bottom_btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bottom_btns.accepted.connect(self._accept)
        bottom_btns.rejected.connect(self.reject)
        main_layout.addWidget(bottom_btns)

        self.prev_btn.clicked.connect(self._prev_group)
        self.next_btn.clicked.connect(self._next_group)
        self.show_daily_cb.stateChanged.connect(lambda: self._apply_global_display_options())
        self.show_avg_cb.stateChanged.connect(lambda: self._apply_global_display_options())
        self.show_max_demand_cb.stateChanged.connect(lambda: self._apply_global_display_options())
        self.mark_anomalies_cb.stateChanged.connect(lambda: self._apply_global_display_options())
        self.highlight_combo.currentIndexChanged.connect(self._on_highlight_mode_changed)
        self.weekend_lw_spin.valueChanged.connect(self._apply_global_display_options)
        self.holiday_lw_spin.valueChanged.connect(self._apply_global_display_options)
        self.show_weekend_bg_cb.stateChanged.connect(self._apply_global_display_options)
        self.legend_pos_combo.currentIndexChanged.connect(self._apply_global_display_options)
        self.title_edit.textChanged.connect(self._update_title_preview)
        self.filename_edit.textChanged.connect(self._check_filename)
        self._update_multiplier_ui()
        self._update_billing_ui()

    # ==================== 日期状态面板 ====================
    def _update_date_panel(self, idx):
        if idx < 0 or idx >= len(self.all_summaries):
            self.date_panel_edit.clear()
            return
        summary = self.all_summaries[idx]
        data = self.provider.get_group_data(summary)
        if not data or 'date_labels' not in data:
            self.date_panel_edit.setHtml("<i>无法加载日期数据</i>")
            return
        dates = data['date_labels']
        highlight_mode = self.render_scheduler.plot_options.get('highlight_mode', 'none')
        enable_holiday = highlight_mode in ('holiday', 'grouped')
        lines = []

        if highlight_mode == 'none':
            lines.append('<span style="color:gray; font-style:italic;">当前未启用日期突出，曲线颜色无特殊含义</span><br>')
            for d in dates:
                parsed = _parse_date_flexible(d)
                weekday_str = ''
                if parsed:
                    weekdays = ['一', '二', '三', '四', '五', '六', '日']
                    weekday_str = f" 周{weekdays[parsed.weekday()]}"
                lines.append(f'<span style="color:black;">{d}{weekday_str}</span>')
        else:
            for d in dates:
                day_type = _get_day_type(d, enable_holiday=enable_holiday)
                if day_type == 'holiday':
                    color = self._holiday_color
                    tag = '节假日'
                elif day_type == 'weekend':
                    color = self._weekend_color
                    tag = '周末'
                else:
                    color = self._weekday_color
                    tag = '工作日'
                parsed = _parse_date_flexible(d)
                weekday_str = ''
                if parsed:
                    weekdays = ['一', '二', '三', '四', '五', '六', '日']
                    weekday_str = f" 周{weekdays[parsed.weekday()]}"
                lines.append(f'<span style="color:{color}; font-weight:bold;">{d}{weekday_str}</span> ({tag})')

        html = '<br>'.join(lines)
        self.date_panel_edit.setHtml(html)

    # ==================== 节假日异步安装 ====================
    def _setup_holiday_warning(self):
        if not _HOLIDAY_AVAILABLE:
            self.highlight_combo.model().item(2).setEnabled(False)
            self.highlight_combo.model().item(3).setEnabled(False)
            self._holiday_warning_label.setText(
                "⚠️ 节假日识别需要安装 chinesecalendar 库，<a href='install'>点击安装</a>")
            self._holiday_warning_label.setStyleSheet("color: orange;")
            self._holiday_warning_label.linkActivated.connect(self._install_chinesecalendar)
        else:
            self._holiday_warning_label.hide()

    def _install_chinesecalendar(self):
        if self._install_process is not None:
            QMessageBox.information(self, "安装中", "安装已在进行中，请稍候。")
            return
        if QMessageBox.question(self, "安装依赖", "是否现在安装 chinesecalendar？程序需要网络连接，安装过程约需几秒。") == QMessageBox.Yes:
            self._install_process = QProcess(self)
            self._install_process.setProcessChannelMode(QProcess.MergedChannels)
            self._install_process.finished.connect(self._on_install_finished)
            self._install_process.setProperty("start_time", time.time())
            self._install_timeout_timer = QTimer(self)
            self._install_timeout_timer.setSingleShot(True)
            self._install_timeout_timer.timeout.connect(self._on_install_timeout)
            self._install_timeout_timer.start(120000)
            cmd = sys.executable
            args = ["-m", "pip", "install", "chinesecalendar"]
            self._install_process.start(cmd, args)
            self._holiday_warning_label.setText("正在安装 chinesecalendar ...")
            self._holiday_warning_label.setEnabled(False)

    def _on_install_timeout(self):
        if self._install_process is not None:
            self._install_process.kill()
            self._install_process = None
            self._holiday_warning_label.setText("安装超时，请手动安装或检查网络。")
            self._holiday_warning_label.setEnabled(True)
            QMessageBox.warning(self, "安装超时", "chinesecalendar 安装超时，请检查网络或稍后重试。")

    def _on_install_finished(self, exit_code, exit_status):
        if hasattr(self, '_install_timeout_timer'):
            self._install_timeout_timer.stop()
        self._holiday_warning_label.setEnabled(True)
        if exit_status == QProcess.NormalExit and exit_code == 0:
            global _HOLIDAY_AVAILABLE
            try:
                from chinese_calendar import is_holiday, is_workday
                _HOLIDAY_AVAILABLE = True
                self.highlight_combo.model().item(2).setEnabled(True)
                self.highlight_combo.model().item(3).setEnabled(True)
                self._holiday_warning_label.hide()
                QMessageBox.information(self, "安装成功", "chinesecalendar 已安装成功，现在可以使用节假日高亮。")
                _get_day_type.cache_clear()
            except ImportError:
                QMessageBox.warning(self, "安装失败", "安装成功但无法导入，请手动重新启动程序。")
        else:
            if self._install_retry_count == 0 and exit_status == QProcess.NormalExit:
                self._install_retry_count += 1
                self._holiday_warning_label.setText("普通安装失败，尝试用户级安装 ...")
                args = ["-m", "pip", "install", "--user", "chinesecalendar"]
                self._install_process = QProcess(self)
                self._install_process.setProcessChannelMode(QProcess.MergedChannels)
                self._install_process.finished.connect(self._on_install_finished)
                self._install_process.start(sys.executable, args)
            else:
                self._install_process = None
                QMessageBox.critical(self, "安装失败",
                    f"安装失败，请手动在命令行执行：pip install chinesecalendar。")
        self._install_process = None

    # ==================== 颜色选择 ====================
    def _pick_weekday_color(self):
        col = QColorDialog.getColor(QColor(self._weekday_color), self, "选择工作日颜色")
        if col.isValid():
            self._weekday_color = col.name()
            self.weekday_color_btn.setStyleSheet(f"background-color: {self._weekday_color}; border: 1px solid gray;")
            self._apply_global_display_options()

    def _pick_weekend_color(self):
        col = QColorDialog.getColor(QColor(self._weekend_color), self, "选择周末颜色")
        if col.isValid():
            self._weekend_color = col.name()
            self.weekend_color_btn.setStyleSheet(f"background-color: {self._weekend_color}; border: 1px solid gray;")
            self._apply_global_display_options()

    def _pick_holiday_color(self):
        col = QColorDialog.getColor(QColor(self._holiday_color), self, "选择节假日颜色")
        if col.isValid():
            self._holiday_color = col.name()
            self.holiday_color_btn.setStyleSheet(f"background-color: {self._holiday_color}; border: 1px solid gray;")
            self._apply_global_display_options()

    # ==================== 倍率操作 ====================
    def _apply_mult_to_current(self):
        idx = self.current_index
        val = self.multiplier_spin.value()
        self.settings_model.set_custom_multiplier(idx, val)
        self._update_multiplier_ui()
        self._restart_preload_timer()
        QMessageBox.information(self, "已应用", f"已将倍率 {val} 应用到当前分组。")

    def _apply_mult_to_selected(self):
        checked = self.tree_controller.get_checked_indices()
        if not checked:
            QMessageBox.warning(self, "提示", "没有选中任何分组")
            return
        val = self.multiplier_spin.value()
        if QMessageBox.question(self, "批量设置倍率", f"将倍率 {val} 应用到选中的 {len(checked)} 个分组？") == QMessageBox.Yes:
            self.settings_model.settingsChanged.disconnect(self._on_settings_changed)
            try:
                for idx in checked:
                    self.settings_model.set_custom_multiplier(idx, val)
            finally:
                self.settings_model.settingsChanged.connect(self._on_settings_changed)
            for idx in checked:
                self.render_scheduler.clear_cache_for_index(idx)
                self.tree_controller.update_status(idx, "加载中...")
                self.render_scheduler.schedule_render(idx)
            if self.current_index in checked:
                self._schedule_render(self.current_index)
            else:
                self._update_multiplier_ui()
            self._restart_preload_timer()
            QMessageBox.information(self, "已应用", f"已将倍率 {val} 应用到选中的 {len(checked)} 个分组。")

    def _apply_mult_to_all(self):
        val = self.multiplier_spin.value()
        if QMessageBox.question(self, "全局应用", f"将倍率 {val} 应用到所有分组（将清除所有自定义倍率）？") == QMessageBox.Yes:
            self._global_multiplier = val
            self.settings_model.settingsChanged.disconnect(self._on_settings_changed)
            try:
                self.settings_model.set_global_multiplier(val)
                for idx in range(len(self.all_summaries)):
                    self.settings_model.clear_custom_multiplier(idx)
            finally:
                self.settings_model.settingsChanged.connect(self._on_settings_changed)
            self._update_multiplier_ui()
            self.render_scheduler.clear_all_caches()
            for i in range(len(self.all_summaries)):
                self.tree_controller.update_status(i, "等待加载...")
            self._schedule_render(self.current_index)
            self._restart_preload_timer()
            QMessageBox.information(self, "已应用", f"已将全局倍率设置为 {val}。")

    def _undo_mult(self):
        idx = self.current_index
        if self.settings_model.undo_last_mult(idx):
            self._update_multiplier_ui()
            self._restart_preload_timer()
            QMessageBox.information(self, "已撤销", "已撤销最后一次倍率更改。")

    # ==================== 容量线操作 ====================
    def _on_bill_enabled_changed(self, state):
        self._update_billing_ui()

    def _apply_bill_to_current(self):
        idx = self.current_index
        if self.bill_enabled_cb.isChecked():
            val = self.capacity_spin.value()
            self.settings_model.set_custom_billing(idx, {'type': 'capacity', 'value': val})
            action_text = f"将容量线 {val} 应用到当前分组"
        else:
            self.settings_model.set_custom_billing(idx, None)
            action_text = "已清除当前分组的容量线"
        self._update_billing_ui()
        self._restart_preload_timer()
        QMessageBox.information(self, "已应用", action_text)

    def _apply_bill_to_selected(self):
        checked = self.tree_controller.get_checked_indices()
        if not checked:
            QMessageBox.warning(self, "提示", "没有选中任何分组")
            return
        action_text = "设置容量" if self.bill_enabled_cb.isChecked() else "清除容量"
        if QMessageBox.question(self, f"批量{action_text}",
                                f"将{action_text}应用到选中的 {len(checked)} 个分组？") == QMessageBox.Yes:
            self.settings_model.settingsChanged.disconnect(self._on_settings_changed)
            try:
                for idx in checked:
                    if self.bill_enabled_cb.isChecked():
                        val = self.capacity_spin.value()
                        self.settings_model.set_custom_billing(idx, {'type': 'capacity', 'value': val})
                    else:
                        self.settings_model.set_custom_billing(idx, None)
            finally:
                self.settings_model.settingsChanged.connect(self._on_settings_changed)
            for idx in checked:
                self.render_scheduler.clear_cache_for_index(idx)
                self.tree_controller.update_status(idx, "加载中...")
                self.render_scheduler.schedule_render(idx)
            if self.current_index in checked:
                self._schedule_render(self.current_index)
            else:
                self._update_billing_ui()
            self._restart_preload_timer()
            QMessageBox.information(self, "已应用",
                                    f"已将{action_text}应用到选中的 {len(checked)} 个分组。")

    def _apply_bill_to_all(self):
        action_text = "设置容量" if self.bill_enabled_cb.isChecked() else "清除容量"
        if QMessageBox.question(self, f"全局{action_text}",
                                f"将{action_text}应用到所有分组（将清除所有自定义容量）？") == QMessageBox.Yes:
            self.settings_model.settingsChanged.disconnect(self._on_settings_changed)
            try:
                if self.bill_enabled_cb.isChecked():
                    val = self.capacity_spin.value()
                    unique_assets = set()
                    for s in self.all_summaries:
                        unique_assets.add(s.asset_value)
                    for asset in unique_assets:
                        self.settings_model.set_global_billing(asset, {'type': 'capacity', 'value': val})
                else:
                    for s in self.all_summaries:
                        self.settings_model.set_global_billing(s.asset_value, None)
                for idx in range(len(self.all_summaries)):
                    self.settings_model.clear_custom_billing(idx)
            finally:
                self.settings_model.settingsChanged.connect(self._on_settings_changed)
            self._update_billing_ui()
            self.render_scheduler.clear_all_caches()
            for i in range(len(self.all_summaries)):
                self.tree_controller.update_status(i, "等待加载...")
            self._schedule_render(self.current_index)
            self._restart_preload_timer()
            QMessageBox.information(self, "已应用", f"已将{action_text}应用到所有分组。")

    def _undo_bill(self):
        idx = self.current_index
        if self.settings_model.undo_last_bill(idx):
            self._update_billing_ui()
            self._restart_preload_timer()
            QMessageBox.information(self, "已撤销", "已撤销最后一次容量更改。")

    # ==================== UI 更新辅助 ====================
    def _update_multiplier_ui(self):
        idx = self.current_index
        is_custom = self.settings_model.has_custom_mult(idx)
        effective = self.settings_model.effective_multiplier(idx)
        self.multiplier_spin.setValue(effective)
        self.mult_status_label.setText("当前分组使用专属倍率" if is_custom else "")
        self.multiplier_spin.setStyleSheet(f"background-color: {ColorConst.HIGHLIGHT_BG};" if is_custom else "")

    def _update_billing_ui(self):
        idx = self.current_index
        is_custom = self.settings_model.has_custom_bill(idx)
        bill = self.settings_model.effective_billing(idx)
        if bill and bill.get('value'):
            self.capacity_spin.setValue(bill['value'])
        else:
            self.capacity_spin.setValue(0.0)
        self.bill_status_label.setText("当前分组使用专属容量" if is_custom else "")
        enabled = self.bill_enabled_cb.isChecked()
        self.capacity_spin.setReadOnly(not enabled)
        if is_custom:
            self.capacity_spin.setStyleSheet(f"background-color: {ColorConst.HIGHLIGHT_BG};")
        elif not enabled:
            self.capacity_spin.setStyleSheet("background-color: #f0f0f0;")
        else:
            self.capacity_spin.setStyleSheet("")

    # ==================== 设置变更 ====================
    def _on_settings_changed(self):
        idx = self.current_index
        self._update_multiplier_ui()
        self._update_billing_ui()
        self._refresh_info_label()
        self.render_scheduler.clear_cache_for_index(idx)
        self.tree_controller.update_status(idx, "加载中...")
        self.render_scheduler.allow_retry(idx)
        self._schedule_render(idx)
        self._restart_preload_timer()

    def _on_tree_selection_changed(self, selected_set):
        pass

    def _filter_tree(self, text):
        self.tree_controller.filter_tree(text)

    def _on_sort_changed(self, key):
        self.tree_controller.set_sort_key(key)
        if self.all_summaries:
            self._navigate_to(0)

    def _select_by_asset(self):
        self.tree_controller.select_by_asset_dialog(self)

    def _undo_check_selection(self):
        if not self.tree_controller.undo_check_change():
            QMessageBox.information(self, "提示", "没有可恢复的勾选状态")

    def _show_scan_summary(self):
        summary = "所有分组统计信息：\n\n"
        for i, s in enumerate(self.all_summaries):
            status = self.tree_controller._status_map.get(i, "")
            fmt_label = ""
            if s.mapping and s.mapping.get('format_type') == 'accumulated':
                fmt_label = " (示数转功率)"
            elif s.mapping and s.mapping.get('format_type') in ('wide', 'wide_transposed'):
                fmt_label = " (宽表)"
            summary += f"【{s.asset_value or '未命名'}】{s.month_str} | 日期:{s.date_count} | 点数:{s.time_points} | 范围:{s.min_val:.2f}~{s.max_val:.2f} | {status}{fmt_label}\n"
        QMessageBox.information(self, "扫描摘要", summary)

    def _on_highlight_mode_changed(self):
        self._apply_global_display_options()

    def _apply_global_display_options(self):
        mode_map = {0: 'none', 1: 'weekend', 2: 'holiday', 3: 'grouped'}
        highlight_mode = mode_map.get(self.highlight_combo.currentIndex(), 'none')
        legend_pos = 'right' if self.legend_pos_combo.currentIndex() == 0 else 'bottom'

        opts = {
            'show_daily': self.show_daily_cb.isChecked(),
            'show_avg': self.show_avg_cb.isChecked(),
            'show_max_demand': self.show_max_demand_cb.isChecked(),
            'fit_types': [],
            'highlight_mode': highlight_mode,
            'weekday_color': self._weekday_color,
            'weekend_color': self._weekend_color,
            'holiday_color': self._holiday_color,
            'weekend_line_width': self.weekend_lw_spin.value(),
            'holiday_line_width': self.holiday_lw_spin.value(),
            'show_weekend_bg': self.show_weekend_bg_cb.isChecked(),
            'legend_position': legend_pos,
            'mark_anomalies': self.mark_anomalies_cb.isChecked(),
        }
        self.render_scheduler.set_plot_options(opts)
        self.render_scheduler.clear_all_caches()
        for i in range(len(self.all_summaries)):
            if i not in self.tree_controller.no_data_indices:
                self.tree_controller.update_status(i, "等待加载...")
        self._schedule_render(self.current_index)
        self._update_date_panel(self.current_index)
        self._restart_preload_timer()

    def _periods_for_idx(self, idx):
        sn = self.strategy_combo.currentText()
        if sn == "无" or not sn:
            return []
        strat = self.config_manager.get_strat(sn)
        if not strat:
            return []
        s = self.all_summaries[idx]
        m = s.month_str.split('-')[1] if '-' in s.month_str else s.month_str
        try:
            month_int = int(m)
        except:
            month_int = 1
        return strat.get(str(month_int), [])

    def _init_periods_fingerprints(self):
        self._periods_fp_map = {}
        for idx in range(len(self.all_summaries)):
            periods = self._periods_for_idx(idx)
            fp = hash(tuple((p['start'], p['end'], p['type']) for p in periods))
            self._periods_fp_map[idx] = fp

    def _on_strategy_changed(self):
        new_text = self.strategy_combo.currentText()
        if new_text == self._last_strategy_text:
            return
        self._last_strategy_text = new_text
        changed = []
        for idx in range(len(self.all_summaries)):
            new_periods = self._periods_for_idx(idx)
            new_fp = hash(tuple((p['start'], p['end'], p['type']) for p in new_periods))
            if new_fp != self._periods_fp_map.get(idx, 0):
                changed.append(idx)
            self._periods_fp_map[idx] = new_fp
        if changed:
            for idx in changed:
                self.render_scheduler.clear_cache_for_index(idx)
                self.tree_controller.update_status(idx, "加载中...")
                self._schedule_render(idx)
            if self.current_index in changed:
                self._schedule_render(self.current_index)
            self._restart_preload_timer()

    def _schedule_render(self, idx):
        if self._is_closing or idx < 0 or idx >= len(self.all_summaries):
            return
        if idx in self.tree_controller.no_data_indices:
            self.tree_controller.update_status(idx, "无数据")
            if idx == self.current_index:
                self.scene.clear()
                text_item = self.scene.addText("该分组无有效数据")
                text_item.setDefaultTextColor(QColor("gray"))
                self._update_date_panel(idx)
            return
        self.render_scheduler.allow_retry(idx)
        if idx in self.render_scheduler._pixmap_cache:
            pix = self.render_scheduler._pixmap_cache[idx]
            self.tree_controller.update_status(idx, "已就绪")
            if idx == self.current_index:
                self._display_pixmap(pix)
                self._update_date_panel(idx)
            return
        self.tree_controller.update_status(idx, "加载中...")
        self.render_scheduler.schedule_render(idx)

    def _on_render_ready(self, idx, pix):
        if self._is_closing:
            return
        self.tree_controller.update_status(idx, "已就绪")
        if idx == self.current_index:
            self._display_pixmap(pix)
            self._update_date_panel(idx)

    def _on_render_error(self, idx, msg):
        if self._is_closing:
            return
        self.tree_controller.update_status(idx, f"错误: {msg[:30]}...")
        if idx == self.current_index:
            self.scene.clear()
            text_item = self.scene.addText(msg)
            text_item.setDefaultTextColor(QColor("red"))
            self.view.fitInView(self.scene.itemsBoundingRect(), Qt.KeepAspectRatio)
            self._update_date_panel(idx)

    def _display_pixmap(self, pix):
        self.scene.clear()
        self.scene.addPixmap(pix)
        self.scene.setSceneRect(pix.rect())
        self.view.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)
        self._zoom_level = 1.0

    def _refresh_info_label(self):
        if self.current_index < len(self.all_summaries):
            s = self.all_summaries[self.current_index]
            actual_mult = self.settings_model.effective_multiplier(self.current_index)
            cur_min = s.min_val * actual_mult
            cur_max = s.max_val * actual_mult
            fmt_label = ""
            if s.mapping and s.mapping.get('format_type') == 'accumulated':
                fmt_label = "<br><b>数据格式:</b> 正向有功示数表（已自动差分）"
            elif s.mapping and s.mapping.get('format_type') in ('wide', 'wide_transposed'):
                fmt_label = "<br><b>数据格式:</b> 宽表"
            else:
                fmt_label = "<br><b>数据格式:</b> 长表"
            self.info_label.setText(
                f"<b>表号:</b> {s.asset_value or '未命名'}<br><b>月份:</b> {s.month_str}<br>"
                f"<b>日期数:</b> {s.date_count}<br><b>时间点数:</b> {s.time_points}<br>"
                f"<b>负荷范围:</b> {cur_min:.2f} ~ {cur_max:.2f}{fmt_label}"
            )
        else:
            self.info_label.setText("")

    def _navigate_to(self, idx):
        if idx < 0 or idx >= len(self.all_summaries):
            return
        self.current_index = idx
        self.index_label.setText(f"{idx + 1} / {len(self.all_summaries)}")
        self._update_multiplier_ui()
        self._update_billing_ui()
        self._refresh_info_label()
        self._update_title_preview()
        self._schedule_render(idx)
        self.tree_controller.set_current_index(idx)
        self._update_date_panel(idx)

    def _prev_group(self):
        idx = self.current_index - 1
        if idx >= 0:
            self._navigate_to(idx)

    def _next_group(self):
        idx = self.current_index + 1
        if idx < len(self.all_summaries):
            self._navigate_to(idx)

    def _refresh_template_list(self):
        self.template_combo.clear()
        self.template_combo.addItem("")
        if self.config_manager:
            self.template_combo.addItems(self.config_manager.get_template_names())

    def _load_template(self, name):
        if not name or not self.config_manager:
            return
        settings = self.config_manager.get_template(name)
        if not settings:
            return
        self.multiplier_spin.setValue(settings.get('multiplier', 1.0))
        self.show_daily_cb.setChecked(settings.get('show_daily', True))
        self.show_avg_cb.setChecked(settings.get('show_avg', True))
        self.show_max_demand_cb.setChecked(settings.get('show_max_demand', True))
        self.mark_anomalies_cb.setChecked(settings.get('mark_anomalies', False))
        self.bill_enabled_cb.setChecked(settings.get('billing_enabled', False))
        self.capacity_spin.setValue(settings.get('capacity_value', 0.0))
        self.title_edit.setText(settings.get('title_template', ''))
        self.filename_edit.setText(settings.get('filename_template', '{table_no}_{year_month}_负荷曲线'))
        self._global_multiplier = settings.get('multiplier', 1.0)

        hl = settings.get('highlight_mode', 'none')
        hl_index = {'none': 0, 'weekend': 1, 'holiday': 2, 'grouped': 3}.get(hl, 0)
        self.highlight_combo.setCurrentIndex(hl_index)
        self._weekday_color = settings.get('weekday_color', DEFAULT_WEEKDAY_COLOR)
        self._weekend_color = settings.get('weekend_color', DEFAULT_WEEKEND_COLOR)
        self._holiday_color = settings.get('holiday_color', DEFAULT_HOLIDAY_COLOR)
        self.weekday_color_btn.setStyleSheet(f"background-color: {self._weekday_color}; border: 1px solid gray;")
        self.weekend_color_btn.setStyleSheet(f"background-color: {self._weekend_color}; border: 1px solid gray;")
        self.holiday_color_btn.setStyleSheet(f"background-color: {self._holiday_color}; border: 1px solid gray;")
        self.weekend_lw_spin.setValue(settings.get('weekend_line_width', DEFAULT_WEEKEND_LINE_WIDTH))
        self.holiday_lw_spin.setValue(settings.get('holiday_line_width', DEFAULT_HOLIDAY_LINE_WIDTH))
        self.show_weekend_bg_cb.setChecked(settings.get('show_weekend_bg', False))
        self.legend_pos_combo.setCurrentIndex(0 if settings.get('legend_position', 'right') == 'right' else 1)

        old_model = self.settings_model
        if old_model:
            try:
                old_model.settingsChanged.disconnect(self._on_settings_changed)
            except:
                pass

        self.settings_model = _PerGroupSettings(self.all_summaries, self._global_multiplier, billing_dict=self._global_billing_dict)
        self.settings_model.settingsChanged.connect(self._on_settings_changed)
        self.render_scheduler.settings_model = self.settings_model

        self.render_scheduler.clear_all_caches()
        for i in range(len(self.all_summaries)):
            self.tree_controller.update_status(i, "等待加载...")
        self._schedule_render(self.current_index)
        self._restart_preload_timer()

    def _save_as_template(self):
        name, ok = QInputDialog.getText(self, "保存参数模板", "输入模板名称:")
        if not ok or not name.strip():
            return
        name = name.strip()
        mode_map = {0: 'none', 1: 'weekend', 2: 'holiday', 3: 'grouped'}
        settings = {
            'multiplier': self._global_multiplier,
            'show_daily': self.show_daily_cb.isChecked(),
            'show_avg': self.show_avg_cb.isChecked(),
            'show_max_demand': self.show_max_demand_cb.isChecked(),
            'mark_anomalies': self.mark_anomalies_cb.isChecked(),
            'billing_enabled': self.bill_enabled_cb.isChecked(),
            'capacity_value': self.capacity_spin.value(),
            'title_template': self.title_edit.text(),
            'filename_template': self.filename_edit.text(),
            'highlight_mode': mode_map.get(self.highlight_combo.currentIndex(), 'none'),
            'weekday_color': self._weekday_color,
            'weekend_color': self._weekend_color,
            'holiday_color': self._holiday_color,
            'weekend_line_width': self.weekend_lw_spin.value(),
            'holiday_line_width': self.holiday_lw_spin.value(),
            'show_weekend_bg': self.show_weekend_bg_cb.isChecked(),
            'legend_position': 'right' if self.legend_pos_combo.currentIndex() == 0 else 'bottom',
        }
        self.config_manager.save_template(name, settings)
        self._refresh_template_list()
        QMessageBox.information(self, "已保存", f"模板“{name}”保存成功。")

    def _export_template(self):
        if self.template_combo.currentText() == "":
            QMessageBox.warning(self, "提示", "请先从模板列表中选择一个模板再导出。")
            return
        name = self.template_combo.currentText()
        settings = self.config_manager.get_template(name)
        if not settings:
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出模板", f"{name}.json", "JSON 文件 (*.json)")
        if not path:
            return
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
            QMessageBox.information(self, "已导出", f"模板“{name}”已导出到:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"导出失败: {e}")

    def _import_template(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入模板", "", "JSON 文件 (*.json)")
        if not path:
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                settings = json.load(f)
            if not isinstance(settings, dict) or 'show_daily' not in settings:
                raise ValueError("无效的模板文件格式")
            name, ok = QInputDialog.getText(self, "导入模板", "输入模板名称:", text=os.path.basename(path).replace('.json',''))
            if ok and name.strip():
                self.config_manager.save_template(name.strip(), settings)
                self._refresh_template_list()
                self.template_combo.setCurrentText(name.strip())
                QMessageBox.information(self, "已导入", f"模板“{name}”导入成功。")
        except Exception as e:
            QMessageBox.critical(self, "导入失败", f"无法导入模板:\n{e}")

    def _update_title_preview(self):
        if self.current_index < len(self.all_summaries):
            s = self.all_summaries[self.current_index]
            ctx = {'year_month': s.month_str, 'table_no': s.asset_value or '未命名', 'asset': s.asset_value or '未命名', 'sheet': s.sheet_name}
            tpl = self.title_edit.text().strip()
            self.title_preview.setText(f"预览: {parse_title_template(tpl, ctx)}" if tpl else "预览: (空)")

    def _check_filename(self):
        if re.search(r'[<>:"/\\|?*]', self.filename_edit.text()):
            self.fname_warning.setText("⚠ 包含非法字符，将替换为下划线")
        else:
            self.fname_warning.setText("")

    def _browse_output_dir(self):
        dir_path = QFileDialog.getExistingDirectory(self, "选择输出目录", self.output_dir_edit.text())
        if dir_path:
            self.output_dir_edit.setText(dir_path)

    def _create_new_folder(self):
        base = self.output_dir_edit.text() or os.path.expanduser("~")
        folder, ok = QInputDialog.getText(self, "新建文件夹", "输入文件夹名称:", text="新输出目录")
        if ok and folder:
            new_dir = os.path.join(base, folder)
            try:
                os.makedirs(new_dir, exist_ok=True)
                self.output_dir_edit.setText(new_dir)
                QMessageBox.information(self, "已创建", f"文件夹已创建: {new_dir}")
            except Exception as e:
                QMessageBox.critical(self, "错误", f"无法创建文件夹: {e}")

    def _open_current_output_dir(self):
        out_dir = self.output_dir_edit.text()
        if os.path.isdir(out_dir):
            try:
                if os.name == 'nt':
                    os.startfile(out_dir)
                else:
                    subprocess.run(['xdg-open', out_dir])
            except:
                pass
        else:
            QMessageBox.information(self, "提示", "目录不存在，将在生成时创建")

    def _manage_strategies(self):
        old_sel = self.strategy_combo.currentText()
        dlg = ChargeDischargeManagerDialog(self, self.config_manager)
        if dlg.exec() == QDialog.Accepted:
            self.strategy_combo.blockSignals(True)
            self.strategy_combo.clear()
            self.strategy_combo.addItem("无")
            self.strategy_combo.addItems(self.config_manager.strat_names())

            if dlg.result:
                self.strategy_combo.setCurrentText(dlg.result)
                self._last_strategy_text = dlg.result
            elif old_sel and old_sel in self.config_manager.strat_names():
                self.strategy_combo.setCurrentText(old_sel)
                self._last_strategy_text = old_sel
            else:
                self.strategy_combo.setCurrentText("无")
                self._last_strategy_text = "无"
            self.strategy_combo.blockSignals(False)

            self._init_periods_fingerprints()
            self.render_scheduler.clear_all_caches()
            for i in range(len(self.all_summaries)):
                self.tree_controller.update_status(i, "加载中...")
            self._schedule_render(self.current_index)
            self._restart_preload_timer()

    def _zoom(self, factor):
        self.view.scale(factor, factor)
        self._zoom_level *= factor

    def _export_midwide(self):
        if self.current_index < 0 or self.current_index >= len(self.all_summaries):
            return
        idx = self.current_index
        summary = self.all_summaries[idx]
        data = self.provider.get_group_data(summary)
        if not data:
            QMessageBox.warning(self, "提示", "当前分组无有效数据")
            return
        try:
            out_dir = self.output_dir_edit.text()
            os.makedirs(out_dir, exist_ok=True)
            mstr = summary.month_str.replace('-', '')
            asset = summary.asset_value or '未命名'
            filepath = os.path.join(out_dir, f"中间宽表_{asset}_{mstr}.xlsx")
            df = pd.DataFrame(data['load_array'], index=data['date_labels'], columns=data['time_labels'])
            df.index.name = '日期'
            df.to_excel(filepath)
            if os.name == 'nt':
                os.startfile(filepath)
            else:
                subprocess.run(['xdg-open', filepath])
            QMessageBox.information(self, "已导出", f"中间宽表已保存至：\n{filepath}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def _accept(self):
        checked = self.tree_controller.get_checked_indices()
        if not checked:
            QMessageBox.warning(self, "提示", "请至少选择一个分组")
            return

        mode_map = {0: 'none', 1: 'weekend', 2: 'holiday', 3: 'grouped'}
        highlight_mode = mode_map.get(self.highlight_combo.currentIndex(), 'none')
        legend_pos = 'right' if self.legend_pos_combo.currentIndex() == 0 else 'bottom'

        self.config_manager.set_app('show_daily', self.show_daily_cb.isChecked())
        self.config_manager.set_app('show_avg', self.show_avg_cb.isChecked())
        self.config_manager.set_app('show_max_demand', self.show_max_demand_cb.isChecked())
        self.config_manager.set_app('mark_anomalies', self.mark_anomalies_cb.isChecked())
        self.config_manager.set_app('highlight_mode', highlight_mode)
        self.config_manager.set_app('weekday_color', self._weekday_color)
        self.config_manager.set_app('weekend_color', self._weekend_color)
        self.config_manager.set_app('holiday_color', self._holiday_color)
        self.config_manager.set_app('weekend_line_width', self.weekend_lw_spin.value())
        self.config_manager.set_app('holiday_line_width', self.holiday_lw_spin.value())
        self.config_manager.set_app('show_weekend_bg', self.show_weekend_bg_cb.isChecked())
        self.config_manager.set_app('legend_position', legend_pos)
        self.config_manager.set_app('last_strategy_name', self.strategy_combo.currentText() if self.strategy_combo.currentText() != "无" else None)
        self.config_manager.set_app('last_billing_enabled', self.bill_enabled_cb.isChecked())
        self.config_manager.set_app('last_billing_value', str(self.capacity_spin.value()))
        self.config_manager.set_app('last_title_template', self.title_edit.text())
        self.config_manager.set_app('last_filename_template', self.filename_edit.text())
        self.config_manager.set_app('classify_by_asset', self.classify_cb.isChecked())
        self.config_manager.set_app('last_multiplier', self._global_multiplier)

        per_group_multiplier = self.settings_model.get_custom_multipliers()
        per_group_billing = self.settings_model.get_custom_billings()

        billing_dict = {}
        for idx in checked:
            s = self.all_summaries[idx]
            bill = self.settings_model.effective_billing(idx)
            if bill:
                billing_dict[idx] = bill

        self.result = {
            'selected_indices': list(checked),
            'global_multiplier': self._global_multiplier,
            'per_group_multiplier': per_group_multiplier,
            'per_group_billing': per_group_billing,
            'output_dir': self.output_dir_edit.text(),
            'strategy_name': self.strategy_combo.currentText() if self.strategy_combo.currentText() != "无" else None,
            'show_daily': self.show_daily_cb.isChecked(),
            'show_avg': self.show_avg_cb.isChecked(),
            'show_max_demand': self.show_max_demand_cb.isChecked(),
            'highlight_mode': highlight_mode,
            'weekday_color': self._weekday_color,
            'weekend_color': self._weekend_color,
            'holiday_color': self._holiday_color,
            'weekend_line_width': self.weekend_lw_spin.value(),
            'holiday_line_width': self.holiday_lw_spin.value(),
            'show_weekend_bg': self.show_weekend_bg_cb.isChecked(),
            'legend_position': legend_pos,
            'mark_anomalies': self.mark_anomalies_cb.isChecked(),
            'title_template': self.title_edit.text().strip(),
            'filename_template': self.filename_edit.text().strip(),
            'billing_dict': billing_dict,
            'classify_by_asset': self.classify_cb.isChecked()
        }
        self.accept()

    def closeEvent(self, event):
        self._is_closing = True
        self._preload_timer.stop()
        if self.render_scheduler:
            self.render_scheduler.cancel_all()
            try:
                self.render_scheduler.renderReady.disconnect(self._on_render_ready)
                self.render_scheduler.renderError.disconnect(self._on_render_error)
                self.render_scheduler.cacheProgress.disconnect(self.cache_progress_label.setText)
            except:
                pass
        if self._install_process is not None:
            self._install_process.kill()
            self._install_process = None
        if self.config_manager:
            self.config_manager.set_window_geometry("PreviewDialog", self.saveGeometry())
            self.config_manager.set_splitter_state("preview_splitter", self.splitter.saveState())
        super().closeEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_1, Qt.Key_2, Qt.Key_3, Qt.Key_4) and event.modifiers() == Qt.ControlModifier:
            idx_map = {Qt.Key_1: 0, Qt.Key_2: 1, Qt.Key_3: 2, Qt.Key_4: 3}
            target_idx = idx_map[event.key()]
            model = self.highlight_combo.model()
            if target_idx < len(self.highlight_combo) and model.item(target_idx).isEnabled():
                self.highlight_combo.setCurrentIndex(target_idx)
                self._apply_global_display_options()
            event.accept()
            return
        if event.key() == Qt.Key_Left:
            self._prev_group()
        elif event.key() == Qt.Key_Right:
            self._next_group()
        else:
            super().keyPressEvent(event)

    def wheelEvent(self, event):
        if event.modifiers() == Qt.ControlModifier:
            delta = event.angleDelta().y()
            self._zoom(1.1 if delta > 0 else 0.9)
            event.accept()
        else:
            super().wheelEvent(event)

# 第三部分结束
# -*- coding: utf-8 -*-
"""
负荷曲线批量生成工具 Pro 融合版 (智能增强版 v2.1)
第四部分：模块级绘图、处理线程、导出确认、样式、主窗口

增强记录：
- 绘图线程增加异常标记支持和累计示数转换参数传递
- 主窗口新增暗色主题切换菜单、生成报告功能
- 快速模式使用配置中的 highlight_enabled_default 记忆高亮开关
- 进度队列异常容错
- 完全消除猴子补丁，所有方法均在 MainWindow 类内
"""

# ======================= 模块级绘图函数（原子写入） =======================
def _plot_single_worker(data, title_template, filename_template,
                        output_dir, classify_by_asset, plot_options,
                        actual_billing, price_periods, actual_multiplier, progress_queue):
    """
    单张图表生成工作函数。
    参数：
        actual_billing : dict or None, 该图表的容量线配置
        actual_multiplier : float, 该图表的实际倍率
    """
    _setup_font()
    ctx = {
        'year_month': data.get('month_str', ''),
        'table_no': data.get('table_no', ''),
        'asset': data.get('asset', ''),
        'sheet': data.get('sheet', '')
    }
    title = parse_title_template(title_template, ctx) if title_template else generate_default_title(ctx)
    filename = generate_filename(filename_template, ctx)

    out_dir = output_dir
    if classify_by_asset:
        asset_name = _sanitize_name(data.get('asset', 'Other')) or 'Other'
        out_dir = os.path.join(out_dir, asset_name)
    os.makedirs(out_dir, exist_ok=True)
    outpath = os.path.join(out_dir, filename)

    # 文件名去重（原子写入，临时文件在同一目录确保 rename 安全）
    base, ext = os.path.splitext(outpath)
    for counter in range(1, MAX_FILENAME_ATTEMPTS + 1):
        if not os.path.exists(outpath):
            break
        outpath = f"{base}_{counter}{ext}"
    else:
        return False, f"文件名冲突已达上限（{MAX_FILENAME_ATTEMPTS}次），无法生成: {outpath}"

    try:
        load_array = np.array(data['load_array']) * actual_multiplier
        if load_array.ndim != 2:
            return False, "负荷数据不是二维数组，可能日期与时间点结构异常"
        if load_array.size == 0:
            return False, "负荷数据为空，无有效记录"

        month_str = data.get('month_str', '1')
        periods = []
        if isinstance(price_periods, dict):
            m = month_str.split('-')[1] if '-' in month_str else month_str
            try:
                month_int = int(m)
            except:
                month_int = 1
            periods = price_periods.get(str(month_int), [])

        # 原子写入：先写入临时文件再使用 os.replace 原子替换
        tmp_path = outpath + '.tmp'
        try:
            _plot_curves(
                data.get('date_labels', []), load_array,
                data.get('time_labels', []), title, tmp_path,
                plot_options, actual_billing, periods
            )
            os.replace(tmp_path, outpath)
        except Exception:
            # 如果移动失败，尝试清理临时文件
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except:
                    pass
            raise

        # 进度通知（忽略 Manager 已关闭导致的异常）
        if progress_queue is not None:
            try:
                progress_queue.put(1)
            except (BrokenPipeError, EOFError, OSError):
                pass
        return True, outpath
    except RuntimeError as e:
        return False, str(e)
    except PermissionError:
        return False, f"写入权限被拒绝: {outpath}"
    except OSError as e:
        return False, f"磁盘写入错误: {str(e)}"
    except MemoryError:
        return False, "内存不足，请减少数据量或关闭其他程序"
    except Exception as e:
        log.error(f"绘图异常: {e}", exc_info=True)
        return False, f"绘图错误: {str(e)}"

# ======================= 多进程资源安全上下文管理器 =======================
class _ManagedProcessPool:
    """封装 ProcessPoolExecutor，保证退出时 shutdown，兼容 Python < 3.9。"""
    def __init__(self, max_workers, cancel_event=None):
        self.executor = ProcessPoolExecutor(max_workers=max_workers)
        self.cancel_event = cancel_event

    def __enter__(self):
        return self.executor

    def __exit__(self, exc_type, exc_val, exc_tb):
        kwargs = {}
        if sys.version_info >= (3, 9):
            kwargs['cancel_futures'] = True
        if self.cancel_event and self.cancel_event.is_set():
            self.executor.shutdown(wait=False, **kwargs)
        else:
            self.executor.shutdown(wait=True, **kwargs)

# ======================= 公共绘图任务执行器 =======================
def _execute_plot_tasks(worker_args, config_manager, signals, cancel_event, progress_queue, total_charts=None, actual_workers=None):
    """
    统一的绘图任务执行逻辑，供 ProcessingThread 和 _generation_only_thread 复用。
    新增 actual_workers 参数，用于内存自适应降级时传入调整后的进程数。
    """
    all_paths = []
    failed_tasks = []
    total = total_charts if total_charts else len(worker_args)
    signals.progress_max.emit(total)
    signals.log.emit(f"开始生成 {total} 张图表...")

    if actual_workers is None:
        actual_workers = config_manager.get_plot_processes()
    start_time = time.time()
    completed = 0

    with _ManagedProcessPool(max_workers=actual_workers, cancel_event=cancel_event) as executor:
        futures = []
        for args in worker_args:
            full_args = args + (progress_queue,)
            futures.append(executor.submit(_plot_single_worker, *full_args))

        for fut in as_completed(futures):
            if cancel_event.is_set():
                break
            try:
                ok, msg = fut.result(timeout=120)
                if ok:
                    all_paths.append(msg)
                    signals.log.emit(f"已生成: {os.path.basename(msg)}")
                else:
                    failed_tasks.append(f"图表失败: {msg}")
                    signals.log.emit(f"图表失败: {msg}")
            except Exception as e:
                friendly = _translate_error(type(e), str(e))
                failed_tasks.append(f"进程异常: {friendly}")
                signals.log.emit(f"图表异常: {friendly}")

            # 消费进度队列
            while not progress_queue.empty():
                try:
                    inc = progress_queue.get_nowait()
                    completed += inc
                    signals.progress.emit(completed)
                    elapsed = time.time() - start_time
                    if elapsed > 0.5 and completed > 0:
                        rate = completed / elapsed
                        remaining = (total - completed) / rate if rate > 0 else 0
                        if remaining > 3600:
                            eta_str = f"已完成 {completed}/{total} 张，预计剩余 {int(remaining // 3600)}时{int((remaining % 3600) // 60)}分"
                        elif remaining > 60:
                            eta_str = f"已完成 {completed}/{total} 张，预计剩余 {int(remaining // 60)}分{int(remaining % 60)}秒"
                        else:
                            eta_str = f"已完成 {completed}/{total} 张，预计剩余 {int(remaining)}秒"
                        signals.eta.emit(eta_str)
                except:
                    break

    return all_paths, failed_tasks


# ======================= 导出确认对话框（增强版） =======================

class ExportConfirmDialog(QDialog):
    def __init__(self, parent, all_summaries, selected_indices, filename_tpl, title_tpl,
                 output_dir, classify, per_group_multiplier=None, per_group_billing=None,
                 billing_dict=None, global_multiplier=1.0, config_manager=None, highlight_flags=None):
        super().__init__(parent)
        self.all_summaries = all_summaries
        self.original_indices = list(selected_indices)
        self.filename_tpl = filename_tpl
        self.title_tpl = title_tpl
        self.output_dir = output_dir
        self.classify = classify
        self.config_manager = config_manager

        self.per_group_multiplier = per_group_multiplier or {}
        self.per_group_billing = per_group_billing or {}
        self.billing_dict = billing_dict or {}
        self.global_multiplier = global_multiplier
        self._highlight_flags = highlight_flags if highlight_flags is not None else {idx: True for idx in self.original_indices}

        self.result = {}
        self.return_to_preview = False

        self.setWindowTitle("生成确认清单")
        self.setMinimumSize(900, 600)
        self.resize(1100, 700)

        if self.config_manager:
            geom = self.config_manager.get_window_geometry("ExportConfirmDialog")
            if geom:
                self.restoreGeometry(geom)
            else:
                self.resize(1100, 700)

        self._path_refresh_timer = QTimer(self)
        self._path_refresh_timer.setSingleShot(True)
        self._path_refresh_timer.timeout.connect(self._update_path_previews)

        layout = QVBoxLayout(self)

        info_label = QLabel(f"<b>输出目录:</b> {self.output_dir}")
        if self.classify:
            info_label.setText(info_label.text() + "<br><i>将按表号创建子文件夹</i>")
        layout.addWidget(info_label)

        self.stats_label = QLabel("")
        layout.addWidget(self.stats_label)

        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels([
            "选择", "表号", "月份", "倍率", "容量线", "文件名预览", "高亮", "状态"
        ])
        for col in range(8):
            item = self.table.horizontalHeaderItem(col)
            if item:
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)

        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeToContents)

        layout.addWidget(self.table, 1)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("修改选中:"))
        self.batch_mult_btn = QPushButton("倍率...")
        self.batch_mult_btn.clicked.connect(self._batch_edit_multiplier)
        toolbar.addWidget(self.batch_mult_btn)
        self.batch_bill_btn = QPushButton("容量...")
        self.batch_bill_btn.clicked.connect(self._batch_edit_billing)
        toolbar.addWidget(self.batch_bill_btn)
        self.reset_custom_btn = QPushButton("重置自定义")
        self.reset_custom_btn.clicked.connect(self._reset_custom)
        toolbar.addWidget(self.reset_custom_btn)
        self.filter_custom_btn = QPushButton("仅显示自定义项")
        self.filter_custom_btn.setCheckable(True)
        self.filter_custom_btn.toggled.connect(self._toggle_filter_custom)
        toolbar.addWidget(self.filter_custom_btn)
        layout.addLayout(toolbar)

        sel_layout = QHBoxLayout()
        sel_layout.addWidget(QPushButton("全选", clicked=self._select_all))
        sel_layout.addWidget(QPushButton("全不选", clicked=self._deselect_all))
        sel_layout.addWidget(QPushButton("反选", clicked=self._invert))
        sel_layout.addStretch()
        layout.addLayout(sel_layout)

        mod_layout = QHBoxLayout()
        mod_layout.addWidget(QLabel("更改输出目录:"))
        self.mod_dir_edit = QLineEdit(self.output_dir)
        self.mod_dir_edit.textChanged.connect(self._on_dir_text_changed)
        mod_layout.addWidget(self.mod_dir_edit, 1)
        browse2_btn = QPushButton("浏览")
        browse2_btn.clicked.connect(self._browse_mod_dir)
        mod_layout.addWidget(browse2_btn)
        new_folder_btn = QPushButton("新建文件夹")
        new_folder_btn.clicked.connect(self._create_new_folder)
        mod_layout.addWidget(new_folder_btn)
        open_dir_btn = QPushButton("打开文件夹")
        open_dir_btn.clicked.connect(self._open_current_dir)
        mod_layout.addWidget(open_dir_btn)
        layout.addLayout(mod_layout)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.ok_btn = QPushButton("确定生成")
        self.ok_btn.clicked.connect(self._on_accept)
        btn_layout.addWidget(self.ok_btn)

        self.return_preview_btn = QPushButton("返回预览窗口")
        self.return_preview_btn.clicked.connect(self._on_return_preview)
        btn_layout.addWidget(self.return_preview_btn)

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)
        layout.addLayout(btn_layout)

        self._populate_table()
        self.table.cellChanged.connect(self._on_cell_changed)
        self._update_stats()

    def _on_dir_text_changed(self):
        self._path_refresh_timer.stop()
        self._path_refresh_timer.start(300)

    def _populate_table(self):
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for row, idx in enumerate(self.original_indices):
            s = self.all_summaries[idx]
            check_item = QTableWidgetItem()
            check_item.setFlags(check_item.flags() | Qt.ItemIsUserCheckable)
            check_item.setCheckState(Qt.Checked)
            self.table.insertRow(row)
            self.table.setItem(row, 0, check_item)
            self.table.setItem(row, 1, QTableWidgetItem(s.asset_value or "未命名"))
            self.table.setItem(row, 2, QTableWidgetItem(s.month_str))
            mult = self._get_effective_multiplier(idx)
            mult_item = QTableWidgetItem(f"{mult:.4f}")
            mult_item.setData(Qt.UserRole, idx)
            if idx in self.per_group_multiplier:
                mult_item.setBackground(QColor(ColorConst.CUSTOM_PARAM_BG))
                mult_item.setToolTip("自定义倍率")
            self.table.setItem(row, 3, mult_item)
            bill = self._get_effective_billing(idx)
            bill_str = f"{bill['value']:.2f}" if bill else ""
            bill_item = QTableWidgetItem(bill_str)
            bill_item.setData(Qt.UserRole, idx)
            if idx in self.per_group_billing:
                bill_item.setBackground(QColor(ColorConst.CUSTOM_PARAM_BG))
                bill_item.setToolTip("自定义容量")
            self.table.setItem(row, 4, bill_item)
            ctx = {
                'year_month': s.month_str,
                'table_no': s.asset_value or '未命名',
                'asset': s.asset_value or '未命名',
                'sheet': s.sheet_name
            }
            fname = generate_filename(self.filename_tpl, ctx)
            sanitized_asset = _sanitize_name(s.asset_value) if s.asset_value else '其他'
            path_preview = os.path.join(sanitized_asset, fname) if self.classify else fname
            self.table.setItem(row, 5, QTableWidgetItem(path_preview))

            highlight_cb = QCheckBox()
            highlight_cb.setChecked(self._highlight_flags.get(idx, True))
            highlight_cb.stateChanged.connect(lambda state, r=row, i=idx: self._on_highlight_changed(r, i, state))
            self.table.setCellWidget(row, 6, highlight_cb)

            full_path = os.path.join(self.output_dir, path_preview.replace('/', os.sep))
            status = "⚠ 将覆盖" if os.path.exists(full_path) else ""
            self.table.setItem(row, 7, QTableWidgetItem(status))
        self.table.blockSignals(False)

    def _on_highlight_changed(self, row, idx, state):
        self._highlight_flags[idx] = (state == Qt.Checked)

    def _get_effective_multiplier(self, idx):
        return self.per_group_multiplier.get(idx, self.global_multiplier)

    def _get_effective_billing(self, idx):
        if idx in self.per_group_billing:
            return self.per_group_billing[idx]
        return self.billing_dict.get(idx)

    def _on_cell_changed(self, row, col):
        if col not in (3, 4):
            return
        idx = None
        item = self.table.item(row, col)
        if item:
            idx = item.data(Qt.UserRole)
        if idx is None:
            return
        if col == 3:
            try:
                new_val = float(item.text())
            except (ValueError, AttributeError):
                self._restore_cell(row, col)
                return
            self.per_group_multiplier[idx] = new_val
            item.setBackground(QColor(ColorConst.CUSTOM_PARAM_BG))
            item.setToolTip("自定义倍率")
        elif col == 4:
            text = item.text().strip()
            if text == "":
                self.per_group_billing.pop(idx, None)
                item.setBackground(QColor("white"))
                item.setToolTip("")
            else:
                try:
                    new_val = float(text)
                except (ValueError, AttributeError):
                    self._restore_cell(row, col)
                    return
                self.per_group_billing[idx] = {'type': 'capacity', 'value': new_val}
                item.setBackground(QColor(ColorConst.CUSTOM_PARAM_BG))
                item.setToolTip("自定义容量")
        self._update_row_preview(row, idx)
        self._update_stats()

    def _update_row_preview(self, row, idx):
        s = self.all_summaries[idx]
        ctx = {
            'year_month': s.month_str,
            'table_no': s.asset_value or '未命名',
            'asset': s.asset_value or '未命名',
            'sheet': s.sheet_name
        }
        fname = generate_filename(self.filename_tpl, ctx)
        sanitized_asset = _sanitize_name(s.asset_value) if s.asset_value else '其他'
        path_preview = os.path.join(sanitized_asset, fname) if self.classify else fname
        self.table.item(row, 5).setText(path_preview)
        full_path = os.path.join(self.mod_dir_edit.text(), path_preview.replace('/', os.sep))
        status = "⚠ 将覆盖" if os.path.exists(full_path) else ""
        self.table.item(row, 7).setText(status)

    def _restore_cell(self, row, col):
        self.table.blockSignals(True)
        try:
            idx = None
            item = self.table.item(row, col)
            if item:
                idx = item.data(Qt.UserRole)
            if idx is None:
                return
            if col == 3:
                val = self._get_effective_multiplier(idx)
                self.table.item(row, col).setText(f"{val:.4f}")
            elif col == 4:
                bill = self._get_effective_billing(idx)
                bill_str = f"{bill['value']:.2f}" if bill else ""
                self.table.item(row, col).setText(bill_str)
        finally:
            self.table.blockSignals(False)

    def _batch_edit_multiplier(self):
        rows = set()
        for idx in self.table.selectionModel().selectedRows():
            rows.add(idx.row())
        if not rows:
            QMessageBox.warning(self, "提示", "请先选中要修改的行")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("批量设置倍率")
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel("输入新的倍率值:"))
        spin = QDoubleSpinBox()
        spin.setRange(0.01, 10000000.0)
        spin.setValue(self.global_multiplier)
        layout.addWidget(spin)
        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)
        layout.addWidget(btn_box)
        if dlg.exec() == QDialog.Accepted:
            val = spin.value()
            for row in rows:
                item = self.table.item(row, 3)
                idx = item.data(Qt.UserRole) if item else None
                if idx is None:
                    continue
                self.per_group_multiplier[idx] = val
                item.setText(f"{val:.4f}")
                item.setBackground(QColor(ColorConst.CUSTOM_PARAM_BG))
                item.setToolTip("自定义倍率")
            self._update_stats()

    def _batch_edit_billing(self):
        rows = set()
        for idx in self.table.selectionModel().selectedRows():
            rows.add(idx.row())
        if not rows:
            QMessageBox.warning(self, "提示", "请先选中要修改的行")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("批量设置容量")
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel("输入容量值 (留空表示删除容量):"))
        edit = QLineEdit()
        layout.addWidget(edit)
        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)
        layout.addWidget(btn_box)
        if dlg.exec() == QDialog.Accepted:
            text = edit.text().strip()
            for row in rows:
                item = self.table.item(row, 4)
                idx = item.data(Qt.UserRole) if item else None
                if idx is None:
                    continue
                if text == "":
                    self.per_group_billing.pop(idx, None)
                    item.setText("")
                    item.setBackground(QColor("white"))
                    item.setToolTip("")
                else:
                    try:
                        val = float(text)
                    except ValueError:
                        continue
                    self.per_group_billing[idx] = {'type': 'capacity', 'value': val}
                    item.setText(f"{val:.2f}")
                    item.setBackground(QColor(ColorConst.CUSTOM_PARAM_BG))
                    item.setToolTip("自定义容量")
            self._update_stats()

    def _reset_custom(self):
        rows = set()
        for idx in self.table.selectionModel().selectedRows():
            rows.add(idx.row())
        if not rows:
            QMessageBox.warning(self, "提示", "请先选中要重置的行")
            return
        if QMessageBox.question(self, "确认", "清除选中行的所有自定义倍率和容量？") == QMessageBox.Yes:
            for row in rows:
                for col in (3, 4):
                    item = self.table.item(row, col)
                    idx = item.data(Qt.UserRole) if item else None
                    if idx is None:
                        continue
                    self.table.blockSignals(True)
                    try:
                        if col == 3 and idx in self.per_group_multiplier:
                            del self.per_group_multiplier[idx]
                            item.setText(f"{self.global_multiplier:.4f}")
                            item.setBackground(QColor("white"))
                            item.setToolTip("")
                        elif col == 4 and idx in self.per_group_billing:
                            del self.per_group_billing[idx]
                            item.setText("")
                            item.setBackground(QColor("white"))
                            item.setToolTip("")
                    finally:
                        self.table.blockSignals(False)
            self._update_stats()

    def _toggle_filter_custom(self, checked):
        for row in range(self.table.rowCount()):
            hide = False
            if checked:
                has_custom = (self.table.item(row, 3).background().color() == QColor(ColorConst.CUSTOM_PARAM_BG) or
                              self.table.item(row, 4).background().color() == QColor(ColorConst.CUSTOM_PARAM_BG))
                hide = not has_custom
            self.table.setRowHidden(row, hide)

    def _update_stats(self):
        count = self.table.rowCount()
        custom_mult = sum(1 for i in range(count) if self.table.item(i, 3) and self.table.item(i, 3).background().color() == QColor(ColorConst.CUSTOM_PARAM_BG))
        custom_bill = sum(1 for i in range(count) if self.table.item(i, 4) and self.table.item(i, 4).background().color() == QColor(ColorConst.CUSTOM_PARAM_BG))
        self.stats_label.setText(f"共 {count} 张图表 | 自定义倍率: {custom_mult} 张 | 自定义容量: {custom_bill} 张")

    def _select_all(self):
        for i in range(self.table.rowCount()):
            self.table.item(i, 0).setCheckState(Qt.Checked)

    def _deselect_all(self):
        for i in range(self.table.rowCount()):
            self.table.item(i, 0).setCheckState(Qt.Unchecked)

    def _invert(self):
        for i in range(self.table.rowCount()):
            item = self.table.item(i, 0)
            item.setCheckState(Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked)

    def _browse_mod_dir(self):
        dir_path = QFileDialog.getExistingDirectory(self, "选择目录", self.mod_dir_edit.text())
        if dir_path:
            self.mod_dir_edit.setText(dir_path)
            self._update_path_previews()

    def _create_new_folder(self):
        base = self.mod_dir_edit.text()
        folder, ok = QInputDialog.getText(self, "新建文件夹", "输入文件夹名称:", text="子目录")
        if ok and folder:
            new_dir = os.path.join(base, folder)
            try:
                os.makedirs(new_dir, exist_ok=True)
                self.mod_dir_edit.setText(new_dir)
            except Exception as e:
                QMessageBox.critical(self, "错误", f"无法创建文件夹: {e}")

    def _open_current_dir(self):
        dir_path = self.mod_dir_edit.text()
        if os.path.isdir(dir_path):
            try:
                if os.name == 'nt':
                    os.startfile(dir_path)
                else:
                    subprocess.run(['xdg-open', dir_path])
            except:
                pass
        else:
            QMessageBox.information(self, "提示", "目录不存在")

    def _update_path_previews(self):
        current_dir = self.mod_dir_edit.text()
        for row in range(self.table.rowCount()):
            idx = None
            item = self.table.item(row, 3)
            if item:
                idx = item.data(Qt.UserRole)
            if idx is None:
                item = self.table.item(row, 4)
                if item:
                    idx = item.data(Qt.UserRole)
            if idx is None:
                continue
            s = self.all_summaries[idx]
            ctx = {'year_month': s.month_str, 'table_no': s.asset_value or '未命名',
                   'asset': s.asset_value or '未命名', 'sheet': s.sheet_name}
            fname = generate_filename(self.filename_tpl, ctx)
            sanitized_asset = _sanitize_name(s.asset_value) if s.asset_value else '其他'
            path_preview = os.path.join(sanitized_asset, fname) if self.classify else fname
            self.table.item(row, 5).setText(path_preview)
            full_path = os.path.join(current_dir, path_preview.replace('/', os.sep))
            status = "⚠ 将覆盖" if os.path.exists(full_path) else ""
            self.table.item(row, 7).setText(status)

    def _on_accept(self):
        selected_idx_list = []
        multipliers = {}
        billings = {}
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).checkState() != Qt.Checked:
                continue
            idx = None
            item = self.table.item(row, 3)
            if item:
                idx = item.data(Qt.UserRole)
            if idx is not None:
                selected_idx_list.append(idx)
                try:
                    mult_val = float(self.table.item(row, 3).text())
                except:
                    mult_val = self.global_multiplier
                multipliers[idx] = mult_val
                bill_text = self.table.item(row, 4).text().strip()
                if bill_text:
                    try:
                        bill_val = float(bill_text)
                        billings[idx] = {'type': 'capacity', 'value': bill_val}
                    except:
                        pass
        if not selected_idx_list:
            QMessageBox.warning(self, "警告", "请至少勾选一项")
            return
        self.output_dir = self.mod_dir_edit.text()
        if not os.path.isdir(self.output_dir):
            try:
                os.makedirs(self.output_dir, exist_ok=True)
            except Exception as e:
                QMessageBox.critical(self, "目录无效", f"无法创建输出目录: {e}")
                return
        self.result = {
            'indices': selected_idx_list,
            'multipliers': multipliers,
            'billings': billings,
            'output_dir': self.output_dir,
            'highlight_flags': self._highlight_flags
        }
        self.accept()

    def _on_return_preview(self):
        self.result = {'highlight_flags': self._highlight_flags.copy()}
        self.return_to_preview = True
        self.reject()

    def closeEvent(self, event):
        if self.config_manager:
            self.config_manager.set_window_geometry("ExportConfirmDialog", self.saveGeometry())
        super().closeEvent(event)


# ======================= 处理线程 =======================
class ProcessingThread(threading.Thread):
    def __init__(self, file_paths, output_dir, config_manager, csv_encoding='auto'):
        super().__init__(daemon=True)
        self.file_paths = file_paths
        self.output_dir = output_dir
        self.config_manager = config_manager
        self.csv_encoding = csv_encoding
        self.signals = ProcessingSignals()
        self.stop_event = threading.Event()
        self.dialog_event = threading.Event()
        self.dialog_result = None

        self.mappings = {}
        self.file_sheets = {}
        self.all_summaries = []
        self.selected_indices = []
        self.failed_tasks = []

        # 从配置加载默认绘图参数
        self.plot_options = {
            'show_daily': True,
            'show_avg': True,
            'fit_types': [],
            'show_max_demand': self.config_manager.app_set('show_max_demand', False),
            'highlight_mode': self.config_manager.app_set('highlight_mode', 'none'),
            'weekday_color': self.config_manager.app_set('weekday_color', DEFAULT_WEEKDAY_COLOR),
            'weekend_color': self.config_manager.app_set('weekend_color', DEFAULT_WEEKEND_COLOR),
            'holiday_color': self.config_manager.app_set('holiday_color', DEFAULT_HOLIDAY_COLOR),
            'weekend_line_width': self.config_manager.app_set('weekend_line_width', DEFAULT_WEEKEND_LINE_WIDTH),
            'holiday_line_width': self.config_manager.app_set('holiday_line_width', DEFAULT_HOLIDAY_LINE_WIDTH),
            'show_weekend_bg': self.config_manager.app_set('show_weekend_bg', False),
            'legend_position': self.config_manager.app_set('legend_position', 'right'),
            'mark_anomalies': self.config_manager.app_set('mark_anomalies', False),
        }
        self.global_multiplier = 1.0
        self.billing_dict = {}
        self.price_periods = {}
        self.title_template = self.config_manager.app_set('last_title_template', '{table_no}_{year_month}_负荷曲线')
        self.filename_template = self.config_manager.app_set('last_filename_template', '{table_no}_{year_month}_负荷曲线')
        self.classify_by_asset = False

        self.per_group_multiplier = {}
        self.per_group_billing = {}
        self.show_max_demand = self.config_manager.app_set('show_max_demand', False)
        self._highlight_flags = {}
        self._last_highlight_flags = None

        self.data_provider = None
        self._max_file_size = 0
        self._plot_worker_count = self.config_manager.get_plot_processes()
        self._scan_workers = self.config_manager.get_scan_workers()

    def run(self):
        self._adjust_for_memory()
        try:
            if self.stop_event.is_set():
                return
            self.signals.log.emit("阶段1/4：文件解析与映射")
            for fp in self.file_paths:
                try:
                    sz = os.path.getsize(fp)
                    if sz > self._max_file_size:
                        self._max_file_size = sz
                except:
                    pass
            if self._max_file_size > HUGE_FILE_SIZE:
                max_mb = self._max_file_size / (1024 * 1024)
                self.signals.log.emit(f"检测到超大文件 ({max_mb:.0f}MB)，将自动降低并行度以避免内存溢出")

            self._parse_and_map()
            if self.stop_event.is_set():
                return

            shared_cache = ThreadSafeCache()
            self.signals.log.emit("阶段2/4：数据扫描")
            self._scan_and_prepare(shared_cache)
            if self.stop_event.is_set():
                return

            total_charts = len(self.all_summaries)
            self.signals.progress_max.emit(total_charts)
            self.signals.log.emit(f"预计生成 {total_charts} 张图表")

            self.signals.log.emit("阶段3/4：缓存预加载")
            self._preload_data(shared_cache)
            if self.stop_event.is_set():
                return

            self.signals.log.emit("阶段4/4：预览与生成")
            self._preview_interaction()
            if self.stop_event.is_set():
                return

            self._export_confirm_interaction()
            if self.stop_event.is_set():
                return

            self._generate_charts()
        except Exception as e:
            friendly = _translate_error(type(e), str(e))
            self.signals.error.emit(friendly)
            log.exception("处理线程异常")
        finally:
            self.signals.finished.emit()

    def _adjust_for_memory(self):
        self._plot_worker_count = self.config_manager.get_plot_processes()
        self._scan_workers = self.config_manager.get_scan_workers()

        try:
            import psutil
            mem = psutil.virtual_memory()
            available_gb = mem.available / (1024 ** 3)
            if available_gb < 1.0:
                self.signals.log.emit("可用内存不足1GB，自动将绘图并行度降至1，扫描并行度降至1")
                self._plot_worker_count = 1
                self._scan_workers = 1
                return
            elif available_gb < 2.0:
                self._plot_worker_count = max(1, self._plot_worker_count // 2)
                self._scan_workers = max(1, self._scan_workers // 2)
                self.signals.log.emit(f"可用内存 {available_gb:.1f}GB，降低并行度：绘图={self._plot_worker_count}，扫描={self._scan_workers}")
        except ImportError:
            log.debug("未安装psutil，无法进行内存自适应降级")
            if self._max_file_size > HUGE_FILE_SIZE:
                self._scan_workers = 1
                self.signals.log.emit("超大文件检测，扫描并行度降至1")

    def _parse_and_map(self):
        cached_base_mapping = None
        total_files = len(self.file_paths)
        for idx, fp in enumerate(self.file_paths):
            if self.stop_event.is_set():
                return
            fname = os.path.basename(fp)
            self.signals.status.emit(f"正在映射文件 {idx + 1}/{total_files}: {fname}")
            try:
                _, sheets = _parse_file_meta_worker(fp)
            except Exception as e:
                self.signals.log.emit(f"文件解析失败 {fname}: {e}")
                sheets = []

            self.file_sheets[fp] = sheets

            if cached_base_mapping is not None and fp != self.file_paths[0]:
                normalized = _normalize_mapping(fp, cached_base_mapping, sheets)
                if normalized:
                    self.mappings[fp] = normalized
                    self.signals.log.emit(f"自动应用映射: {fname}")
                else:
                    self.signals.log.emit(f"无法自动应用映射到 {fname}，跳过")
                continue

            self.signals.mapping_request.emit((fp, sheets))
            self.dialog_event.clear()
            self.dialog_event.wait()
            if self.stop_event.is_set():
                return
            if self.dialog_result == 'skip':
                self.signals.log.emit(f"跳过文件: {fname}")
                continue
            elif self.dialog_result == 'cancel' or self.dialog_result is None:
                self.signals.log.emit("用户取消处理")
                self.stop_event.set()
                return
            else:
                raw_result = self.dialog_result
                apply_all_files = False
                if isinstance(raw_result, dict) and raw_result.get('__apply_to_all_files'):
                    apply_all_files = True
                    base_map = {k: v for k, v in raw_result.items() if not k.startswith('__')}
                    cached_base_mapping = {
                        '__apply_to_all_sheets': True,
                        '__base_mapping': base_map
                    }
                    normalized = _normalize_mapping(fp, raw_result, sheets)
                else:
                    normalized = _normalize_mapping(fp, raw_result, sheets)

                if normalized:
                    self.mappings[fp] = normalized
                    if apply_all_files:
                        self.signals.log.emit(f"已保存映射，将自动应用于后续文件: {fname}")
        if not self.mappings:
            self.signals.log.emit("没有有效的文件映射，流程终止")
            self.stop_event.set()

    def _scan_and_prepare(self, shared_cache):
        scan_tasks = []
        cancel_events = {}
        for fp, mapping_info in self.mappings.items():
            fallback = ""
            tb_match = re.search(r'表号[：:\s]*(\d+)', os.path.basename(fp))
            if tb_match:
                fallback = tb_match.group(1)
            for sheet, smap in mapping_info['sheets'].items():
                ce = threading.Event()
                cancel_events[(fp, sheet)] = ce
                task = (fp, sheet, smap, fallback, ce, shared_cache)
                scan_tasks.append(task)

        self.all_summaries = []
        total_tasks = len(scan_tasks)
        scan_workers = self._scan_workers
        with ThreadPoolExecutor(max_workers=scan_workers) as executor:
            futures = {}
            for task in scan_tasks:
                fp, sheet = task[0], task[1]
                fut = executor.submit(_scan_file_worker, task)
                futures[fut] = (fp, sheet)

            completed = 0
            for fut in as_completed(futures):
                if self.stop_event.is_set():
                    for ce in cancel_events.values():
                        ce.set()
                    if sys.version_info >= (3, 9):
                        executor.shutdown(wait=False, cancel_futures=True)
                    else:
                        executor.shutdown(wait=False)
                    return

                fp, sheet = futures[fut]
                completed += 1
                self.signals.status.emit(f"扫描文件: {completed}/{total_tasks}")
                try:
                    file_size = os.path.getsize(fp)
                except:
                    file_size = 0
                timeout = max(30, file_size // (10 * 1024 * 1024) * 5)
                try:
                    res = fut.result(timeout=timeout)
                    if isinstance(res, dict) and 'error' in res:
                        friendly = _translate_error(None, res['error'])
                        self.signals.log.emit(f"扫描错误: {friendly}")
                    elif isinstance(res, list):
                        for item in res:
                            self.all_summaries.append(GroupSummary(**item))
                except Exception as e:
                    friendly = _translate_error(type(e), str(e))
                    self.signals.log.emit(f"扫描异常 ({fp}/{sheet}): {friendly}")

        self.all_summaries.sort(key=lambda s: (s.month_str, s.asset_value))
        self.signals.log.emit(f"扫描完成，共 {len(self.all_summaries)} 个分组")

    def _preload_data(self, shared_cache=None):
        if not self.all_summaries:
            return
        self.data_provider = DataProvider(self.csv_encoding)
        if shared_cache:
            for (fp, sheet), df in shared_cache.items():
                self.data_provider.inject_raw_file(fp, sheet, df)
            self.signals.log.emit("已注入扫描缓存数据，避免重复读取。")
        self.signals.log.emit("预加载完成")

    def _preview_interaction(self):
        if not self.all_summaries:
            self.stop_event.set()
            return
        self.signals.preview_request.emit((self.all_summaries, self.global_multiplier, self.data_provider))
        self.dialog_event.clear()
        self.dialog_event.wait()
        if self.stop_event.is_set():
            return
        if self.dialog_result is None:
            self.stop_event.set()
            return
        settings = self.dialog_result
        self.plot_options = {
            'show_daily': settings.get('show_daily', True),
            'show_avg': settings.get('show_avg', True),
            'show_max_demand': settings.get('show_max_demand', False),
            'fit_types': [],
            'highlight_mode': settings.get('highlight_mode', 'none'),
            'weekday_color': settings.get('weekday_color', DEFAULT_WEEKDAY_COLOR),
            'weekend_color': settings.get('weekend_color', DEFAULT_WEEKEND_COLOR),
            'holiday_color': settings.get('holiday_color', DEFAULT_HOLIDAY_COLOR),
            'weekend_line_width': settings.get('weekend_line_width', DEFAULT_WEEKEND_LINE_WIDTH),
            'holiday_line_width': settings.get('holiday_line_width', DEFAULT_HOLIDAY_LINE_WIDTH),
            'show_weekend_bg': settings.get('show_weekend_bg', False),
            'legend_position': settings.get('legend_position', 'right'),
            'mark_anomalies': settings.get('mark_anomalies', False),
        }
        self.global_multiplier = settings.get('global_multiplier', self.global_multiplier)
        self.per_group_multiplier = settings.get('per_group_multiplier', {})
        self.per_group_billing = settings.get('per_group_billing', {})
        self.billing_dict = settings.get('billing_dict', {})
        self.title_template = settings.get('title_template', self.title_template)
        self.filename_template = settings.get('filename_template', self.filename_template)
        self.output_dir = settings.get('output_dir', self.output_dir)
        self.classify_by_asset = settings.get('classify_by_asset', False)
        sn = settings.get('strategy_name')
        if sn:
            strat = self.config_manager.get_strat(sn)
            if strat:
                self.price_periods = strat
        self.selected_indices = settings.get('selected_indices', [])
        self._highlight_flags = settings.get('highlight_flags', {idx: True for idx in self.selected_indices})
        if not self.selected_indices:
            self.stop_event.set()
            return

    def _export_confirm_interaction(self):
        while not self.stop_event.is_set():
            confirm_data = {
                'all_summaries': self.all_summaries,
                'selected_indices': self.selected_indices,
                'filename_tpl': self.filename_template,
                'title_tpl': self.title_template,
                'output_dir': self.output_dir,
                'classify': self.classify_by_asset,
                'per_group_multiplier': self.per_group_multiplier,
                'per_group_billing': self.per_group_billing,
                'billing_dict': self.billing_dict,
                'global_multiplier': self.global_multiplier,
                'config_manager': self.config_manager,
                'highlight_flags': self._highlight_flags.copy()
            }
            self.signals.export_confirm_request.emit(confirm_data)
            self.dialog_event.clear()
            self.dialog_event.wait()

            if self.stop_event.is_set():
                return

            result = self.dialog_result

            if result == 'return_preview':
                self._last_highlight_flags = confirm_data['highlight_flags']
                self._preview_interaction()
                if self.stop_event.is_set():
                    return
                if self._last_highlight_flags:
                    new_flags = {}
                    for idx in self.selected_indices:
                        new_flags[idx] = self._last_highlight_flags.get(idx, True)
                    self._highlight_flags = new_flags
                continue
            elif isinstance(result, dict) and 'indices' in result:
                final_indices = result.get('indices', self.selected_indices)
                final_multipliers = result.get('multipliers', {})
                final_billings = result.get('billings', {})
                self._highlight_flags = result.get('highlight_flags', {})

                if not final_indices:
                    self.signals.log.emit("用户在确认清单中未选择任何图表，取消生成。")
                    self.stop_event.set()
                    return

                for idx in final_indices:
                    if idx in final_multipliers:
                        self.per_group_multiplier[idx] = final_multipliers[idx]
                    if idx in final_billings:
                        self.per_group_billing[idx] = final_billings[idx]

                if 'output_dir' in result:
                    self.output_dir = result['output_dir']

                self.selected_indices = final_indices
                return
            else:
                self.stop_event.set()
                return

    def _generate_charts(self):
        self.signals.log.emit("开始生成图表...")
        provider = self.data_provider if self.data_provider else DataProvider(self.csv_encoding)
        tasks = []
        for orig_idx in self.selected_indices:
            summary = self.all_summaries[orig_idx]
            data = provider.get_group_data(summary)
            if data:
                mult = self.per_group_multiplier.get(orig_idx, self.global_multiplier)
                bill = self.per_group_billing.get(orig_idx) or self.billing_dict.get(orig_idx)
                tasks.append((orig_idx, data, mult, bill))
            else:
                error_msg = provider.get_load_error(summary)
                self.failed_tasks.append(f"{summary.asset_value or '未命名'} - {summary.month_str} ({error_msg or '无数据'})")
        if not tasks:
            self.signals.log.emit("无有效数据，流程结束")
            return

        worker_args = []
        for orig_idx, data, mult, bill in tasks:
            opts = self.plot_options.copy()
            if not self._highlight_flags.get(orig_idx, True):
                opts['highlight_mode'] = 'none'
            worker_args.append((data, self.title_template, self.filename_template,
                                self.output_dir, self.classify_by_asset, opts,
                                bill, self.price_periods, mult))

        manager = mp.Manager()
        progress_queue = manager.Queue()
        try:
            all_paths, failed = _execute_plot_tasks(
                worker_args, self.config_manager, self.signals, self.stop_event,
                progress_queue, total_charts=len(tasks),
                actual_workers=self._plot_worker_count
            )
            self.failed_tasks.extend(failed)
        finally:
            manager.shutdown()

        self.signals.generation_finished.emit((all_paths, self.failed_tasks))
        if provider:
            provider.clear()

    def set_mapping_result(self, result):
        self.dialog_result = result
        self.dialog_event.set()

    def set_preview_result(self, result):
        self.dialog_result = result
        self.dialog_event.set()

    def set_export_confirm_result(self, result):
        self.dialog_result = result
        self.dialog_event.set()

    def cancel(self):
        self.stop_event.set()
        self.dialog_event.set()


# ======================= 全局样式表 =======================
def apply_global_stylesheet(app: QApplication):
    app.setStyleSheet("""
        QPushButton {
            border-radius: 4px;
            padding: 5px 14px;
            font-weight: bold;
        }
        QPushButton:hover {
            border: 1px solid palette(highlight);
        }
        QPushButton:pressed {
            background-color: palette(highlight);
            color: palette(highlighted-text);
        }
        QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
            border-radius: 4px;
            padding: 4px 8px;
        }
        QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
            border: 1px solid palette(highlight);
        }
        QGroupBox {
            border: 1px solid palette(mid);
            border-radius: 6px;
            margin-top: 14px;
            padding-top: 14px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 5px;
            font-weight: bold;
        }
        QListWidget, QTreeWidget, QTableWidget {
            border-radius: 4px;
        }
        QListWidget::item:selected, QTreeWidget::item:selected, QTableWidget::item:selected {
            background-color: palette(highlight);
            color: palette(highlighted-text);
        }
        QProgressBar {
            border: 1px solid palette(mid);
            border-radius: 4px;
            text-align: center;
        }
        QProgressBar::chunk {
            background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 palette(highlight), stop:1 palette(highlight).lighter(130));
            border-radius: 3px;
        }
        QScrollBar:vertical {
            width: 10px;
            background: transparent;
        }
        QScrollBar::handle:vertical {
            background: palette(mid);
            border-radius: 5px;
            min-height: 20px;
        }
        QScrollBar::handle:vertical:hover {
            background: palette(dark);
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0px;
        }
        QScrollBar:horizontal {
            height: 10px;
            background: transparent;
        }
        QScrollBar::handle:horizontal {
            background: palette(mid);
            border-radius: 5px;
            min-width: 20px;
        }
        QScrollBar::handle:horizontal:hover {
            background: palette(dark);
        }
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
            width: 0px;
        }
        QMenuBar {
            background: palette(window);
            border-bottom: 1px solid palette(mid);
            padding: 2px;
        }
        QMenuBar::item {
            padding: 4px 12px;
            border-radius: 4px;
        }
        QMenuBar::item:selected {
            background: palette(highlight);
        }
        QMenu {
            background: palette(window);
            border: 1px solid palette(mid);
            border-radius: 4px;
        }
        QMenu::item {
            padding: 6px 30px;
        }
        QMenu::item:selected {
            background: palette(highlight);
            color: palette(highlighted-text);
        }
        QListWidget[dragOver="true"] {
            border: 2px solid #4CAF50;
            background-color: rgba(76, 175, 80, 0.1);
        }
    """)

# ======================= 文件拖拽列表 =======================
class FileListWidget(QListWidget):
    files_added = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setSelectionMode(QListWidget.ExtendedSelection)
        self.setStyleSheet("QListWidget { border: 2px dashed palette(mid); border-radius: 4px; padding: 4px; }")
        self.setToolTip("拖放 Excel/CSV 文件或文件夹\n支持 xlsx、xls、csv")
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)
        self._file_paths = []

        self._empty_label = QLabel("📂  拖放 Excel / CSV 文件或文件夹到此处\n或点击「添加文件」按钮", self)
        self._empty_label.setAlignment(Qt.AlignCenter)
        self._empty_label.setStyleSheet("color: gray; font-size: 12px; border: none;")
        self._empty_label.setWordWrap(True)
        self.model().rowsInserted.connect(self._update_empty_label)
        self.model().rowsRemoved.connect(self._update_empty_label)
        self.model().modelReset.connect(self._update_empty_label)
        self._update_empty_label()

    def _update_empty_label(self):
        self._empty_label.setVisible(self.count() == 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._empty_label.setGeometry(self.rect())

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setProperty("dragOver", True)
            self.style().unpolish(self)
            self.style().polish(self)
        else:
            super().dragEnterEvent(event)

    def dragLeaveEvent(self, event):
        self.setProperty("dragOver", False)
        self.style().unpolish(self)
        self.style().polish(self)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent):
        self.setProperty("dragOver", False)
        self.style().unpolish(self)
        self.style().polish(self)
        if event.mimeData().hasUrls():
            files = []
            for url in event.mimeData().urls():
                p = url.toLocalFile()
                if os.path.isfile(p) and p.lower().endswith(SUPPORTED_EXTS):
                    files.append(p)
                elif os.path.isdir(p):
                    for root, _, fns in os.walk(p):
                        for fn in fns:
                            if fn.lower().endswith(SUPPORTED_EXTS):
                                files.append(os.path.join(root, fn))
            if files:
                self.files_added.emit(files)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    def add_file_item(self, filepath):
        fname = os.path.basename(filepath)
        try:
            sz = os.path.getsize(filepath)
            size_str = f"{sz / 1024:.1f} KB" if sz < 1024 * 1024 else f"{sz / (1024 * 1024):.1f} MB"
        except:
            size_str = "未知"
        item = QListWidgetItem(f"{fname}  [{size_str}]")
        item.setData(Qt.UserRole, filepath)
        self.addItem(item)
        self._file_paths.append(filepath)

    def remove_item_by_row(self, row):
        if 0 <= row < len(self._file_paths):
            del self._file_paths[row]
            self.takeItem(row)

    def clear_files(self):
        self._file_paths.clear()
        self.clear()

    def get_file_paths(self):
        return self._file_paths[:]

    def _context_menu(self, pos):
        item = self.itemAt(pos)
        menu = QMenu()
        if item:
            act_open = menu.addAction("打开文件所在位置")
            act_remove = menu.addAction("从列表中移除")
            action = menu.exec(self.viewport().mapToGlobal(pos))
            if action == act_open:
                fp = item.data(Qt.UserRole)
                if fp and os.path.exists(fp):
                    try:
                        if os.name == 'nt':
                            subprocess.run(['explorer', os.path.dirname(fp)])
                        else:
                            subprocess.run(['xdg-open', os.path.dirname(fp)])
                    except:
                        pass
            elif action == act_remove:
                self.remove_item_by_row(self.row(item))
        else:
            act_clear = menu.addAction("清空列表")
            if menu.exec(self.viewport().mapToGlobal(pos)) == act_clear:
                self.clear_files()

# ======================= 拖放覆盖层 =======================
class DropOverlay(QWidget):
    files_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setVisible(False)
        self._label = QLabel("释放以添加文件", self)
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setStyleSheet("background: rgba(0,0,0,120); color: white; font-size:24px; border-radius:10px;")
        self._label.setFixedSize(300, 80)

    def dragEnterEvent(self, event):
        event.acceptProposedAction()
        self.setVisible(True)
        self.raise_()

    def dragLeaveEvent(self, event):
        self.setVisible(False)

    def dropEvent(self, event):
        self.setVisible(False)
        if event.mimeData().hasUrls():
            paths = []
            for url in event.mimeData().urls():
                p = url.toLocalFile()
                if os.path.isfile(p) and p.lower().endswith(SUPPORTED_EXTS):
                    paths.append(p)
                elif os.path.isdir(p):
                    for root, _, files in os.walk(p):
                        for f in files:
                            if f.lower().endswith(SUPPORTED_EXTS):
                                paths.append(os.path.join(root, f))
            if paths:
                self.files_dropped.emit(paths)
            event.acceptProposedAction()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.parent():
            self.setGeometry(self.parent().rect())
            self._label.move((self.width() - 300) // 2, (self.height() - 80) // 2)


# ======================= 主窗口（UI 构建与基础交互） =======================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("负荷曲线批量生成工具 Pro 融合版")
        self.setMinimumSize(900, 600)

        self.config_manager = Config()
        self.files = []
        self.output_dir = self.config_manager.app_set('last_output_dir', '')
        if not self.output_dir or not os.path.isdir(self.output_dir):
            self.output_dir = os.path.join(os.path.expanduser("~"), "Desktop", "负荷曲线输出")

        # 恢复窗口几何
        geom = self.config_manager.get_window_geometry("MainWindow")
        if geom:
            self.restoreGeometry(geom)
        else:
            self.resize(900, 600)

        self._thread = None
        self._dialog_active = False
        self._quick_mode = self.config_manager.app_set('quick_mode', False)
        self._processing = False
        self._preview_dlg = None
        self._last_summaries = []
        self._last_data_provider = None
        self._last_preview_settings = None

        self._gen_signals = None
        self._gen_cancel_event = None
        self._gen_thread = None

        self._active_mapping_dlg = None

        self._taskbar_button = None
        if sys.platform == 'win32' and QWinTaskbarButton:
            try:
                self._taskbar_button = QWinTaskbarButton(self)
            except:
                pass

        # 暗色主题状态
        self._dark_theme = self.config_manager.app_set('dark_theme', False)

        self._cleanup_temp_files()

        self._create_actions()
        self._create_menus()
        self._create_toolbar()
        self._init_central_widget()
        self._init_status_bar()

        self._overlay = DropOverlay(self)
        self._overlay.files_dropped.connect(self._on_files_dropped)

        self._set_ui_state('idle')
        self._update_start_btn()
        self._log("程序启动，等待操作...")

        # 应用保存的主题
        if self._dark_theme:
            self._apply_dark_theme()

        sys.excepthook = self._gui_exception_handler

        if self.config_manager.restored_from_backup:
            QTimer.singleShot(500, lambda: QMessageBox.warning(self, "配置恢复",
                "配置文件已损坏或丢失，已自动恢复为默认设置。您可能需要重新配置列映射和策略。"))
            self.status_label.setText("就绪 | 配置文件已恢复默认，请检查设置")

    def _cleanup_temp_files(self):
        try:
            if os.path.isdir(self.output_dir):
                for fname in os.listdir(self.output_dir):
                    if fname.endswith('.tmp'):
                        try:
                            os.remove(os.path.join(self.output_dir, fname))
                            log.debug(f"已删除残留临时文件: {fname}")
                        except:
                            pass
        except:
            pass

    def _create_actions(self):
        self.add_file_action = QAction("添加表格文件...", self)
        self.add_file_action.setShortcut("Ctrl+O")
        self.add_file_action.triggered.connect(self._add_files)

        self.clear_files_action = QAction("清空文件列表", self)
        self.clear_files_action.triggered.connect(self._clear_files)

        self.open_output_action = QAction("打开输出文件夹", self)
        self.open_output_action.triggered.connect(self._open_output)

        self.import_cfg_action = QAction("导入配置...", self)
        self.import_cfg_action.triggered.connect(self._import_config)

        self.export_cfg_action = QAction("导出配置...", self)
        self.export_cfg_action.triggered.connect(self._export_config)

        self.exit_action = QAction("退出", self)
        self.exit_action.setShortcut("Ctrl+Q")
        self.exit_action.triggered.connect(self.close)

        self.about_action = QAction("关于", self)
        self.about_action.triggered.connect(self._show_about)

        # 暗色主题切换
        self.toggle_theme_action = QAction("暗色主题", self)
        self.toggle_theme_action.setCheckable(True)
        self.toggle_theme_action.setChecked(self._dark_theme)
        self.toggle_theme_action.triggered.connect(self._toggle_dark_theme)

    def _create_menus(self):
        menubar = self.menuBar()

        file_menu = menubar.addMenu("文件")
        file_menu.addAction(self.add_file_action)
        file_menu.addAction(self.clear_files_action)
        file_menu.addSeparator()
        file_menu.addAction(self.open_output_action)
        file_menu.addSeparator()
        file_menu.addAction(self.import_cfg_action)
        file_menu.addAction(self.export_cfg_action)
        file_menu.addSeparator()
        file_menu.addAction(self.exit_action)

        view_menu = menubar.addMenu("视图")
        view_menu.addAction(self.toggle_theme_action)

        help_menu = menubar.addMenu("帮助")
        help_menu.addAction(self.about_action)

    def _create_toolbar(self):
        toolbar = self.addToolBar("工具栏")
        toolbar.addAction(self.open_output_action)

    def _init_central_widget(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(10, 10, 10, 10)

        top_layout = QHBoxLayout()
        self.add_file_btn = QPushButton("添加文件...")
        self.add_file_btn.clicked.connect(self._add_files)
        top_layout.addWidget(self.add_file_btn)

        self.del_btn = QPushButton("删除选中")
        self.del_btn.clicked.connect(self._delete_selected)
        top_layout.addWidget(self.del_btn)

        self.clear_btn = QPushButton("清空列表")
        self.clear_btn.clicked.connect(self._clear_files)
        top_layout.addWidget(self.clear_btn)

        top_layout.addStretch()

        self.quick_mode_cb = QCheckBox("快速模式(跳过预览设置，仍会显示导出确认)")
        self.quick_mode_cb.setChecked(self._quick_mode)
        self.quick_mode_cb.stateChanged.connect(self._toggle_quick_mode)
        top_layout.addWidget(self.quick_mode_cb)
        layout.addLayout(top_layout)

        self.file_list = FileListWidget()
        self.file_list.files_added.connect(self._on_files_dropped)
        layout.addWidget(self.file_list, 1)

        ctrl_layout = QHBoxLayout()
        self.start_btn = QPushButton("开始生成图表")
        self.start_btn.clicked.connect(self._start_processing)
        ctrl_layout.addWidget(self.start_btn)

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel)
        ctrl_layout.addWidget(self.cancel_btn)

        self.progress_bar = QProgressBar()
        ctrl_layout.addWidget(self.progress_bar, 1)

        layout.addLayout(ctrl_layout)

        self.phase_label = QLabel("")
        self.phase_label.setStyleSheet("color: #2196F3; font-weight: bold;")
        layout.addWidget(self.phase_label)

        self.eta_label = QLabel("")
        self.eta_label.setStyleSheet("color: gray;")
        layout.addWidget(self.eta_label)

        splitter = QSplitter(Qt.Vertical)
        log_area_widget = QWidget()
        log_layout = QVBoxLayout(log_area_widget)
        log_layout.setContentsMargins(0, 0, 0, 0)
        self.log_area = QPlainTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setMaximumBlockCount(2000)
        log_layout.addWidget(self.log_area)
        splitter.addWidget(log_area_widget)
        layout.addWidget(splitter, 1)

    def _init_status_bar(self):
        self.status_label = QLabel("就绪 | 可将 Excel/CSV 文件拖入窗口")
        self.statusBar().addWidget(self.status_label, 1)

    # ---------- 暗色主题 ----------
    def _toggle_dark_theme(self):
        self._dark_theme = not self._dark_theme
        self.toggle_theme_action.setChecked(self._dark_theme)
        self.config_manager.set_app('dark_theme', self._dark_theme)
        if self._dark_theme:
            self._apply_dark_theme()
        else:
            self._apply_light_theme()

    def _apply_dark_theme(self):
        dark_palette = QPalette()
        dark_palette.setColor(QPalette.Window, QColor(53, 53, 53))
        dark_palette.setColor(QPalette.WindowText, Qt.white)
        dark_palette.setColor(QPalette.Base, QColor(35, 35, 35))
        dark_palette.setColor(QPalette.AlternateBase, QColor(53, 53, 53))
        dark_palette.setColor(QPalette.ToolTipBase, Qt.white)
        dark_palette.setColor(QPalette.ToolTipText, Qt.white)
        dark_palette.setColor(QPalette.Text, Qt.white)
        dark_palette.setColor(QPalette.Button, QColor(53, 53, 53))
        dark_palette.setColor(QPalette.ButtonText, Qt.white)
        dark_palette.setColor(QPalette.BrightText, Qt.red)
        dark_palette.setColor(QPalette.Link, QColor(42, 130, 218))
        dark_palette.setColor(QPalette.Highlight, QColor(42, 130, 218))
        dark_palette.setColor(QPalette.HighlightedText, Qt.black)
        QApplication.instance().setPalette(dark_palette)

    def _apply_light_theme(self):
        QApplication.instance().setPalette(QApplication.style().standardPalette())

    # ---------- 状态管理 ----------
    def _set_ui_state(self, state):
        if state == 'idle':
            self.start_btn.setEnabled(True)
            self.cancel_btn.setEnabled(False)
            self.file_list.setEnabled(True)
            self.progress_bar.setRange(0, 1)
            self.progress_bar.setValue(0)
            self.phase_label.setText("")
            self.eta_label.setText("")
            if self._taskbar_button:
                self._taskbar_button.progress().setVisible(False)
                self._taskbar_button.progress().setValue(0)
        elif state == 'processing':
            self.start_btn.setEnabled(False)
            self.cancel_btn.setEnabled(True)
            self.file_list.setEnabled(False)
        elif state == 'dialog_open':
            self.start_btn.setEnabled(False)
            self.cancel_btn.setEnabled(True)
            self.file_list.setEnabled(False)
        self._update_start_btn()

    def _on_files_dropped(self, paths):
        existing = set(self.file_list.get_file_paths())
        existing_norm = {os.path.normcase(os.path.normpath(p)) for p in existing}
        added = 0
        for p in paths:
            norm_p = os.path.normcase(os.path.normpath(p))
            if norm_p in existing_norm:
                continue
            try:
                sz = os.path.getsize(p)
                if sz > HUGE_FILE_SIZE:
                    QMessageBox.information(self, "超大文件提示",
                        f"文件 {os.path.basename(p)} 大小 {sz/(1024*1024):.1f}MB，处理可能需要较长时间，建议先检查文件是否完整。")
            except:
                pass
            self.file_list.add_file_item(p)
            self.files.append(p)
            added += 1
        if added:
            self._log(f"已添加 {added} 个文件")
            self.status_label.setText(f"共 {len(self.files)} 个文件待处理")
        self._update_start_btn()

    def _add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择表格文件", "",
            "表格文件 (*.xlsx *.xls *.csv);;所有文件 (*.*)"
        )
        if paths:
            self._on_files_dropped(paths)

    def _clear_files(self):
        if self.files and QMessageBox.question(self, "确认", "清空所有文件？") == QMessageBox.Yes:
            self.files.clear()
            self.file_list.clear_files()
            self._update_start_btn()
            self._log("文件列表已清空")
            self.status_label.setText("就绪")

    def _delete_selected(self):
        items = self.file_list.selectedItems()
        if not items:
            return
        if QMessageBox.question(self, "确认", f"删除选中的 {len(items)} 个文件？") == QMessageBox.Yes:
            for item in reversed(items):
                row = self.file_list.row(item)
                self.file_list.remove_item_by_row(row)
                if row < len(self.files):
                    del self.files[row]
            self._update_start_btn()

    def _update_start_btn(self):
        self.start_btn.setEnabled(len(self.file_list.get_file_paths()) > 0 and not self._processing)

    def _toggle_quick_mode(self):
        self._quick_mode = self.quick_mode_cb.isChecked()
        self.config_manager.set_app('quick_mode', self._quick_mode)

    def _open_output(self):
        if os.path.isdir(self.output_dir):
            try:
                if os.name == 'nt':
                    os.startfile(self.output_dir)
                else:
                    subprocess.run(['xdg-open', self.output_dir])
            except Exception as e:
                QMessageBox.warning(self, "错误", f"无法打开文件夹: {e}")
        else:
            QMessageBox.information(self, "提示", "输出目录尚不存在，生成图表后将自动创建。")

    # ===================== 处理方法（全部在类内） =====================
    def _reset_processing_state(self):
        if self._thread:
            self._thread.cancel()
            if self._thread.is_alive():
                self._thread.join(timeout=3)
            self._thread = None
        if self._gen_cancel_event:
            self._gen_cancel_event.set()
        if self._gen_thread and self._gen_thread.is_alive():
            self._gen_thread.join(timeout=3)
        self._gen_thread = None
        self._gen_cancel_event = None
        if self._gen_signals:
            try:
                self._gen_signals.log.disconnect()
                self._gen_signals.progress.disconnect()
                self._gen_signals.progress_max.disconnect()
                self._gen_signals.status.disconnect()
                self._gen_signals.error.disconnect()
                self._gen_signals.finished.disconnect()
                self._gen_signals.generation_finished.disconnect()
                self._gen_signals.eta.disconnect()
            except:
                pass
            self._gen_signals = None

        self._processing = False
        self._dialog_active = False
        self._set_ui_state('idle')
        self._update_start_btn()
        self.status_label.setText("就绪")
        self.phase_label.setText("")
        self.eta_label.setText("")
        if self._taskbar_button:
            self._taskbar_button.progress().setVisible(False)

    def _start_processing(self):
        if self._processing or (self._thread and self._thread.is_alive()):
            QMessageBox.warning(self, "提示", "已有处理任务正在进行，请等待完成或取消。")
            return
        if not self.file_list.get_file_paths():
            return
        self._processing = True
        self._set_ui_state('processing')
        self.phase_label.setText("当前阶段：文件解析与映射")
        self._log("=" * 40)
        self._log("开始处理流程...")

        out_dir = self.output_dir
        if not os.path.exists(out_dir):
            try:
                os.makedirs(out_dir)
            except Exception as e:
                QMessageBox.critical(self, "错误", f"无法创建输出目录: {e}")
                self._reset_processing_state()
                return
        self._cleanup_temp_files()

        self._thread = ProcessingThread(
            file_paths=self.file_list.get_file_paths(),
            output_dir=out_dir,
            config_manager=self.config_manager,
            csv_encoding=self.config_manager.app_set('last_csv_encoding', 'auto')
        )
        sig = self._thread.signals
        sig.log.connect(self._log)
        sig.progress.connect(self._on_progress_update)
        sig.progress_max.connect(self._set_progress_max)
        sig.status.connect(self.status_label.setText)
        sig.error.connect(self._on_error)
        sig.finished.connect(self._on_thread_finished)
        sig.generation_finished.connect(self._on_generation_finished)
        sig.mapping_request.connect(self._handle_mapping_request)
        sig.preview_request.connect(self._handle_preview_request)
        sig.export_confirm_request.connect(self._handle_export_confirm_request)
        sig.eta.connect(self.eta_label.setText)
        self._thread.start()

    def _handle_mapping_request(self, data):
        fp, sheets = data
        self._dialog_active = True
        self._set_ui_state('dialog_open')
        fname = os.path.basename(fp)
        self.status_label.setText(f"正在配置映射: {fname}")
        self._log(f"弹出映射对话框: {fname}")

        ext = os.path.splitext(fp)[1].lower()
        enc = self.config_manager.app_set('last_csv_encoding', 'auto')
        delimiter = ','
        if ext == '.csv':
            sheet = "CSV文件"
            multi = False
            delimiter = _detect_csv_delimiter(fp)
        else:
            sheet = sheets[0] if sheets else "Sheet1"
            multi = len(sheets) > 1

        batch_multi_file = len(self.file_list.get_file_paths()) > 1

        fmt_type = _detect_file_format_type(
            fp, sheet,
            hrow=0,
            enc=self.config_manager.app_set('last_csv_encoding', 'auto'),
            delimiter=delimiter
        )

        dlg = MappingDialog(self, fp, sheet, cfg=self.config_manager,
                            hrow=0, multi_sheet=multi, enc=enc, delimiter=delimiter,
                            batch_multi_file=batch_multi_file,
                            initial_format_type=fmt_type)
        self._active_mapping_dlg = dlg
        if dlg.exec() == QDialog.Accepted:
            if dlg.result == 'cancel':
                self._thread.set_mapping_result('cancel')
                self._log("用户取消映射")
            elif dlg.result == 'skip':
                self._thread.set_mapping_result('skip')
                self._log(f"跳过文件: {fname}")
            else:
                self._thread.set_mapping_result(dlg.result)
                self._log(f"映射完成: {fname}")
        else:
            self._thread.set_mapping_result('cancel')
            self._log("用户取消映射")
        self._active_mapping_dlg = None
        self._dialog_active = False
        self._set_ui_state('processing')

    def _handle_preview_request(self, data):
        summaries, multiplier, provider = data
        self._last_summaries = summaries
        self._last_data_provider = provider

        if self._quick_mode:
            highlight_default = self.config_manager.app_set('highlight_enabled_default', True)
            settings = {
                'selected_indices': list(range(len(summaries))),
                'global_multiplier': self.config_manager.app_set('last_multiplier', 1.0),
                'output_dir': self.output_dir,
                'show_daily': self.config_manager.app_set('show_daily', True),
                'show_avg': self.config_manager.app_set('show_avg', True),
                'show_max_demand': self.config_manager.app_set('show_max_demand', False),
                'highlight_mode': self.config_manager.app_set('highlight_mode', 'none'),
                'weekday_color': self.config_manager.app_set('weekday_color', DEFAULT_WEEKDAY_COLOR),
                'weekend_color': self.config_manager.app_set('weekend_color', DEFAULT_WEEKEND_COLOR),
                'holiday_color': self.config_manager.app_set('holiday_color', DEFAULT_HOLIDAY_COLOR),
                'weekend_line_width': self.config_manager.app_set('weekend_line_width', DEFAULT_WEEKEND_LINE_WIDTH),
                'holiday_line_width': self.config_manager.app_set('holiday_line_width', DEFAULT_HOLIDAY_LINE_WIDTH),
                'show_weekend_bg': self.config_manager.app_set('show_weekend_bg', False),
                'legend_position': self.config_manager.app_set('legend_position', 'right'),
                'mark_anomalies': self.config_manager.app_set('mark_anomalies', False),
                'strategy_name': self.config_manager.active_strat(),
                'title_template': self.config_manager.app_set('last_title_template', '{table_no}_{year_month}_负荷曲线'),
                'filename_template': self.config_manager.app_set('last_filename_template', '{table_no}_{year_month}_负荷曲线'),
                'classify_by_asset': self.config_manager.app_set('classify_by_asset', False),
                'billing_dict': {},
                'per_group_multiplier': {},
                'per_group_billing': {},
                'price_periods': {},
                'highlight_flags': {idx: highlight_default for idx in range(len(summaries))}
            }
            sn = settings['strategy_name']
            if sn:
                strat = self.config_manager.get_strat(sn)
                if strat:
                    settings['price_periods'] = strat
            self._thread.set_preview_result(settings)
            self._log("快速模式：跳过预览，使用保存的默认参数")
            return

        self._dialog_active = True
        self._set_ui_state('dialog_open')
        self.phase_label.setText("当前阶段：预览与参数设置")
        dlg = PreviewDialog(
            self, summaries, self.config_manager,
            initial_multiplier=self.config_manager.app_set('last_multiplier', 1.0),
            initial_show_max_demand=False,
            initial_strategy_name=None,
            initial_output_dir=self.output_dir,
            data_provider=provider
        )
        dlg.setAttribute(Qt.WA_DeleteOnClose, False)
        self._preview_dlg = dlg
        if dlg.exec() == QDialog.Accepted and dlg.result:
            self.output_dir = dlg.result.get('output_dir', self.output_dir)
            self.config_manager.set_app('last_output_dir', self.output_dir)
            self.config_manager.set_app('last_multiplier', dlg.result.get('global_multiplier', 1.0))
            highlight_flags = dlg.result.get('highlight_flags', {})
            if highlight_flags:
                some_enabled = any(v for v in highlight_flags.values())
                self.config_manager.set_app('highlight_enabled_default', some_enabled)
            settings = {
                'selected_indices': dlg.result['selected_indices'],
                'global_multiplier': dlg.result.get('global_multiplier', multiplier),
                'output_dir': self.output_dir,
                'show_daily': dlg.result['show_daily'],
                'show_avg': dlg.result['show_avg'],
                'show_max_demand': dlg.result.get('show_max_demand', False),
                'highlight_mode': dlg.result.get('highlight_mode', 'none'),
                'weekday_color': dlg.result.get('weekday_color', DEFAULT_WEEKDAY_COLOR),
                'weekend_color': dlg.result.get('weekend_color', DEFAULT_WEEKEND_COLOR),
                'holiday_color': dlg.result.get('holiday_color', DEFAULT_HOLIDAY_COLOR),
                'weekend_line_width': dlg.result.get('weekend_line_width', DEFAULT_WEEKEND_LINE_WIDTH),
                'holiday_line_width': dlg.result.get('holiday_line_width', DEFAULT_HOLIDAY_LINE_WIDTH),
                'show_weekend_bg': dlg.result.get('show_weekend_bg', False),
                'legend_position': dlg.result.get('legend_position', 'right'),
                'mark_anomalies': dlg.result.get('mark_anomalies', False),
                'strategy_name': dlg.result.get('strategy_name'),
                'title_template': dlg.result['title_template'],
                'filename_template': dlg.result['filename_template'],
                'classify_by_asset': dlg.result.get('classify_by_asset', False),
                'billing_dict': dlg.result.get('billing_dict', {}),
                'per_group_multiplier': dlg.result.get('per_group_multiplier', {}),
                'per_group_billing': dlg.result.get('per_group_billing', {}),
                'plot_options': {
                    'show_daily': dlg.result['show_daily'],
                    'show_avg': dlg.result['show_avg'],
                    'show_max_demand': dlg.result.get('show_max_demand', False),
                    'fit_types': [],
                    'highlight_mode': dlg.result.get('highlight_mode', 'none'),
                    'weekday_color': dlg.result.get('weekday_color', DEFAULT_WEEKDAY_COLOR),
                    'weekend_color': dlg.result.get('weekend_color', DEFAULT_WEEKEND_COLOR),
                    'holiday_color': dlg.result.get('holiday_color', DEFAULT_HOLIDAY_COLOR),
                    'weekend_line_width': dlg.result.get('weekend_line_width', DEFAULT_WEEKEND_LINE_WIDTH),
                    'holiday_line_width': dlg.result.get('holiday_line_width', DEFAULT_HOLIDAY_LINE_WIDTH),
                    'show_weekend_bg': dlg.result.get('show_weekend_bg', False),
                    'legend_position': dlg.result.get('legend_position', 'right'),
                    'mark_anomalies': dlg.result.get('mark_anomalies', False),
                },
                'price_periods': {},
                'highlight_flags': dlg.result.get('highlight_flags', {idx: True for idx in dlg.result['selected_indices']})
            }
            sn = settings['strategy_name']
            if sn:
                strat = self.config_manager.get_strat(sn)
                if strat:
                    settings['price_periods'] = strat
            self._last_preview_settings = settings
            self._thread.set_preview_result(settings)
            self.phase_label.setText("当前阶段：正在生成图表...")
        else:
            self._thread.set_preview_result(None)
            self._preview_dlg = None
        self._dialog_active = False
        self._set_ui_state('processing')

    def _handle_export_confirm_request(self, data):
        self._dialog_active = True
        self._set_ui_state('dialog_open')
        highlight_flags = data.get('highlight_flags', {})
        dlg = ExportConfirmDialog(
            self,
            data['all_summaries'],
            data['selected_indices'],
            data['filename_tpl'],
            data['title_tpl'],
            data['output_dir'],
            data['classify'],
            per_group_multiplier=data['per_group_multiplier'],
            per_group_billing=data['per_group_billing'],
            billing_dict=data['billing_dict'],
            global_multiplier=data['global_multiplier'],
            config_manager=data['config_manager'],
            highlight_flags=highlight_flags
        )
        if dlg.exec() == QDialog.Accepted and dlg.result:
            if 'output_dir' in dlg.result:
                self.output_dir = dlg.result['output_dir']
                self.config_manager.set_app('last_output_dir', self.output_dir)
            self._thread.set_export_confirm_result(dlg.result)
            self._log("用户确认生成清单")
        else:
            if dlg.return_to_preview:
                modified_flags = dlg.result.get('highlight_flags', data.get('highlight_flags', {}))
                self._thread._last_highlight_flags = modified_flags
                self._thread.set_export_confirm_result('return_preview')
                self._log("用户从确认清单返回预览窗口")
            else:
                self._thread.set_export_confirm_result(None)
                self._log("用户取消生成")
        self._dialog_active = False
        self._set_ui_state('processing')

    def _on_generation_finished(self, result):
        paths, failed = result
        self._log(f"成功生成 {len(paths)} 张图表")
        if failed:
            self._log(f"失败 {len(failed)} 项")
            fail_str = "\n".join(f"- {f}" for f in failed)
            QMessageBox.warning(self, "部分图表生成失败", f"以下图表生成失败:\n{fail_str}")
        self.progress_bar.setValue(self.progress_bar.maximum())
        self.phase_label.setText("生成完毕")
        self.eta_label.setText("")
        if self._taskbar_button:
            self._taskbar_button.progress().setValue(self.progress_bar.maximum())
            self._taskbar_button.progress().setVisible(False)
        if self.config_manager.app_set('auto_open_dir', True) and paths:
            try:
                if os.name == 'nt':
                    os.startfile(self.output_dir)
                else:
                    subprocess.run(['xdg-open', self.output_dir])
            except Exception as e:
                self._log(f"无法打开输出目录: {e}")

        # 生成 HTML 报告
        report_path = self._generate_report(paths, failed)
        self.status_label.setText(f"完成，已生成 {len(paths)} 张图表至 {self.output_dir}")
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("生成完成")
        msg_box.setText(f"图表已成功生成至:\n{self.output_dir}\n共 {len(paths)} 张\n报告: {report_path}")
        open_btn = msg_box.addButton("打开文件夹", QMessageBox.ActionRole)
        copy_btn = msg_box.addButton("复制路径", QMessageBox.ActionRole)
        report_btn = msg_box.addButton("打开报告", QMessageBox.ActionRole)
        return_preview_btn = None
        if self._preview_dlg is not None:
            return_preview_btn = msg_box.addButton("返回预览窗口", QMessageBox.ActionRole)
        msg_box.addButton(QMessageBox.Ok)
        msg_box.exec()

        clicked = msg_box.clickedButton()
        if clicked == open_btn:
            self._open_output()
        elif clicked == copy_btn:
            QApplication.clipboard().setText(self.output_dir)
            QMessageBox.information(self, "已复制", f"路径已复制到剪贴板:\n{self.output_dir}")
        elif clicked == report_btn:
            try:
                if os.name == 'nt':
                    os.startfile(report_path)
                else:
                    subprocess.run(['xdg-open', report_path])
            except:
                pass
        elif clicked == return_preview_btn:
            self._return_to_preview()

    def _generate_report(self, paths, failed):
        """生成 HTML 报告文件并返回路径"""
        report_path = os.path.join(self.output_dir, "生成报告.html")
        try:
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            success_count = len(paths)
            fail_count = len(failed)
            total = success_count + fail_count

            html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>负荷曲线生成报告</title>
<style>
body {{ font-family: 'Microsoft YaHei', sans-serif; margin: 40px; }}
h1 {{ color: #333; }}
.success {{ color: green; }}
.failure {{ color: red; }}
table {{ border-collapse: collapse; width: 100%; margin-top: 20px; }}
th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
th {{ background-color: #4CAF50; color: white; }}
tr:nth-child(even) {{ background-color: #f2f2f2; }}
</style>
</head>
<body>
<h1>负荷曲线批量生成报告</h1>
<p>生成时间: {now}</p>
<p>输出目录: {self.output_dir}</p>
<p>成功 <span class="success">{success_count}</span> 张，失败 <span class="failure">{fail_count}</span> 张，总计 {total} 张。</p>
"""

            if success_count > 0:
                html += "<h2>成功列表</h2><table><tr><th>序号</th><th>文件名</th><th>表号</th><th>月份</th><th>源文件</th></tr>"
                for i, p in enumerate(paths, 1):
                    info = {}
                    for s in self._thread.all_summaries if self._thread else []:
                        if os.path.basename(p).startswith(_sanitize_name(s.asset_value)):
                            info = {'asset': s.asset_value, 'month': s.month_str, 'file': os.path.basename(s.file_path)}
                            break
                    html += f"<tr><td>{i}</td><td>{os.path.basename(p)}</td><td>{info.get('asset','?')}</td><td>{info.get('month','?')}</td><td>{info.get('file','?')}</td></tr>"
                html += "</table>"

            if fail_count > 0:
                html += "<h2>失败列表</h2><ul>"
                for f in failed:
                    html += f"<li class='failure'>{f}</li>"
                html += "</ul>"

            html += "</body></html>"

            with open(report_path, 'w', encoding='utf-8') as f:
                f.write(html)
            self._log(f"生成报告已保存至: {report_path}")
            return report_path
        except Exception as e:
            self._log(f"生成报告失败: {e}")
            return report_path

    def _return_to_preview(self):
        if not self._preview_dlg:
            return
        self._log("返回预览窗口，保留所有参数与缓存...")
        self._preview_dlg.show()
        self._preview_dlg.raise_()
        if self._preview_dlg.exec() == QDialog.Accepted and self._preview_dlg.result:
            settings = self._preview_dlg.result
            self.output_dir = settings.get('output_dir', self.output_dir)
            self.config_manager.set_app('last_output_dir', self.output_dir)
            self.config_manager.set_app('last_multiplier', settings.get('global_multiplier', 1.0))
            generate_settings = {
                'selected_indices': settings['selected_indices'],
                'global_multiplier': settings.get('global_multiplier', 1.0),
                'output_dir': self.output_dir,
                'show_daily': settings['show_daily'],
                'show_avg': settings['show_avg'],
                'show_max_demand': settings.get('show_max_demand', False),
                'highlight_mode': settings.get('highlight_mode', 'none'),
                'weekday_color': settings.get('weekday_color', DEFAULT_WEEKDAY_COLOR),
                'weekend_color': settings.get('weekend_color', DEFAULT_WEEKEND_COLOR),
                'holiday_color': settings.get('holiday_color', DEFAULT_HOLIDAY_COLOR),
                'weekend_line_width': settings.get('weekend_line_width', DEFAULT_WEEKEND_LINE_WIDTH),
                'holiday_line_width': settings.get('holiday_line_width', DEFAULT_HOLIDAY_LINE_WIDTH),
                'show_weekend_bg': settings.get('show_weekend_bg', False),
                'legend_position': settings.get('legend_position', 'right'),
                'mark_anomalies': settings.get('mark_anomalies', False),
                'strategy_name': settings.get('strategy_name'),
                'title_template': settings['title_template'],
                'filename_template': settings['filename_template'],
                'classify_by_asset': settings.get('classify_by_asset', False),
                'billing_dict': settings.get('billing_dict', {}),
                'per_group_multiplier': settings.get('per_group_multiplier', {}),
                'per_group_billing': settings.get('per_group_billing', {}),
                'plot_options': {
                    'show_daily': settings['show_daily'],
                    'show_avg': settings['show_avg'],
                    'show_max_demand': settings.get('show_max_demand', False),
                    'fit_types': [],
                    'highlight_mode': settings.get('highlight_mode', 'none'),
                    'weekday_color': settings.get('weekday_color', DEFAULT_WEEKDAY_COLOR),
                    'weekend_color': settings.get('weekend_color', DEFAULT_WEEKEND_COLOR),
                    'holiday_color': settings.get('holiday_color', DEFAULT_HOLIDAY_COLOR),
                    'weekend_line_width': settings.get('weekend_line_width', DEFAULT_WEEKEND_LINE_WIDTH),
                    'holiday_line_width': settings.get('holiday_line_width', DEFAULT_HOLIDAY_LINE_WIDTH),
                    'show_weekend_bg': settings.get('show_weekend_bg', False),
                    'legend_position': settings.get('legend_position', 'right'),
                    'mark_anomalies': settings.get('mark_anomalies', False),
                },
                'price_periods': {},
                'highlight_flags': self._thread._last_highlight_flags if self._thread and self._thread._last_highlight_flags else {idx: True for idx in settings['selected_indices']}
            }
            sn = generate_settings['strategy_name']
            if sn:
                strat = self.config_manager.get_strat(sn)
                if strat:
                    generate_settings['price_periods'] = strat
            self._start_chart_generation(generate_settings)
        else:
            self._log("用户退出预览窗口")

    def _start_chart_generation(self, settings):
        if not self._last_summaries or not self._last_data_provider:
            QMessageBox.warning(self, "错误", "无法获取上次扫描的数据，请重新开始处理。")
            return
        self._processing = True
        self._set_ui_state('processing')
        self.phase_label.setText("当前阶段：正在生成图表...")
        self._log("开始重新生成图表...")

        self._gen_signals = ProcessingSignals()
        self._gen_signals.log.connect(self._log)
        self._gen_signals.progress.connect(self._on_progress_update)
        self._gen_signals.progress_max.connect(self._set_progress_max)
        self._gen_signals.status.connect(self.status_label.setText)
        self._gen_signals.error.connect(self._on_error)
        self._gen_signals.finished.connect(self._on_gen_finished)
        self._gen_signals.generation_finished.connect(self._on_generation_finished)
        self._gen_signals.eta.connect(self.eta_label.setText)

        self._gen_cancel_event = threading.Event()
        self._gen_thread = threading.Thread(
            target=self._generation_only_thread,
            args=(settings, self._gen_signals, self._gen_cancel_event),
            daemon=True
        )
        self._gen_thread.start()

    def _on_gen_finished(self):
        if self._gen_signals:
            try:
                self._gen_signals.log.disconnect(self._log)
                self._gen_signals.progress.disconnect(self._on_progress_update)
                self._gen_signals.progress_max.disconnect(self._set_progress_max)
                self._gen_signals.status.disconnect(self.status_label.setText)
                self._gen_signals.error.disconnect(self._on_error)
                self._gen_signals.finished.disconnect(self._on_gen_finished)
                self._gen_signals.generation_finished.disconnect(self._on_generation_finished)
                self._gen_signals.eta.disconnect(self.eta_label.setText)
            except:
                pass
            self._gen_signals = None
        self._gen_thread = None
        self._gen_cancel_event = None

    def _generation_only_thread(self, settings, signals, cancel_event):
        try:
            provider = self._last_data_provider
            summaries = self._last_summaries
            selected_indices = settings['selected_indices']
            output_dir = settings['output_dir']
            title_template = settings['title_template']
            filename_template = settings['filename_template']
            classify = settings['classify_by_asset']
            plot_options = settings['plot_options']
            global_mult = settings['global_multiplier']
            per_mult = settings.get('per_group_multiplier', {})
            per_bill = settings.get('per_group_billing', {})
            billing_dict = settings.get('billing_dict', {})
            price_periods = settings.get('price_periods', {})
            highlight_flags = settings.get('highlight_flags', {})

            tasks = []
            for orig_idx in selected_indices:
                if cancel_event.is_set():
                    signals.log.emit("生成被取消")
                    return
                summary = summaries[orig_idx]
                data = provider.get_group_data(summary)
                if data:
                    mult = per_mult.get(orig_idx, global_mult)
                    bill = per_bill.get(orig_idx) or billing_dict.get(orig_idx)
                    tasks.append((orig_idx, data, mult, bill))
                else:
                    error_msg = provider.get_load_error(summary)
                    signals.log.emit(f"加载失败: {summary.asset_value} - {summary.month_str} ({error_msg})")

            if not tasks:
                signals.log.emit("没有有效数据，无法生成")
                signals.finished.emit()
                return

            worker_args = []
            for orig_idx, data, mult, bill in tasks:
                opts = plot_options.copy()
                if not highlight_flags.get(orig_idx, True):
                    opts['highlight_mode'] = 'none'
                worker_args.append((data, title_template, filename_template,
                                    output_dir, classify, opts,
                                    bill, price_periods, mult))

            manager = mp.Manager()
            progress_queue = manager.Queue()
            try:
                all_paths, failed = _execute_plot_tasks(
                    worker_args, self.config_manager, signals, cancel_event,
                    progress_queue, total_charts=len(tasks),
                    actual_workers=getattr(self, '_plot_worker_count', self.config_manager.get_plot_processes())
                )
            finally:
                manager.shutdown()

            signals.generation_finished.emit((all_paths, failed))
        except Exception as e:
            signals.error.emit(_translate_error(type(e), str(e)))
        finally:
            signals.finished.emit()

    # ---------- 其他辅助方法 ----------
    def _set_progress_max(self, val):
        self.progress_bar.setRange(0, val)
        self.progress_bar.setValue(0)
        if self._taskbar_button:
            self._taskbar_button.progress().setVisible(True)
            self._taskbar_button.progress().setRange(0, val)

    def _on_progress_update(self, val):
        self.progress_bar.setValue(val)
        if self._taskbar_button:
            self._taskbar_button.progress().setValue(val)

    def _on_error(self, msg):
        self._log(f"错误: {msg}")
        QMessageBox.critical(self, "处理错误", f"流程发生错误:\n{msg}")
        self._reset_processing_state()

    def _on_thread_finished(self):
        self._log("处理线程已退出")
        self._processing = False
        self._dialog_active = False
        self._set_ui_state('idle')
        self._update_start_btn()
        self.status_label.setText("就绪")
        self.phase_label.setText("")

    def _cancel(self):
        self._log("用户取消操作")
        if self._active_mapping_dlg is not None:
            try:
                self._active_mapping_dlg.reject()
            except:
                pass
        if self._preview_dlg is not None and self._preview_dlg.isVisible():
            try:
                self._preview_dlg.reject()
            except:
                pass
        if self._gen_cancel_event:
            self._gen_cancel_event.set()
        self._reset_processing_state()

    def _log(self, msg):
        ts = datetime.now().strftime('%H:%M:%S')
        self.log_area.appendPlainText(f"[{ts}] {msg}")
        log.info(msg)

    def _import_config(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入配置", "", "JSON配置文件 (*.json)")
        if not path:
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                new_cfg = json.load(f)
            if QMessageBox.question(self, "确认", "导入将覆盖当前配置，继续？") == QMessageBox.Yes:
                self.config_manager.cfg = new_cfg
                self.config_manager._migrate()
                self.config_manager.save()
                QMessageBox.information(self, "完成", "配置已导入")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"导入失败: {e}")

    def _export_config(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出配置", "", "JSON配置文件 (*.json)")
        if not path:
            return
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(self.config_manager.cfg, f, ensure_ascii=False, indent=2)
            QMessageBox.information(self, "完成", "配置已导出")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"导出失败: {e}")

    def _show_about(self):
        import importlib.util
        scipy_status = "已安装" if importlib.util.find_spec("scipy") else "未安装 (部分拟合功能不可用)"
        chardet_status = "已安装" if importlib.util.find_spec("chardet") else "未安装 (编码检测降级)"
        holiday_status = "已安装" if importlib.util.find_spec("chinese_calendar") else "未安装 (节假日识别不可用)"
        taskbar_status = "支持" if self._taskbar_button else "不支持"
        QMessageBox.information(self, "关于",
            f"负荷曲线批量生成工具 Pro 融合版 v2.1\n\n"
            f"新特性：\n"
            f"• 新增正向有功示数表独立格式，智能识别并自动差分\n"
            f"• 对话框尺寸自适应优化，防止控件显示不全\n"
            f"• 累计示数自动转功率\n"
            f"• 异常数据可视化标记\n"
            f"• 暗色主题切换\n"
            f"• 生成报告自动输出\n"
            f"• 模板独立导入/导出\n"
            f"• 快速模式高亮记忆\n"
            f"• 配置版本 v13\n"
            f"\n依赖状态:\n"
            f"• scipy: {scipy_status}\n"
            f"• chardet: {chardet_status}\n"
            f"• chinesecalendar: {holiday_status}"
        )

    def _gui_exception_handler(self, exc_type, exc_value, exc_tb):
        log.critical(f"GUI异常:\n{''.join(traceback.format_exception(exc_type, exc_value, exc_tb))}")
        QMessageBox.critical(self, "致命错误", f"程序遇到未处理异常:\n{''.join(traceback.format_exception(exc_type, exc_value, exc_tb))}\n建议重启程序。")

    # 事件重写
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._overlay.setVisible(True)
            self._overlay.raise_()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dragLeaveEvent(self, event):
        self._overlay.setVisible(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self._overlay.setVisible(False)
        if event.mimeData().hasUrls():
            paths = []
            for url in event.mimeData().urls():
                p = url.toLocalFile()
                if os.path.isfile(p) and p.lower().endswith(SUPPORTED_EXTS):
                    paths.append(p)
                elif os.path.isdir(p):
                    for root, _, files in os.walk(p):
                        for f in files:
                            if f.lower().endswith(SUPPORTED_EXTS):
                                paths.append(os.path.join(root, f))
            if paths:
                self._on_files_dropped(paths)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, '_overlay'):
            self._overlay.setGeometry(self.rect())

    def closeEvent(self, event):
        if self._processing or (self._thread and self._thread.is_alive()):
            reply = QMessageBox.question(
                self, "确认退出",
                "正在处理文件，如果退出，未完成的任务将会丢失。\n确定要退出吗？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if reply == QMessageBox.No:
                event.ignore()
                return
        self._reset_processing_state()
        if self._preview_dlg:
            self._preview_dlg.close()
            self._preview_dlg = None
        self.config_manager.set_window_geometry("MainWindow", self.saveGeometry())
        self.config_manager.save()
        super().closeEvent(event)

# 第四部分结束
# -*- coding: utf-8 -*-
"""
负荷曲线批量生成工具 Pro 融合版 (智能增强版 v2.1)
第五部分：主入口

注意：所有模块均已在前面四个部分中定义，本部分仅提供程序启动入口。
"""

# ======================= 主入口 =======================
def main():
    mp.freeze_support()

    _check_and_install_dependencies()

    # 清理旧日志文件，保留最近10个
    try:
        log_dir = "logs"
        if os.path.isdir(log_dir):
            log_files = [f for f in os.listdir(log_dir) if f.endswith(".txt") and f.startswith("log_")]
            log_files.sort()
            while len(log_files) > 10:
                oldest = log_files.pop(0)
                os.remove(os.path.join(log_dir, oldest))
                log.debug(f"已删除旧日志文件: {oldest}")
    except Exception as e:
        log.debug(f"日志清理异常: {e}")

    try:
        _setup_font()
    except Exception as e:
        print(f"字体初始化警告: {e}")

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName("负荷曲线批量生成工具 Pro")
    app.setOrganizationName("PowerCurve")

    apply_global_stylesheet(app)

    try:
        log_dir = "logs"
        os.makedirs(log_dir, exist_ok=True)
        timestamp_log = os.path.join(log_dir, f"log_{datetime.now():%Y%m%d_%H%M%S}.txt")
        file_handler = logging.FileHandler(timestamp_log, encoding='utf-8')
        file_handler.setFormatter(log_formatter)
        logging.getLogger(__name__).addHandler(file_handler)
    except Exception as e:
        print(f"创建时间戳日志文件失败: {e}")

    window = MainWindow()
    if window._taskbar_button:
        window._taskbar_button.setWindow(window)

    window.show()

    exit_code = app.exec()
    log.info("程序正常退出")
    return exit_code

if __name__ == '__main__':
    sys.exit(main())
# 全程序结束
