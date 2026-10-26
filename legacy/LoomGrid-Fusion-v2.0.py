# -*- coding: utf-8 -*-
"""
负荷曲线批量生成工具 Pro 融合版 (最终优化版)
第一部分：基础模块、配置、映射、数据读取与扫描
优化点：
- 资产列扫描与加载阶段统一 .str.strip()
- 长表日期解析移除过度推断，恢复 pandas 自动推断
- 宽表视觉矩阵检测（智能识别宽表负荷块）
- 列映射对话框模糊匹配高亮反馈
- CSV 编码回退链（C 引擎优先，失败回退 Python）
- 超大文件提示
- 配置文件完整性校验
- 日期解析失败率警告
- 绘制异常详细分类
- 通俗化技术错误提示
- 配置文件完全损坏时安全默认值
- 缓存容量提升，扫描缓存传递至 DataProvider，消除重复解析
- ThreadSafeCache 增加线程安全的 items() 方法
- 宽表负荷列缺失时输出警告日志
- 新增充电/放电空间计算函数（支持需量管理、光伏消纳、混合模式）
"""

import sys, os, re, json, time, io, logging, atexit, warnings, subprocess, traceback, gc
import logging.handlers
import multiprocessing as mp
import threading
import csv
import shutil
import tempfile
from datetime import datetime, time as dt_time
from collections import OrderedDict, defaultdict
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
import numpy as np
import pandas as pd

# -------------------- PySide6 统一导入 --------------------
from PySide6.QtCore import (
    QObject, Qt, Signal, Slot, QTimer, QThreadPool, QRunnable, QSemaphore, QByteArray,
    QSize, QPoint, QRect, QEvent, QMutex, QMutexLocker,
    QPropertyAnimation, QEasingCurve, QUrl
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QListWidget, QListWidgetItem, QPushButton, QLabel, QProgressBar,
    QFileDialog, QMessageBox, QDialog, QComboBox,
    QLineEdit, QSpinBox, QDoubleSpinBox, QCheckBox, QRadioButton, QButtonGroup,
    QGroupBox, QSplitter, QScrollArea, QGridLayout, QStatusBar,
    QToolBar, QTreeWidget, QTreeWidgetItem, QDialogButtonBox, QPlainTextEdit, QFrame,
    QGraphicsScene, QGraphicsView, QMenu, QInputDialog, QSizeGrip, QStyle,
    QSizePolicy, QHeaderView, QMenuBar, QGraphicsDropShadowEffect,
    QToolTip, QScrollBar, QTableWidget, QTableWidgetItem, QAbstractItemView,
    QFormLayout          # ← 添加这一行
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
    """检查可选依赖，缺失时尝试自动安装（需用户确认）"""
    missing = []
    try:
        from scipy import interpolate, ndimage
    except ImportError:
        missing.append('scipy')
    try:
        import chardet
    except ImportError:
        missing.append('chardet')
    if not missing:
        return

    msg = "检测到以下可选依赖未安装，可能影响部分功能（如曲线平滑、编码检测）：\n"
    msg += "\n".join(f"  - {pkg}" for pkg in missing)
    msg += "\n\n是否尝试自动安装？（需要网络连接，约几秒钟）"
    user_input = input(f"{msg}\n输入 y 安装，其他键跳过：").strip().lower()
    if user_input == 'y':
        for pkg in missing:
            print(f"正在安装 {pkg}...")
            try:
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install", pkg, "--user"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT
                )
                print(f"{pkg} 安装成功")
            except subprocess.CalledProcessError as e:
                print(f"安装 {pkg} 失败: {e}，请手动执行 pip install {pkg}")
    else:
        print("已跳过自动安装，部分功能可能受限。")

# ======================= 常量 =======================
INVALID_MINUTES = 9999
MAX_FILENAME_LEN = 50
DEFAULT_ENCODING_LIST = ['utf-8-sig', 'utf-8', 'gb18030', 'gbk', 'gb2312', 'latin1']
SUPPORTED_EXTS = ('.csv', '.xlsx', '.xls')
PERIOD_CHARGE = 'charge'
PERIOD_DISCHARGE = 'discharge'
MAX_SCAN_WORKERS = min(4, max(1, mp.cpu_count() - 1))
MAX_PLOT_PROCESSES = min(4, max(1, mp.cpu_count() - 1))
MAX_PRELOAD_THREADS = min(4, max(1, mp.cpu_count()))
LARGE_FILE_SIZE = 50 * 1024 * 1024   # 50 MB
HUGE_FILE_SIZE = 200 * 1024 * 1024  # 200 MB 提示
TIME_CACHE_MAXSIZE = 5000
DATA_CACHE_MAXSIZE = 60
FILE_CACHE_MAXSIZE = 20
PREVIEW_CACHE_MAXSIZE = 100
MAX_FILENAME_ATTEMPTS = 1000

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

def _find_cjk_font():
    try:
        fontManager.__init__()
    except:
        pass
    available = {f.name for f in fontManager.ttflist}
    for name in _CJK_FONT_CANDIDATES:
        if name in available:
            return name
    for f in fontManager.ttflist:
        if any(k in f.name.lower() for k in ['cjk', 'chinese', 'simhei', 'simsun', 'yahei', 'songti', 'heiti']):
            return f.name
    log.warning("未找到任何中文字体！图表中文将显示为方框，请安装中文字体。")
    return 'DejaVu Sans'

_FONT_NAME = None

def _get_cached_font():
    global _FONT_NAME
    if _FONT_NAME:
        return _FONT_NAME
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
    """安全获取列，若缺失则模糊匹配最相似列，并记录日志"""
    if col_name not in df.columns:
        import difflib
        best_score = 0
        best_col = None
        for c in df.columns:
            score = difflib.SequenceMatcher(None, col_name.lower(), str(c).lower()).ratio()
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

def _parse_min(t) -> int:
    if isinstance(t, dt_time):
        minutes = t.hour*60 + t.minute
        TIME_CACHE.put(str(t), (minutes, f"{minutes//60:02d}:{minutes%60:02d}"))
        return minutes
    key = str(t).strip()
    cached = TIME_CACHE.get(key)
    if cached is not None:
        return cached[0]
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
    try:
        return pd.to_datetime(series, format=fmt, errors='coerce') if fmt else pd.to_datetime(series, errors='coerce')
    except:
        return pd.to_datetime(series, errors='coerce')

def _valid_time_cols(cols):
    valid = []
    for c in cols:
        m = _parse_min(c)
        if m!=INVALID_MINUTES:
            valid.append((m,c))
    valid.sort(key=lambda x: x[0])
    return [c for _,c in valid]

# ======================= 宽表视觉矩阵检测 =======================
def _detect_wide_matrix(df: pd.DataFrame, cols, max_sample=50):
    """在样本数据中寻找宽表负荷矩阵。"""
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

# ======================= 数据智能分类 =======================
def _smart_classify_columns(cols, sample_df=None, max_sample=50):
    result = {}
    date_kw = ['日期','date','时间戳','timestamp']
    time_kw = ['时间','time','时刻','时分']
    asset_kw = ['资产','户号','编号','表号','id','用户','meter']
    load_kw = ['负荷','负载','功率','load','有功','无功','电流','电压']

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

    if sample_df is not None and not sample_df.empty:
        wdate, wload = _detect_wide_matrix(sample_df, cols, max_sample)
        if wdate and wload:
            result['date_col'] = wdate
            result['load_col'] = wload
            result['format_type'] = 'wide'
    return result

# ======================= 充电/放电空间计算函数（增强版） =======================
# ======================= 充电/放电空间计算函数（高性能向量化 + 缓存） =======================
def compute_spaces_for_summary(summary, provider, multiplier, billing, price_strategy,
                               valid_threshold=0.01, mode='fixed', clip_negative=False, P_rated=None,
                               cached_data=None):
    """
    计算单个分组的每日充电空间、放电空间及 daily_min（单位 kWh）。
    参数:
        ...
        cached_data: 可选的已加载数据 dict，若提供则跳过 provider.get_group_data。
    返回:
        dict
    """
    # ---- 前置检查 ----
    if mode == 'fixed':
        if billing is None or billing.get('value') is None:
            raise ValueError("需量管理模式下必须设置容量线")
        if not price_strategy:
            raise ValueError("需量管理模式下必须设置充放策略")
        cap_value = billing['value']
    elif mode == 'pv_self':
        if P_rated is None or P_rated <= 0:
            raise ValueError("纯光伏消纳模式下必须设置储能额定功率")
    elif mode == 'pv_peak':
        if P_rated is None or P_rated <= 0:
            raise ValueError("混合模式下必须设置储能额定功率")
        cap_value = billing['value'] if billing and billing.get('value') else np.inf
        if not price_strategy:
            raise ValueError("混合模式下必须设置放电时段")
    else:
        raise ValueError(f"未知模式: {mode}")

    if cached_data is not None:
        data = cached_data
    else:
        data = provider.get_group_data(summary)
        if not data:
            raise ValueError("无法加载分组数据")

    load_array = np.array(data['load_array']) * multiplier
    time_labels = data['time_labels']
    n_dates, n_times = load_array.shape

    # 时间标签 → 小时数，并排序
    time_hours = np.array([_parse_min(tl) / 60.0 for tl in time_labels])
    sort_idx = np.argsort(time_hours)
    time_hours = time_hours[sort_idx]
    load_array = load_array[:, sort_idx]

    if mode == 'fixed' and clip_negative:
        load_array = np.clip(load_array, 0, None)

    # 预计算差分时间
    dt = np.diff(time_hours)

    daily_charge = np.zeros(n_dates)
    daily_discharge = np.zeros(n_dates)

    if mode == 'fixed':
        expanded = _expand_overnight(price_strategy)
        charge_periods = [p for p in expanded if p['type'] == 'charge']
        discharge_periods = [p for p in expanded if p['type'] == 'discharge']

        for p in charge_periods:
            mask = (time_hours >= p['start']) & (time_hours <= p['end'])
            if mask.sum() < 2:
                continue
            vals = np.maximum(0, cap_value - load_array[:, mask])
            dt_sel = dt[mask[:-1]]
            y_avg = (vals[:, :-1] + vals[:, 1:]) * 0.5
            daily_charge += np.dot(y_avg, dt_sel)

        for p in discharge_periods:
            mask = (time_hours >= p['start']) & (time_hours <= p['end'])
            if mask.sum() < 2:
                continue
            vals = np.maximum(0, load_array[:, mask] - cap_value)
            dt_sel = dt[mask[:-1]]
            y_avg = (vals[:, :-1] + vals[:, 1:]) * 0.5
            daily_discharge += np.dot(y_avg, dt_sel)

    elif mode == 'pv_self':
        charge_vals = np.minimum(P_rated, np.maximum(0, -load_array))
        discharge_vals = np.minimum(P_rated, np.maximum(0, load_array))
        y_avg_charge = (charge_vals[:, :-1] + charge_vals[:, 1:]) * 0.5
        daily_charge = np.dot(y_avg_charge, dt)
        y_avg_discharge = (discharge_vals[:, :-1] + discharge_vals[:, 1:]) * 0.5
        daily_discharge = np.dot(y_avg_discharge, dt)

    elif mode == 'pv_peak':
        charge_vals = np.minimum(P_rated, np.maximum(0, -load_array))
        y_avg_charge = (charge_vals[:, :-1] + charge_vals[:, 1:]) * 0.5
        daily_charge = np.dot(y_avg_charge, dt)

        expanded = _expand_overnight(price_strategy)
        discharge_periods = [p for p in expanded if p['type'] == 'discharge']
        for p in discharge_periods:
            mask = (time_hours >= p['start']) & (time_hours <= p['end'])
            if mask.sum() < 2:
                continue
            vals = np.minimum(P_rated, np.maximum(0, load_array[:, mask] - cap_value))
            dt_sel = dt[mask[:-1]]
            y_avg = (vals[:, :-1] + vals[:, 1:]) * 0.5
            daily_discharge += np.dot(y_avg, dt_sel)

    daily_min = np.minimum(daily_charge, daily_discharge)
    valid_mask = (daily_min >= valid_threshold)

    return {
        'dates': data['date_labels'],
        'daily_charge': daily_charge,
        'daily_discharge': daily_discharge,
        'daily_min': daily_min,
        'valid_mask': valid_mask,
        'monthly_charge_total': np.sum(daily_charge),
        'monthly_discharge_total': np.sum(daily_discharge)
    }
# ======================= 配置管理 =======================
class Config:
    FILE = "column_mapping_config.json"
    BACKUP_DIR = "config_backups"
    MAX_BACKUPS = 5
    MAX_WEEKLY_BACKUPS = 3
    CONFIG_VERSION = 5

    DEFAULT_CONFIG = {
        'config_version': CONFIG_VERSION,
        'price_strategies': {},
        'active_strategy': None,
        'preview_templates': {},
        'window_geometry': {},
        'app_settings': {
            'auto_open_dir': True, 'last_output_dir': '',
            'last_multiplier': 1.0, 'last_csv_encoding': 'auto',
            'last_title_template': '{table_no}_{year_month}_负荷曲线',
            'last_filename_template': '{table_no}_{year_month}_负荷曲线',
            'show_daily': True, 'show_avg': True,
            'show_max_demand': True,
            'last_billing_enabled': False, 'last_billing_value': '',
            'last_strategy_name': None,
            'classify_by_asset': False, 'quick_mode': False
        },
        'last_mapping': None,
        'column_fingerprints': {},
        'feedback_data': {}
    }

    def __init__(self):
        self._lock = threading.RLock()
        self.cfg = self._load()
        with self._lock:
            for key, default_val in self.DEFAULT_CONFIG.items():
                self.cfg.setdefault(key, default_val)
            os.makedirs(self.BACKUP_DIR, exist_ok=True)
            self._migrate()

    def _migrate(self):
        with self._lock:
            if 'charge_discharge_strategies' in self.cfg:
                if 'price_strategies' not in self.cfg:
                    self.cfg['price_strategies'] = self.cfg.pop('charge_discharge_strategies')
                else:
                    self.cfg['price_strategies'].update(self.cfg.pop('charge_discharge_strategies'))
            self.cfg['config_version'] = self.CONFIG_VERSION
            self.cfg.setdefault('app_settings', {})
            self.cfg['app_settings'].setdefault('show_max_demand', True)
            self.save()

    def _load(self):
        if os.path.exists(self.FILE):
            try:
                with open(self.FILE,'r',encoding='utf-8') as f:
                    cfg = json.load(f)
                if not isinstance(cfg,dict):
                    raise ValueError("格式错误")
                cleaned = {}
                for k,v in cfg.items():
                    if k in ('price_strategies','active_strategy','preview_templates','app_settings',
                             'last_mapping','window_geometry','config_version','charge_discharge_strategies',
                             'column_fingerprints','feedback_data'):
                        cleaned[k] = v
                        continue
                    if '|' not in k:
                        continue
                    if isinstance(v,dict) and 'date_col' in v and 'load_col' in v:
                        cleaned[k] = v
                cleaned.setdefault('config_version', self.CONFIG_VERSION)
                cleaned.setdefault('app_settings', {})
                cleaned['app_settings'].setdefault('show_max_demand', True)
                return cleaned
            except Exception as e:
                log.error(f"配置加载失败: {e}")
                return self._restore_backup()
        return {}

    def _restore_backup(self):
        backups = sorted([f for f in os.listdir(self.BACKUP_DIR) if f.endswith('.json')], reverse=True)
        for bk in backups:
            try:
                with open(os.path.join(self.BACKUP_DIR,bk),'r',encoding='utf-8') as f:
                    cfg = json.load(f)
                log.info(f"从备份 {bk} 恢复配置")
                return cfg
            except:
                continue
        log.critical("无法恢复任何配置，使用硬编码默认配置")
        return self.DEFAULT_CONFIG.copy()

    def save(self):
        with self._lock:
            try:
                tmp = self.FILE+'.tmp'
                with open(tmp,'w',encoding='utf-8') as f:
                    json.dump(self.cfg,f,ensure_ascii=False,indent=2)
                if os.path.exists(self.FILE):
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
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
                         'window_geometry','config_version','charge_discharge_strategies','column_fingerprints','feedback_data'):
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
                         'window_geometry','config_version','charge_discharge_strategies','column_fingerprints','feedback_data'):
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
        if m['format_type']=='wide':
            for c in m['load_col']:
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
        ls = max(self._bsim(c,col) for c in (lc if isinstance(lc,list) else [lc]))
        ts = self._bsim(m.get('time_col',''),col) if m.get('time_col') else 1.0
        ass = self._bsim(m.get('asset_col',''),col) if m.get('asset_col') else 1.0
        return (ds+ls+ts+ass)/4

    def _bsim(self,tar,cand):
        import difflib
        if tar in cand:
            return 1.0
        return max(difflib.SequenceMatcher(None,tar.lower(),str(c).lower()).ratio() for c in cand)

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
            return self.cfg.get('app_settings',{}).get(k,d)
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
    return ''

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
    existing = [c for c in load_cols if c in df.columns]
    if not existing:
        log.warning(f"宽表负荷列 {load_cols} 在文件中均未找到，跳过扫描。文件: {fp}, 工作表: {sheet}")
        return
    dser = _clean_series(df[date_col])
    ldf = df[existing].apply(pd.to_numeric, errors='coerce')
    yms = dser.apply(_extract_ym)
    for ym in yms.unique():
        if cancel_event and cancel_event.is_set():
            return
        if pd.isna(ym) or ym == '' or ym == '未知月份':
            continue
        idxs = yms == ym
        sub = ldf.loc[idxs]
        vcols = _valid_time_cols(sub.columns)
        if not vcols:
            continue
        arr = sub[vcols].values
        if arr.size == 0:
            continue
        asset = _extract_asset_from_name(fp, sheet) or '未知'
        key = (ym, asset)
        monthly_stats.setdefault(key, {'date_count': 0, 'min_val': np.inf, 'max_val': -np.inf, 'time_set': set()})
        monthly_stats[key]['date_count'] += arr.shape[0]
        monthly_stats[key]['min_val'] = min(monthly_stats[key]['min_val'], np.nanmin(arr))
        monthly_stats[key]['max_val'] = max(monthly_stats[key]['max_val'], np.nanmax(arr))
        monthly_stats[key]['time_set'].update(vcols)

def _scan_long_chunk(fp, sheet, mapping, df, fallback, monthly_stats, cancel_event=None):
    date_col = mapping['date_col']
    time_col = mapping.get('time_col')
    asset_col = mapping.get('asset_col')
    load_col = mapping['load_col']
    if load_col not in df.columns:
        return
    df2, dropped_ratio = _parse_long_dt(df, date_col, time_col, load_col, 1.0, cancel_event)
    if df2.empty:
        return
    if dropped_ratio > 0.5:
        log.warning(f"扫描阶段长表解析丢弃比例 {dropped_ratio:.1%}，文件: {fp}/{sheet}")
    df2['__ym__'] = df2['__dt__'].dt.strftime('%Y-%m')
    g = ['__ym__']
    if asset_col and asset_col in df2.columns:
        raw_asset = df2[asset_col]
        if pd.api.types.is_numeric_dtype(raw_asset):
            df2[asset_col] = raw_asset.astype('Int64').astype(str).str.strip()
        else:
            df2[asset_col] = raw_asset.astype(str).str.strip()
        g.insert(0, asset_col)
    for keys, grp in df2.groupby(g):
        if cancel_event and cancel_event.is_set():
            return
        av = keys[0] if asset_col else None
        ym_val = keys[1] if asset_col else keys[0] if isinstance(keys, tuple) else keys
        ym = str(ym_val)
        asset_val = str(av) if asset_col and av else _extract_asset_from_name(fp, sheet) or '未知'
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

def _scan_file_worker(args):
    fp, sheet, mapping_dict, fallback, cancel_event, shared_cache = args
    if not isinstance(mapping_dict, dict) or 'date_col' not in mapping_dict:
        return {'error': f'{fp}/{sheet}: invalid mapping'}
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
        if mapping['date_col'] not in df.columns:
            return {'error': f"日期列不存在，可用列: {list(df.columns)}"}
        monthly_stats = {}
        normative = mapping.copy()
        if mapping['format_type'] == 'wide':
            _scan_wide_chunk(fp, sheet, normative, df, fallback, monthly_stats, cancel_event)
        else:
            load_col = mapping['load_col']
            if isinstance(load_col, list):
                if not load_col:
                    return {'error': "长表 load_col 列表为空"}
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
        return {'error': f"{fp}/{sheet}: {e}"}

def _normalize_mapping(fp, raw_result, sheets):
    if raw_result is None:
        return None
    if isinstance(raw_result, dict) and 'date_col' in raw_result:
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
    eta = Signal(str)

# ====================== 列刷新任务 =====================
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

# ====================== GroupSummary 与 DataProvider =====================
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
            if mapping['format_type'] == 'wide':
                load_cols = mapping['load_col']
                if not isinstance(load_cols, list):
                    load_cols = [str(load_cols)]
                needed = [date_col] + ([time_col] if time_col else []) + ([asset_col] if asset_col else []) + load_cols
                if any(c not in df.columns for c in needed):
                    missing = [c for c in needed if c not in df.columns]
                    return None, f"缺少列: {missing}"
                dser = _clean_series(df[date_col])
                sub_df = df[load_cols].copy().apply(pd.to_numeric, errors='coerce')
                yms = dser.apply(_extract_ym)
                idxs = yms.groupby(yms).groups.get(s.month_str)
                if idxs is None:
                    return None, f"未找到月份 '{s.month_str}' 的数据"
                dates = [_fmt_date(dser.iloc[i]) for i in idxs]
                sub = sub_df.iloc[idxs]
                vcols = _valid_time_cols(sub.columns)
                if not vcols:
                    return None, "没有有效的时间列"
                time_labels = [_fmt_time(t) for t in vcols]
                arr = sub[vcols].values
                return {'date_labels': dates, 'load_array': arr, 'time_labels': time_labels,
                        'month_str': s.month_str, 'table_no': s.asset_value or '未知',
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
                df2, dropped_ratio = _parse_long_dt(sub_df, date_col, time_col, load_col, 1.0, cancel_event)
                if df2.empty:
                    return None, "解析后无有效数据"
                if dropped_ratio > 0.5:
                    log.warning(f"长表解析丢弃 {dropped_ratio:.1%} 行，请检查日期格式：{s.file_path}/{s.sheet_name}")
                df2['__ym__'] = df2['__dt__'].dt.strftime('%Y-%m')
                if asset_col and asset_col in df2.columns:
                    raw_asset = df2[asset_col]
                    if pd.api.types.is_numeric_dtype(raw_asset):
                        asset_series = raw_asset.astype('Int64').astype(str).str.strip()
                    else:
                        asset_series = raw_asset.astype(str).str.strip()
                    grp = df2[(df2['__ym__'] == s.month_str) & (asset_series == s.asset_value)] if s.asset_value and s.asset_value != '未知' else df2[df2['__ym__'] == s.month_str]
                    if grp.empty and s.asset_value != '未知':
                        return None, f"资产号 '{s.asset_value}' 在月份 '{s.month_str}' 无数据"
                else:
                    grp = df2[df2['__ym__'] == s.month_str]
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
                        'month_str': s.month_str, 'table_no': s.asset_value or '未知',
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
# -*- coding: utf-8 -*-
"""
负荷曲线批量生成工具 Pro 融合版 (最终优化版)
第二部分：数据解析、绘图、列映射对话框、策略编辑器、预览渲染任务
优化点：
- 预览渲染任务支持传递分组个性化参数（为后续优化预留接口）
- 列映射对话框增加“应用到本次所有文件”复选框，提升多文件效率
- 读取列名失败时弹出错误提示并允许跳过文件，不再终止整个流程
- 支持“当月最大需量”显示
"""

# ===================== 第二部分：数据解析、绘图、对话框、预览渲染 =====================

def _parse_long_dt(df, date_col, time_col, load_col, multiplier, cancel_event=None):
    """
    解析长表数据，返回 DataFrame 和丢弃行比例。
    优化6：计算并返回丢弃行比例，而不是仅返回 DataFrame。
    """
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
    return df_clean, ratio

# ======================= 实际绘图函数 =======================
def _plot_curves(dates, load, time_labels, title, outpath, opts=None, bill=None, periods=None, ctitle=None):
    """
    绘制负荷曲线图，优化9：细化异常分类。
    新增：支持 opts 中的 'show_max_demand' 键，绘制当月最大需量线。
    """
    with plt.rc_context({'font.sans-serif': [_get_cached_font()], 'axes.unicode_minus': False}):
        if opts is None:
            opts = {'show_daily': True, 'show_avg': True, 'fit_types': [], 'show_max_demand': False}
        n_dates, n_times = load.shape
        if n_times < 2:
            raise ValueError("时间点数量小于2，无法绘制有意义曲线")
        x = np.arange(n_times)
        pp = n_times / 24.0
        fig, ax = plt.subplots(figsize=(16, 8))
        try:
            if periods:
                for p in _expand_overnight(periods):
                    xs = _hour_x(p["start"], pp)
                    xe = min(_hour_x(p["end"], pp), n_times - 1)
                    ax.axvspan(xs, xe, alpha=0.15, color='#90EE90' if p["type"] in ("charge", "充电") else '#FFA07A', zorder=0)
            colors = plt.cm.tab10(np.linspace(0, 1, min(n_dates, 10)))
            thresh = 500
            if opts.get('show_daily'):
                for i in range(n_dates):
                    xp, yp = (_downsample(x, load[i], thresh) if n_times > thresh else (x, load[i]))
                    ax.plot(xp, yp, label=dates[i], color=colors[i % len(colors)], alpha=0.75, lw=1.5, zorder=10)
            avg = np.nanmean(load, axis=0)
            if opts.get('show_avg'):
                xp, yp = (_downsample(x, avg, thresh) if n_times > thresh else (x, avg))
                ax.plot(xp, yp, label='平均负荷', color='black', ls='--', lw=2.0, zorder=20)
            # 当月最大需量线
            if opts.get('show_max_demand'):
                max_val = np.nanmax(load) if load.size > 0 else 0
                if max_val > 0:
                    ax.axhline(y=max_val, color='blue', ls='--', lw=2.0, label=f'当月最大需量({max_val:.2f})', zorder=25)
            fits = opts.get('fit_types', [])
            if fits:
                for nm, (curve, lab) in _compute_fits(x, load, fits).items():
                    xp, yp = (_downsample(x, curve, thresh) if n_times > thresh else (x, curve))
                    ax.plot(xp, yp, label=lab, ls='-.', lw=2.0, zorder=20)
            if bill and bill.get('type') == 'capacity' and bill.get('value'):
                ax.axhline(y=bill['value'], color='red', ls=':', lw=2.0, label=f'容量线({bill["value"]:.2f})', zorder=30)
            ax.set_title(_clean_plot(ctitle or title), fontsize=16)
            ax.set_xlabel('时间', fontsize=12)
            ax.set_ylabel('负荷值', fontsize=12)
            if n_times > 0:
                idxs, lbls = [], []
                for i in range(25):
                    pos = min(int(round(i * pp)), n_times - 1)
                    idxs.append(pos)
                    lbls.append(_clean_plot(f"{i}:00" if i < 24 else "0:00"))
                ax.set_xticks(idxs)
                ax.set_xticklabels(lbls, rotation=45, fontsize=9)
            ax.grid(True, alpha=0.3)
            handles, labels = ax.get_legend_handles_labels()
            if periods:
                handles.extend([mpatches.Patch(color='#90EE90', alpha=0.3, label='充电(低谷)'),
                                mpatches.Patch(color='#FFA07A', alpha=0.3, label='放电(高峰)')])
                labels.extend(['充电(低谷)', '放电(高峰)'])
            if handles:
                ax.legend(handles=handles, labels=labels, bbox_to_anchor=(1.02, 1), loc='upper left', fontsize=9)
            fig.tight_layout()
            os.makedirs(os.path.dirname(outpath), exist_ok=True)
            fig.savefig(outpath, dpi=200, bbox_inches='tight')
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

# ====================== 列映射对话框（含模糊匹配高亮反馈、一键多文件应用） ======================
class MappingDialog(QDialog):
    def __init__(self, parent, fp, sheet, cfg=None, hrow=0, multi_sheet=False, enc=None, delimiter=',',
                 batch_multi_file=False):
        super().__init__(parent)
        self.fp = fp
        self.sheet = sheet
        self.cfg = cfg
        self.result = None
        self.hrow = hrow
        self.enc = enc if enc else 'auto'
        self.delimiter = delimiter
        self._refresh_task = None
        self._highlighted_controls = []  # 记录高亮控件
        self._batch_multi_file = batch_multi_file  # 当前是否处于多文件处理状态

        fname = os.path.basename(fp)
        self.setWindowTitle(f"配置字段映射 — {fname} [{sheet}]")
        self.setMinimumSize(700, 550)

        if self.cfg:
            geom = self.cfg.get_window_geometry("MappingDialog")
            if geom:
                self.restoreGeometry(geom)
            else:
                self.resize(750, 600)
        else:
            self.resize(750, 600)

        # 尝试读取列名，失败不终止，提供重试或跳过选项
        try:
            self.cols = get_columns_fast(fp, sheet if sheet != "CSV文件" else None, hrow, self.enc, self.delimiter)
        except Exception as e:
            QMessageBox.warning(self, "警告", f"无法读取列名: {e}\n\n您可以选择重试、跳过此文件或取消。")
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("列名读取失败")
            msg_box.setText(f"无法读取文件“{fname}”的列名:\n{e}")
            msg_box.setInformativeText("请检查文件是否损坏或编码是否正确。")
            retry_btn = msg_box.addButton("重试", QMessageBox.ActionRole)
            skip_btn = msg_box.addButton("跳过此文件", QMessageBox.ActionRole)
            cancel_btn = msg_box.addButton("取消全部", QMessageBox.RejectRole)
            msg_box.exec()
            clicked = msg_box.clickedButton()
            if clicked == retry_btn:
                try:
                    self.cols = get_columns_fast(fp, sheet if sheet != "CSV文件" else None, hrow, self.enc, self.delimiter)
                except:
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

        if not self.cols:
            QMessageBox.warning(self, "警告", "未能识别任何列名。")
            self.result = 'skip'
            self.accept()
            return

        self._sample_df = None
        try:
            if os.path.splitext(fp)[1].lower() == '.csv':
                e = self.enc if self.enc != 'auto' else _detect_enc(fp)
                self._sample_df = pd.read_csv(fp, encoding=e, nrows=50, header=hrow)
            else:
                self._sample_df = pd.read_excel(fp, sheet_name=sheet if sheet != "CSV文件" else 0, nrows=50,
                                                header=hrow)
            self._sample_df.columns = [str(c) for c in self._sample_df.columns]
        except:
            pass

        layout = QVBoxLayout(self)

        info = QLabel(f"<b>文件:</b> {fname}<br><b>工作表:</b> {sheet}<br><b>结构:</b> {'多工作表' if multi_sheet else '单工作表'}")
        layout.addWidget(info)

        hrow_group = QGroupBox("标题行设置")
        hrow_layout = QHBoxLayout(hrow_group)
        hrow_layout.addWidget(QLabel("标题行(从1开始):"))
        self.header_row_spin = QSpinBox()
        self.header_row_spin.setRange(1, 20)
        self.header_row_spin.setValue(hrow + 1)
        hrow_layout.addWidget(self.header_row_spin)

        is_csv = os.path.splitext(fp)[1].lower() == '.csv'
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

        fmt_group = QGroupBox("数据格式")
        fmt_layout = QVBoxLayout(fmt_group)
        self.fmt_long = QRadioButton("长表（单列负荷）")
        self.fmt_wide = QRadioButton("宽表（多列时间点负荷）")
        self.fmt_long.setChecked(True)
        fmt_layout.addWidget(self.fmt_long)
        fmt_layout.addWidget(self.fmt_wide)
        self.fmt_group_btns = QButtonGroup()
        self.fmt_group_btns.addButton(self.fmt_long, 1)
        self.fmt_group_btns.addButton(self.fmt_wide, 2)
        self.fmt_group_btns.buttonClicked.connect(self._fmt_chg)
        layout.addWidget(fmt_group)

        # 列名查看
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

        # 映射设置
        select_group = QGroupBox("映射设置")
        grid = QGridLayout(select_group)
        grid.addWidget(QLabel("日期列:"), 0, 0)
        self.date_combo = QComboBox()
        grid.addWidget(self.date_combo, 0, 1)
        grid.addWidget(QLabel("时间列(可选):"), 1, 0)
        self.time_combo = QComboBox()
        self.time_combo.addItem("")
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

        # 选项
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

        # 按钮区
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        auto_btn = QPushButton("智能识别")
        auto_btn.clicked.connect(self._auto)
        btn_layout.addWidget(auto_btn)
        last_btn = QPushButton("上次配置")
        last_btn.clicked.connect(self._last)
        btn_layout.addWidget(last_btn)
        skip_sheet_btn = QPushButton("跳过此工作表")
        skip_sheet_btn.clicked.connect(self._skip_sheet)
        btn_layout.addWidget(skip_sheet_btn)
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

    def _populate_columns(self, cols):
        """填充列名并清除高亮样式"""
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
        """高亮匹配到的控件"""
        combo.setStyleSheet("background-color: #FFF9C4;")
        if combo not in self._highlighted_controls:
            self._highlighted_controls.append(combo)

    def _find_best_match(self, target, cols):
        """返回最佳匹配列名及相似度"""
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
        if 'date_col' in rec:
            self.date_combo.setCurrentText(rec['date_col'])
        if 'time_col' in rec:
            self.time_combo.setCurrentText(rec['time_col'])
        if 'asset_col' in rec:
            self.asset_combo.setCurrentText(rec['asset_col'])
        if 'load_col' in rec:
            if isinstance(rec.get('load_col'), list):
                self.fmt_wide.setChecked(True)
                self._fmt_chg()
                for i in range(self.load_list.count()):
                    if self.load_list.item(i).text() in rec.get('load_col', []):
                        self.load_list.item(i).setSelected(True)
            else:
                self.load_combo.setCurrentText(rec.get('load_col', ''))

    def _apply_rec(self, m, perform_highlight=True):
        """应用映射配置，并可选进行高亮反馈"""
        if not m or m.get('date_col') not in self.cols:
            # 尝试模糊匹配
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
        if m['format_type'] == 'wide':
            self.fmt_wide.setChecked(True)
            self._fmt_chg()
            for i in range(self.load_list.count()):
                item = self.load_list.item(i)
                in_list = item.text() in m['load_col']
                item.setSelected(in_list)
            # 对于宽表，如果任何负荷列匹配失败，高亮列表
            missing = [lc for lc in m['load_col'] if lc not in self.cols]
            if missing:
                self.load_list.setStyleSheet("background-color: #FFF9C4;")
                if perform_highlight:
                    self._highlighted_controls.append(self.load_list)
                    self.confidence_label.setText(
                        f"部分负荷列未直接匹配，已自动选择相似列")
        else:
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
        if 'header_row' in m:
            self.header_row_spin.setValue(m['header_row'] + 1)
        if 'delimiter' in m and self.delim_combo.isVisible():
            d = m['delimiter']
            self.delim_combo.setCurrentText(d if d != ' ' else '空格')

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
        task = ColumnRefreshTask(self.fp, self.sheet if self.sheet != "CSV文件" else None, new_hrow, enc, delim)
        task.columns_ready.connect(self._on_columns_ready)
        task.error_occurred.connect(self._on_refresh_error)
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
            QMessageBox.warning(self, "刷新失败", f"无法读取列名:\n{error_msg}")
            self.refresh_status.setText("")
        self.header_row_spin.setEnabled(True)
        self.refresh_btn.setEnabled(True)
        self._set_mapping_enabled(True)

    def _set_mapping_enabled(self, enabled):
        self.fmt_long.setEnabled(enabled)
        self.fmt_wide.setEnabled(enabled)
        self.date_combo.setEnabled(enabled)
        self.time_combo.setEnabled(enabled)
        self.asset_combo.setEnabled(enabled)
        self.load_combo.setEnabled(enabled)
        self.load_list.setEnabled(enabled)

    def _auto(self):
        cand = self.cols
        smart_rec = _smart_classify_columns(cand, self._sample_df)
        if smart_rec and 'date_col' in smart_rec:
            self._apply_smart_rec(smart_rec)
            self.confidence_label.setText("已自动推荐（智能识别）")
            QMessageBox.information(self, "智能识别", "已根据列名与数据样本自动推荐字段。")
        else:
            dg = next((c for c in cand if '日期' in c or 'date' in c.lower()), cand[0] if cand else '')
            self.date_combo.setCurrentText(dg)
            ag = next((c for c in cand if '资产' in c or '户号' in c or '表号' in c), '')
            self.asset_combo.setCurrentText(ag)
            self.load_combo.setCurrentText(cand[1] if len(cand) > 1 else '')
            self.confidence_label.setText("")
            QMessageBox.information(self, "智能识别", "已自动填写常用字段，请核对。")

    def _last(self):
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
        else:
            self.load_combo.setVisible(True)
            self.load_list.setVisible(False)

    def _ok(self):
        d = self.date_combo.currentText().strip()
        t = self.time_combo.currentText().strip() or None
        a = self.asset_combo.currentText().strip() or None
        if not d:
            QMessageBox.critical(self, "错误", "必须选择日期列！")
            return
        if self.fmt_long.isChecked():
            l = [self.load_combo.currentText().strip()]
            if not l[0]:
                QMessageBox.critical(self, "错误", "必须选择负荷列！")
                return
        else:
            selected = self.load_list.selectedItems()
            if not selected:
                QMessageBox.critical(self, "错误", "宽表格式必须至少勾选一个负荷列！")
                return
            l = [item.text() for item in selected]
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
            'format_type': 'long' if self.fmt_long.isChecked() else 'wide',
            'header_row': self.header_row_spin.value() - 1,
            'delimiter': delim
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

    def _skip_sheet(self):
        self.result = 'skip_sheet'
        self.accept()

    def closeEvent(self, event):
        if self.cfg:
            self.cfg.set_window_geometry("MappingDialog", self.saveGeometry())
        super().closeEvent(event)

# ======================= 时段编辑对话框（含几何记忆） =======================
class PeriodEditDialog(QDialog):
    def __init__(self, parent, period=None, config_manager=None):
        super().__init__(parent)
        self.setWindowTitle("编辑时段")
        self.setMinimumSize(350, 240)
        self.result = None
        self.config_manager = config_manager

        if self.config_manager:
            geom = self.config_manager.get_window_geometry("PeriodEditDialog")
            if geom:
                self.restoreGeometry(geom)
            else:
                self.resize(380, 260)
        else:
            self.resize(380, 260)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("<b>开始时间</b> (例: 08:30 / 8.5 / 8 30 / 830):"))
        self.start_edit = QLineEdit(_fmt_hour(period['start']) if period else "08:00")
        layout.addWidget(self.start_edit)
        layout.addWidget(QLabel("<b>结束时间</b>:"))
        self.end_edit = QLineEdit(_fmt_hour(period['end']) if period else "11:00")
        layout.addWidget(self.end_edit)
        layout.addWidget(QLabel("<b>类型</b>:"))
        self.type_combo = QComboBox()
        self.type_combo.addItems(['充电(低谷)', '放电(高峰)'])
        if period and period['type'] in ('charge', '充电'):
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
            typ = 'charge' if '充电' in self.type_combo.currentText() else 'discharge'
            self.result = {'start': s, 'end': e, 'type': typ}
            self.accept()
        except Exception as ex:
            QMessageBox.critical(self, "时间格式错误", f"请输入正确的时间:\n{ex}")

    def closeEvent(self, event):
        if self.config_manager:
            self.config_manager.set_window_geometry("PeriodEditDialog", self.saveGeometry())
        super().closeEvent(event)

# ======================= 充放策略管理对话框 =======================
class ChargeDischargeManagerDialog(QDialog):
    strategy_selected = Signal(str)

    def __init__(self, parent, cfg):
        super().__init__(parent)
        self.cfg = cfg
        self.result = None
        self.setWindowTitle("充放时段策略管理")
        self.setMinimumSize(500, 350)

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
        if items and QMessageBox.question(self, "确认", f"确定要删除策略 '{items[0].text(0)}' 吗？"):
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

# ======================= 充放策略编辑对话框 =======================
class ChargeDischargeEditDialog(QDialog):
    def __init__(self, parent, cfg, initial_name=None):
        super().__init__(parent)
        self.cfg = cfg
        self.result = None
        self.setWindowTitle("编辑充放时段")
        self.setMinimumSize(720, 520)
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
                self.resize(800, 600)
        else:
            self.resize(800, 600)

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
        copy_btn = QPushButton("勾选月份复制...")
        copy_btn.clicked.connect(self._copy_to_selected_months)
        month_layout.addWidget(copy_btn)
        layout.addLayout(month_layout)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['开始', '结束', '类型'])
        self.tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.tree.itemDoubleClicked.connect(self._edit_selected)
        layout.addWidget(self.tree)

        btn_layout = QHBoxLayout()
        btn_layout.addWidget(QPushButton("添加时段", clicked=self._add_period))
        btn_layout.addWidget(QPushButton("编辑选中", clicked=self._edit_selected))
        btn_layout.addWidget(QPushButton("删除选中", clicked=self._delete_selected))
        btn_layout.addWidget(QPushButton("清空本月", clicked=self._clear_month))
        btn_layout.addWidget(QPushButton("批量修改类型", clicked=self._batch_change_type))
        btn_layout.addWidget(QPushButton("快速模板: 经典充放", clicked=self._preset_classic))
        btn_layout.addWidget(QPushButton("快速模板: 夏季尖峰", clicked=self._preset_summer))
        layout.addLayout(btn_layout)

        undo_layout = QHBoxLayout()
        self.undo_btn = QPushButton("↩ 撤销 (Ctrl+Z)")
        self.undo_btn.clicked.connect(self._undo)
        self.redo_btn = QPushButton("↪ 重做 (Ctrl+Y)")
        self.redo_btn.clicked.connect(self._redo)
        undo_layout.addWidget(self.undo_btn)
        undo_layout.addWidget(self.redo_btn)
        undo_layout.addStretch()
        layout.addLayout(undo_layout)

        dlg_btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        dlg_btns.accepted.connect(self._save)
        dlg_btns.rejected.connect(self.reject)
        layout.addWidget(dlg_btns)

        self._populate_list()
        self._update_month_status()
        self._update_undo_redo_buttons()

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

    def _update_month_status(self):
        cnt = len(self.current_periods)
        self.month_status.setText(f"已配置 {cnt} 个时段" if cnt else "尚未配置")

    def _populate_list(self):
        self.tree.clear()
        self.current_periods.sort(key=lambda x: x['start'])
        for p in self.current_periods:
            s = _fmt_hour(p['start'])
            e = _fmt_hour(p['end'])
            t = '充电(低谷)' if p['type'] in ('charge', '充电') else '放电(高峰)'
            item = QTreeWidgetItem([s, e, t])
            if '充电' in t:
                item.setBackground(2, QColor('#e0ffe0'))
                item.setForeground(2, QColor('green'))
            else:
                item.setBackground(2, QColor('#ffe0d0'))
                item.setForeground(2, QColor('darkorange'))
            self.tree.addTopLevelItem(item)

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

    def _delete_selected(self):
        items = self.tree.selectedItems()
        if not items:
            return
        self._push_history()
        if QMessageBox.question(self, "确认", f"删除选中的 {len(items)} 个时段？"):
            for item in reversed(items):
                idx = self.tree.indexOfTopLevelItem(item)
                del self.current_periods[idx]
            self._populate_list()
            self._update_month_status()
        elif self._undo_stack:
            self._undo_stack.pop()
            self._update_undo_redo_buttons()

    def _clear_month(self):
        if self.current_periods:
            self._push_history()
            if QMessageBox.question(self, "确认", "清空本月全部时段？"):
                self.current_periods.clear()
                self._populate_list()
                self._update_month_status()
            elif self._undo_stack:
                self._undo_stack.pop()
                self._update_undo_redo_buttons()

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
        combo.addItems(['充电(低谷)', '放电(高峰)'])
        layout.addWidget(combo)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)
        if dlg.exec() == QDialog.Accepted:
            new_type = 'charge' if '充电' in combo.currentText() else 'discharge'
            for item in items:
                idx = self.tree.indexOfTopLevelItem(item)
                self.current_periods[idx]['type'] = new_type
            self._populate_list()
        elif self._undo_stack:
            self._undo_stack.pop()
            self._update_undo_redo_buttons()

    def _preset_classic(self):
        self._push_history()
        new_periods = [
            {'start': 8, 'end': 11, 'type': 'discharge'},
            {'start': 18, 'end': 21, 'type': 'discharge'},
            {'start': 23, 'end': 7, 'type': 'charge'}
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

    def _preset_summer(self):
        self._push_history()
        new_periods = [
            {'start': 10, 'end': 12, 'type': 'discharge'},
            {'start': 14, 'end': 16, 'type': 'discharge'},
            {'start': 19, 'end': 22, 'type': 'discharge'},
            {'start': 22, 'end': 6, 'type': 'charge'}
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

    def _apply_to_all_months(self):
        if not self.current_periods:
            QMessageBox.warning(self, "提示", "当前月份没有时段")
            return
        if QMessageBox.question(self, "确认", "将当前月份的时段复制到所有12个月？"):
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
            if targets and QMessageBox.question(self, "确认", f"复制到 {len(targets)} 个月份？"):
                self._push_history()
                for m in targets:
                    self._session_data[m] = self.current_periods[:]
                self._update_month_status()
                QMessageBox.information(self, "完成", "已复制")

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

# ======================= 预览渲染任务（预留个性化参数） =======================
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
        self.multiplier = multiplier              # 可能是个性化的倍率
        self.billing = billing                    # 可能是个性化的容量
        self.periods = periods
        self.title_tpl = title_tpl
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        if self._cancelled:
            return
        try:
            with plt.rc_context({'font.sans-serif': [_get_cached_font()], 'axes.unicode_minus': False}):
                data = self.provider.get_group_data(self.summary)
                if not data:
                    error_msg = self.provider.get_load_error(self.summary)
                    self.error_occurred.emit(self.index,
                        f"无法加载数据: {error_msg}\n文件:{self.summary.file_path}\n工作表:{self.summary.sheet_name}\n月份:{self.summary.month_str}")
                    return
                load_array = np.array(data['load_array']) * self.multiplier
                if load_array.ndim != 2 or load_array.size == 0:
                    self.error_occurred.emit(self.index, f"数据形状异常 {load_array.shape}")
                    return
                n_dates, n_times = load_array.shape
                x = np.arange(n_times)
                pp = n_times / 24.0
                fig, ax = plt.subplots(figsize=(10, 6), dpi=100)
                if self.periods:
                    for p in _expand_overnight(self.periods):
                        xs = _hour_x(p["start"], pp)
                        xe = min(_hour_x(p["end"], pp), n_times - 1)
                        ax.axvspan(xs, xe, alpha=0.15, color='#90EE90' if p["type"] in ("charge", "充电") else '#FFA07A', zorder=0)
                colors = plt.cm.tab10(np.linspace(0, 1, min(n_dates, 10)))
                if self.plot_options.get('show_daily'):
                    for i in range(n_dates):
                        ax.plot(x, load_array[i], label=data['date_labels'][i], color=colors[i % 10], alpha=0.8, lw=1.2)
                avg = np.nanmean(load_array, axis=0)
                if self.plot_options.get('show_avg'):
                    ax.plot(x, avg, 'k--', lw=2.0, label='平均负荷')
                # 当月最大需量线
                if self.plot_options.get('show_max_demand'):
                    max_val = np.nanmax(load_array) if load_array.size > 0 else 0
                    if max_val > 0:
                        ax.axhline(y=max_val, color='blue', ls='--', lw=2.0, label=f'当月最大需量({max_val:.2f})')
                if self.billing and self.billing.get('type') == 'capacity' and self.billing.get('value'):
                    ax.axhline(y=self.billing['value'], color='red', ls=':', lw=2.0, label=f'容量线({self.billing["value"]:.2f})')
                ctx = {
                    'year_month': data.get('month_str', ''),
                    'table_no': data.get('table_no', ''),
                    'asset': data.get('asset', ''),
                    'sheet': data.get('sheet', '')
                }
                title = parse_title_template(self.title_tpl, ctx) if self.title_tpl else generate_default_title(ctx)
                ax.set_title(title, fontsize=14)
                ax.set_xlabel('时间', fontsize=10)
                ax.set_ylabel('负荷值', fontsize=10)
                if n_times > 0:
                    idxs, lbls = [], []
                    for i in range(25):
                        pos = min(int(round(i * pp)), n_times - 1)
                        idxs.append(pos)
                        lbls.append(f"{i}:00" if i < 24 else "0:00")
                    ax.set_xticks(idxs)
                    ax.set_xticklabels(lbls, rotation=45, fontsize=8)
                ax.grid(True, alpha=0.3)
                handles, labels = ax.get_legend_handles_labels()
                if handles:
                    ax.legend(bbox_to_anchor=(1.02, 1), loc='upper left', fontsize=8)
                fig.tight_layout()
                buf = io.BytesIO()
                fig.savefig(buf, format='png', dpi=100)
                plt.close(fig)
                buf.seek(0)
                pix = QPixmap()
                pix.loadFromData(buf.read(), "PNG")
                self.preview_ready.emit(self.index, pix)
        except Exception as e:
            log.error(f"预览渲染失败 {self.summary.file_path}: {e}", exc_info=True)
            self.error_occurred.emit(self.index, str(e))
        finally:
            self.task_done.emit(self.index)
# -*- coding: utf-8 -*-
"""
负荷曲线批量生成工具 Pro 融合版 (最终优化版)
第三部分：预览对话框、导出确认对话框、专业分析窗口、文件拖拽列表、拖放覆盖层
优化点：
- 预览对话框：支持单个/批量设置倍率与容量；“仅当前”与“应用到选中”
- 导出确认对话框：表格化、双击编辑、批量修改、文件冲突检测、统计信息
- 新增 ProfessionalModeDialog：选择评估模式并进行前置校验
- 新增 ProfessionalWindow：支持需量管理/光伏消纳/混合模式，含忽略负值开关和光伏返送分析
- 新增 PVFeedbackDetailDialog：展示月度光伏返送详情
- 修复信号风暴、缓存问题，增加当月最大需量选项
"""

class PreviewDialog(QDialog):
    def __init__(self, parent, all_summaries, config_manager, initial_multiplier=1.0,
                 initial_strategy_name=None, initial_output_dir="", data_provider=None):
        super().__init__(parent)
        self.setWindowFlags(self.windowFlags() | Qt.WindowMinMaxButtonsHint)
        self.all_summaries = all_summaries
        self.config_manager = config_manager
        self.current_index = 0
        self.selected_indices = set(range(len(all_summaries)))
        self.provider = data_provider if data_provider else DataProvider(config_manager.app_set('last_csv_encoding', 'auto'))
        self.pixmap_cache = OrderedDict()
        self._max_pixmap_cache = PREVIEW_CACHE_MAXSIZE
        self.billing_dict = {}
        self._per_group_multiplier = {}
        self._per_group_billing = {}
        self._rendering_set = set()
        self._render_tasks = {}
        self._preload_queue = []
        self._no_data_indices = set()
        self._current_zoom = 1.0
        self._last_strategy_text = ""
        self._last_applied_multiplier = initial_multiplier
        self._first_render_done = False

        self._param_fingerprints = {}

        self._preload_timer = QTimer(self)
        self._preload_timer.timeout.connect(self._preload_step)
        self._preload_timer.setInterval(800)

        QThreadPool.globalInstance().setMaxThreadCount(2)

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

        main_layout = QVBoxLayout(self)
        self.splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(self.splitter, 1)

        # ========== 左侧面板 ==========
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        tree_widget = QWidget()
        tree_layout = QVBoxLayout(tree_widget)
        tree_layout.setContentsMargins(0, 0, 0, 0)
        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel("搜索:"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("输入表号或月份关键字...")
        self.search_edit.textChanged.connect(self._filter_tree)
        search_layout.addWidget(self.search_edit)
        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["按表号", "按月份"])
        self.sort_combo.currentIndexChanged.connect(self._rebuild_tree)
        search_layout.addWidget(QLabel("排序:"))
        search_layout.addWidget(self.sort_combo)
        tree_layout.addLayout(search_layout)

        self.group_tree = QTreeWidget()
        self.group_tree.setHeaderLabels(['分组', '状态'])
        self.group_tree.header().setStretchLastSection(False)
        self.group_tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.group_tree.header().resizeSection(1, 120)
        self.group_tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.group_tree.itemClicked.connect(self._on_tree_item_clicked)
        self.group_tree.itemChanged.connect(self._on_tree_item_changed)
        tree_layout.addWidget(self.group_tree)

        sel_btns = QHBoxLayout()
        sel_btns.addWidget(QPushButton("全选", clicked=lambda: self._set_all_check(True)))
        sel_btns.addWidget(QPushButton("全不选", clicked=lambda: self._set_all_check(False)))
        self.asset_select_btn = QPushButton("按表号多选...")
        self.asset_select_btn.clicked.connect(self._select_by_asset_dialog)
        sel_btns.addWidget(self.asset_select_btn)
        tree_layout.addLayout(sel_btns)
        left_layout.addWidget(tree_widget, 3)

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
        self.param_layout.addLayout(template_frame)
        self._refresh_template_list()

        # ===== 倍率设置 =====
        mult_group = QGroupBox("倍率设置")
        mult_outer = QVBoxLayout(mult_group)
        mult_row1 = QHBoxLayout()
        mult_row1.addWidget(QLabel("倍率:"))
        self.multiplier_spin = QDoubleSpinBox()
        self.multiplier_spin.setRange(0.01, 999999.0)
        self.multiplier_spin.setValue(initial_multiplier)
        self.multiplier_spin.setDecimals(4)
        self.multiplier_spin.setToolTip("全局倍率，或当前分组的专属倍率（若勾选仅当前）")
        mult_row1.addWidget(self.multiplier_spin)
        apply_mult_btn = QPushButton("应用")
        apply_mult_btn.clicked.connect(self._apply_multiplier)
        mult_row1.addWidget(apply_mult_btn)
        mult_outer.addLayout(mult_row1)

        mult_row2 = QHBoxLayout()
        self.mult_only_current_cb = QCheckBox("仅当前")
        self.mult_only_current_cb.setToolTip("勾选后修改的倍率仅应用于当前分组")
        self.mult_only_current_cb.stateChanged.connect(self._on_mult_only_current_changed)
        mult_row2.addWidget(self.mult_only_current_cb)
        self.apply_mult_to_selected_btn = QPushButton("应用到选中")
        self.apply_mult_to_selected_btn.clicked.connect(self._apply_mult_to_selected)
        mult_row2.addWidget(self.apply_mult_to_selected_btn)
        self.reset_mult_btn = QPushButton("重置为默认")
        self.reset_mult_btn.clicked.connect(self._reset_mult_to_default)
        mult_row2.addWidget(self.reset_mult_btn)
        mult_outer.addLayout(mult_row2)
        self.mult_status_label = QLabel("")
        self.mult_status_label.setStyleSheet("color: #FF9800;")
        mult_outer.addWidget(self.mult_status_label)
        self.param_layout.addWidget(mult_group)

        # ===== 计费容量设置 =====
        bill_group = QGroupBox("计费容量线")
        bill_outer = QVBoxLayout(bill_group)
        bill_enable_layout = QHBoxLayout()
        self.bill_enabled_cb = QCheckBox("启用")
        self.bill_enabled_cb.setChecked(config_manager.app_set('last_billing_enabled', False))
        bill_enable_layout.addWidget(self.bill_enabled_cb)
        bill_outer.addLayout(bill_enable_layout)

        bill_val_layout = QHBoxLayout()
        bill_val_layout.addWidget(QLabel("值:"))
        self.capacity_edit = QLineEdit(config_manager.app_set('last_billing_value', ''))
        self.capacity_edit.setToolTip("容量值，全局或当前分组的专属值")
        bill_val_layout.addWidget(self.capacity_edit)
        apply_bill_btn = QPushButton("应用")
        apply_bill_btn.clicked.connect(self._apply_billing)
        bill_val_layout.addWidget(apply_bill_btn)
        bill_outer.addLayout(bill_val_layout)

        bill_custom_row = QHBoxLayout()
        self.bill_only_current_cb = QCheckBox("仅当前")
        self.bill_only_current_cb.setToolTip("勾选后修改的容量仅应用于当前分组")
        self.bill_only_current_cb.stateChanged.connect(self._on_bill_only_current_changed)
        bill_custom_row.addWidget(self.bill_only_current_cb)
        self.apply_bill_to_selected_btn = QPushButton("应用到选中")
        self.apply_bill_to_selected_btn.clicked.connect(self._apply_bill_to_selected)
        bill_custom_row.addWidget(self.apply_bill_to_selected_btn)
        self.reset_bill_btn = QPushButton("重置为默认")
        self.reset_bill_btn.clicked.connect(self._reset_bill_to_default)
        bill_custom_row.addWidget(self.reset_bill_btn)
        bill_outer.addLayout(bill_custom_row)
        self.bill_status_label = QLabel("")
        self.bill_status_label.setStyleSheet("color: #FF9800;")
        bill_outer.addWidget(self.bill_status_label)
        self.param_layout.addWidget(bill_group)

        disp_group = QGroupBox("曲线显示")
        disp_layout = QVBoxLayout(disp_group)
        self.show_daily_cb = QCheckBox("每日曲线")
        self.show_daily_cb.setChecked(config_manager.app_set('show_daily', True))
        self.show_avg_cb = QCheckBox("平均曲线")
        self.show_avg_cb.setChecked(config_manager.app_set('show_avg', True))
        self.show_max_demand_cb = QCheckBox("显示当月最大需量")
        self.show_max_demand_cb.setChecked(config_manager.app_set('show_max_demand', True))
        disp_layout.addWidget(self.show_daily_cb)
        disp_layout.addWidget(self.show_avg_cb)
        disp_layout.addWidget(self.show_max_demand_cb)
        self.param_layout.addWidget(disp_group)

        strat_layout = QHBoxLayout()
        strat_layout.addWidget(QLabel("充放策略:"))
        self.strategy_combo = QComboBox()
        self.strategy_combo.addItem("无")
        self.strategy_combo.addItems(config_manager.strat_names())
        last_strat = config_manager.app_set('last_strategy_name')
        if last_strat and last_strat in config_manager.strat_names():
            self.strategy_combo.setCurrentText(last_strat)
        elif initial_strategy_name:
            self.strategy_combo.setCurrentText(initial_strategy_name)
        self._last_strategy_text = self.strategy_combo.currentText()
        self.strategy_combo.currentTextChanged.connect(self._on_strategy_changed)
        strat_layout.addWidget(self.strategy_combo)
        edit_btn = QPushButton("管理策略")
        edit_btn.clicked.connect(self._manage_strategies)
        strat_layout.addWidget(edit_btn)
        self.param_layout.addLayout(strat_layout)

        # ---- 专业模式按钮（始终可用）----
        self.professional_btn = QPushButton("专业模式")
        self.professional_btn.clicked.connect(self._open_professional)
        self.professional_btn.setEnabled(True)  # 默认启用，无数据时点击会有提示
        self.param_layout.addWidget(self.professional_btn)

        out_dir_layout = QHBoxLayout()
        out_dir_layout.addWidget(QLabel("输出目录:"))
        self.output_dir_edit = QLineEdit(initial_output_dir)
        self.output_dir_edit.setReadOnly(True)
        out_dir_layout.addWidget(self.output_dir_edit, 1)
        browse_btn = QPushButton("浏览...")
        browse_btn.clicked.connect(self._browse_output_dir)
        out_dir_layout.addWidget(browse_btn)
        open_dir_btn = QPushButton("打开")
        open_dir_btn.clicked.connect(self._open_current_output_dir)
        out_dir_layout.addWidget(open_dir_btn)
        self.param_layout.addLayout(out_dir_layout)

        self.classify_cb = QCheckBox("按表号创建子文件夹")
        self.classify_cb.setChecked(config_manager.app_set('classify_by_asset', False))
        self.param_layout.addWidget(self.classify_cb)

        title_layout = QVBoxLayout()
        title_layout.addWidget(QLabel("<b>图表标题模板</b><br><small>变量：{表号} {年月} {资产} {工作表}</small>"))
        self.title_edit = QLineEdit(config_manager.app_set('last_title_template', '{table_no}_{year_month}_负荷曲线'))
        title_layout.addWidget(self.title_edit)
        quick_btns = QHBoxLayout()
        quick_btns.addWidget(QPushButton("表号_年月", clicked=lambda: self._set_title_tpl("{table_no}_{year_month}_负荷曲线")))
        quick_btns.addWidget(QPushButton("资产_年月", clicked=lambda: self._set_title_tpl("{asset}_{year_month}_负荷")))
        quick_btns.addWidget(QPushButton("详细", clicked=lambda: self._set_title_tpl("{sheet}_{asset}_{year_month}")))
        quick_btns.addWidget(QPushButton("纯年月", clicked=lambda: self._set_title_tpl("{year_month}_负荷曲线")))
        title_layout.addLayout(quick_btns)
        self.title_preview = QLabel("")
        self.title_preview.setStyleSheet("color: gray;")
        title_layout.addWidget(self.title_preview)
        self.param_layout.addLayout(title_layout)

        fname_layout = QVBoxLayout()
        fname_layout.addWidget(QLabel("<b>输出文件名模板</b><br><small>变量：{表号} {年月} {资产} {工作表}</small>"))
        self.filename_edit = QLineEdit(config_manager.app_set('last_filename_template', '{table_no}_{year_month}_负荷曲线'))
        fname_layout.addWidget(self.filename_edit)
        fname_quick_btns = QHBoxLayout()
        fname_quick_btns.addWidget(QPushButton("表号_年月", clicked=lambda: self.filename_edit.setText("{table_no}_{year_month}_负荷曲线")))
        fname_quick_btns.addWidget(QPushButton("资产_年月", clicked=lambda: self.filename_edit.setText("{asset}_{year_month}_负荷")))
        fname_quick_btns.addWidget(QPushButton("详细", clicked=lambda: self.filename_edit.setText("{sheet}_{asset}_{year_month}")))
        fname_quick_btns.addWidget(QPushButton("纯年月", clicked=lambda: self.filename_edit.setText("{year_month}_负荷曲线")))
        fname_layout.addLayout(fname_quick_btns)
        self.fname_warning = QLabel("")
        self.fname_warning.setStyleSheet("color: #FF9800; font-weight: bold;")
        fname_layout.addWidget(self.fname_warning)
        self.param_layout.addLayout(fname_layout)

        scroll.setWidget(param_widget)
        left_layout.addWidget(scroll, 2)

        # ========== 右侧图形视图 ==========
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
        self.show_daily_cb.stateChanged.connect(lambda: self._apply_parameters())
        self.show_avg_cb.stateChanged.connect(lambda: self._apply_parameters())
        self.show_max_demand_cb.stateChanged.connect(lambda: self._apply_parameters())
        self.title_edit.textChanged.connect(self._update_title_preview)
        self.filename_edit.textChanged.connect(self._check_filename)

        self._rebuild_tree()
        if self.all_summaries:
            self.current_index = 0
            self.index_label.setText(f"1 / {len(self.all_summaries)}")
            self._update_info()
            self._update_multiplier_ui()
            self._update_billing_ui()
        self._init_periods_fingerprints()

    def _init_periods_fingerprints(self):
        self._periods_fingerprint_map = {}
        for idx in range(len(self.all_summaries)):
            s = self.all_summaries[idx]
            periods = self._get_periods_for_summary(s)
            self._periods_fingerprint_map[idx] = hash(tuple((p['start'], p['end'], p['type']) for p in periods))

    # ---------- showEvent ----------
    def showEvent(self, event):
        super().showEvent(event)
        if not self._first_render_done and self.all_summaries:
            self._schedule_render(0)
            self._first_render_done = True
            self._start_lazy_preload()

    # ---------- 获取有效参数 ----------
    def get_effective_multiplier(self, idx):
        if idx is not None and idx in self._per_group_multiplier:
            return self._per_group_multiplier[idx]
        return self.multiplier_spin.value()

    def get_effective_billing(self, idx):
        if idx is not None and idx in self._per_group_billing:
            return self._per_group_billing[idx]
        summary = self.all_summaries[idx] if idx is not None and 0 <= idx < len(self.all_summaries) else None
        if summary and summary.asset_value in self.billing_dict:
            return self.billing_dict[summary.asset_value]
        return None

    # ---------- UI 状态更新 ----------
    def _update_multiplier_ui(self):
        idx = self.current_index
        is_custom = idx in self._per_group_multiplier
        self.mult_only_current_cb.blockSignals(True)
        self.mult_only_current_cb.setChecked(is_custom)
        self.mult_only_current_cb.blockSignals(False)
        if is_custom:
            self.multiplier_spin.setValue(self._per_group_multiplier[idx])
            self.mult_status_label.setText("当前分组使用专属倍率")
            self.multiplier_spin.setStyleSheet("background-color: #FFF9C4;")
        else:
            self.multiplier_spin.setValue(self._last_applied_multiplier)
            self.mult_status_label.setText("")
            self.multiplier_spin.setStyleSheet("")
        self.reset_mult_btn.setEnabled(is_custom)

    def _update_billing_ui(self):
        idx = self.current_index
        is_custom = idx in self._per_group_billing
        self.bill_only_current_cb.blockSignals(True)
        self.bill_only_current_cb.setChecked(is_custom)
        self.bill_only_current_cb.blockSignals(False)
        if is_custom:
            self.capacity_edit.setText(str(self._per_group_billing[idx].get('value', '')))
            self.bill_enabled_cb.setChecked(True)
            self.bill_status_label.setText("当前分组使用专属容量")
            self.capacity_edit.setStyleSheet("background-color: #FFF9C4;")
        else:
            summary = self.all_summaries[idx]
            asset_bill = self.billing_dict.get(summary.asset_value) if summary else None
            if asset_bill:
                self.capacity_edit.setText(str(asset_bill['value']))
                self.bill_enabled_cb.setChecked(True)
            else:
                self.capacity_edit.setText("")
                self.bill_enabled_cb.setChecked(False)
            self.bill_status_label.setText("")
            self.capacity_edit.setStyleSheet("")
        self.reset_bill_btn.setEnabled(is_custom)

    # 专业模式按钮状态已移除，按钮始终启用

    # ---------- 交互回调 ----------
    def _on_mult_only_current_changed(self):
        idx = self.current_index
        if self.mult_only_current_cb.isChecked():
            self._per_group_multiplier[idx] = self.multiplier_spin.value()
        else:
            self._per_group_multiplier.pop(idx, None)
        self._update_multiplier_ui()
        self._refresh_specific_indices([idx])

    def _on_bill_only_current_changed(self):
        idx = self.current_index
        if self.bill_only_current_cb.isChecked():
            try:
                val = float(self.capacity_edit.text().strip())
            except:
                val = 0
            self._per_group_billing[idx] = {'type': 'capacity', 'value': val}
        else:
            self._per_group_billing.pop(idx, None)
        self._update_billing_ui()
        self._refresh_specific_indices([idx])

    def _apply_mult_to_selected(self):
        indices = self._get_all_checked_indices()
        if not indices:
            QMessageBox.warning(self, "提示", "没有选中任何分组")
            return
        if QMessageBox.question(
            self,
            "批量设置倍率",
            f"将倍率 {self.multiplier_spin.value()} 应用到选中的 {len(indices)} 个分组？\n"
        ) != QMessageBox.Yes:
            return
        for idx in indices:
            self._per_group_multiplier[idx] = self.multiplier_spin.value()
        self._update_multiplier_ui()
        self._refresh_specific_indices(indices)

    def _apply_bill_to_selected(self):
        indices = self._get_all_checked_indices()
        if not indices:
            QMessageBox.warning(self, "提示", "没有选中任何分组")
            return
        try:
            val = float(self.capacity_edit.text().strip())
        except:
            QMessageBox.critical(self, "错误", "请输入有效的容量值")
            return
        if QMessageBox.question(
            self,
            "批量设置容量",
            f"将容量 {val} 应用到选中的 {len(indices)} 个分组？\n"
        ) != QMessageBox.Yes:
            return
        for idx in indices:
            self._per_group_billing[idx] = {'type': 'capacity', 'value': val}
        self._update_billing_ui()
        self._refresh_specific_indices(indices)

    def _reset_mult_to_default(self):
        idx = self.current_index
        if idx in self._per_group_multiplier:
            del self._per_group_multiplier[idx]
            self._update_multiplier_ui()
            self._refresh_specific_indices([idx])

    def _reset_bill_to_default(self):
        idx = self.current_index
        if idx in self._per_group_billing:
            del self._per_group_billing[idx]
            self._update_billing_ui()
            self._refresh_specific_indices([idx])

    def _get_all_checked_indices(self):
        indices = set()
        for i in range(self.group_tree.topLevelItemCount()):
            asset_item = self.group_tree.topLevelItem(i)
            for j in range(asset_item.childCount()):
                child = asset_item.child(j)
                idx = child.data(0, Qt.UserRole)
                if idx >= 0 and child.checkState(0) == Qt.Checked:
                    indices.add(idx)
        return indices

    # ---------- 渲染参数指纹判断 ----------
    def _get_rendering_fingerprint(self, idx):
        periods = self._get_periods_for_summary(self.all_summaries[idx])
        periods_tuple = tuple(sorted(((p['start'], p['end'], p['type']) for p in periods)))
        fingerprint = (
            self.get_effective_multiplier(idx),
            self.get_effective_billing(idx)['value'] if self.get_effective_billing(idx) else None,
            self.show_daily_cb.isChecked(),
            self.show_avg_cb.isChecked(),
            self.show_max_demand_cb.isChecked(),
            periods_tuple
        )
        return hash(fingerprint)

    # ---------- 核心渲染调度 ----------
    def _schedule_render(self, idx):
        if idx in self._no_data_indices:
            return
        new_fp = self._get_rendering_fingerprint(idx)
        if idx in self.pixmap_cache and self._param_fingerprints.get(idx) == new_fp:
            pix = self.pixmap_cache[idx]
            self.pixmap_cache.move_to_end(idx)
            if idx == self.current_index:
                self._display_pixmap(pix)
            return

        if idx in self._rendering_set:
            return
        if idx == self.current_index:
            self.scene.clear()
            text = self.scene.addText("⏳ 加载中...")
            text.setDefaultTextColor(QColor("gray"))
            self.view.fitInView(self.scene.itemsBoundingRect(), Qt.KeepAspectRatio)

        if idx in self._render_tasks:
            self._render_tasks[idx].cancel()
            del self._render_tasks[idx]

        self._rendering_set.add(idx)
        summary = self.all_summaries[idx]
        periods = self._get_periods_for_summary(summary)
        billing = self.get_effective_billing(idx)
        multiplier = self.get_effective_multiplier(idx)
        task = PreviewRenderTask(
            idx, summary, self.provider,
            {'show_daily': self.show_daily_cb.isChecked(),
             'show_avg': self.show_avg_cb.isChecked(),
             'show_max_demand': self.show_max_demand_cb.isChecked(),
             'fit_types': []},
            multiplier,
            billing, periods,
            self.title_edit.text()
        )
        task.preview_ready.connect(self._on_render_ready)
        task.error_occurred.connect(self._on_render_error)
        task.task_done.connect(self._on_task_done)
        self._render_tasks[idx] = task
        QThreadPool.globalInstance().start(task)

    def _on_render_ready(self, idx, pix):
        self._param_fingerprints[idx] = self._get_rendering_fingerprint(idx)
        self.pixmap_cache[idx] = pix
        while len(self.pixmap_cache) > self._max_pixmap_cache:
            self.pixmap_cache.popitem(last=False)
        self._rendering_set.discard(idx)
        self._update_tree_status(idx, "已就绪")
        self._update_cache_progress()
        if idx == self.current_index:
            self._display_pixmap(pix)

    def _display_pixmap(self, pix):
        self.scene.clear()
        self.scene.addPixmap(pix)
        self.scene.setSceneRect(pix.rect())
        self.view.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)
        self.view.viewport().update()
        self._current_zoom = 1.0

    def _on_render_error(self, idx, error_msg):
        self._rendering_set.discard(idx)
        self._no_data_indices.add(idx)
        self.selected_indices.discard(idx)
        summary = self.all_summaries[idx]
        provider_error = self.provider.get_load_error(summary)
        if provider_error and provider_error not in error_msg:
            error_msg += f"\n详细信息: {provider_error}"
        self._update_tree_status(idx, "无有效数据")
        if idx == self.current_index:
            self.scene.clear()
            text_item = self.scene.addText(error_msg)
            text_item.setDefaultTextColor(QColor("red"))
            self.view.fitInView(self.scene.itemsBoundingRect(), Qt.KeepAspectRatio)
        self._update_cache_progress()

    def _on_task_done(self, idx):
        if idx in self._render_tasks:
            del self._render_tasks[idx]

    def _start_lazy_preload(self):
        remaining = [i for i in range(len(self.all_summaries))
                     if i not in self.pixmap_cache and i not in self._rendering_set and i not in self._no_data_indices]
        if not remaining:
            self._preload_timer.stop()
            return
        cur = self.current_index
        ordered = []
        for i in range(1, len(self.all_summaries) + 1):
            cand = (cur + i) % len(self.all_summaries)
            if cand in remaining:
                ordered.append(cand)
        self._preload_queue = ordered
        self._preload_timer.start()

    def _preload_step(self):
        self._preload_queue = [i for i in self._preload_queue
                               if i not in self.pixmap_cache and i not in self._rendering_set and i not in self._no_data_indices]
        if not self._preload_queue:
            self._preload_timer.stop()
            return
        idx = self._preload_queue.pop(0)
        self._schedule_render(idx)

    # ---------- 导航 ----------
    def _navigate_to(self, index):
        if index < 0 or index >= len(self.all_summaries) or index in self._no_data_indices:
            return
        self.current_index = index
        self.index_label.setText(f"{index + 1} / {len(self.all_summaries)}")
        self._update_info()
        self._update_multiplier_ui()
        self._update_billing_ui()
        self._schedule_render(index)
        if hasattr(self, '_professional_window') and self._professional_window.isVisible():
            self._professional_window.set_current_group(self.all_summaries[index])

    def _update_info(self):
        if self.all_summaries:
            s = self.all_summaries[self.current_index]
            self.info_label.setText(
                f"<b>表号:</b> {s.asset_value or '无'}<br><b>月份:</b> {s.month_str}<br>"
                f"<b>日期数:</b> {s.date_count}<br><b>时间点数:</b> {s.time_points}<br>"
                f"<b>负荷范围:</b> {s.min_val:.2f} ~ {s.max_val:.2f}"
            )
            self._update_title_preview()

    # ---------- 参数设置 ----------
    def _apply_multiplier(self):
        if self.mult_only_current_cb.isChecked():
            idx = self.current_index
            self._per_group_multiplier[idx] = self.multiplier_spin.value()
            self._update_multiplier_ui()
            self._refresh_specific_indices([idx])
        else:
            affected = [i for i in range(len(self.all_summaries)) if i not in self._per_group_multiplier]
            self._last_applied_multiplier = self.multiplier_spin.value()
            self._update_multiplier_ui()
            self._refresh_specific_indices(affected)
        # 专业窗口同步
        if hasattr(self, '_professional_window') and self._professional_window.isVisible():
            self._professional_window.invalidate_and_recalc()

    def _apply_billing(self):
        if self.bill_only_current_cb.isChecked():
            idx = self.current_index
            try:
                val = float(self.capacity_edit.text().strip())
            except:
                val = 0
            self._per_group_billing[idx] = {'type': 'capacity', 'value': val}
            self._update_billing_ui()
            self._refresh_specific_indices([idx])
        else:
            idx = self.current_index
            summary = self.all_summaries[idx]
            asset = summary.asset_value
            if self.bill_enabled_cb.isChecked():
                try:
                    val = float(self.capacity_edit.text().strip())
                    self.billing_dict[asset] = {'type': 'capacity', 'value': val}
                except:
                    self.billing_dict.pop(asset, None)
            else:
                self.billing_dict.pop(asset, None)
            affected = [i for i, s in enumerate(self.all_summaries)
                        if s.asset_value == asset and i not in self._per_group_billing]
            self._update_billing_ui()
            self._refresh_specific_indices(affected)
        if hasattr(self, '_professional_window') and self._professional_window.isVisible():
            self._professional_window.invalidate_and_recalc()

    def _apply_parameters(self, indices=None):
        if indices is None:
            indices = list(range(len(self.all_summaries)))
        for idx in indices:
            self.pixmap_cache.pop(idx, None)
            if idx in self._render_tasks:
                self._render_tasks[idx].cancel()
                del self._render_tasks[idx]
        self._rendering_set.clear()
        self.group_tree.blockSignals(True)
        for idx in indices:
            if idx not in self._no_data_indices:
                self._update_tree_status(idx, "等待加载...")
        self.group_tree.blockSignals(False)
        if self.current_index in indices:
            self._schedule_render(self.current_index)
        self._preload_queue.clear()
        self._preload_timer.stop()
        QTimer.singleShot(200, self._start_lazy_preload)
        self._update_cache_progress()
        # 专业窗口同步
        if hasattr(self, '_professional_window') and self._professional_window.isVisible():
            self._professional_window.invalidate_and_recalc()

    def _refresh_specific_indices(self, indices):
        self._apply_parameters(indices)

    # ---------- 策略变更精准刷新 ----------
    def _on_strategy_changed(self):
        new_strat = self.strategy_combo.currentText()
        if new_strat == self._last_strategy_text:
            return
        self._last_strategy_text = new_strat
        changed_indices = []
        for idx in range(len(self.all_summaries)):
            s = self.all_summaries[idx]
            new_periods = self._get_periods_for_summary(s)
            new_fp = hash(tuple((p['start'], p['end'], p['type']) for p in new_periods))
            old_fp = self._periods_fingerprint_map.get(idx, 0)
            if new_fp != old_fp:
                changed_indices.append(idx)
            self._periods_fingerprint_map[idx] = new_fp
        if changed_indices:
            self._refresh_specific_indices(changed_indices)
        if hasattr(self, '_professional_window') and self._professional_window.isVisible():
            self._professional_window.invalidate_and_recalc()

    # ---------- 树控件相关 ----------
    def _rebuild_tree(self):
        self.group_tree.blockSignals(True)
        self.group_tree.clear()
        summaries = list(enumerate(self.all_summaries))
        if self.sort_combo.currentText() == "按月份":
            summaries.sort(key=lambda x: x[1].month_str)
        else:
            summaries.sort(key=lambda x: x[1].asset_value or '')
        asset_dict = OrderedDict()
        for idx, s in summaries:
            asset = s.asset_value or "未知表号"
            asset_dict.setdefault(asset, []).append((idx, s))
        for asset, items in asset_dict.items():
            asset_item = QTreeWidgetItem([asset, ""])
            asset_item.setFlags(asset_item.flags() | Qt.ItemIsUserCheckable)
            asset_item.setData(0, Qt.UserRole, -1)
            self.group_tree.addTopLevelItem(asset_item)
            for idx, s in items:
                month_item = QTreeWidgetItem([s.month_str, "等待加载..."])
                month_item.setFlags(month_item.flags() | Qt.ItemIsUserCheckable)
                month_item.setData(0, Qt.UserRole, idx)
                if idx in self._no_data_indices:
                    month_item.setCheckState(0, Qt.Unchecked)
                    month_item.setForeground(0, QColor('gray'))
                else:
                    month_item.setCheckState(0, Qt.Checked if idx in self.selected_indices else Qt.Unchecked)
                asset_item.addChild(month_item)
            all_checked = all(asset_item.child(i).checkState(0) == Qt.Checked for i in range(asset_item.childCount()))
            any_checked = any(asset_item.child(i).checkState(0) == Qt.Checked for i in range(asset_item.childCount()))
            if all_checked:
                asset_item.setCheckState(0, Qt.Checked)
            elif any_checked:
                asset_item.setCheckState(0, Qt.PartiallyChecked)
            else:
                asset_item.setCheckState(0, Qt.Unchecked)
        self.group_tree.expandAll()
        self.group_tree.blockSignals(False)
        self._filter_tree()
        self._update_cache_progress()

    def _filter_tree(self):
        text = self.search_edit.text().strip().lower()
        for i in range(self.group_tree.topLevelItemCount()):
            asset_item = self.group_tree.topLevelItem(i)
            asset_visible = False
            for j in range(asset_item.childCount()):
                child = asset_item.child(j)
                match = not text or text in child.text(0).lower() or text in asset_item.text(0).lower()
                child.setHidden(not match)
                if match:
                    asset_visible = True
            asset_item.setHidden(not asset_visible)

    def _on_tree_item_clicked(self, item, col):
        idx = item.data(0, Qt.UserRole)
        if idx is not None and idx >= 0 and idx not in self._no_data_indices:
            self._navigate_to(idx)

    def _on_tree_item_changed(self, item, col):
        if item.data(0, Qt.UserRole) == -1:
            state = item.checkState(0)
            self.group_tree.blockSignals(True)
            for i in range(item.childCount()):
                child = item.child(i)
                child.setCheckState(0, state)
                cidx = child.data(0, Qt.UserRole)
                if cidx >= 0 and cidx not in self._no_data_indices:
                    if state == Qt.Checked:
                        self.selected_indices.add(cidx)
                    else:
                        self.selected_indices.discard(cidx)
            self.group_tree.blockSignals(False)
            self._update_parent_check(item)
        else:
            idx = item.data(0, Qt.UserRole)
            if idx >= 0 and idx not in self._no_data_indices:
                if item.checkState(0) == Qt.Checked:
                    self.selected_indices.add(idx)
                else:
                    self.selected_indices.discard(idx)
                parent = item.parent()
                if parent:
                    self._update_parent_check(parent)

    def _update_parent_check(self, parent):
        self.group_tree.blockSignals(True)
        all_checked = all(parent.child(i).checkState(0) == Qt.Checked for i in range(parent.childCount()))
        any_checked = any(parent.child(i).checkState(0) == Qt.Checked for i in range(parent.childCount()))
        if all_checked:
            parent.setCheckState(0, Qt.Checked)
        elif any_checked:
            parent.setCheckState(0, Qt.PartiallyChecked)
        else:
            parent.setCheckState(0, Qt.Unchecked)
        self.group_tree.blockSignals(False)

    def _set_all_check(self, checked):
        self.selected_indices = set(range(len(self.all_summaries))) - self._no_data_indices if checked else set()
        self._rebuild_tree()

    def _select_by_asset_dialog(self):
        assets = set(s.asset_value for s in self.all_summaries if s.asset_value)
        if not assets:
            QMessageBox.information(self, "提示", "没有识别到任何表号")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("按表号多选")
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel("勾选表号:"))
        checks = {}
        for a in sorted(assets):
            cb = QCheckBox(a)
            checks[a] = cb
            layout.addWidget(cb)
        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)
        layout.addWidget(btn_box)
        if dlg.exec() == QDialog.Accepted:
            targets = [a for a, cb in checks.items() if cb.isChecked()]
            for i, s in enumerate(self.all_summaries):
                if s.asset_value in targets and i not in self._no_data_indices:
                    self.selected_indices.add(i)
            self._rebuild_tree()

    def _update_tree_status(self, idx, status):
        self.group_tree.blockSignals(True)
        for i in range(self.group_tree.topLevelItemCount()):
            asset_item = self.group_tree.topLevelItem(i)
            for j in range(asset_item.childCount()):
                child = asset_item.child(j)
                if child.data(0, Qt.UserRole) == idx:
                    child.setText(1, status)
                    break
        self.group_tree.blockSignals(False)

    def _update_cache_progress(self):
        cached = len(self.pixmap_cache)
        total = len(self.all_summaries) - len(self._no_data_indices)
        self.cache_progress_label.setText(f"已缓存: {cached} / {total}")

    def _get_periods_for_summary(self, summary):
        sn = self.strategy_combo.currentText()
        if sn == "无" or not sn:
            return []
        strat = self.config_manager.get_strat(sn)
        if not strat:
            return []
        m = summary.month_str.split('-')[1] if '-' in summary.month_str else summary.month_str
        try:
            month_int = int(m)
        except:
            month_int = 1
        return strat.get(str(month_int), [])

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
        self.bill_enabled_cb.setChecked(settings.get('billing_enabled', False))
        self.capacity_edit.setText(str(settings.get('capacity_value', '')))
        self.title_edit.setText(settings.get('title_template', ''))
        self.filename_edit.setText(settings.get('filename_template', '{table_no}_{year_month}_负荷曲线'))
        self._per_group_multiplier.clear()
        self._per_group_billing.clear()
        self._apply_parameters()

    def _save_as_template(self):
        name, ok = QInputDialog.getText(self, "保存参数模板", "输入模板名称:")
        if not ok or not name.strip():
            return
        name = name.strip()
        settings = {
            'multiplier': self.multiplier_spin.value(),
            'show_daily': self.show_daily_cb.isChecked(),
            'show_avg': self.show_avg_cb.isChecked(),
            'show_max_demand': self.show_max_demand_cb.isChecked(),
            'billing_enabled': self.bill_enabled_cb.isChecked(),
            'capacity_value': self.capacity_edit.text(),
            'title_template': self.title_edit.text(),
            'filename_template': self.filename_edit.text()
        }
        self.config_manager.save_template(name, settings)
        self._refresh_template_list()

    def _set_title_tpl(self, tpl):
        self.title_edit.setText(tpl)

    def _update_title_preview(self):
        if 0 <= self.current_index < len(self.all_summaries):
            s = self.all_summaries[self.current_index]
            ctx = {'year_month': s.month_str, 'table_no': s.asset_value or '',
                   'asset': s.asset_value or '', 'sheet': s.sheet_name}
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
        dlg.exec()
        self.strategy_combo.clear()
        self.strategy_combo.addItem("无")
        self.strategy_combo.addItems(self.config_manager.strat_names())
        if dlg.result:
            self.strategy_combo.setCurrentText(dlg.result)
        elif old_sel and old_sel in self.config_manager.strat_names():
            self.strategy_combo.setCurrentText(old_sel)
        else:
            self.strategy_combo.setCurrentText("无")

    def _zoom(self, factor):
        self.view.scale(factor, factor)
        self._current_zoom *= factor

    def _prev_group(self):
        if self.current_index > 0:
            new_idx = self.current_index - 1
            while new_idx >= 0 and new_idx in self._no_data_indices:
                new_idx -= 1
            if new_idx >= 0:
                self._navigate_to(new_idx)

    def _next_group(self):
        if self.current_index < len(self.all_summaries) - 1:
            new_idx = self.current_index + 1
            while new_idx < len(self.all_summaries) and new_idx in self._no_data_indices:
                new_idx += 1
            if new_idx < len(self.all_summaries):
                self._navigate_to(new_idx)

    def keyPressEvent(self, event):
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

    def _export_midwide(self):
        if self.current_index < 0 or self.current_index >= len(self.all_summaries):
            return
        idx = self.current_index
        summary = self.all_summaries[idx]
        data = self.provider.get_group_data(summary)
        if not data:
            QMessageBox.warning(self, "提示", "当前分组无有效数据，无法导出中间宽表")
            return
        try:
            out_dir = self.output_dir_edit.text()
            os.makedirs(out_dir, exist_ok=True)
            mstr = summary.month_str.replace('-', '')
            asset = summary.asset_value or '未知'
            raw_filename = f"中间宽表_{asset}_{mstr}.xlsx"
            safe_name = _sanitize_name(raw_filename)
            filepath = os.path.join(out_dir, safe_name)
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

    # ---------- 专业模式 ----------
    def _open_professional(self):
        if not self.all_summaries:
            QMessageBox.information(self, "提示", "请先添加并解析至少一个负荷数据文件。")
            return

        # 获取当前策略和是否有容量线
        strategy_name = self.strategy_combo.currentText()
        strategy = self.config_manager.get_strat(strategy_name) if strategy_name != "无" else None

        # 检查是否有任何分组设置了容量线
        has_any_capacity = any(
            self.get_effective_billing(i) is not None for i in range(len(self.all_summaries))
        )

        # 弹出模式选择对话框（校验由对话框内部完成）
        mode_dlg = ProfessionalModeDialog(self, strategy_name, strategy, has_any_capacity)
        if mode_dlg.exec() != QDialog.Accepted:
            return

        selected_mode = mode_dlg.selected_mode

        # 根据模式准备 ProfessionalWindow 参数
        if not hasattr(self, '_professional_window'):
            self._professional_window = ProfessionalWindow(self, selected_mode)
        else:
            self._professional_window.set_mode(selected_mode)
        self._professional_window.show()
        self._professional_window.raise_()
        self._professional_window.set_current_group(self.all_summaries[self.current_index])

    def _accept(self):
        if not self.selected_indices:
            QMessageBox.warning(self, "提示", "请至少选择一个分组")
            return

        self.config_manager.set_app('show_daily', self.show_daily_cb.isChecked())
        self.config_manager.set_app('show_avg', self.show_avg_cb.isChecked())
        self.config_manager.set_app('show_max_demand', self.show_max_demand_cb.isChecked())
        self.config_manager.set_app('last_strategy_name',
                                    self.strategy_combo.currentText() if self.strategy_combo.currentText() != "无" else None)
        self.config_manager.set_app('last_billing_enabled', self.bill_enabled_cb.isChecked())
        self.config_manager.set_app('last_billing_value', self.capacity_edit.text())
        self.config_manager.set_app('last_title_template', self.title_edit.text())
        self.config_manager.set_app('last_filename_template', self.filename_edit.text())
        self.config_manager.set_app('classify_by_asset', self.classify_cb.isChecked())
        self.config_manager.set_app('last_multiplier', self.multiplier_spin.value())

        confirm_dlg = ExportConfirmDialog(
            self, self.all_summaries, self.selected_indices,
            self.filename_edit.text(), self.title_edit.text(),
            self.output_dir_edit.text(), self.classify_cb.isChecked(),
            per_group_multiplier=self._per_group_multiplier,
            per_group_billing=self._per_group_billing,
            billing_dict=self.billing_dict,
            global_multiplier=self.multiplier_spin.value(),
            config_manager=self.config_manager
        )
        if confirm_dlg.exec() == QDialog.Accepted:
            final_indices = confirm_dlg.result['indices']
            if not final_indices:
                QMessageBox.warning(self, "提示", "没有选择任何分组")
                return
            self.result = {
                'selected_indices': final_indices,
                'global_multiplier': self.multiplier_spin.value(),
                'per_group_multiplier': confirm_dlg.result.get('multipliers', {}),
                'output_dir': confirm_dlg.output_dir,
                'strategy_name': self.strategy_combo.currentText() if self.strategy_combo.currentText() != "无" else None,
                'show_daily': self.show_daily_cb.isChecked(),
                'show_avg': self.show_avg_cb.isChecked(),
                'show_max_demand': self.show_max_demand_cb.isChecked(),
                'title_template': self.title_edit.text().strip(),
                'filename_template': self.filename_edit.text().strip(),
                'billing_dict': confirm_dlg.result.get('billings', {}),
                'classify_by_asset': self.classify_cb.isChecked(),
                'per_group_billing': confirm_dlg.result.get('billings', {})
            }
            self.accept()

    def closeEvent(self, event):
        for idx in list(self._render_tasks.keys()):
            if idx in self._render_tasks:
                self._render_tasks[idx].cancel()
        self._render_tasks.clear()
        self.pixmap_cache.clear()
        self._preload_timer.stop()
        if self.config_manager:
            self.config_manager.set_window_geometry("PreviewDialog", self.saveGeometry())
            self.config_manager.set_splitter_state("preview_splitter", self.splitter.saveState())
        if hasattr(self, '_professional_window'):
            self._professional_window.close()
        super().closeEvent(event)

# ======================= ProfessionalModeDialog =======================
class ProfessionalModeDialog(QDialog):
    def __init__(self, parent, strategy_name, strategy, has_capacity):
        super().__init__(parent)
        self.setWindowTitle("选择专业分析模式")
        self.selected_mode = None
        self.strategy_name = strategy_name
        self.strategy = strategy  # dict or None
        self.has_capacity = has_capacity

        layout = QVBoxLayout(self)

        label = QLabel("请选择要进行的分析模式：")
        layout.addWidget(label)

        self.mode_combo = QComboBox()
        modes = ["需量管理 / 峰谷套利", "纯光伏消纳", "光伏消纳 + 峰谷套利（混合）"]
        self.mode_combo.addItems(modes)
        layout.addWidget(self.mode_combo)

        # 提示信息
        self.hint_label = QLabel("")
        self.hint_label.setWordWrap(True)
        layout.addWidget(self.hint_label)

        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self._validate_and_accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        self.mode_combo.currentIndexChanged.connect(self._update_hint)
        self._update_hint()

    def _update_hint(self):
        idx = self.mode_combo.currentIndex()
        if idx == 0:  # 需量管理
            has_charge = False
            has_discharge = False
            if self.strategy:
                for month_periods in self.strategy.values():
                    for p in month_periods:
                        if p['type'] == 'charge':
                            has_charge = True
                        if p['type'] == 'discharge':
                            has_discharge = True
            conditions = []
            if not self.strategy:
                conditions.append("• 尚未选择充放策略（请返回预览界面选择一个策略，或点击“管理策略”新建）")
            else:
                if not has_charge:
                    conditions.append("• 策略中缺少充电时段（请在策略中添加充电时段）")
                if not has_discharge:
                    conditions.append("• 策略中缺少放电时段（请在策略中添加放电时段）")
            if not self.has_capacity:
                conditions.append("• 未设置任何容量线（请在预览界面启用计费容量线并填入数值）")
            if conditions:
                self.hint_label.setText("⚠ 缺少以下条件：\n" + "\n".join(conditions))
            else:
                self.hint_label.setText("✓ 已满足所有必需条件：充放电时段 + 容量线")
        elif idx == 1:  # 纯光伏消纳
            self.hint_label.setText("✓ 无需充放策略或容量线\n进入分析窗口后请设置储能额定功率")
        else:  # 混合模式
            has_discharge = False
            if self.strategy:
                for month_periods in self.strategy.values():
                    if any(p['type'] == 'discharge' for p in month_periods):
                        has_discharge = True
                        break
            if not self.strategy or not has_discharge:
                self.hint_label.setText("⚠ 缺少放电时段（需在充放策略中设置放电时段）\n请返回预览界面选择或新建策略")
            else:
                self.hint_label.setText("✓ 已满足放电时段要求\n容量线可选，需在分析窗口输入储能额定功率")

    def _validate_and_accept(self):
        idx = self.mode_combo.currentIndex()
        if idx == 0:  # 需量管理
            if not self.strategy or not self.has_capacity:
                QMessageBox.warning(self, "条件不满足",
                    "缺少充放策略或容量线，请先在预览界面设置。\n\n"
                    "• 设置充放策略：在预览界面下拉框选择或点击“管理策略”新建\n"
                    "• 设置容量线：勾选“计费容量线”并输入数值")
                return
            has_charge = any(any(p['type'] == 'charge' for p in v) for v in self.strategy.values())
            has_discharge = any(any(p['type'] == 'discharge' for p in v) for v in self.strategy.values())
            if not has_charge or not has_discharge:
                QMessageBox.warning(self, "条件不满足",
                    "策略中必须同时包含充电和放电时段。\n请在策略编辑界面添加对应时段。")
                return
        elif idx == 2:  # 混合模式
            if not self.strategy:
                QMessageBox.warning(self, "条件不满足",
                    "混合模式需要设置充放策略（至少包含放电时段）。\n请返回预览界面选择或新建策略。")
                return
            has_discharge = any(any(p['type'] == 'discharge' for p in v) for v in self.strategy.values())
            if not has_discharge:
                QMessageBox.warning(self, "条件不满足",
                    "策略中必须包含放电时段。\n请编辑策略添加至少一个“放电(高峰)”时段。")
                return

        self.selected_mode = self.mode_combo.currentText()
        self.accept()
# -*- coding: utf-8 -*-
"""
负荷曲线批量生成工具 Pro 融合版 (最终优化版)
第四部分：ProfessionalWindow、PVFeedbackDetailDialog、ExportConfirmDialog、FileListWidget、DropOverlay
"""

# ======================= 信号类 =======================
class CalcSignals(QObject):
    finished = Signal()
    progress = Signal(int)
    total_months = Signal(int)

# ======================= 模块级计算函数（供多进程调用） =======================
def _calc_single_month(month_str, mult, billing, periods, cached_data, mode, ignore_negative, P_rated, valid_threshold):
    """独立计算函数，供多进程/线程池调用"""
    try:
        # 构造一个最小化的假 summary 对象，仅用于兼容 compute_spaces_for_summary 签名
        class FakeSummary:
            pass
        summary = FakeSummary()
        summary.file_path = ''
        summary.sheet_name = ''
        summary.month_str = month_str
        summary.asset_value = ''

        result = compute_spaces_for_summary(
            summary, None, mult, billing, periods,
            valid_threshold=valid_threshold,
            mode='fixed' if mode == "需量管理 / 峰谷套利" else
                 'pv_self' if mode == "纯光伏消纳" else 'pv_peak',
            clip_negative=ignore_negative if mode == "需量管理 / 峰谷套利" else False,
            P_rated=P_rated if mode != "需量管理 / 峰谷套利" else None,
            cached_data=cached_data
        )
        return result['daily_min'][result['valid_mask']]
    except Exception:
        return None

# ======================= 后台计算工作类（多进程版） =======================
class CalcWorker(QRunnable):
    def __init__(self, parent_window, summaries, mode, ignore_negative, P_rated, valid_threshold):
        super().__init__()
        self.parent = parent_window
        self.summaries = summaries
        self.mode = mode
        self.ignore_negative = ignore_negative
        self.P_rated = P_rated
        self.valid_threshold = valid_threshold
        self.signals = CalcSignals()

    def run(self):
        tasks = []
        for i, summ in enumerate(self.summaries):
            if self.parent._stop_calc:
                return
            idx = self.parent._find_summary_index(summ)
            mult = self.parent.preview_dialog.get_effective_multiplier(idx) if idx is not None else 1.0
            billing = self.parent.preview_dialog.get_effective_billing(idx) if idx is not None else None

            strategy_name = self.parent.preview_dialog.strategy_combo.currentText()
            periods = []
            if strategy_name and strategy_name != "无":
                strat = self.parent.config_manager.get_strat(strategy_name)
                if strat:
                    m = summ.month_str.split('-')[1] if '-' in summ.month_str else summ.month_str
                    try:
                        month_int = int(m)
                    except:
                        month_int = 1
                    periods = strat.get(str(month_int), [])

            if self.mode == "需量管理 / 峰谷套利" and (not billing or not periods):
                continue
            if self.mode == "光伏消纳 + 峰谷套利（混合）" and not periods:
                continue

            cache_key = (summ.file_path, summ.sheet_name, summ.month_str, summ.asset_value)
            cached_data = self.parent._data_cache.get(cache_key)
            if not cached_data:
                cached_data = self.parent.provider.get_group_data(summ)
                if cached_data:
                    self.parent._data_cache[cache_key] = cached_data
            if not cached_data:
                continue

            tasks.append((summ.month_str, mult, billing, periods, cached_data))

        total = len(tasks)
        self.signals.total_months.emit(total)

        if not tasks:
            self.parent._daily_min_all = np.array([])
            self.parent._sorted_mins = np.array([])
            self.parent._total_valid_days = 0
            self.signals.finished.emit()
            return

        all_daily_mins = []
        max_workers = min(4, max(1, mp.cpu_count() - 1))

        def process_one(task):
            month_str, mult, billing, periods, cached_data = task
            return _calc_single_month(month_str, mult, billing, periods, cached_data,
                                      self.mode, self.ignore_negative, self.P_rated, self.valid_threshold)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(process_one, task): i for i, task in enumerate(tasks)}
            for future in as_completed(futures):
                if self.parent._stop_calc:
                    executor.shutdown(wait=False, cancel_futures=True)
                    return
                idx = futures[future]
                try:
                    mins = future.result()
                    if mins is not None and len(mins) > 0:
                        all_daily_mins.append(mins)
                except Exception:
                    pass
                self.signals.progress.emit(idx + 1)

        self.parent._daily_min_all = np.concatenate(all_daily_mins) if all_daily_mins else np.array([])
        self.parent._sorted_mins = np.sort(self.parent._daily_min_all)[::-1]
        self.parent._total_valid_days = len(self.parent._sorted_mins)
        self.signals.finished.emit()

# ======================= ProfessionalWindow（最终高性能版，异步月度详情） =======================
class ProfessionalWindow(QDialog):
    """基于目标天数的储能容量反推与配置评估，支持三模式，后台多进程计算"""

    def __init__(self, preview_dialog, mode, parent=None):
        super().__init__(parent)
        self.preview_dialog = preview_dialog
        self.config_manager = preview_dialog.config_manager
        self.provider = preview_dialog.provider
        self.mode = mode
        self.setWindowTitle(f"专业分析 - {mode}")
        self.setMinimumSize(700, 550)

        # ---- 内部状态 ----
        self._current_asset = None
        self._daily_min_all = np.array([])
        self._total_valid_days = 0
        self._sorted_mins = np.array([])
        self._valid_threshold = 0.01
        self._ignore_negative = False
        self._P_rated = 200.0
        self._stop_calc = False
        self._worker = None
        self._data_cache = {}   # 分组数据缓存

        # ---- UI 构建 ----
        main_layout = QVBoxLayout(self)

        # 标题
        self.title_label = QLabel(f"专业分析 — {mode}  — 表号: <待选择>")
        self.title_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        main_layout.addWidget(self.title_label)

        # ---- 储能配置评估 ----
        assess_group = QGroupBox("储能配置评估")
        form_layout = QFormLayout(assess_group)

        self.target_days_spin = QSpinBox()
        self.target_days_spin.setRange(1, 366)
        self.target_days_spin.setSuffix(" 天")
        self.target_days_spin.valueChanged.connect(self._on_target_changed)
        form_layout.addRow("目标满充满放天数:", self.target_days_spin)

        self.valid_days_label = QLabel("0 天")
        form_layout.addRow("当前有效天数:", self.valid_days_label)

        self.C_target_label = QLabel("-- kWh")
        self.C_target_label.setStyleSheet("font-weight: bold;")
        form_layout.addRow("满足目标所需最小容量:", self.C_target_label)

        self.unit_capacity_spin = QDoubleSpinBox()
        self.unit_capacity_spin.setRange(1, 10000)
        self.unit_capacity_spin.setValue(200)
        self.unit_capacity_spin.setSuffix(" kWh")
        self.unit_capacity_spin.valueChanged.connect(self._on_config_changed)
        form_layout.addRow("单柜容量:", self.unit_capacity_spin)

        self.factor_spin = QDoubleSpinBox()
        self.factor_spin.setRange(0.5, 5.0)
        self.factor_spin.setSingleStep(0.1)
        self.factor_spin.setValue(1.0)
        self.factor_spin.setDecimals(2)
        self.factor_spin.valueChanged.connect(self._on_config_changed)
        form_layout.addRow("配置系数:", self.factor_spin)

        self.cabinet_count_label = QLabel("-- 台")
        form_layout.addRow("建议柜数 (基础):", self.cabinet_count_label)

        self.C_actual_label = QLabel("-- kWh")
        form_layout.addRow("实际配置总容量:", self.C_actual_label)

        self.actual_days_label = QLabel("-- 天 (--%)")
        self.actual_days_progress = QProgressBar()
        self.actual_days_progress.setRange(0, 100)
        form_layout.addRow("实际满充满放天数:", self.actual_days_label)
        form_layout.addRow("占比:", self.actual_days_progress)

        # 储能额定功率
        self.P_rated_spin = QDoubleSpinBox()
        self.P_rated_spin.setRange(1, 10000)
        self.P_rated_spin.setValue(200)
        self.P_rated_spin.setSuffix(" kW")
        self.P_rated_spin.valueChanged.connect(self._on_P_rated_changed)
        form_layout.addRow("储能额定功率:", self.P_rated_spin)

        self.ignore_negative_cb = QCheckBox("忽略负荷负值（光伏返送视为零负荷）")
        self.ignore_negative_cb.stateChanged.connect(self._on_ignore_negative_changed)
        form_layout.addRow("", self.ignore_negative_cb)

        # 计算进度
        self.calc_progress = QProgressBar()
        self.calc_progress.setRange(0, 1)
        self.calc_progress.setVisible(False)
        form_layout.addRow("计算进度:", self.calc_progress)

        main_layout.addWidget(assess_group)

        # ---- 有效天阈值 ----
        thresh_layout = QHBoxLayout()
        thresh_layout.addWidget(QLabel("有效天最小空间阈值:"))
        self.thresh_spin = QDoubleSpinBox()
        self.thresh_spin.setRange(0.0, 100.0)
        self.thresh_spin.setDecimals(4)
        self.thresh_spin.setSingleStep(0.001)
        self.thresh_spin.setValue(0.01)
        self.thresh_spin.setSuffix(" kWh")
        self.thresh_spin.valueChanged.connect(self._on_threshold_changed)
        thresh_layout.addWidget(self.thresh_spin)
        main_layout.addLayout(thresh_layout)

        # 光伏返送分析按钮
        self.pv_feedback_btn = QPushButton("分析当前月份光伏返送")
        self.pv_feedback_btn.clicked.connect(self._show_pv_feedback)
        main_layout.addWidget(self.pv_feedback_btn)

        # ---- 当月详情 ----
        month_group = QGroupBox("当前月份详情")
        month_form = QFormLayout(month_group)

        self.month_label = QLabel("--")
        month_form.addRow("月份:", self.month_label)

        self.month_charge_label = QLabel("-- kWh")
        month_form.addRow("月充电空间:", self.month_charge_label)

        self.month_discharge_label = QLabel("-- kWh")
        month_form.addRow("月放电空间:", self.month_discharge_label)

        self.month_valid_days_label = QLabel("-- 天")
        month_form.addRow("当月有效天数:", self.month_valid_days_label)

        self.month_full_days_label = QLabel("-- 天")
        month_form.addRow("当月满充满放天数:", self.month_full_days_label)

        main_layout.addWidget(month_group)

        # 底部提示
        note_label = QLabel("提示：修改参数将实时更新评估结果。")
        note_label.setStyleSheet("color: gray; font-size: 10px;")
        main_layout.addWidget(note_label)

        self._update_ui_by_mode()
        self._init_data()

    def _update_ui_by_mode(self):
        if self.mode == "需量管理 / 峰谷套利":
            self.P_rated_spin.setEnabled(False)
            self.ignore_negative_cb.setEnabled(True)
        elif self.mode == "纯光伏消纳":
            self.P_rated_spin.setEnabled(True)
            self.ignore_negative_cb.setEnabled(False)
            self.ignore_negative_cb.setChecked(False)
        else:
            self.P_rated_spin.setEnabled(True)
            self.ignore_negative_cb.setEnabled(False)
            self.ignore_negative_cb.setChecked(False)

    def set_mode(self, mode):
        self.mode = mode
        self.setWindowTitle(f"专业分析 - {mode}")
        self._update_ui_by_mode()
        self._data_cache.clear()
        self.invalidate_and_recalc()

    def set_current_group(self, summary):
        new_asset = summary.asset_value
        if new_asset != self._current_asset:
            self._current_asset = new_asset
            self.title_label.setText(f"专业分析 — {self.mode}  — 表号: {new_asset or '未知'}")
            self._data_cache.clear()
            self._start_rebuild_async()
        self._update_monthly_detail_async(summary)

    def invalidate_and_recalc(self):
        if self._current_asset:
            self._data_cache.clear()
            self._start_rebuild_async()

    def _init_data(self):
        if self.preview_dialog.all_summaries:
            first_idx = self.preview_dialog.current_index
            if 0 <= first_idx < len(self.preview_dialog.all_summaries):
                summary = self.preview_dialog.all_summaries[first_idx]
                self.set_current_group(summary)

    def _start_rebuild_async(self):
        if not self._current_asset:
            return
        if self._worker:
            self._stop_calc = True
        self._stop_calc = False

        asset_summaries = [s for s in self.preview_dialog.all_summaries
                           if s.asset_value == self._current_asset]
        if not asset_summaries:
            self._daily_min_all = np.array([])
            self._sorted_mins = np.array([])
            self._total_valid_days = 0
            self._update_display()
            return

        self._set_inputs_enabled(False)
        self.calc_progress.setVisible(True)
        self.calc_progress.setRange(0, len(asset_summaries))
        self.calc_progress.setValue(0)

        self._worker = CalcWorker(self, asset_summaries, self.mode,
                                  self._ignore_negative, self._P_rated, self._valid_threshold)
        self._worker.signals.progress.connect(self._on_calc_progress)
        self._worker.signals.finished.connect(self._on_calc_finished)
        QThreadPool.globalInstance().start(self._worker)

    def _on_calc_progress(self, val):
        self.calc_progress.setValue(val)

    def _on_calc_finished(self):
        self._worker = None
        self._stop_calc = False
        self._set_inputs_enabled(True)
        self.calc_progress.setVisible(False)
        self._total_valid_days = len(self._sorted_mins)
        self.target_days_spin.setMaximum(max(1, self._total_valid_days))
        self.target_days_spin.setValue(min(self.target_days_spin.value(), self._total_valid_days))
        self._update_display()
        # 异步刷新当月详情
        if self.preview_dialog.all_summaries:
            curr_idx = self.preview_dialog.current_index
            if 0 <= curr_idx < len(self.preview_dialog.all_summaries):
                self._update_monthly_detail_async(self.preview_dialog.all_summaries[curr_idx])

    def _set_inputs_enabled(self, enabled):
        self.target_days_spin.setEnabled(enabled)
        self.unit_capacity_spin.setEnabled(enabled)
        self.factor_spin.setEnabled(enabled)
        self.P_rated_spin.setEnabled(enabled and self.mode != "需量管理 / 峰谷套利")
        self.ignore_negative_cb.setEnabled(enabled and self.mode == "需量管理 / 峰谷套利")
        self.thresh_spin.setEnabled(enabled)

    def _on_target_changed(self):
        self._update_display()

    def _on_config_changed(self):
        self._update_display()

    def _on_threshold_changed(self, val):
        self._valid_threshold = val
        self._start_rebuild_async()

    def _on_P_rated_changed(self, val):
        self._P_rated = val
        if self.mode != "需量管理 / 峰谷套利":
            self._start_rebuild_async()

    def _on_ignore_negative_changed(self):
        self._ignore_negative = self.ignore_negative_cb.isChecked()
        if self.mode == "需量管理 / 峰谷套利":
            self._start_rebuild_async()

    def _update_display(self):
        self.valid_days_label.setText(f"{self._total_valid_days} 天")

        target_n = self.target_days_spin.value()
        if self._total_valid_days < target_n:
            self.C_target_label.setText("有效天数不足")
            C_target = None
        else:
            C_target = self._sorted_mins[target_n - 1] if target_n <= len(self._sorted_mins) else 0
            self.C_target_label.setText(f"{C_target:.2f} kWh")

        U = self.unit_capacity_spin.value()
        k = self.factor_spin.value()

        if C_target is not None and C_target > 0:
            base_cabinets = max(1, int(np.ceil(C_target / U)))
            self.cabinet_count_label.setText(f"{base_cabinets} 台")
            C_actual = base_cabinets * U * k
            self.C_actual_label.setText(f"{C_actual:.2f} kWh")

            if self._total_valid_days > 0:
                actual_days = np.sum(self._sorted_mins >= C_actual)
                pct = (actual_days / self._total_valid_days) * 100 if self._total_valid_days > 0 else 0
                self.actual_days_label.setText(f"{actual_days} 天 ({pct:.1f}%)")
                self.actual_days_progress.setValue(int(pct))
            else:
                self.actual_days_label.setText("-- 天")
                self.actual_days_progress.setValue(0)
        else:
            self.cabinet_count_label.setText("-- 台")
            self.C_actual_label.setText("-- kWh")
            self.actual_days_label.setText("-- 天")
            self.actual_days_progress.setValue(0)

    # ---- 异步月度详情 ----
    def _update_monthly_detail_async(self, summary):
        """在后台线程中计算当月详情，计算完成后通过信号更新 UI"""
        if not self._current_asset or summary.asset_value != self._current_asset:
            return

        class MonthDetailWorker(QRunnable):
            def __init__(self, window, summary):
                super().__init__()
                self.window = window
                self.summary = summary
            def run(self):
                idx = self.window._find_summary_index(self.summary)
                mult = self.window.preview_dialog.get_effective_multiplier(idx) if idx is not None else 1.0
                billing = self.window.preview_dialog.get_effective_billing(idx) if idx is not None else None
                strategy_name = self.window.preview_dialog.strategy_combo.currentText()
                periods = []
                if strategy_name and strategy_name != "无":
                    strat = self.window.config_manager.get_strat(strategy_name)
                    if strat:
                        m = self.summary.month_str.split('-')[1] if '-' in self.summary.month_str else self.summary.month_str
                        try:
                            month_int = int(m)
                        except:
                            month_int = 1
                        periods = strat.get(str(month_int), [])

                cache_key = (self.summary.file_path, self.summary.sheet_name, self.summary.month_str, self.summary.asset_value)
                cached_data = self.window._data_cache.get(cache_key) or self.window.provider.get_group_data(self.summary)
                if not cached_data:
                    self._emit_result(None, "无法加载数据")
                    return

                try:
                    result = compute_spaces_for_summary(
                        self.summary, self.window.provider, mult, billing, periods,
                        valid_threshold=self.window._valid_threshold,
                        mode='fixed' if self.window.mode == "需量管理 / 峰谷套利" else
                             'pv_self' if self.window.mode == "纯光伏消纳" else 'pv_peak',
                        clip_negative=self.window._ignore_negative if self.window.mode == "需量管理 / 峰谷套利" else False,
                        P_rated=self.window._P_rated if self.window.mode != "需量管理 / 峰谷套利" else None,
                        cached_data=cached_data
                    )
                    self._emit_result(result, None)
                except Exception as e:
                    self._emit_result(None, str(e))

            def _emit_result(self, result, error_msg):
                # 必须在主线程更新 UI，使用 QTimer 调度
                QTimer.singleShot(0, lambda: self.window._on_month_detail_ready(result, error_msg))

        worker = MonthDetailWorker(self, summary)
        QThreadPool.globalInstance().start(worker)

    def _on_month_detail_ready(self, result, error_msg):
        if error_msg:
            self.month_charge_label.setText("出错")
            self.month_discharge_label.setText("出错")
            self.month_valid_days_label.setText("出错")
            self.month_full_days_label.setText(error_msg)
            return

        self.month_label.setText(result.get('month_str', ''))
        self.month_charge_label.setText(f"{result['monthly_charge_total']:.2f} kWh")
        self.month_discharge_label.setText(f"{result['monthly_discharge_total']:.2f} kWh")
        valid_days = np.sum(result['valid_mask'])
        self.month_valid_days_label.setText(f"{valid_days} 天")

        C_actual_text = self.C_actual_label.text().split()[0]
        try:
            C_actual_val = float(C_actual_text)
        except ValueError:
            C_actual_val = None
        if C_actual_val is not None:
            full_days = np.sum(result['daily_min'][result['valid_mask']] >= C_actual_val)
            self.month_full_days_label.setText(f"{full_days} 天")
        else:
            self.month_full_days_label.setText("计算中...")

    # ---- 光伏返送分析 (保留原实现) ----
    def _show_pv_feedback(self):
        if not self.preview_dialog.all_summaries:
            return
        current_idx = self.preview_dialog.current_index
        if current_idx < 0 or current_idx >= len(self.preview_dialog.all_summaries):
            return
        summary = self.preview_dialog.all_summaries[current_idx]
        idx = self._find_summary_index(summary)
        mult = self.preview_dialog.get_effective_multiplier(idx) if idx is not None else 1.0

        data = self.provider.get_group_data(summary)
        if not data:
            QMessageBox.warning(self, "提示", "当前月份无有效数据")
            return

        load_array = np.array(data['load_array']) * mult
        time_labels = data['time_labels']

        time_hours = []
        for tl in time_labels:
            m = _parse_min(tl)
            if m != INVALID_MINUTES:
                time_hours.append(m / 60.0)
            else:
                time_hours.append(np.nan)
        time_hours = np.array(time_hours)

        sort_idx = np.argsort(time_hours)
        time_hours = time_hours[sort_idx]
        load_array = load_array[:, sort_idx]

        feedback_data = self._get_pv_feedback_data(load_array, time_hours, data['date_labels'])
        dlg = PVFeedbackDetailDialog(self, summary, feedback_data)
        dlg.exec()

    def _get_pv_feedback_data(self, load_array, time_hours, date_labels):
        n_dates, n_times = load_array.shape
        daily_records = []

        for d in range(n_dates):
            row = load_array[d]
            neg_mask = row < 0
            if not neg_mask.any():
                continue

            segments = []
            start_idx = None
            for i in range(n_times):
                if neg_mask[i]:
                    if start_idx is None:
                        start_idx = i
                else:
                    if start_idx is not None:
                        segments.append((start_idx, i - 1))
                        start_idx = None
            if start_idx is not None:
                segments.append((start_idx, n_times - 1))

            total_energy = 0.0
            max_power = 0.0
            segment_descs = []
            for s, e in segments:
                seg_power = -row[s:e + 1]
                max_seg = np.max(seg_power)
                energy = 0.0
                for i in range(s, e):
                    dt = time_hours[i + 1] - time_hours[i]
                    avg_p = (seg_power[i - s] + seg_power[i - s + 1]) / 2.0
                    energy += avg_p * dt
                total_energy += energy
                max_power = max(max_power, max_seg)
                start_time = _fmt_hour(time_hours[s])
                end_time = _fmt_hour(time_hours[e] if e < n_times - 1 else time_hours[e])
                segment_descs.append(f"{start_time}-{end_time} 最大:{max_seg:.2f}kW")

            daily_records.append({
                'date': date_labels[d],
                'segments': ', '.join(segment_descs),
                'max_power': max_power,
                'energy': total_energy,
                'duration': sum((e - s + 1) for s, e in segments) * (time_hours[1] - time_hours[0]) if n_times > 1 else 0
            })

        total_energy = sum(r['energy'] for r in daily_records)
        avg_energy = total_energy / len(daily_records) if daily_records else 0
        max_power_all = max(r['max_power'] for r in daily_records) if daily_records else 0
        feedback_days = len(daily_records)

        return {
            'daily': daily_records,
            'total_energy': total_energy,
            'avg_energy_per_day': avg_energy,
            'max_power': max_power_all,
            'feedback_days': feedback_days
        }

    def _find_summary_index(self, summary):
        for i, s in enumerate(self.preview_dialog.all_summaries):
            if (s.file_path == summary.file_path and
                s.sheet_name == summary.sheet_name and
                s.month_str == summary.month_str and
                s.asset_value == summary.asset_value):
                return i
        return None
    
# ======================= PVFeedbackDetailDialog =======================
class PVFeedbackDetailDialog(QDialog):
    def __init__(self, parent, summary, data):
        super().__init__(parent)
        self.setWindowTitle(f"光伏返送详情 - {summary.asset_value or '未知'} {summary.month_str}")
        self.setMinimumSize(800, 500)
        
        layout = QVBoxLayout(self)
        
        # 标题
        title = QLabel(f"<b>表号:</b> {summary.asset_value or '未知'} &nbsp; <b>月份:</b> {summary.month_str}")
        layout.addWidget(title)
        
        # 表格
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["日期", "返送时段", "最大返送功率(kW)", "返送电量(kWh)", "返送时长(h)"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.table, 1)
        
        # 汇总
        summary_label = QLabel(
            f"<b>月度汇总:</b> 返送天数: {data['feedback_days']} &nbsp; "
            f"总返送电量: {data['total_energy']:.2f} kWh &nbsp; "
            f"日均返送: {data['avg_energy_per_day']:.2f} kWh &nbsp; "
            f"最大返送功率: {data['max_power']:.2f} kW"
        )
        layout.addWidget(summary_label)
        
        # 关闭按钮
        btn_box = QDialogButtonBox(QDialogButtonBox.Close)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)
        
        self._populate_table(data['daily'])
    
    def _populate_table(self, daily_records):
        self.table.setRowCount(len(daily_records))
        for row, rec in enumerate(daily_records):
            self.table.setItem(row, 0, QTableWidgetItem(rec['date']))
            self.table.setItem(row, 1, QTableWidgetItem(rec['segments']))
            self.table.setItem(row, 2, QTableWidgetItem(f"{rec['max_power']:.2f}"))
            self.table.setItem(row, 3, QTableWidgetItem(f"{rec['energy']:.2f}"))
            self.table.setItem(row, 4, QTableWidgetItem(f"{rec['duration']:.2f}"))

# ======================= 增强导出确认对话框 =======================
class ExportConfirmDialog(QDialog):
    def __init__(self, parent, all_summaries, selected_indices, filename_tpl, title_tpl,
                 output_dir, classify, per_group_multiplier=None, per_group_billing=None,
                 billing_dict=None, global_multiplier=1.0, config_manager=None):
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

        self.result = {}

        self.setWindowTitle("生成确认清单")
        self.setMinimumSize(800, 600)
        self.resize(1000, 700)

        if self.config_manager:
            geom = self.config_manager.get_window_geometry("ExportConfirmDialog")
            if geom:
                self.restoreGeometry(geom)
            else:
                self.resize(1000, 700)

        layout = QVBoxLayout(self)

        info_label = QLabel(f"<b>输出目录:</b> {self.output_dir}")
        if self.classify:
            info_label.setText(info_label.text() + "<br><i>将按表号创建子文件夹</i>")
        layout.addWidget(info_label)

        self.stats_label = QLabel("")
        layout.addWidget(self.stats_label)

        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels([
            "选择", "表号", "月份", "倍率", "容量线", "文件名预览", "状态"
        ])
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

        dlg_btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        dlg_btns.accepted.connect(self._on_accept)
        dlg_btns.rejected.connect(self.reject)
        layout.addWidget(dlg_btns)

        self._populate_table()
        self.table.cellChanged.connect(self._on_cell_changed)
        self._update_stats()

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
            self.table.setItem(row, 1, QTableWidgetItem(s.asset_value or "无"))
            self.table.setItem(row, 2, QTableWidgetItem(s.month_str))
            mult = self._get_effective_multiplier(idx)
            mult_item = QTableWidgetItem(f"{mult:.4f}")
            mult_item.setData(Qt.UserRole, idx)
            if idx in self.per_group_multiplier:
                mult_item.setBackground(QColor("#E3F2FD"))
                mult_item.setToolTip("自定义倍率")
            self.table.setItem(row, 3, mult_item)
            bill = self._get_effective_billing(idx)
            bill_str = f"{bill['value']:.2f}" if bill else ""
            bill_item = QTableWidgetItem(bill_str)
            bill_item.setData(Qt.UserRole, idx)
            if idx in self.per_group_billing:
                bill_item.setBackground(QColor("#E3F2FD"))
                bill_item.setToolTip("自定义容量")
            self.table.setItem(row, 4, bill_item)
            ctx = {
                'year_month': s.month_str,
                'table_no': s.asset_value or '',
                'asset': s.asset_value or '',
                'sheet': s.sheet_name
            }
            fname = generate_filename(self.filename_tpl, ctx)
            path_preview = os.path.join(s.asset_value, fname) if self.classify else fname
            self.table.setItem(row, 5, QTableWidgetItem(path_preview))
            full_path = os.path.join(self.output_dir, path_preview.replace('/', os.sep))
            status = ""
            if os.path.exists(full_path):
                status = "⚠ 将覆盖"
            self.table.setItem(row, 6, QTableWidgetItem(status))
        self.table.blockSignals(False)

    def _get_effective_multiplier(self, idx):
        if idx in self.per_group_multiplier:
            return self.per_group_multiplier[idx]
        return self.global_multiplier

    def _get_effective_billing(self, idx):
        if idx in self.per_group_billing:
            return self.per_group_billing[idx]
        s = self.all_summaries[idx]
        return self.billing_dict.get(s.asset_value)

    def _on_cell_changed(self, row, col):
        idx = None
        for col_candidate in (3, 4):
            item = self.table.item(row, col_candidate)
            if item and item.data(Qt.UserRole) is not None:
                idx = item.data(Qt.UserRole)
                break
        if idx is None:
            self._restore_cell(row, col)
            return
        if col == 3:
            try:
                new_val = float(self.table.item(row, col).text())
            except (ValueError, AttributeError):
                self._restore_cell(row, col)
                QMessageBox.warning(self, "格式错误", "请输入有效数字")
                return
            self.per_group_multiplier[idx] = new_val
            mult_item = self.table.item(row, col)
            mult_item.setBackground(QColor("#E3F2FD"))
            mult_item.setToolTip("自定义倍率")
            self._update_stats()
        elif col == 4:
            text = self.table.item(row, col).text().strip()
            if text == "":
                self.per_group_billing.pop(idx, None)
                self.table.item(row, col).setBackground(QColor("white"))
                self.table.item(row, col).setToolTip("")
                self._update_stats()
                return
            try:
                new_val = float(text)
            except (ValueError, AttributeError):
                self._restore_cell(row, col)
                QMessageBox.warning(self, "格式错误", "请输入有效数字")
                return
            self.per_group_billing[idx] = {'type': 'capacity', 'value': new_val}
            bill_item = self.table.item(row, col)
            bill_item.setBackground(QColor("#E3F2FD"))
            bill_item.setToolTip("自定义容量")
            self._update_stats()

    def _restore_cell(self, row, col):
        idx = None
        for c in (3, 4):
            item = self.table.item(row, c)
            if item and item.data(Qt.UserRole) is not None:
                idx = item.data(Qt.UserRole)
                break
        if idx is None:
            return
        if col == 3:
            val = self._get_effective_multiplier(idx)
            self.table.item(row, col).setText(f"{val:.4f}")
        elif col == 4:
            bill = self._get_effective_billing(idx)
            bill_str = f"{bill['value']:.2f}" if bill else ""
            self.table.item(row, col).setText(bill_str)

    def _batch_edit_multiplier(self):
        rows = set(item.row() for item in self.table.selectedItems())
        if not rows:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("批量设置倍率")
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel("输入新的倍率值:"))
        spin = QDoubleSpinBox()
        spin.setRange(0.01, 999999.0)
        spin.setValue(self.global_multiplier)
        layout.addWidget(spin)
        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)
        layout.addWidget(btn_box)
        if dlg.exec() == QDialog.Accepted:
            val = spin.value()
            for row in rows:
                mult_item = self.table.item(row, 3)
                idx = mult_item.data(Qt.UserRole) if mult_item else None
                if idx is None:
                    continue
                self.per_group_multiplier[idx] = val
                self.table.item(row, 3).setText(f"{val:.4f}")
                self.table.item(row, 3).setBackground(QColor("#E3F2FD"))
                self.table.item(row, 3).setToolTip("自定义倍率")
            self._update_stats()

    def _batch_edit_billing(self):
        rows = set(item.row() for item in self.table.selectedItems())
        if not rows:
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
                bill_item = self.table.item(row, 4)
                idx = bill_item.data(Qt.UserRole) if bill_item else None
                if idx is None:
                    continue
                if text == "":
                    self.per_group_billing.pop(idx, None)
                    self.table.item(row, 4).setText("")
                    self.table.item(row, 4).setBackground(QColor("white"))
                    self.table.item(row, 4).setToolTip("")
                else:
                    try:
                        val = float(text)
                    except ValueError:
                        continue
                    self.per_group_billing[idx] = {'type': 'capacity', 'value': val}
                    self.table.item(row, 4).setText(f"{val:.2f}")
                    self.table.item(row, 4).setBackground(QColor("#E3F2FD"))
                    self.table.item(row, 4).setToolTip("自定义容量")
            self._update_stats()

    def _reset_custom(self):
        rows = set(item.row() for item in self.table.selectedItems())
        if not rows:
            return
        if QMessageBox.question(self, "确认", "清除选中行的所有自定义倍率和容量？"):
            for row in rows:
                for col in (3, 4):
                    item = self.table.item(row, col)
                    idx = item.data(Qt.UserRole) if item else None
                    if idx is None:
                        continue
                    if col == 3 and idx in self.per_group_multiplier:
                        del self.per_group_multiplier[idx]
                        self.table.item(row, col).setText(f"{self.global_multiplier:.4f}")
                        self.table.item(row, col).setBackground(QColor("white"))
                        self.table.item(row, col).setToolTip("")
                    elif col == 4 and idx in self.per_group_billing:
                        del self.per_group_billing[idx]
                        self.table.item(row, col).setText("")
                        self.table.item(row, col).setBackground(QColor("white"))
                        self.table.item(row, col).setToolTip("")
            self._update_stats()

    def _toggle_filter_custom(self, checked):
        for row in range(self.table.rowCount()):
            hide = False
            if checked:
                mult_item = self.table.item(row, 3)
                bill_item = self.table.item(row, 4)
                idx_mult = mult_item.data(Qt.UserRole) if mult_item else None
                idx_bill = bill_item.data(Qt.UserRole) if bill_item else None
                has_custom = (idx_mult in self.per_group_multiplier) or (idx_bill in self.per_group_billing)
                hide = not has_custom
            self.table.setRowHidden(row, hide)

    def _update_stats(self):
        count = self.table.rowCount()
        custom_mult = sum(1 for i in range(count) if self.table.item(i, 3) and self.table.item(i, 3).background().color() == QColor("#E3F2FD"))
        custom_bill = sum(1 for i in range(count) if self.table.item(i, 4) and self.table.item(i, 4).background().color() == QColor("#E3F2FD"))
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
                self._update_path_previews()
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
        for row in range(self.table.rowCount()):
            idx = None
            mult_item = self.table.item(row, 3)
            if mult_item and mult_item.data(Qt.UserRole) is not None:
                idx = mult_item.data(Qt.UserRole)
            if idx is None:
                bill_item = self.table.item(row, 4)
                if bill_item and bill_item.data(Qt.UserRole) is not None:
                    idx = bill_item.data(Qt.UserRole)
            if idx is None:
                continue
            s = self.all_summaries[idx]
            ctx = {
                'year_month': s.month_str,
                'table_no': s.asset_value or '',
                'asset': s.asset_value or '',
                'sheet': s.sheet_name
            }
            fname = generate_filename(self.filename_tpl, ctx)
            path_preview = os.path.join(s.asset_value, fname) if self.classify else fname
            self.table.item(row, 5).setText(path_preview)

    def _on_accept(self):
        selected_idx_list = []
        multipliers = {}
        billings = {}
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).checkState() == Qt.Checked:
                idx = None
                mult_item = self.table.item(row, 3)
                if mult_item and mult_item.data(Qt.UserRole) is not None:
                    idx = mult_item.data(Qt.UserRole)
                if idx is not None:
                    selected_idx_list.append(idx)
                    mult_text = self.table.item(row, 3).text()
                    try:
                        mult_val = float(mult_text)
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
            'billings': billings
        }
        self.accept()

    def closeEvent(self, event):
        if self.config_manager:
            self.config_manager.set_window_geometry("ExportConfirmDialog", self.saveGeometry())
        super().closeEvent(event)

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
# -*- coding: utf-8 -*-
"""
负荷曲线批量生成工具 Pro 融合版 (最终优化版)
第五部分：模块级绘图函数、处理线程、样式表、主窗口、入口
优化点：
- 绘图函数支持独立倍率与容量参数
- 处理线程传递个性化倍率/容量字典
- 缓存优化：扫描后注入 DataProvider 避免重复读取
- 保留 C 引擎优先的 CSV 读取策略
- 支持一键多文件映射
- 支持当月最大需量显示
- 支持专业模式储能容量评估
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

    # 文件名去重
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

        # 原子写入：先写入临时文件再移动
        tmp_fd, tmp_path = tempfile.mkstemp(suffix='.png', dir=out_dir)
        os.close(tmp_fd)
        try:
            _plot_curves(
                data.get('date_labels', []), load_array,
                data.get('time_labels', []), title, tmp_path,
                plot_options, actual_billing, periods
            )
            shutil.move(tmp_path, outpath)
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

        if progress_queue is not None:
            progress_queue.put(1)
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
        return False, f"绘图错误: {str(e)}"

# ======================= 错误信息翻译 =======================
_ERROR_TRANSLATION = {
    "KeyError": "{key}列不存在，请检查列名是否有多余空格或全角符号，可用列：{avail}",
    "ValueError": "{key}列的数值转换失败，请确认负荷列不含文本或无效符号",
    "MemoryError": "系统内存不足，请关闭其他应用程序或减少批量处理数量",
    "PermissionError": "文件写入权限不足，请检查输出目录是否可写",
}

def _translate_error(exc_type, exc_msg):
    """将技术错误转换为用户友好提示"""
    for tech, template in _ERROR_TRANSLATION.items():
        if tech in str(exc_type):
            return template
    if "KeyError" in str(exc_type):
        match = re.search(r"'([^']*)'", str(exc_msg))
        if match:
            return f"未找到列“{match.group(1)}”，请检查列名是否存在多余空格或全角字符"
    return f"发生错误：{exc_msg}"

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
        self.failed_tasks = []

        self.plot_options = {'show_daily': True, 'show_avg': True, 'fit_types': [], 'show_max_demand': True}
        self.global_multiplier = 1.0
        self.billing_dict = {}
        self.price_periods = {}
        self.title_template = self.config_manager.app_set('last_title_template', '{table_no}_{year_month}_负荷曲线')
        self.filename_template = self.config_manager.app_set('last_filename_template', '{table_no}_{year_month}_负荷曲线')
        self.classify_by_asset = False

        # 个性化参数
        self.per_group_multiplier = {}
        self.per_group_billing = {}

        self.data_provider = None
        self._max_file_size = 0

    def run(self):
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
                max_mb = self._max_file_size / (1024*1024)
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

            self._generate_charts()
        except Exception as e:
            friendly = _translate_error(type(e), str(e))
            self.signals.error.emit(friendly)
            log.exception("处理线程异常")
        finally:
            self.signals.finished.emit()

    def _parse_and_map(self):
        cached_base_mapping = None
        for fp in self.file_paths:
            if self.stop_event.is_set():
                return
            fname = os.path.basename(fp)
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
            if self.dialog_result == 'skip_sheet':
                self.signals.log.emit(f"跳过工作表: {fname}")
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
                    cached_base_mapping = base_map
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
        with ThreadPoolExecutor(max_workers=MAX_SCAN_WORKERS) as executor:
            futures = {}
            for task in scan_tasks:
                fp, sheet = task[0], task[1]
                fut = executor.submit(_scan_file_worker, task)
                futures[fut] = (fp, sheet)

            for i, fut in enumerate(as_completed(futures)):
                if self.stop_event.is_set():
                    for ce in cancel_events.values():
                        ce.set()
                    executor.shutdown(wait=False, cancel_futures=True)
                    return

                fp, sheet = futures[fut]
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

                self.signals.status.emit(f"扫描文件: {i+1}/{len(scan_tasks)}")

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
            'show_max_demand': settings.get('show_max_demand', True),
            'fit_types': []
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
        if not self.selected_indices:
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
                bill = self.per_group_billing.get(orig_idx) or self.billing_dict.get(summary.asset_value)
                tasks.append((data, mult, bill))
            else:
                error_msg = provider.get_load_error(summary)
                self.failed_tasks.append(f"{summary.asset_value or '未知表号'} - {summary.month_str} ({error_msg or '无数据'})")
        if not tasks:
            self.signals.log.emit("无有效数据，流程结束")
            return

        total_charts = len(tasks)
        worker_args = []
        for data, mult, bill in tasks:
            worker_args.append((data, self.title_template, self.filename_template,
                                self.output_dir, self.classify_by_asset, self.plot_options,
                                bill, self.price_periods, mult))

        actual_workers = MAX_PLOT_PROCESSES
        if self._max_file_size > HUGE_FILE_SIZE:
            actual_workers = max(1, MAX_PLOT_PROCESSES // 2)
            self.signals.log.emit(f"已调整并行进程数为 {actual_workers}")

        all_paths = []
        manager = mp.Manager()
        progress_queue = manager.Queue()
        completed = 0
        start_time = time.time()

        try:
            with ProcessPoolExecutor(max_workers=actual_workers) as executor:
                futures = [executor.submit(_plot_single_worker, *args, progress_queue) for args in worker_args]
                for fut in as_completed(futures):
                    if self.stop_event.is_set():
                        executor.shutdown(wait=False, cancel_futures=True)
                        return
                    try:
                        ok, msg = fut.result(timeout=120)
                        if ok:
                            all_paths.append(msg)
                            self.signals.log.emit(f"已生成: {os.path.basename(msg)}")
                        else:
                            self.failed_tasks.append(f"图表失败: {msg}")
                            self.signals.log.emit(f"图表失败: {msg}")
                    except Exception as e:
                        friendly = _translate_error(type(e), str(e))
                        self.failed_tasks.append(f"进程异常: {friendly}")
                        self.signals.log.emit(f"图表异常: {friendly}")

                    while not progress_queue.empty():
                        try:
                            inc = progress_queue.get_nowait()
                            completed += inc
                            self.signals.progress.emit(completed)
                            elapsed = time.time() - start_time
                            if completed > 0 and elapsed > 0.5:
                                rate = completed / elapsed
                                remaining = (total_charts - completed) / rate if rate > 0 else 0
                                if remaining > 3600:
                                    eta_str = f"已完成 {completed}/{total_charts} 张，预计剩余 {int(remaining // 3600)}时{int((remaining % 3600) // 60)}分"
                                elif remaining > 60:
                                    eta_str = f"已完成 {completed}/{total_charts} 张，预计剩余 {int(remaining // 60)}分{int(remaining % 60)}秒"
                                else:
                                    eta_str = f"已完成 {completed}/{total_charts} 张，预计剩余 {int(remaining)}秒"
                                self.signals.eta.emit(eta_str)
                        except:
                            break
        except Exception as e:
            friendly = _translate_error(type(e), str(e))
            self.signals.error.emit(f"绘图进程池错误: {friendly}")
        self.signals.generation_finished.emit((all_paths, self.failed_tasks))

    def set_mapping_result(self, result):
        self.dialog_result = result
        self.dialog_event.set()

    def set_preview_result(self, result):
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

# ======================= 主窗口（含任务栏进度条、统一状态重置） =======================
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

        self._thread = None
        self._dialog_active = False
        self._quick_mode = self.config_manager.app_set('quick_mode', False)
        self._processing = False

        # Windows任务栏进度条
        self._taskbar_button = None
        if sys.platform == 'win32' and QWinTaskbarButton:
            try:
                self._taskbar_button = QWinTaskbarButton(self)
            except:
                pass

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
        sys.excepthook = self._gui_exception_handler

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

        # 顶部按钮区
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

        self.quick_mode_cb = QCheckBox("快速模式(跳过预览)")
        self.quick_mode_cb.setChecked(self._quick_mode)
        self.quick_mode_cb.stateChanged.connect(self._toggle_quick_mode)
        top_layout.addWidget(self.quick_mode_cb)
        layout.addLayout(top_layout)

        # 文件列表
        self.file_list = FileListWidget()
        self.file_list.files_added.connect(self._on_files_dropped)
        layout.addWidget(self.file_list, 1)

        # 进度条与执行按钮
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

        # 日志区域
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

    def _update_taskbar_progress(self, value, maximum):
        if self._taskbar_button:
            self._taskbar_button.progress().setVisible(True)
            self._taskbar_button.progress().setRange(0, maximum)
            self._taskbar_button.progress().setValue(value)

    # ---------- 文件操作 ----------
    def _on_files_dropped(self, paths):
        existing = set(self.file_list.get_file_paths())
        added = 0
        for p in paths:
            if os.path.normcase(p) in {os.path.normcase(x) for x in existing}:
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
        if self.files and QMessageBox.question(self, "确认", "清空所有文件？"):
            self.files.clear()
            self.file_list.clear_files()
            self._update_start_btn()
            self._log("文件列表已清空")
            self.status_label.setText("就绪")

    def _delete_selected(self):
        items = self.file_list.selectedItems()
        if not items:
            return
        if QMessageBox.question(self, "确认", f"删除选中的 {len(items)} 个文件？"):
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

    def _reset_processing_state(self):
        if self._thread:
            self._thread.cancel()
            if self._thread.is_alive():
                self._thread.join(timeout=3)
            self._thread = None
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
        sig.eta.connect(self.eta_label.setText)
        self._thread.start()

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

        dlg = MappingDialog(self, fp, sheet, cfg=self.config_manager,
                            hrow=0, multi_sheet=multi, enc=enc, delimiter=delimiter,
                            batch_multi_file=batch_multi_file)
        if dlg.exec() == QDialog.Accepted:
            if dlg.result == 'cancel':
                self._thread.set_mapping_result('cancel')
                self._log("用户取消映射")
            elif dlg.result == 'skip':
                self._thread.set_mapping_result('skip')
                self._log(f"跳过文件: {fname}")
            elif dlg.result == 'skip_sheet':
                self._thread.set_mapping_result('skip_sheet')
                self._log(f"跳过工作表: {fname}")
            else:
                self._thread.set_mapping_result(dlg.result)
                self._log(f"映射完成: {fname}")
        else:
            self._thread.set_mapping_result('cancel')
            self._log("用户取消映射")
        self._dialog_active = False
        self._set_ui_state('processing')

    def _handle_preview_request(self, data):
        summaries, multiplier, provider = data
        if self._quick_mode:
            confirm = QMessageBox.question(
                self, "快速模式确认",
                f"即将生成 {len(summaries)} 张图表\n倍率: {multiplier}\n输出目录: {self.output_dir}\n\n确认开始生成？",
                QMessageBox.Ok | QMessageBox.Cancel
            )
            if confirm != QMessageBox.Ok:
                self._thread.set_preview_result(None)
                return
            settings = {
                'selected_indices': list(range(len(summaries))),
                'global_multiplier': multiplier,
                'output_dir': self.output_dir,
                'show_daily': self.config_manager.app_set('show_daily', True),
                'show_avg': self.config_manager.app_set('show_avg', True),
                'show_max_demand': self.config_manager.app_set('show_max_demand', True),
                'strategy_name': self.config_manager.active_strat(),
                'title_template': self.config_manager.app_set('last_title_template', '{table_no}_{year_month}_负荷曲线'),
                'filename_template': self.config_manager.app_set('last_filename_template', '{table_no}_{year_month}_负荷曲线'),
                'classify_by_asset': self.config_manager.app_set('classify_by_asset', False),
                'billing_dict': {},
                'per_group_multiplier': {},
                'per_group_billing': {}
            }
            sn = self.config_manager.active_strat()
            if sn:
                strat = self.config_manager.get_strat(sn)
                if strat:
                    settings['price_periods'] = strat
            self._thread.set_preview_result(settings)
            return

        self._dialog_active = True
        self._set_ui_state('dialog_open')
        self.phase_label.setText("当前阶段：预览与参数设置")
        dlg = PreviewDialog(
            self, summaries, self.config_manager,
            initial_multiplier=multiplier,
            initial_strategy_name=self.config_manager.active_strat(),
            initial_output_dir=self.output_dir,
            data_provider=provider
        )
        if dlg.exec() == QDialog.Accepted and dlg.result:
            self.output_dir = dlg.result.get('output_dir', self.output_dir)
            self.config_manager.set_app('last_output_dir', self.output_dir)
            self.config_manager.set_app('last_multiplier', dlg.result.get('global_multiplier', 1.0))
            settings = {
                'selected_indices': dlg.result['selected_indices'],
                'global_multiplier': dlg.result.get('global_multiplier', multiplier),
                'output_dir': self.output_dir,
                'show_daily': dlg.result['show_daily'],
                'show_avg': dlg.result['show_avg'],
                'show_max_demand': dlg.result.get('show_max_demand', True),
                'strategy_name': dlg.result.get('strategy_name'),
                'title_template': dlg.result['title_template'],
                'filename_template': dlg.result['filename_template'],
                'classify_by_asset': dlg.result.get('classify_by_asset', False),
                'billing_dict': dlg.result.get('billing_dict', {}),
                'per_group_multiplier': dlg.result.get('per_group_multiplier', {}),
                'per_group_billing': dlg.result.get('per_group_billing', {}),
                'plot_options': {'show_daily': dlg.result['show_daily'],
                                 'show_avg': dlg.result['show_avg'],
                                 'show_max_demand': dlg.result.get('show_max_demand', True),
                                 'fit_types': []},
                'price_periods': {}
            }
            sn = settings['strategy_name']
            if sn:
                strat = self.config_manager.get_strat(sn)
                if strat:
                    settings['price_periods'] = strat
            self._thread.set_preview_result(settings)
            self.phase_label.setText("当前阶段：正在生成图表...")
        else:
            self._thread.set_preview_result(None)
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

        self.status_label.setText(f"完成，已生成 {len(paths)} 张图表至 {self.output_dir}")
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("生成完成")
        msg_box.setText(f"图表已成功生成至:\n{self.output_dir}\n共 {len(paths)} 张")
        open_btn = msg_box.addButton("打开文件夹", QMessageBox.ActionRole)
        copy_btn = msg_box.addButton("复制路径", QMessageBox.ActionRole)
        msg_box.addButton(QMessageBox.Ok)
        msg_box.exec()
        if msg_box.clickedButton() == open_btn:
            self._open_output()
        elif msg_box.clickedButton() == copy_btn:
            QApplication.clipboard().setText(self.output_dir)
            QMessageBox.information(self, "已复制", f"路径已复制到剪贴板:\n{self.output_dir}")

    def _on_error(self, msg):
        self._log(f"错误: {msg}")
        QMessageBox.critical(self, "处理错误", f"流程发生错误:\n{msg}")
        self._reset_processing_state()

    def _on_thread_finished(self):
        self._log("处理线程已退出")
        self._reset_processing_state()

    def _cancel(self):
        self._log("用户取消操作")
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
            if QMessageBox.question(self, "确认", "导入将覆盖当前配置，继续？"):
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
        scipy_status = "已安装" if any('scipy' in p for p in sys.modules) else "未安装 (部分拟合功能不可用)"
        chardet_status = "已安装" if CHARDET else "未安装 (编码检测降级)"
        taskbar_status = "支持" if self._taskbar_button else "不支持"
        QMessageBox.information(self, "关于",
            f"负荷曲线批量生成工具 Pro 融合版\n\n"
            f"集成优化：\n"
            f"• 智能列映射与宽表视觉检测\n"
            f"• 大文件分块读取\n"
            f"• 细粒度进度与剩余时间估算\n"
            f"• 文件拖拽高亮反馈\n"
            f"• 对话框窗口几何记忆\n"
            f"• 日志文件按时间戳独立保存\n"
            f"• 中间宽表导出核查透视\n"
            f"• 全局低风险样式表\n"
            f"• 超大文件自动降并行度\n"
            f"• 任务栏进度条: {taskbar_status}\n"
            f"• 分组级倍率/容量独立设置与批量应用\n"
            f"• 一键多文件映射\n"
            f"• 扫描超时自适应\n"
            f"• 缓存复用与线程安全优化\n"
            f"• 当月最大需量显示\n"
            f"• 充放策略精准刷新\n"
            f"• 专业模式储能容量评估（需量管理/光伏消纳/混合模式）\n\n"
            f"依赖状态:\n"
            f"• scipy: {scipy_status}\n"
            f"• chardet: {chardet_status}"
        )

    def _gui_exception_handler(self, exc_type, exc_value, exc_tb):
        err_msg = ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))
        log.critical(f"GUI异常:\n{err_msg}")
        QMessageBox.critical(self, "致命错误", f"程序遇到未处理异常:\n{err_msg}\n建议重启程序。")

    def dragEnterEvent(self, event: QDragEnterEvent):
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

    def dropEvent(self, event: QDropEvent):
        self._overlay.setVisible(False)
        if event.mimeData().hasUrls():
            paths = []
            for url in event.mimeData().urls():
                p = url.toLocalFile()
                if os.path.isfile(p) and p.lower().endswith(SUPPORTED_EXTS):
                    paths.append(p)
                elif os.path.isdir(p):
                    try:
                        for root, _, files in os.walk(p):
                            for f in files:
                                if f.lower().endswith(SUPPORTED_EXTS):
                                    paths.append(os.path.join(root, f))
                    except Exception as e:
                        self._log(f"遍历文件夹失败: {e}")
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
        self._reset_processing_state()
        self.config_manager.save()
        super().closeEvent(event)

# ======================= 主入口 =======================
def main():
    mp.freeze_support()

    _check_and_install_dependencies()

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
