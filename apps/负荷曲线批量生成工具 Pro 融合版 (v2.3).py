# -*- coding: utf-8 -*-
"""
负荷曲线批量生成工具 Pro 融合版
发布版本：LoomGrid Fusion v2.3.9（2026-11-04 全面审查修复：2 条 P1 + 16 条 P2，含出图竞态与示数表静默降级）
第一部分：基础模块、配置、映射、数据读取与扫描

优化增强 (v3.2)：
- PlotOptions 数据类统一定义（含 dark_theme）
- MemoryLimitedCache 封装修复，增加 remove 方法
- 所有静默异常块增加日志追踪
- _plot_curves 明确返回 bool
- 移除旧版配置兼容，强制升级至最新策略架构
- 内存敏感缓存替换固定容量 LRU
- 子进程绘图支持可中断取消
- Windows 保留设备名防护
- psutil 依赖提示
- 配置文件 JSON 结构校验
- 减少不必要 DataFrame 深拷贝
"""

import sys, os, re, json, math, time, io, logging, atexit, warnings, subprocess, traceback, gc
# v2.3.9fix: html 模块别名导入。_generate_report 里有同名局部变量 html，
# 直接 import html 会被那个局部变量遮蔽，导致 html.escape 抛 UnboundLocalError。
import html as html_mod
import logging.handlers
import multiprocessing as mp
import threading
import functools
import csv
import shutil
import tempfile
from datetime import datetime, time as dt_time, date, timedelta
from collections import OrderedDict, defaultdict
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import List, Optional
import numpy as np
import pandas as pd

# v2.3.2fix: 冻结环境（cx_Freeze / PyInstaller）下 sys.stdin/stdout/stderr 可能为 None，
# 此时任何 print 或 .isatty() 都会抛 AttributeError（如 'NoneType' object has no
# attribute 'isatty'），程序在启动阶段就死在那一行。在 import 后立刻补上标准流，
# 后续所有代码都能安全使用它们。
if sys.stdin is None:
    sys.stdin = open(os.devnull, 'r', encoding='utf-8', errors='replace')
if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w', encoding='utf-8', errors='replace')
if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w', encoding='utf-8', errors='replace')

# ── 阶段 1：core 层（纯 Python，零 Qt）──────────────────────────
# 主程序可能被 spec_from_file_location / PyInstaller 以多种方式加载，
# 不能假定仓库根已在 sys.path 里；这里主动补一次（幂等）。
_PKG_ROOT = os.path.dirname(os.path.abspath(__file__))
# v2.3.9fix(P0): 主程序被收纳到 apps/ 之后，__file__ 的目录是 apps/，
# 而 core/ services/ plotting/ 仍在仓库根 → 直接双击该文件会直接报
# ModuleNotFoundError: No module named 'core'。向上找一层，取含 core/ 的目录。
# 打包后（PyInstaller 已把模块打进去）这段不生效，不影响产物。
if not os.path.isdir(os.path.join(_PKG_ROOT, 'core')):
    _up = os.path.dirname(_PKG_ROOT)
    if os.path.isdir(os.path.join(_up, 'core')):
        _PKG_ROOT = _up
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)
from core.models import (  # noqa: E402
    DEFAULT_WEEKDAY_COLOR, DEFAULT_WEEKEND_COLOR, DEFAULT_HOLIDAY_COLOR,
    DEFAULT_WEEKEND_LINE_WIDTH, DEFAULT_HOLIDAY_LINE_WIDTH,
    PeriodType, CurveLabel, ColorConst, PlotOptions, GroupSummary,
)
from core.timegrid import (  # noqa: E402
    INVALID_MINUTES, TIME_CACHE_MAXSIZE, TIME_CACHE, LRUCache,
    _clean_plot, _normalize_fullwidth_chars, _CLEAN_RE,
    _set_24h_belong, _is_holiday_available, _set_holiday_available,
    _parse_min, _fmt_time, _fmt_date, parse_flexible_time, _fmt_hour,
    _expand_overnight, _valid_periods, _hour_x, _parse_date_flexible,
    _get_day_type, invalidate_day_type_cache, _normalize_time_column_name,
)
from core.spaces import (  # noqa: E402
    CHARGING_EFFICIENCY, DISCHARGING_EFFICIENCY,
    compute_segmented_spaces, calc_capacity_for_days, calc_days_for_capacity,
)
from core.periods import _segment_periods  # noqa: E402
from plotting.fonts import (  # noqa: E402
    _CJK_FONT_CANDIDATES, _FONT_LOCK, _find_cjk_font, _get_cached_font, _setup_font,
)
from plotting.naming import (  # noqa: E402
    MAX_FILENAME_LEN, MAX_FILENAME_ATTEMPTS, _PATH_ILLEGAL_CHARS_RE, _PATH_DOT_RE,
    _WINDOWS_RESERVED_NAMES, _sanitize_name, _clean_series, generate_filename,
    generate_default_title, parse_title_template,
)
from plotting.curves import (  # noqa: E402
    WEEKEND_BG_ALPHA, _downsample, _compute_fits, _draw_curves_core,
    _plot_curves, _plot_single_worker,
)
from core.ingest import (  # noqa: E402
    _read_file_full, _scan_file_worker, _scan_wide_chunk, _scan_long_chunk,
    _smart_classify_columns, _detect_wide_matrix, _detect_transposed_wide,
    _wide_row_accumulated_to_power, _detect_accumulated_pattern,
    _convert_accumulated_to_power, _parse_long_to_standard,
    _detect_csv_delimiter, _detect_enc, _detect_file_format_type,
    _parse_file_meta_worker, _extract_asset_from_name, _mapping_is_long_shape,
    _normalize_mapping, _safe_read_excel, _safe_excel_sheets,
    get_columns_fast, _valid_time_cols,
    _safe_get_col, _get_excel_engine, _to_dt, _extract_ym, _translate_error,
    _normalize_asset, _ERROR_TRANSLATION, _warned_dup_lock, _warned_dup_cols,
    LARGE_FILE_SIZE, DEFAULT_ENCODING_LIST,
)
from services.config import Config, _validate_config_schema  # noqa: E402
from services.provider import DataProvider, MemoryLimitedCache  # noqa: E402
# v2.3.8fix: 逐日吞吐量的唯一实现。主程序原先三处各自算「逐段配对 min」，
# 而同一索引的充段与放段是不同时段，配对 min 必然被占位零或错位压成 0。
from services.capacity import daily_throughput_series  # noqa: E402
from ui.window_state import (  # noqa: E402
    get_window_geometry, set_window_geometry,
    get_splitter_state, set_splitter_state,
)

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
    QTabWidget,
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
    try:
        import chinese_calendar  # noqa: F401
    except ImportError:
        missing.append('chinese_calendar')
    try:
        import psutil
    except ImportError:
        missing.append('psutil')

    if not missing:
        return

    # 仅在交互式终端（tty）环境下询问用户，避免 GUI 启动时卡死
    # v2.3.2fix: 即使上面补了 devnull，仍加一层 try——isatty() 在某些冻结环境下会抛
    # ValueError / OSError，不应因为探测终端而阻止程序启动。
    try:
        _interactive_tty = sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError, OSError):
        _interactive_tty = False
    if not _interactive_tty:
        print("检测到非交互式环境，跳过自动安装。以下可选依赖缺失，部分功能可能受限：")
        for pkg in missing:
            print(f"  - {pkg}")
        if 'psutil' in missing:
            print("  psutil 未安装：超大文件时无法根据内存自适应并行度，可能因内存不足崩溃。建议手动安装：pip install psutil")
        return

    msg = "检测到以下可选依赖未安装，可能影响部分功能（如曲线平滑、编码检测、节假日识别、内存自适应）：\n"
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


# v2.3.6fix(K5): 版本号只能有一个来源。
# 原来主窗口标题写死 "v2.3.1"、欢迎弹窗写死 "Pro v3.2"（还是 Ultra 的号段），
# 发版时没人会想到去改这两处。现在统一引用，发版只改 FUSION_VERSION。
FUSION_VERSION = "2.3.9"
FUSION_APP_NAME = "LoomGrid Fusion"
SUPPORTED_EXTS = ('.csv', '.xlsx', '.xls', '.xlsm', '.xlsb')
_SUPPORTED_EXTS_SET = frozenset(SUPPORTED_EXTS)  # v2.3.4fix(J6): O(1) 成员查找


def _is_supported_file(path: str) -> bool:
    """v2.3.4fix(J6): 原 Fusion 没有这个函数，_on_files_dropped 不过滤类型，
    拖入 .txt/.zip/.exe 也会进列表并被当表格处理。"""
    return os.path.splitext(path)[1].lower() in _SUPPORTED_EXTS_SET


def _parse_month_num(month_str):
    """从分组月份标识提取 1..12 的月份号；无法识别返回 None。

    month_str 由 core.ingest._extract_ym 产出，正常形态是 "YYYY-MM"，
    解析失败时返回哨兵字符串「未知月份」。此前两处消费方各自兜底：
      PreviewDialog._periods_for_idx     兜成 1 月（静默套用冬季分时电价）
      ProfessionalWindow._recalc_spaces  int() 直接抛 ValueError（窗口打不开）
    统一走本函数，调用方必须显式处理 None——不得再猜月份。
    """
    if not month_str:
        return None
    s = str(month_str).strip()
    if '-' in s:
        parts = s.split('-')
        if len(parts) < 2:
            return None
        s = parts[1]
    try:
        m = int(s)
    except (TypeError, ValueError):
        return None
    return m if 1 <= m <= 12 else None

PREVIEW_CACHE_MAXSIZE = 100

HUGE_FILE_SIZE = 200 * 1024 * 1024  # 200 MB，提示并降低并行度

# 内存保护阈值 (字节)
MEMORY_LOW_THRESHOLD = 1 * 1024 * 1024 * 1024  # 1 GB

BESS_DOD_DEFAULT = 0.90     # 默认放电深度(SOC区间 5%~95%,与 BESS_SOC_MIN/MAX 一致)
BESS_SOC_MIN = 0.05         # 最小SOC(5%保护)
BESS_SOC_MAX = 0.95         # 最大SOC(95%)
BESS_CYCLE_LIFE_DEFAULT = 6000  # v3.2fix: 默认循环寿命

# PeriodType / CurveLabel / ColorConst 已搬到 core/models.py（阶段 1 · 第一块）



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

# core/ 与 plotting/ 的子 logger 也要写进同一个文件。
# 它们用 logging.getLogger(__name__)（与主程序同一套），是 "core.timegrid" 这类
# 层级名；给两个父 logger 挂 handler，所有子 logger 自动继承。
# 反例（已避免）：曾用 loguru 写过，日志根本不进 logs/app.log，排障时看不到。
for _sub in ("core", "plotting"):
    _lg = logging.getLogger(_sub)
    _lg.setLevel(logging.INFO)
    _lg.addHandler(log_handler)
    _lg.propagate = False


# ======================= 字体管理 =======================
try:
    from matplotlib.font_manager import fontManager
except ImportError:
    print("matplotlib 未安装，请先安装 matplotlib")
    sys.exit(1)






# ======================= 绘图选项数据类 =======================
# PlotOptions 已搬到 core/models.py（阶段 1 · 第一块）































# ======================= 日期类型识别 =======================
from functools import lru_cache





# ======================= CSV 工具 =======================
try:
    import chardet
    CHARDET = True
except ImportError:
    CHARDET = False
    log.debug("chardet 未安装，编码检测将降级为顺序尝试。")





# endregion






















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

# ===================== 对话框握手保护 =====================
def _dialog_handshake(result_method, fallback, label):
    """v2.3.5fix(K4): 保证对话框处理器无论成败都回复一次。

    ProcessingThread 用 dialog_event.wait()（无超时）等处理器回话，
    而三个 handler 原本都没有异常保护——对话框构造失败、文件探测出错、
    data 缺键（如 KeyError: 'filename_tpl'）都会让 set_*_result 永不执行，
    线程永久阻塞，UI 卡在 dialog_open 只能强杀进程。

    这个装饰器不改 body，只在外面套一层：记账 → 异常兜底 → 恢复 UI 状态。
    """
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(self, *args, **kwargs):
            answered = [False]
            thread = getattr(self, '_thread', None)
            original = getattr(thread, result_method, None) if thread is not None else None

            if original is not None:
                def _tracked(value):
                    answered[0] = True
                    return original(value)
                try:
                    setattr(thread, result_method, _tracked)
                    patched = True
                except Exception:
                    patched = False
            else:
                patched = False

            try:
                return fn(self, *args, **kwargs)
            except Exception as e:
                log.exception("%s 处理异常", label)
                try:
                    self._log(f"{label}异常：{e}")
                except Exception:
                    pass
                try:
                    QMessageBox.warning(self, "界面异常",
                                        f"{label}出错，本条已跳过：\n{e}")
                except Exception:
                    pass
                return None
            finally:
                if not answered[0] and original is not None:
                    try:
                        original(fallback)
                    except Exception:
                        log.exception("%s 兜底回复失败", label)
                if patched:
                    try:
                        delattr(thread, result_method)
                    except Exception:
                        pass
                # 原实现在异常时这两行也会被跳过，一并收进 finally
                for attr in ('_active_mapping_dlg',):
                    if hasattr(self, attr):
                        try:
                            setattr(self, attr, None)
                        except Exception:
                            pass
                try:
                    self._dialog_active = False
                except Exception:
                    pass
                try:
                    self._set_ui_state('processing')
                except Exception:
                    pass
        return wrapper
    return deco


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
# GroupSummary 已搬到 core/models.py（阶段 1 · 第一块）


# 第一部分结束
# -*- coding: utf-8 -*-
# 第二部分：绘图核心、充放电空间计算、策略编辑UI、映射对话框
# 注意：本部分代码紧跟第一部分，共享同一命名空间，无需重复导入公共定义。



# ====================== 充放电空间计算（修正版） ======================










# ====================== 策略编辑对话框（重构版，汉化，增加日志） ======================
class PeriodEditDialog(QDialog):
    """时段编辑对话框"""
    def __init__(self, parent, period=None, config_manager=None):
        super().__init__(parent)
        self.setWindowTitle("编辑时段")
        self.setMinimumSize(400, 260)
        self.result = None
        self.config_manager = config_manager

        if self.config_manager:
            geom = get_window_geometry(self.config_manager, "PeriodEditDialog")
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
            log.debug("时段编辑时间解析错误", exc_info=True)
            QMessageBox.critical(self, "时间格式错误", f"请输入正确的时间:\n{ex}")

    def closeEvent(self, event):
        if self.config_manager:
            set_window_geometry(self.config_manager, "PeriodEditDialog", self.saveGeometry())
        super().closeEvent(event)


class GroupPeriodEditDialog(QDialog):
    """策略组编辑对话框"""
    def __init__(self, parent, config_manager, province_name, group_data=None):
        super().__init__(parent)
        self.config_manager = config_manager
        self.province_name = province_name
        self.original_name = group_data['name'] if group_data else None
        self.result = None
        self.current_periods = group_data['periods'][:] if group_data else []
        self.selected_months = set(group_data['months']) if group_data else set()
        self._undo_stack = []
        self._redo_stack = []
        self._max_history = 30
        self._skip_history = False

        self.setWindowTitle("编辑策略组" if group_data else "新建策略组")
        self.setMinimumSize(820, 620)

        if self.config_manager:
            geom = get_window_geometry(self.config_manager, "GroupPeriodEditDialog")
            if geom:
                self.restoreGeometry(geom)
            else:
                self.resize(850, 620)
        else:
            self.resize(850, 620)

        layout = QVBoxLayout(self)

        name_layout = QHBoxLayout()
        name_layout.addWidget(QLabel("策略组名称:"))
        self.name_edit = QLineEdit(group_data['name'] if group_data else "")
        name_layout.addWidget(self.name_edit)
        layout.addLayout(name_layout)

        months_group = QGroupBox("适用月份")
        months_layout = QGridLayout(months_group)
        self.month_checks = {}
        for i in range(1, 13):
            cb = QCheckBox(f"{i}月")
            if i in self.selected_months:
                cb.setChecked(True)
            self.month_checks[str(i)] = cb
            row = (i-1) // 4
            col = (i-1) % 4
            months_layout.addWidget(cb, row, col)
        layout.addWidget(months_group)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['开始', '结束', '类型'])
        self.tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.tree.setDragDropMode(QTreeWidget.InternalMove)
        self.tree.setDefaultDropAction(Qt.MoveAction)
        self.tree.itemDoubleClicked.connect(self._edit_selected)
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
        edit_layout.addWidget(QPushButton("清空", clicked=self._clear_periods))
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
        self._undo_stack.append(self.current_periods[:])
        if len(self._undo_stack) > self._max_history:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self._update_undo_redo_buttons()

    def _undo(self):
        if not self._undo_stack:
            return
        self._redo_stack.append(self.current_periods[:])
        self.current_periods = self._undo_stack.pop()
        self._populate_list()
        self._update_undo_redo_buttons()
        self._check_conflicts()

    def _redo(self):
        if not self._redo_stack:
            return
        self._undo_stack.append(self.current_periods[:])
        self.current_periods = self._redo_stack.pop()
        self._populate_list()
        self._update_undo_redo_buttons()
        self._check_conflicts()

    def _update_undo_redo_buttons(self):
        self.undo_btn.setEnabled(len(self._undo_stack) > 0)
        self.redo_btn.setEnabled(len(self._redo_stack) > 0)

    def _find_overnight_indices(self):
        indices = set()
        for i in range(len(self.current_periods)-1):
            p1, p2 = self.current_periods[i], self.current_periods[i+1]
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
        dlg = PeriodEditDialog(self, config_manager=self.config_manager)
        if dlg.exec() == QDialog.Accepted and dlg.result:
            self.current_periods.append(dlg.result)
            ok, msg, _ = _valid_periods(self.current_periods)
            if not ok:
                self.current_periods.pop()
                log.debug("添加时段导致冲突: %s", msg)
                QMessageBox.critical(self, "时段冲突", msg)
            else:
                self._populate_list()
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
        dlg = PeriodEditDialog(self, self.current_periods[idx], config_manager=self.config_manager)
        if dlg.exec() == QDialog.Accepted and dlg.result:
            old = self.current_periods[idx]
            self.current_periods[idx] = dlg.result
            ok, msg, _ = _valid_periods(self.current_periods)
            if not ok:
                self.current_periods[idx] = old
                QMessageBox.critical(self, "时段冲突", msg)
            else:
                self._populate_list()
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
        elif self._undo_stack:
            self._undo_stack.pop()
            self._update_undo_redo_buttons()
        self._check_conflicts()

    def _clear_periods(self):
        if self.current_periods:
            self._push_history()
            if QMessageBox.question(self, "确认", "清空所有时段？") == QMessageBox.Yes:
                self.current_periods.clear()
                self._populate_list()
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
            log.debug("经典模板无效: %s", msg)
            QMessageBox.critical(self, "快速模板无效", msg)
            self._undo_stack.pop()
            self._update_undo_redo_buttons()
            return
        self.current_periods = new_periods
        self._populate_list()
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
            log.debug("夏季模板无效: %s", msg)
            QMessageBox.critical(self, "快速模板无效", msg)
            self._undo_stack.pop()
            self._update_undo_redo_buttons()
            return
        self.current_periods = new_periods
        self._populate_list()
        self._check_conflicts()

    def _check_conflicts(self):
        ok, msg, _ = _valid_periods(self.current_periods)
        if not ok:
            self.conflict_label.setText(f"⚠ {msg}")
        else:
            self.conflict_label.setText("")

    def _save(self):
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "提示", "请输入策略组名称")
            return

        selected_months = sorted([int(m) for m, cb in self.month_checks.items() if cb.isChecked()])
        if not selected_months:
            QMessageBox.warning(self, "提示", "请至少选择一个适用月份")
            return
        if not self.current_periods:
            QMessageBox.warning(self, "提示", "请至少添加一个充放电时段")
            return

        ok, msg, _ = _valid_periods(self.current_periods)
        if not ok:
            QMessageBox.critical(self, "时段校验失败", msg)
            return

        self.result = {
            'name': name,
            'months': selected_months,
            'periods': self.current_periods[:]
        }
        self.accept()

    def reject(self):
        self.result = None
        super().reject()

    def closeEvent(self, event):
        if self.config_manager:
            set_window_geometry(self.config_manager, "GroupPeriodEditDialog", self.saveGeometry())
        super().closeEvent(event)


class ProvinceStrategyEditDialog(QDialog):
    """省份策略编辑对话框"""
    strategy_saved = Signal()

    def __init__(self, parent, config_manager, province_name):
        super().__init__(parent)
        self.config_manager = config_manager
        self.province_name = province_name
        self.setWindowTitle(f"编辑省份策略 — {province_name}")
        self.setMinimumSize(750, 500)

        if self.config_manager:
            geom = get_window_geometry(self.config_manager, "ProvinceStrategyEditDialog")
            if geom:
                self.restoreGeometry(geom)
            else:
                self.resize(800, 550)
        else:
            self.resize(800, 550)

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(f"<b>省份:</b> {self.province_name}"))

        self.group_tree = QTreeWidget()
        self.group_tree.setHeaderLabels(['策略组名称', '适用月份', '时段数'])
        self.group_tree.itemDoubleClicked.connect(self._edit_group)
        layout.addWidget(self.group_tree)

        self.month_panel = QWidget()
        month_grid = QGridLayout(self.month_panel)
        self.month_labels = {}
        for i in range(1, 13):
            lbl = QLabel(f"{i}月")
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setFixedSize(60, 28)
            lbl.setStyleSheet("background-color: lightgray; border: 1px solid gray; border-radius: 4px;")
            self.month_labels[i] = lbl
            month_grid.addWidget(lbl, (i-1)//4, (i-1)%4)
        layout.addWidget(QLabel("月份覆盖:"))
        layout.addWidget(self.month_panel)

        btn_layout = QHBoxLayout()
        btn_layout.addWidget(QPushButton("添加策略组", clicked=self._add_group))
        btn_layout.addWidget(QPushButton("编辑选中组", clicked=self._edit_group))
        btn_layout.addWidget(QPushButton("删除选中组", clicked=self._delete_group))
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        dlg_btns = QDialogButtonBox(QDialogButtonBox.Close)
        dlg_btns.rejected.connect(self.accept)
        layout.addWidget(dlg_btns)

        self._refresh()

    def _refresh(self):
        self.group_tree.clear()
        groups = self.config_manager.get_province_groups(self.province_name)
        used_months = set()
        for g in groups:
            months_str = ','.join(str(m) for m in g['months'])
            self.group_tree.addTopLevelItem(QTreeWidgetItem([g['name'], months_str, str(len(g['periods']))]))
            used_months.update(g['months'])

        for m in range(1, 13):
            lbl = self.month_labels[m]
            if m in used_months:
                lbl.setStyleSheet("background-color: #90EE90; border: 1px solid gray; border-radius: 4px;")
            else:
                lbl.setStyleSheet("background-color: #FFB6C1; border: 1px solid gray; border-radius: 4px;")

    def _add_group(self):
        dlg = GroupPeriodEditDialog(self, self.config_manager, self.province_name)
        if dlg.exec() == QDialog.Accepted and dlg.result:
            res = dlg.result
            success = self.config_manager.add_group(
                self.province_name, res['name'], res['months'], res['periods']
            )
            if not success:
                QMessageBox.warning(self, "添加失败", "月份重叠，请检查。")
            else:
                self._refresh()
                self.strategy_saved.emit()

    def _edit_group(self):
        items = self.group_tree.selectedItems()
        if not items:
            return
        group_name = items[0].text(0)
        groups = self.config_manager.get_province_groups(self.province_name)
        target = next((g for g in groups if g['name'] == group_name), None)
        if not target:
            return
        dlg = GroupPeriodEditDialog(self, self.config_manager, self.province_name, target)
        if dlg.exec() == QDialog.Accepted and dlg.result:
            res = dlg.result
            success = self.config_manager.update_group(
                self.province_name, group_name, res['name'], res['months'], res['periods']
            )
            if not success:
                QMessageBox.warning(self, "更新失败", "月份重叠，请检查。")
            else:
                self._refresh()
                self.strategy_saved.emit()

    def _delete_group(self):
        items = self.group_tree.selectedItems()
        if not items:
            return
        group_name = items[0].text(0)
        if QMessageBox.question(self, "确认", f"删除策略组“{group_name}”？") == QMessageBox.Yes:
            self.config_manager.delete_group(self.province_name, group_name)
            self._refresh()
            self.strategy_saved.emit()

    def closeEvent(self, event):
        if self.config_manager:
            set_window_geometry(self.config_manager, "ProvinceStrategyEditDialog", self.saveGeometry())
        super().closeEvent(event)


class ChargeDischargeManagerDialog(QDialog):
    """充放策略管理对话框（重构为省份树）"""
    strategy_selected = Signal(str)

    def __init__(self, parent, cfg):
        super().__init__(parent)
        self.cfg = cfg
        self.result = None
        self.setWindowTitle("充放时段策略管理")
        self.setMinimumSize(520, 400)

        if self.cfg:
            geom = get_window_geometry(self.cfg, "ChargeDischargeManagerDialog")
            if geom:
                self.restoreGeometry(geom)
            else:
                self.resize(550, 400)
        else:
            self.resize(550, 400)

        layout = QVBoxLayout(self)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['省份/策略组', '月份/时段数'])
        self.tree.itemDoubleClicked.connect(self._edit_item)
        layout.addWidget(self.tree)

        btn_layout = QHBoxLayout()
        btn_layout.addWidget(QPushButton("新建省份", clicked=self._new_province))
        btn_layout.addWidget(QPushButton("重命名", clicked=self._rename_province))
        btn_layout.addWidget(QPushButton("编辑省份", clicked=self._edit_province))
        btn_layout.addWidget(QPushButton("删除省份", clicked=self._delete_province))
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        dlg_btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        dlg_btns.accepted.connect(self._accept)
        dlg_btns.rejected.connect(self.reject)
        layout.addWidget(dlg_btns)

        self.refresh_tree()

    def refresh_tree(self):
        self.tree.clear()
        for prov_name in self.cfg.provinces():
            prov_item = QTreeWidgetItem([prov_name, ""])
            prov_item.setData(0, Qt.UserRole, ('province', prov_name))
            groups = self.cfg.get_province_groups(prov_name)
            for g in groups:
                months_str = ','.join(str(m) for m in g['months'])
                g_item = QTreeWidgetItem([g['name'], months_str])
                g_item.setData(0, Qt.UserRole, ('group', prov_name, g['name']))
                prov_item.addChild(g_item)
            self.tree.addTopLevelItem(prov_item)
            self.tree.expandItem(prov_item)

    def _new_province(self):
        name, ok = QInputDialog.getText(self, "新建省份", "输入省份名称:")
        if ok and name.strip():
            if name.strip() in self.cfg.provinces():
                QMessageBox.warning(self, "错误", "省份已存在")
                return
            self.cfg.add_province(name.strip())
            self.refresh_tree()

    def _rename_province(self):
        """v2.3fix: 重命名省份"""
        items = self.tree.selectedItems()
        if not items:
            QMessageBox.information(self, "提示", "请先在左侧树中选中要重命名的省份")
            return
        data = items[0].data(0, Qt.UserRole)
        if data[0] != 'province':
            QMessageBox.information(self, "提示", "请选中省份节点（而非策略组）进行重命名")
            return
        old_name = data[1]
        new_name, ok = QInputDialog.getText(self, "重命名省份", "输入新名称:", text=old_name)
        if ok and new_name.strip() and new_name.strip() != old_name:
            if new_name.strip() in self.cfg.provinces():
                QMessageBox.warning(self, "错误", "省份名称已存在")
                return
            success = self.cfg.rename_province(old_name, new_name.strip())
            if success:
                self.refresh_tree()
            else:
                QMessageBox.warning(self, "错误", "重命名失败")

    def _edit_province(self):
        items = self.tree.selectedItems()
        if not items:
            return
        data = items[0].data(0, Qt.UserRole)
        if data[0] == 'province':
            prov_name = data[1]
        elif data[0] == 'group':
            prov_name = data[1]
        else:
            return
        dlg = ProvinceStrategyEditDialog(self, self.cfg, prov_name)
        dlg.strategy_saved.connect(self.refresh_tree)
        dlg.exec()
        self.refresh_tree()

    def _delete_province(self):
        items = self.tree.selectedItems()
        if not items:
            return
        data = items[0].data(0, Qt.UserRole)
        if data[0] == 'province':
            prov_name = data[1]
        elif data[0] == 'group':
            prov_name = data[1]
        else:
            return
        if QMessageBox.question(self, "确认", f"删除省份“{prov_name}”及其所有策略组？") == QMessageBox.Yes:
            self.cfg.delete_province(prov_name)
            self.refresh_tree()

    def _edit_item(self, item):
        data = item.data(0, Qt.UserRole)
        if data and data[0] == 'province':
            self._edit_province()
        elif data and data[0] == 'group':
            prov_name = data[1]
            dlg = ProvinceStrategyEditDialog(self, self.cfg, prov_name)
            dlg.strategy_saved.connect(self.refresh_tree)
            dlg.exec()
            self.refresh_tree()

    def _accept(self):
        items = self.tree.selectedItems()
        if items:
            data = items[0].data(0, Qt.UserRole)
            if data and data[0] == 'province':
                self.result = data[1]
            elif data and data[0] == 'group':
                self.result = data[1]
        self.accept()

    def closeEvent(self, event):
        if self.cfg:
            set_window_geometry(self.cfg, "ChargeDischargeManagerDialog", self.saveGeometry())
        super().closeEvent(event)


# ======================= 映射对话框（汉化，增加日志记录） =======================
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
        self.setMinimumSize(820, 600)

        if self.cfg:
            geom = get_window_geometry(self.cfg, "MappingDialog")
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
                # v2.3fix: 使用安全引擎检测读取样本
                self._sample_df = _safe_read_excel(fp, sheet=sheet if sheet != "CSV文件" else 0, nrows=100, header=hrow)
            self._sample_df.columns = [str(c) for c in self._sample_df.columns]
        except Exception:
            log.debug("读取样本数据失败，智能识别将降级", exc_info=True)

        self._init_ui(multi_sheet)

        self._apply_initial_format()
        self._run_auto_recommendation()

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
                log.debug("重试读取列名失败", exc_info=True)
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

    def _refresh_sample_df(self):
        """重新读取样本数据，使用当前标题行设置。刷新列名后调用以保持样本与列名同步。"""
        hrow = self.header_row_spin.value() - 1
        try:
            if os.path.splitext(self.fp)[1].lower() == '.csv':
                e = self.enc if self.enc != 'auto' else _detect_enc(self.fp)
                self._sample_df = pd.read_csv(self.fp, encoding=e, nrows=100, header=hrow)
            else:
                self._sample_df = _safe_read_excel(self.fp,
                    sheet=self.sheet if self.sheet != "CSV文件" else 0,
                    nrows=100, header=hrow)
            self._sample_df.columns = [str(c) for c in self._sample_df.columns]
        except Exception:
            log.debug("刷新样本数据失败，智能识别将降级", exc_info=True)
            self._sample_df = None

    def _run_auto_recommendation(self):
        """统一推荐管道：先智能分类，失败则回落历史配置匹配。初始化、刷新列名、智能识别按钮三条路径共用。"""
        self._save_pre_auto_state()
        smart_rec = _smart_classify_columns(self.cols, self._sample_df)
        if smart_rec and 'date_col' in smart_rec:
            self._apply_smart_rec(smart_rec)
            basis = "智能识别"
            if smart_rec.get('format_type') == 'wide':
                basis += "（检测到多列时间点负荷，宽表格式）"
            elif smart_rec.get('format_type') == 'wide_transposed':
                basis += "（检测到转置宽表，日期列已自动勾选为负荷数据）"
            elif smart_rec.get('format_type') == 'accumulated':
                basis += "（检测到正向有功示数特征，将自动差分计算功率）"
            else:
                basis += "（长表格式）"
            self.confidence_label.setText(f"已自动推荐（{basis}）")
            return True
        if self.cfg:
            rec = self.cfg.recommend(self.fp, self.sheet, self.cols)
            if rec:
                self._apply_rec(rec, perform_highlight=True)
                self.confidence_label.setText("已自动推荐（历史配置匹配）")
                return True
        self.confidence_label.setText("未找到推荐配置，请手动选择")
        return False

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

        fmt_group = QGroupBox("数据格式")
        fmt_layout = QVBoxLayout(fmt_group)
        self.fmt_long = QRadioButton("长表（单列负荷）")
        self.fmt_accumulated = QRadioButton("正向有功示数表（多列累积电度，自动差分计算功率）")
        self.fmt_wide = QRadioButton("宽表（多列时间点负荷）")
        if self._initial_format_type in ('wide', 'wide_transposed'):
            self.fmt_wide.setChecked(True)
        elif self._initial_format_type == 'accumulated':
            self.fmt_accumulated.setChecked(True)
        else:
            self.fmt_long.setChecked(True)
        if self._initial_format_type is None:
            # v2.3.9fix(P2-1): 自动检测失败时明确告知。原来失败也默认勾长表，
            # 宽表被直接当长表解析，报「缺少列」而用户以为是自己选错了列。
            _fmt_warn = QLabel("自动检测失败，请手动确认数据格式")
            _fmt_warn.setStyleSheet("color: #c62828;")
            fmt_layout.addWidget(_fmt_warn)
        fmt_layout.addWidget(self.fmt_long)
        fmt_layout.addWidget(self.fmt_accumulated)
        fmt_layout.addWidget(self.fmt_wide)
        self.fmt_group_btns = QButtonGroup()
        self.fmt_group_btns.addButton(self.fmt_long, 1)
        self.fmt_group_btns.addButton(self.fmt_accumulated, 2)
        self.fmt_group_btns.addButton(self.fmt_wide, 3)
        self.fmt_group_btns.buttonClicked.connect(self._fmt_chg)
        layout.addWidget(fmt_group)

        self.accumulated_hint_label = QLabel(
            "<small>程序将对每个选中的列按日期分组、按时间顺序差分，自动计算平均功率。请勾选所有累积电度列。</small>"
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
        self.time_combo.setToolTip("宽表/示数表时无需选择时间列，时间点将从列名自动识别")
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
        from difflib import SequenceMatcher
        if target in cols:
            return target, 1.0
        best_score = 0
        best_col = None
        for c in cols:
            score = SequenceMatcher(None, target.lower(), str(c).lower()).ratio()
            if score > best_score:
                best_score = score
                best_col = c
        if best_score >= 0.6:
            return best_col, best_score
        return target, 0.0

    def _apply_smart_rec(self, rec):
        if 'date_col' in rec:
            self.date_combo.setCurrentText(rec['date_col'])
        if rec.get('time_col'):
            self.time_combo.setCurrentText(rec['time_col'])
        if 'asset_col' in rec:
            self.asset_combo.setCurrentText(rec['asset_col'])
        fmt = rec.get('format_type')
        if fmt in ('wide', 'wide_transposed', 'accumulated'):
            self.fmt_wide.setChecked(True)
            if fmt == 'accumulated':
                self.fmt_accumulated.setChecked(True)
        self._fmt_chg()
        if 'load_col' in rec:
            load_cols = rec.get('load_col', [])
            if isinstance(load_cols, str):
                load_cols = [load_cols]
            if fmt in ('wide', 'wide_transposed', 'accumulated'):
                for i in range(self.load_list.count()):
                    self.load_list.item(i).setSelected(self.load_list.item(i).text() in load_cols)
            else:
                lc = load_cols[0] if load_cols else ''
                self.load_combo.setCurrentText(lc)

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

        fmt = m.get('format_type', 'long')
        if fmt in ('wide', 'wide_transposed', 'accumulated'):
            self.fmt_wide.setChecked(True)
            if fmt == 'accumulated':
                self.fmt_accumulated.setChecked(True)
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
        if self.fmt_wide.isChecked() or self.fmt_accumulated.isChecked():
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
            except Exception:
                log.debug("断开旧刷新任务信号失败", exc_info=True)
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
            self._refresh_sample_df()
            self.confidence_label.setText("列名已刷新，请点击「智能识别」重新匹配")
            self.refresh_status.setText("✓ 已更新")
        self.header_row_spin.setEnabled(True)
        self.refresh_btn.setEnabled(True)
        self._set_mapping_enabled(True)
        QTimer.singleShot(2000, lambda: self.refresh_status.setText(""))

    def _on_refresh_error(self, fp, error_msg):
        if fp == self.fp:
            log.debug("刷新列名失败: %s", error_msg)
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
        if state['fmt_wide'] or state['fmt_accumulated']:
            if state['fmt_accumulated']:
                self.fmt_accumulated.setChecked(True)
            else:
                self.fmt_wide.setChecked(True)
            self._fmt_chg()
            for i in range(self.load_list.count()):
                self.load_list.item(i).setSelected(self.load_list.item(i).text() in state['load_list'])
        else:
            self.fmt_long.setChecked(True)
            self._fmt_chg()
            self.load_combo.setCurrentText(state['load'])
        self._pre_auto_state = None
        self.undo_auto_btn.setEnabled(False)
        self.confidence_label.setText("已撤销智能识别")

    def _auto(self):
        if not self._run_auto_recommendation():
            cand = self.cols
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
        is_wide_or_accum = self.fmt_wide.isChecked() or self.fmt_accumulated.isChecked()
        if is_wide_or_accum:
            self.load_combo.setVisible(False)
            self.load_list.setVisible(True)
            self.time_combo.setCurrentText('')
            self.time_combo.setEnabled(False)
            self.time_label.setToolTip("宽表/示数表时无需选择时间列，时间点将从列名中自动识别")
            self.accumulated_hint_label.setVisible(self.fmt_accumulated.isChecked())
        else:
            self.load_combo.setVisible(True)
            self.load_list.setVisible(False)
            self.time_combo.setEnabled(True)
            self.time_label.setToolTip("")
            self.accumulated_hint_label.setVisible(False)

    def _ok(self):
        d = self.date_combo.currentText().strip()
        t = self.time_combo.currentText().strip() or None
        a = self.asset_combo.currentText().strip() or None
        if not d:
            QMessageBox.critical(self, "错误", "必须选择日期列！")
            return
        if self.fmt_wide.isChecked() or self.fmt_accumulated.isChecked():
            selected = self.load_list.selectedItems()
            if not selected:
                QMessageBox.critical(self, "错误", "宽表/示数表格式必须至少勾选一个负荷列！")
                return
            l = [item.text() for item in selected]
            fmt = 'accumulated' if self.fmt_accumulated.isChecked() else 'wide'
        else:
            l = [self.load_combo.currentText().strip()]
            if not l[0]:
                QMessageBox.critical(self, "错误", "必须选择负荷列！")
                return
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
            'convert_accumulated': fmt == 'accumulated'
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
            except Exception:
                pass
            self._refresh_task = None
        if self.cfg:
            set_window_geometry(self.cfg, "MappingDialog", self.saveGeometry())
        super().closeEvent(event)
# -*- coding: utf-8 -*-
# 第三部分：预览对话框辅助控制器、预览对话框
# 注意：本部分代码紧跟第一、二部分，共享同一命名空间

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

# ======================= 预览渲染任务（补遗） =======================
class PreviewRenderTask(QObject, QRunnable):
    preview_ready = Signal(int, QPixmap)
    error_occurred = Signal(int, str)
    task_done = Signal(int)

    def __init__(self, index, summary, provider, plot_options: PlotOptions, multiplier, billing, periods, title_tpl):
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

                # 使用 PlotOptions 数据类的 to_dict 方法
                opts_dict = self.plot_options.to_dict()
                fig, ax = plt.subplots(figsize=(10, 6), dpi=100)
                if self.plot_options.dark_theme:
                    fig.patch.set_facecolor('#1e1e1e')
                    ax.set_facecolor('#2e2e2e')
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
                                  title, opts_dict, self.billing, self.periods)

                fig.tight_layout()
                buf = io.BytesIO()
                if self._cancel_event.is_set():
                    plt.close(fig)
                    return
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
        self.plot_options = PlotOptions()  # 使用数据类默认值

    def set_plot_options(self, opts: PlotOptions):
        self.plot_options = opts

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
            self.plot_options.show_daily,
            self.plot_options.show_avg,
            self.plot_options.show_max_demand,
            self.plot_options.highlight_mode,
            self.plot_options.weekday_color,
            self.plot_options.weekend_color,
            self.plot_options.holiday_color,
            self.plot_options.weekend_line_width,
            self.plot_options.holiday_line_width,
            self.plot_options.show_weekend_bg,
            self.plot_options.legend_position,
            self.plot_options.mark_anomalies,
            self.plot_options.dark_theme,
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


# ===================== 预览对话框（策略联动改进 + 专业模式入口 + 暗色主题支持） =====================

class PreviewDialog(QDialog):
    def __init__(self, parent, all_summaries, config_manager, initial_multiplier=1.0,
                 initial_show_max_demand=False, initial_strategy_name=None, initial_output_dir="", data_provider=None):
        super().__init__(parent)
        self.setWindowFlags(self.windowFlags() | Qt.WindowMinMaxButtonsHint)
        self.all_summaries = all_summaries
        self.config_manager = config_manager
        self.current_index = 0
        self._first_render_done = False
        self._last_province_text = ""
        self._zoom_level = 1.0
        self._show_max_demand = initial_show_max_demand if initial_show_max_demand is not None else False
        self._selected_province = None

        self.provider = data_provider if data_provider else DataProvider(
            self.config_manager.app_set('last_csv_encoding', 'auto'))

        self.setWindowTitle("负荷曲线预览与设置")
        self.setMinimumSize(1000, 650)
        if self.config_manager:
            geom = get_window_geometry(self.config_manager, "PreviewDialog")
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
        self._dark_theme = self.config_manager.app_set('dark_theme', False)

        self._preload_timer = None
        self._is_closing = False
        self._reusable = True
        self._install_process = None
        self._install_retry_count = 0

        # 专业窗口引用（非模态）
        self._professional_window = None

        self._init_ui(initial_multiplier, initial_output_dir)

        # 初始化渲染调度器，使用 PlotOptions 对象
        plot_opts = PlotOptions(
            show_daily=self.config_manager.app_set('show_daily', True),
            show_avg=self.config_manager.app_set('show_avg', True),
            show_max_demand=self._show_max_demand,
            highlight_mode=self._highlight_mode,
            weekday_color=self._weekday_color,
            weekend_color=self._weekend_color,
            holiday_color=self._holiday_color,
            weekend_line_width=self._weekend_line_width,
            holiday_line_width=self._holiday_line_width,
            show_weekend_bg=self._show_weekend_bg,
            legend_position=self._legend_position,
            mark_anomalies=self._mark_anomalies,
            dark_theme=self._dark_theme,
        )
        self.render_scheduler = _PreviewRenderScheduler(
            self.provider,
            self.all_summaries,
            self.settings_model,
            strategy_getter=lambda idx: self._periods_for_idx(idx),
            title_tpl_getter=lambda: self.title_edit.text()
        )
        self.render_scheduler.set_plot_options(plot_opts)
        self._connect_render_signals()
        self._apply_global_display_options()

        self.tree_controller.load(self.all_summaries, set())
        self.tree_controller.itemClicked.connect(self._navigate_to)
        self.tree_controller.selectionChanged.connect(self._on_tree_selection_changed)
        self.tree_controller.set_all_check(True)

        self.province_combo.currentTextChanged.connect(self._on_province_changed)
        self._last_province_text = self.province_combo.currentText()
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
        except Exception:
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

        # 新增：一键最大需量按钮
        max_demand_layout = QHBoxLayout()
        self.max_demand_btn = QPushButton("一键最大需量")
        self.max_demand_btn.setToolTip("将分组的当月最大需量（负荷峰值）设为计费容量线")
        self.max_demand_btn.clicked.connect(self._set_billing_to_max_demand)
        max_demand_layout.addWidget(self.max_demand_btn)
        max_demand_layout.addStretch()
        bill_outer.addLayout(max_demand_layout)

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
        # 暗色主题开关
        self.dark_theme_cb = QCheckBox("暗色图表背景")
        self.dark_theme_cb.setChecked(self._dark_theme)
        self.dark_theme_cb.stateChanged.connect(self._apply_global_display_options)
        extra_layout.addWidget(self.dark_theme_cb)

        disp_layout.addLayout(extra_layout)

        self.param_layout.addWidget(disp_group)

        # -------------------- 充放策略（改为省份选择） --------------------
        strat_layout = QHBoxLayout()
        strat_layout.addWidget(QLabel("省份:"))
        self.province_combo = QComboBox()
        self.province_combo.addItem("无")
        self.province_combo.addItems(self.config_manager.provinces())
        self.province_combo.setCurrentText("无")
        self._last_province_text = self.province_combo.currentText()
        strat_layout.addWidget(self.province_combo)
        edit_btn = QPushButton("管理策略")
        edit_btn.clicked.connect(self._manage_strategies)
        strat_layout.addWidget(edit_btn)
        self.professional_btn = QPushButton("专业模式")
        self.professional_btn.setToolTip("进入专业分析模式（需量管理/峰谷套利/光伏消纳）")
        self.professional_btn.clicked.connect(self._open_professional_mode)
        strat_layout.addWidget(self.professional_btn)
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
            state = get_splitter_state(self.config_manager, "preview_splitter")
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
        self.dark_theme_cb.stateChanged.connect(self._apply_global_display_options)
        self.title_edit.textChanged.connect(self._update_title_preview)
        self.filename_edit.textChanged.connect(self._check_filename)
        self._update_multiplier_ui()
        self._update_billing_ui()

    # ==================== 一键最大需量设置容量线 ====================
    def _set_billing_to_max_demand(self):
        """
        一键将当月最大需量（图表中实际负荷峰值）设为计费容量线。
        弹出菜单选择应用范围：当前分组、勾选分组、全部分组。
        """
        menu = QMenu(self)
        act_current = menu.addAction("应用至当前分组")
        act_selected = menu.addAction("应用至勾选分组")
        act_all = menu.addAction("应用至全部分组")
        action = menu.exec(self.max_demand_btn.mapToGlobal(QPoint(0, self.max_demand_btn.height())))
        if not action:
            return

        if action == act_current:
            indices = [self.current_index]
        elif action == act_selected:
            indices = self.tree_controller.get_checked_indices()
            if not indices:
                QMessageBox.warning(self, "提示", "没有勾选任何分组")
                return
        elif action == act_all:
            indices = list(range(len(self.all_summaries)))
        else:
            return

        count = 0
        for idx in indices:
            summary = self.all_summaries[idx]
            # 获取已乘倍率的实际负荷数据
            data = self.provider.get_group_data(summary)
            if not data:
                continue
            load_array = np.array(data['load_array']) * self.settings_model.effective_multiplier(idx)
            if load_array.size == 0:
                continue
            max_load = float(np.nanmax(load_array))
            self.settings_model.set_custom_billing(idx, {'type': 'capacity', 'value': max_load})
            count += 1

        # 清除受影响分组的缓存并刷新当前视图
        for idx in indices:
            self.render_scheduler.clear_cache_for_index(idx)
        if self.current_index in indices:
            self._update_billing_ui()
            self._schedule_render(self.current_index)
        else:
            self._update_billing_ui()

        self._restart_preload_timer()
        # v3.2fix: 一键最大需量后自动启用容量线
        if hasattr(self, 'bill_enabled_cb') and not self.bill_enabled_cb.isChecked():
            self.bill_enabled_cb.setChecked(True)
        QMessageBox.information(self, "已设置", f"已将 {count} 个分组的容量线设置为当月最大需量")
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
        highlight_mode = self.render_scheduler.plot_options.highlight_mode
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
        if not _is_holiday_available():
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
            try:
                from chinese_calendar import is_holiday, is_workday
                _set_holiday_available(True)
                self.highlight_combo.model().item(2).setEnabled(True)
                self.highlight_combo.model().item(3).setEnabled(True)
                self._holiday_warning_label.hide()
                QMessageBox.information(self, "安装成功", "chinesecalendar 已安装成功，现在可以使用节假日高亮。")
                invalidate_day_type_cache()
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
        dark_theme = self.dark_theme_cb.isChecked()

        opts = PlotOptions(
            show_daily=self.show_daily_cb.isChecked(),
            show_avg=self.show_avg_cb.isChecked(),
            show_max_demand=self.show_max_demand_cb.isChecked(),
            fit_types=[],
            highlight_mode=highlight_mode,
            weekday_color=self._weekday_color,
            weekend_color=self._weekend_color,
            holiday_color=self._holiday_color,
            weekend_line_width=self.weekend_lw_spin.value(),
            holiday_line_width=self.holiday_lw_spin.value(),
            show_weekend_bg=self.show_weekend_bg_cb.isChecked(),
            legend_position=legend_pos,
            mark_anomalies=self.mark_anomalies_cb.isChecked(),
            dark_theme=dark_theme,
        )
        self._dark_theme = dark_theme
        self.render_scheduler.set_plot_options(opts)
        self.render_scheduler.clear_all_caches()
        for i in range(len(self.all_summaries)):
            if i not in self.tree_controller.no_data_indices:
                self.tree_controller.update_status(i, "等待加载...")
        self._schedule_render(self.current_index)
        self._update_date_panel(self.current_index)
        self._restart_preload_timer()

    def _periods_for_idx(self, idx):
        """根据省份和分组月份获取时段（新接口）"""
        province = self.province_combo.currentText()
        if province == "无" or not province:
            return []
        s = self.all_summaries[idx]
        month_int = _parse_month_num(s.month_str)
        if month_int is None:
            # 不猜月份：猜成 1 月会静默套用冬季分时电价，图看着正常但测算结果是错的。
            log.warning(f"月份无法识别({s.month_str!r})，该分组不套用分时时段")
            return []
        return self.config_manager.get_periods_for_month(province, month_int)

    def _init_periods_fingerprints(self):
        self._periods_fp_map = {}
        for idx in range(len(self.all_summaries)):
            periods = self._periods_for_idx(idx)
            fp = hash(tuple((p['start'], p['end'], p['type']) for p in periods))
            self._periods_fp_map[idx] = fp

    def _on_province_changed(self):
        new_text = self.province_combo.currentText()
        if new_text == self._last_province_text:
            return
        self._last_province_text = new_text
        self._selected_province = new_text if new_text != "无" else None
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
        previous_asset = None
        if 0 <= self.current_index < len(self.all_summaries):
            previous_asset = self.all_summaries[self.current_index].asset_value

        self.current_index = idx
        self.index_label.setText(f"{idx + 1} / {len(self.all_summaries)}")
        self._update_multiplier_ui()
        self._update_billing_ui()
        self._refresh_info_label()
        self._update_title_preview()
        self._schedule_render(idx)
        self.tree_controller.set_current_index(idx)
        self._update_date_panel(idx)

        if self._professional_window is not None and self._professional_window.isVisible():
            current_summary = self.all_summaries[idx]
            current_asset = current_summary.asset_value
            if current_asset != previous_asset:
                asset_summaries = [s for s in self.all_summaries if s.asset_value == current_asset]
                if asset_summaries:
                    self._professional_window.update_asset_summaries(
                        asset_summaries,
                        self.province_combo.currentText(),
                        self.render_scheduler.plot_options.to_dict(),
                        self._global_multiplier
                    )

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
        self.dark_theme_cb.setChecked(settings.get('dark_theme', False))

        old_model = self.settings_model
        if old_model:
            try:
                old_model.settingsChanged.disconnect(self._on_settings_changed)
            except Exception:
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
            'dark_theme': self.dark_theme_cb.isChecked(),
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
            except Exception:
                pass
        else:
            QMessageBox.information(self, "提示", "目录不存在，将在生成时创建")

    def _manage_strategies(self):
        old_sel = self.province_combo.currentText()
        dlg = ChargeDischargeManagerDialog(self, self.config_manager)
        if dlg.exec() == QDialog.Accepted:
            self.province_combo.blockSignals(True)
            self.province_combo.clear()
            self.province_combo.addItem("无")
            self.province_combo.addItems(self.config_manager.provinces())

            if dlg.result:
                self.province_combo.setCurrentText(dlg.result)
                self._last_province_text = dlg.result
                self._selected_province = dlg.result
            elif old_sel and old_sel in self.config_manager.provinces():
                self.province_combo.setCurrentText(old_sel)
                self._last_province_text = old_sel
                self._selected_province = old_sel if old_sel != "无" else None
            else:
                self.province_combo.setCurrentText("无")
                self._last_province_text = "无"
                self._selected_province = None
            self.province_combo.blockSignals(False)

            self._init_periods_fingerprints()
            self.render_scheduler.clear_all_caches()
            for i in range(len(self.all_summaries)):
                self.tree_controller.update_status(i, "加载中...")
            self._schedule_render(self.current_index)
            self._restart_preload_timer()

    # ==================== 专业模式入口（非模态） ====================
    def _open_professional_mode(self):
        if self.current_index < 0 or self.current_index >= len(self.all_summaries):
            QMessageBox.warning(self, "提示", "请先选择一个有效的分组。")
            return
        current_summary = self.all_summaries[self.current_index]
        current_asset = current_summary.asset_value
        asset_summaries = [s for s in self.all_summaries if s.asset_value == current_asset]
        if not asset_summaries:
            QMessageBox.warning(self, "提示", "未找到该表号下的数据。")
            return

        dlg = ProfessionalModeDialog(
            self,
            config_manager=self.config_manager,
            settings_model=self.settings_model,
            summaries=asset_summaries,
            current_province=self.province_combo.currentText(),
            current_idx=self.current_index
        )
        if dlg.exec() == QDialog.Accepted and dlg.selected_mode:
            mode = dlg.selected_mode

            if self._professional_window is not None:
                self._professional_window.close()
                self._professional_window = None

            prof_win = ProfessionalWindow(
                mode=mode,
                summaries=asset_summaries,
                provider=self.provider,
                config_manager=self.config_manager,
                settings_model=self.settings_model,
                province_name=self.province_combo.currentText() if self.province_combo.currentText() != "无" else None,
                plot_options=self.render_scheduler.plot_options.to_dict(),
                global_multiplier=self._global_multiplier,
                parent=self
            )
            prof_win.setAttribute(Qt.WA_DeleteOnClose)
            prof_win.destroyed.connect(self._on_professional_window_closed)
            prof_win.show()
            self._professional_window = prof_win

    def _on_professional_window_closed(self):
        self._professional_window = None

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
        dark_theme = self.dark_theme_cb.isChecked()

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
        self.config_manager.set_app('dark_theme', dark_theme)
        self.config_manager.set_app('last_province_name', self.province_combo.currentText() if self.province_combo.currentText() != "无" else None)
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

        province = self.province_combo.currentText()
        if province and province != "无":
            price_periods = self.config_manager.get_periods_for_all_months(province)
        else:
            price_periods = {}

        self.result = {
            'selected_indices': list(checked),
            'global_multiplier': self._global_multiplier,
            'per_group_multiplier': per_group_multiplier,
            'per_group_billing': per_group_billing,
            'output_dir': self.output_dir_edit.text(),
            'province_name': self.province_combo.currentText() if self.province_combo.currentText() != "无" else None,
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
            'dark_theme': dark_theme,
            'title_template': self.title_edit.text().strip(),
            'filename_template': self.filename_edit.text().strip(),
            'billing_dict': billing_dict,
            'classify_by_asset': self.classify_cb.isChecked(),
            'price_periods': price_periods
        }
        self.accept()

    def closeEvent(self, event):
        self._is_closing = True
        # v3.2fix: 停止所有内部定时器
        for timer_attr in ('_preload_timer', '_install_timeout_timer'):
            t = getattr(self, timer_attr, None)
            if t is not None and t.isActive():
                t.stop()
        if self.render_scheduler:
            self.render_scheduler.cancel_all()
            try:
                self.render_scheduler.renderReady.disconnect(self._on_render_ready)
                self.render_scheduler.renderError.disconnect(self._on_render_error)
                self.render_scheduler.cacheProgress.disconnect(self.cache_progress_label.setText)
            except Exception:
                pass
        if self._install_process is not None:
            self._install_process.kill()
            self._install_process = None
        if self._professional_window is not None:
            try:
                self._professional_window.destroyed.disconnect(self._on_professional_window_closed)
            except (RuntimeError, TypeError):
                pass
            try:
                self._professional_window.close()
            except RuntimeError:
                pass
            self._professional_window = None
        if self.config_manager:
            set_window_geometry(self.config_manager, "PreviewDialog", self.saveGeometry())
            set_splitter_state(self.config_manager, "preview_splitter", self.splitter.saveState())
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
# -*- coding: utf-8 -*-
# 第四部分：专业模式窗口、模块级绘图、处理线程、导出确认
# 注意：本部分代码紧跟第一、二、三部分，共享同一命名空间

class PVFeedbackDialog(QDialog):
    """光伏返送详情对话框：支持按月份查看每日光伏返送统计"""
    def __init__(self, monthly_data, global_multiplier, parent=None):
        super().__init__(parent)
        self.monthly_data = monthly_data
        self.global_multiplier = global_multiplier
        self.setWindowTitle("光伏返送详情")
        self.setMinimumSize(700, 500)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        top_layout = QHBoxLayout()
        top_layout.addWidget(QLabel("选择月份:"))
        self.month_combo = QComboBox()
        self.month_combo.addItems(sorted(self.monthly_data.keys()))
        self.month_combo.currentTextChanged.connect(self._refresh)
        top_layout.addWidget(self.month_combo)
        top_layout.addStretch()
        layout.addLayout(top_layout)

        self.summary_label = QLabel("")
        layout.addWidget(self.summary_label)

        self.table = QTableWidget()
        layout.addWidget(self.table)

        btn_box = QDialogButtonBox(QDialogButtonBox.Ok)
        btn_box.accepted.connect(self.accept)
        layout.addWidget(btn_box)

        if self.month_combo.count() > 0:
            self._refresh(self.month_combo.currentText())

    def _refresh(self, month):
        if not month or month not in self.monthly_data:
            self.table.setRowCount(0)
            self.summary_label.setText("")
            return
        data = self.monthly_data[month]
        load_array = np.array(data['load_array']) * self.global_multiplier
        dates = data.get('date_labels', [])
        n_days = load_array.shape[0]

        time_labels = data.get('time_labels', [])
        hours = np.array([_parse_min(_normalize_time_column_name(t)) / 60.0 for t in time_labels])
        dt = np.zeros_like(hours)
        if len(hours) > 1:
            dt[0] = hours[1] - hours[0]
            dt[-1] = hours[-1] - hours[-2]
            for i in range(1, len(hours) - 1):
                dt[i] = (hours[i + 1] - hours[i - 1]) / 2.0
        dt = np.maximum(dt, 0.0)

        daily_neg_energy = []
        daily_neg_max_power = []
        for d in range(n_days):
            neg_mask = load_array[d] < 0
            if not np.any(neg_mask):
                daily_neg_energy.append(0.0)
                daily_neg_max_power.append(0.0)
            else:
                daily_neg_energy.append(np.sum(np.abs(load_array[d][neg_mask]) * dt[neg_mask]))
                daily_neg_max_power.append(np.max(np.abs(load_array[d][neg_mask])))

        col_labels = ['日期', '返送电量 (kWh)', '最大返送功率 (kW)']
        self.table.setColumnCount(len(col_labels))
        self.table.setHorizontalHeaderLabels(col_labels)
        self.table.setRowCount(n_days)
        for d in range(n_days):
            self.table.setItem(d, 0, QTableWidgetItem(str(dates[d])))
            self.table.setItem(d, 1, QTableWidgetItem(f"{daily_neg_energy[d]:.3f}"))
            self.table.setItem(d, 2, QTableWidgetItem(f"{daily_neg_max_power[d]:.3f}"))

        self.table.resizeColumnsToContents()

        total_energy = np.sum(daily_neg_energy)
        max_power = max(daily_neg_max_power) if daily_neg_max_power else 0.0
        self.summary_label.setText(f"本月总返送电量: {total_energy:.3f} kWh，最大返送功率: {max_power:.3f} kW")


class ProfessionalModeDialog(QDialog):
    """专业模式选择对话框，适配新策略结构"""
    def __init__(self, parent, config_manager, settings_model, summaries, current_province, current_idx):
        super().__init__(parent)
        self.config_manager = config_manager
        self.settings_model = settings_model
        self.summaries = summaries
        self.current_province = current_province
        self.current_idx = current_idx
        self.selected_mode = None

        self.setWindowTitle("选择专业分析模式")
        self.setMinimumSize(500, 400)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("<b>请选择分析场景：</b>"))

        self.mode_group = QButtonGroup(self)
        self.radio_a = QRadioButton("需量管理 / 峰谷套利")
        self.radio_b = QRadioButton("纯光伏消纳")
        self.radio_c = QRadioButton("光伏消纳 + 峰谷套利（混合）")
        self.radio_a.setChecked(True)
        self.mode_group.addButton(self.radio_a, 0)
        self.mode_group.addButton(self.radio_b, 1)
        self.mode_group.addButton(self.radio_c, 2)

        desc_layout = QVBoxLayout()
        desc_layout.addWidget(QLabel("A：充放电时段+容量线，适用于需量管理/峰谷套利"))
        desc_layout.addWidget(QLabel("B：仅光伏返送存电，无需充放策略和容量线，需输入储能额定功率"))
        desc_layout.addWidget(QLabel("C：光伏充电+峰谷放电，需策略放电时段，可选容量限制"))
        layout.addWidget(self.radio_a)
        layout.addWidget(self.radio_b)
        layout.addWidget(self.radio_c)
        layout.addLayout(desc_layout)

        status_group = QGroupBox("当前参数状态")
        status_layout = QVBoxLayout(status_group)
        province_label = QLabel(f"当前省份：{current_province}")
        status_layout.addWidget(province_label)

        has_strategy = current_province != "无" and current_province in self.config_manager.provinces()
        status_layout.addWidget(QLabel(f"策略组数量：{len(self.config_manager.get_province_groups(current_province)) if has_strategy else '无'}"))

        any_bill = False
        for s in summaries:
            for i, sm in enumerate(settings_model.summaries):
                if sm == s:
                    bill = settings_model.effective_billing(i)
                    if bill and bill.get('value'):
                        any_bill = True
                        break
            if any_bill:
                break
        status_layout.addWidget(QLabel(f"容量线：{'已设置' if any_bill else '未设置'}"))
        layout.addWidget(status_group)

        self.warning_label = QLabel("")
        self.warning_label.setStyleSheet("color: red; font-weight: bold;")
        self.warning_label.setWordWrap(True)
        layout.addWidget(self.warning_label)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        enter_btn = QPushButton("进入分析")
        enter_btn.clicked.connect(self._on_enter)
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(enter_btn)
        layout.addLayout(btn_layout)

    def _on_enter(self):
        mode_id = self.mode_group.checkedId()
        if mode_id == 0:
            mode = 'A'
        elif mode_id == 1:
            mode = 'B'
        else:
            mode = 'C'

        errors = []
        any_bill = False
        for s in self.summaries:
            for i, sm in enumerate(self.settings_model.summaries):
                if sm == s:
                    bill = self.settings_model.effective_billing(i)
                    if bill and bill.get('value'):
                        any_bill = True
                        break
            if any_bill:
                break

        has_strategy = self.current_province != "无" and self.current_province in self.config_manager.provinces()

        if mode == 'A':
            if not has_strategy:
                errors.append("需量管理/峰谷套利模式必须选择省份策略。")
            if not any_bill:
                errors.append("未设置容量线（需量限制）。")
        elif mode == 'C':
            if not has_strategy:
                errors.append("混合模式建议选择省份策略以定义放电时段。")

        if errors:
            self.warning_label.setText("\n".join(errors))
            return
        self.selected_mode = mode
        self.accept()

class SimulationConfigDialog(QDialog):
    """
    负荷模拟配置对话框（增强版）
    - 支持多年份选择
    - 基于季节映射自动推荐源月份
    - 支持循环复制 / 平均曲线两种策略
    - 提供一键恢复原始数据入口
    """

    def __init__(self, professional_window, available_years, monthly_data, season_map, config_manager, parent=None):
        super().__init__(parent)
        self.pro_win = professional_window
        self.available_years = available_years
        self.monthly_data = monthly_data
        self.season_map = season_map
        self.config_manager = config_manager

        self._grid_widgets = {}
        self.result_configs = {}

        self.setWindowTitle("负荷模拟配置")
        self.setMinimumSize(650, 520)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        year_layout = QHBoxLayout()
        year_layout.addWidget(QLabel("目标年份:"))
        self.year_combo = QComboBox()
        self.year_combo.addItems([str(y) for y in self.available_years])
        self.year_combo.addItem("补全所有年份")
        self.year_combo.setCurrentIndex(0)
        self.year_combo.currentTextChanged.connect(self._on_year_changed)
        year_layout.addWidget(self.year_combo)
        layout.addLayout(year_layout)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_widget = QWidget()
        self.grid = QGridLayout(scroll_widget)
        self.grid.setContentsMargins(4, 4, 4, 4)
        scroll.setWidget(scroll_widget)
        layout.addWidget(scroll, 1)

        opt_layout = QHBoxLayout()
        self.auto_btn = QPushButton("自动推荐")
        self.auto_btn.clicked.connect(self._auto_fill)
        opt_layout.addWidget(self.auto_btn)

        self.avg_all_cb = QCheckBox("全部缺失月份使用平均曲线")
        self.avg_all_cb.stateChanged.connect(lambda: self._refresh_grid(self._get_current_year()))
        opt_layout.addWidget(self.avg_all_cb)

        opt_layout.addStretch()
        layout.addLayout(opt_layout)

        self.restore_btn = QPushButton("恢复原始数据")
        self.restore_btn.clicked.connect(self._restore_original)
        self.restore_btn.setEnabled(getattr(self.pro_win, '_use_simulation', False))
        layout.addWidget(self.restore_btn)

        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        self._refresh_grid(self._get_current_year())

    def _get_current_year(self):
        txt = self.year_combo.currentText()
        if txt == "补全所有年份":
            return None
        try:
            return int(txt)
        except ValueError:
            return None

    def _on_year_changed(self, txt):
        self._refresh_grid(txt if txt != "补全所有年份" else None)

    def _refresh_grid(self, year):
        for i in reversed(range(self.grid.count())):
            widget = self.grid.itemAt(i).widget()
            if widget:
                widget.setParent(None)

        self.grid.addWidget(QLabel("<b>月份</b>"), 0, 0)
        self.grid.addWidget(QLabel("<b>状态</b>"), 0, 1)
        self.grid.addWidget(QLabel("<b>模拟策略</b>"), 0, 2)
        self.grid.addWidget(QLabel("<b>源月份</b>"), 0, 3)

        if year is None:
            hint = QLabel("请先选择一个具体年份查看/设置配置。\n确认时将按相同配置补全所有年份。")
            hint.setWordWrap(True)
            self.grid.addWidget(hint, 1, 0, 1, 4)
            return

        existing_months = self._get_existing_months(year)
        src_options = [f"{m:02d}月" for m in sorted(existing_months)]
        if not src_options:
            QMessageBox.warning(self, "警告", f"{year}年没有可用数据，无法模拟。")
            return

        self._grid_widgets[year] = {}
        for month_int in range(1, 13):
            row = month_int
            self.grid.addWidget(QLabel(f"{month_int}月"), row, 0)

            key = f"{year}-{month_int:02d}"
            is_existing = month_int in existing_months
            status_label = QLabel("已有数据" if is_existing else "缺失")
            if not is_existing:
                status_label.setStyleSheet("color: red; font-weight: bold;")
            self.grid.addWidget(status_label, row, 1)

            strategy_combo = QComboBox()
            strategy_combo.addItems(["不模拟", "循环复制", "平均曲线"])
            if is_existing:
                strategy_combo.setCurrentIndex(0)
                strategy_combo.setEnabled(False)
            else:
                strategy_combo.setCurrentIndex(1)
                if self.avg_all_cb.isChecked():
                    strategy_combo.setCurrentIndex(2)
            self.grid.addWidget(strategy_combo, row, 2)

            src_combo = QComboBox()
            src_combo.addItems(src_options)
            if existing_months:
                nearest = min(existing_months, key=lambda x: abs(x - month_int))
                idx = src_combo.findText(f"{nearest:02d}月")
                if idx >= 0:
                    src_combo.setCurrentIndex(idx)
            self.grid.addWidget(src_combo, row, 3)

            self._grid_widgets[year][month_int] = (strategy_combo, src_combo)

    def _get_existing_months(self, year):
        months = []
        for key in self.monthly_data.keys():
            m = re.match(rf'^{year}-(\d{{2}})$', key)
            if m:
                months.append(int(m.group(1)))
        return months

    def _auto_fill(self):
        year = self._get_current_year()
        if year is None:
            QMessageBox.information(self, "提示", "请先选择一个具体年份，再使用自动推荐。")
            return
        existing = self._get_existing_months(year)
        if not existing:
            QMessageBox.warning(self, "警告", f"{year}年没有可用数据，无法推荐。")
            return

        widgets = self._grid_widgets.get(year, {})
        for month_int in range(1, 13):
            if month_int in existing:
                continue
            if month_int not in widgets:
                continue

            strategy_combo, src_combo = widgets[month_int]
            best_src = self._find_best_source(month_int, existing)
            if best_src is not None:
                idx = src_combo.findText(f"{best_src:02d}月")
                if idx >= 0:
                    src_combo.setCurrentIndex(idx)

        QMessageBox.information(self, "完成", "已按季节映射自动推荐源月份。")

    def _find_best_source(self, target_month, available_months):
        if not self.season_map:
            return min(available_months, key=lambda x: abs(x - target_month))

        for season, months in self.season_map.items():
            if target_month in months:
                candidates = [m for m in available_months if m in months]
                if candidates:
                    return min(candidates, key=lambda x: abs(x - target_month))
        return min(available_months, key=lambda x: abs(x - target_month))

    def _on_accept(self):
        year_txt = self.year_combo.currentText()
        target_years = []
        if year_txt == "补全所有年份":
            target_years = self.available_years[:]
        else:
            target_years = [int(year_txt)]

        configs = {}
        for year in target_years:
            widgets = self._grid_widgets.get(year, {})
            if not widgets:
                continue
            year_config = {}
            for month_int, (strategy_combo, src_combo) in widgets.items():
                strategy = strategy_combo.currentText()
                if strategy == "不模拟":
                    continue
                src_text = src_combo.currentText()
                if not src_text:
                    continue
                year_config[month_int] = {
                    "src": src_text,
                    "strategy": strategy
                }
            if year_config:
                configs[year] = year_config

        if not configs:
            QMessageBox.warning(self, "警告", "没有配置任何模拟月份，请检查设置。")
            return

        self.result_configs = configs
        self.accept()

    def _restore_original(self):
        if QMessageBox.question(self, "确认", "确定要恢复为原始数据，清除所有模拟结果吗？") == QMessageBox.Yes:
            self.pro_win._restore_original()
            self.restore_btn.setEnabled(False)

class ProfessionalWindow(QMainWindow):
    """专业分析窗口（增强版：年份自适应、统一分辨率、负荷模拟优化、容量规划模式选择）"""

    def __init__(self, mode, summaries, provider, config_manager, settings_model,
                 province_name, plot_options, global_multiplier, parent=None):
        super().__init__(parent)
        self.mode = mode
        self.summaries = summaries
        self.provider = provider
        self.config_manager = config_manager
        self.settings_model = settings_model
        self.province_name = province_name
        self.plot_options = plot_options
        self.global_multiplier = global_multiplier
        self.rated_power = None
        self.need_capacity = False
        self.ignore_negative = (mode == 'A')

        # 新增：容量规划模式
        self.capacity_mode = "two_two"           # "two_two" 或 "one_one"
        self.charge_option = "charge_1"          # "charge_1", "charge_2", "combined"
        self.discharge_option = "discharge_1"    # "discharge_1", "discharge_2", "combined"

        self.setWindowTitle(f"专业分析 - 模式{'A' if mode=='A' else ('B' if mode=='B' else 'C')}")
        self.setMinimumSize(950, 700)
        # v3.2fix: 恢复窗口几何
        if self.config_manager:
            geom = get_window_geometry(self.config_manager, "ProfessionalWindow")
            if geom:
                self.restoreGeometry(geom)
            else:
                self.resize(950, 700)

        if isinstance(self.plot_options, dict):
            self.plot_options = PlotOptions.from_dict(self.plot_options)
        elif not isinstance(self.plot_options, PlotOptions):
            self.plot_options = PlotOptions()

        self._build_idx_map()
        self.monthly_data = {}
        self.available_days = 0
        self._load_all_data()
        self.available_years = []
        self._extract_years()
        self._unify_time_resolution()

        self.simulated_monthly_data = {}
        self._use_simulation = False

        self.charge_segments_dict = {}
        self.discharge_segments_dict = {}
        self._month_capacity_needs = {}
        self.charge_labels = []
        self.discharge_labels = []
        self._recalc_spaces()

        # v3.2fix: 恢复上次关闭时保存的参数
        if self.config_manager:
            saved_power = self.config_manager.app_set('pw_rated_power', None)
            if saved_power is not None:
                self.rated_power = saved_power
            self.capacity_mode = self.config_manager.app_set('pw_capacity_mode', 'two_two')
            self.ignore_negative = self.config_manager.app_set('pw_ignore_negative', self.ignore_negative)

        self._init_ui()
        # v3.2fix: 容量线自动同步定时器(1.5s轮询)
        self._cap_sync_timer = QTimer(self)
        self._cap_sync_timer.setInterval(1500)
        self._cap_sync_timer.timeout.connect(self._update_cap_label)
        self._cap_sync_timer.start()

    # ---------- 内部辅助 ----------
    def _build_idx_map(self):
        self._summary_idx_map = {}
        for idx, s in enumerate(self.settings_model.summaries):
            key = (s.asset_value, s.month_str)
            self._summary_idx_map[key] = idx

    def _get_work_data(self):
        if self._use_simulation and self.simulated_monthly_data:
            return self.simulated_monthly_data
        return self.monthly_data

    def _load_all_data(self):
        self.available_days = 0
        self.monthly_data.clear()
        for s in self.summaries:
            data = self.provider.get_group_data(s)
            if data:
                self.monthly_data[s.month_str] = data
                load_arr = np.array(data['load_array'])
                if load_arr.ndim == 2:
                    self.available_days += load_arr.shape[0]
            else:
                log.warning(f"专业模式：无法加载数据 {s.file_path}/{s.sheet_name}/{s.month_str}")

    def _extract_years(self):
        years = set()
        for key in self.monthly_data.keys():
            m = re.match(r'^(\d{4})-\d{2}$', key)
            if m:
                years.add(int(m.group(1)))
        self.available_years = sorted(list(years))
        if not self.available_years:
            self.available_years = [datetime.now().year]

    def _unify_time_resolution(self):
        if not self.monthly_data:
            return
        max_points = max(v['load_array'].shape[1] for v in self.monthly_data.values())
        ref_labels = None
        for v in self.monthly_data.values():
            if v['load_array'].shape[1] == max_points:
                ref_labels = v['time_labels']
                break
        if ref_labels is None or max_points < 2:
            return

        new_hours = np.array([_parse_min(t) / 60.0 for t in ref_labels])
        for month, data in self.monthly_data.items():
            arr = data['load_array']
            if arr.shape[1] == max_points:
                continue
            old_hours = np.array([_parse_min(t) / 60.0 for t in data['time_labels']])
            new_arr = np.zeros((arr.shape[0], max_points))
            for i in range(arr.shape[0]):
                new_arr[i, :] = np.interp(new_hours, old_hours, arr[i, :], left=np.nan, right=np.nan)
            data['load_array'] = new_arr
            data['time_labels'] = ref_labels
            log.info(f"月份 {month} 时间分辨率已统一为 {max_points} 点")

    def _recalc_spaces(self):
        self.charge_segments_dict.clear()
        self.discharge_segments_dict.clear()
        self._month_capacity_needs.clear()
        self.charge_labels = []
        self.discharge_labels = []

        strategy_periods = {}
        if self.province_name and self.province_name != "无":
            strategy_periods = self.config_manager.get_periods_for_all_months(self.province_name)

        billing = None
        # v3.0fix: 详细诊断 billing 链路
        log.info(f"[_recalc_spaces] mode={self.mode}, summaries={len(self.summaries)}, summary_idx_map={len(self._summary_idx_map)}")
        for s in self.summaries:
            key = (s.asset_value, s.month_str)
            idx = self._summary_idx_map.get(key)
            if idx is not None:
                bill = self.settings_model.effective_billing(idx)
                log.debug(f"  billing lookup: asset={s.asset_value}, month={s.month_str}, idx={idx}, bill={bill}")
                if bill and bill.get('value'):
                    billing = bill
                    log.info(f"[_recalc_spaces] billing found: value={bill.get('value')}, type={bill.get('type')}, asset={s.asset_value}")
                    break
        if billing is None:
            log.warning(f"[_recalc_spaces] billing NOT FOUND. self.settings_model._global_billing={getattr(self.settings_model, '_global_billing', 'N/A')}")
        if self.mode == 'C' and not self.need_capacity:
            billing = None
        # v3.0fix: Mode A 必须要有容量线, 否则充电空间全为0
        if self.mode == 'A' and (not billing or not billing.get('value')):
            log.warning("Mode A 未检测到容量线, 充电空间将全为0. 请在预览窗口设置容量线.")
        self._current_cap = billing.get('value', 0) if billing else 0
        if hasattr(self, '_cap_label'):
            self._cap_label.setText(f"容量线:{self._current_cap:.2f}")

        work_data = self._get_work_data()
        for month_str, data in work_data.items():
            key = (self.summaries[0].asset_value if self.summaries else None, month_str)
            idx = self._summary_idx_map.get(key)
            mult = self.settings_model.effective_multiplier(idx) if idx is not None else self.global_multiplier
            load_array = np.array(data['load_array']) * mult
            # v3.0fix: 每月独立取billing(按需缴费下每月容量线不同)
            month_billing = billing  # 默认用全局
            if idx is not None:
                mb = self.settings_model.effective_billing(idx)
                if mb and mb.get('value'):
                    month_billing = mb
            month_num = _parse_month_num(month_str)
            if month_num is None:
                # v2.3.8fix: 原代码 int(month_str) 无保护，月份为「未知月份」时
                # ValueError 直接抽穿 __init__，专业分析窗口根本打不开。
                # 跳过该月并在日志里点名，比崩掉或猜一个月份都正确。
                log.error(f"[_recalc_spaces] 月份无法识别({month_str!r})，跳过该月测算；请检查文件名或数据内日期列")
                continue

            # --- 修复：光伏消纳模式下充放电段逻辑 ---
            # B模式：全天24h，充电=光伏返送存电，放电=正向负荷放电
            # C模式：充电=全天光伏返送，放电=策略峰段（套利）
            # A模式：沿用原有策略时段逻辑
            if self.mode == 'B':
                # 纯光伏消纳：充放电均用全天段，不依赖峰谷策略
                segments = [
                    {'type': PeriodType.CHARGE, 'start': 0.0, 'end': 24.0,
                     'label': '光伏返送存电', 'sub_periods': []},
                    {'type': PeriodType.DISCHARGE, 'start': 0.0, 'end': 24.0,
                     'label': '负荷放电', 'sub_periods': []},
                ]
            elif self.mode == 'C':
                # 混合模式：充电来自全天光伏返送，放电来自策略峰段
                periods = strategy_periods.get(str(month_num), []) if month_num is not None else []
                strategy_segs = _segment_periods(periods) if periods else []
                # 提取策略中的放电段（峰/尖峰），充电段用全天替换
                discharge_segs = [s for s in strategy_segs
                                  if s['type'] in (PeriodType.DISCHARGE, PeriodType.DISCHARGE_LABEL)]
                # 重新编号放电段标签
                for i, ds in enumerate(discharge_segs):
                    ds['label'] = f'放电{i+1}'
                # 无策略放电段时，回退为全天放电（确保容量规划有数据）
                if not discharge_segs:
                    discharge_segs = [{'type': PeriodType.DISCHARGE, 'start': 0.0, 'end': 24.0,
                                       'label': '放电1', 'sub_periods': []}]
                charge_seg = {'type': PeriodType.CHARGE, 'start': 0.0, 'end': 24.0,
                              'label': '光伏返送存电', 'sub_periods': []}
                segments = [charge_seg] + discharge_segs
            else:
                # A模式：原有策略时段逻辑
                periods = strategy_periods.get(str(month_num), []) if month_num is not None else []
                segments = _segment_periods(periods)

            c_segs, d_segs, c_labs, d_labs = compute_segmented_spaces(
                load_array, data['time_labels'], segments,
                billing=month_billing, mode=self.mode, rated_power=self.rated_power,
                ignore_negative=self.ignore_negative
            )
            self.charge_segments_dict[month_str] = c_segs
            self.discharge_segments_dict[month_str] = d_segs
            # v2.30fix: 取所有月份标签的并集(最大集), 非第一个月的快照
            # 同一省份不同月份可能用不同策略(1C1D vs 2C2D), 标签必须覆盖全部
            for lab in c_labs:
                if lab not in self.charge_labels:
                    self.charge_labels.append(lab)
            for lab in d_labs:
                if lab not in self.discharge_labels:
                    self.discharge_labels.append(lab)

    # ---------- UI 构建 ----------
    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        asset = self.summaries[0].asset_value if self.summaries else "未知"
        self.asset_info_label = QLabel(f"<b>资产：{asset}</b>  模式：{self.mode}")
        main_layout.addWidget(self.asset_info_label)

        splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(splitter, 1)

        # ---------- 左侧参数面板 ----------
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(4, 4, 4, 4)

        if self.mode == 'A':
            left_layout.addWidget(QLabel("<b>参数设置</b>"))
            bill = self.settings_model.effective_billing(
                self._summary_idx_map.get((self.summaries[0].asset_value, self.summaries[0].month_str), 0)
            )
            cap_val = bill['value'] if bill else 0
            self._cap_label = QLabel(f"容量线：{cap_val:.2f}")
            left_layout.addWidget(self._cap_label)
            self.ignore_neg_cb = QCheckBox("忽略负荷负值（光伏返送）")
            self.ignore_neg_cb.setChecked(self.ignore_negative)
            self.ignore_neg_cb.stateChanged.connect(self._on_param_changed)
            left_layout.addWidget(self.ignore_neg_cb)

        elif self.mode == 'B':
            left_layout.addWidget(QLabel("<b>储能参数</b>"))
            left_layout.addWidget(QLabel("额定充放电功率 (kW):"))
            self.rated_power_spin = QDoubleSpinBox()
            self.rated_power_spin.setRange(0.1, 1000000.0)
            self.rated_power_spin.setValue(100.0)
            self.rated_power_spin.valueChanged.connect(self._on_param_changed)
            left_layout.addWidget(self.rated_power_spin)
            self.rated_power = self.rated_power_spin.value()
            self.ignore_neg_cb = QCheckBox("忽略负荷负值")
            self.ignore_neg_cb.setChecked(False)
            self.ignore_neg_cb.setEnabled(False)
            left_layout.addWidget(self.ignore_neg_cb)

        elif self.mode == 'C':
            left_layout.addWidget(QLabel("<b>储能参数</b>"))
            left_layout.addWidget(QLabel("额定充放电功率 (kW):"))
            self.rated_power_spin = QDoubleSpinBox()
            self.rated_power_spin.setRange(0.1, 1000000.0)
            self.rated_power_spin.setValue(100.0)
            self.rated_power_spin.valueChanged.connect(self._on_param_changed)
            left_layout.addWidget(self.rated_power_spin)
            self.rated_power = self.rated_power_spin.value()
            self.need_cap_cb = QCheckBox("启用需量限制（需设置容量线）")
            self.need_cap_cb.stateChanged.connect(self._on_param_changed)
            left_layout.addWidget(self.need_cap_cb)
            self.ignore_neg_cb = QCheckBox("忽略负荷负值")
            self.ignore_neg_cb.setChecked(False)
            self.ignore_neg_cb.stateChanged.connect(self._on_param_changed)
            left_layout.addWidget(self.ignore_neg_cb)

        self.simulate_btn = QPushButton("负荷模拟")
        self.simulate_btn.clicked.connect(self._open_simulation_dialog)
        left_layout.addWidget(self.simulate_btn)

        left_layout.addStretch()
        splitter.addWidget(left_widget)

        # ---------- 右侧结果区 ----------
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)

        self.tab_widget = QTabWidget()
        right_layout.addWidget(self.tab_widget)

        monthly_tab = QWidget()
        monthly_layout = QVBoxLayout(monthly_tab)
        monthly_layout.addWidget(QLabel("<b>月度充放电空间 (kWh)</b>"))
        self.space_table = QTableWidget()
        monthly_layout.addWidget(self.space_table)
        self.tab_widget.addTab(monthly_tab, "月度汇总")

        daily_tab = QWidget()
        daily_layout = QVBoxLayout(daily_tab)
        daily_header = QHBoxLayout()
        daily_header.addWidget(QLabel("选择月份:"))
        self.daily_month_combo = QComboBox()
        self.daily_month_combo.currentTextChanged.connect(self._fill_daily_table)
        daily_header.addWidget(self.daily_month_combo)
        daily_header.addStretch()
        daily_layout.addLayout(daily_header)
        self.daily_table = QTableWidget()
        daily_layout.addWidget(self.daily_table)
        self.tab_widget.addTab(daily_tab, "每日详情")

        # ---------- 储能容量规划器 ----------
        planner_group = QGroupBox("储能容量规划")
        planner_layout = QVBoxLayout(planner_group)

        row0 = QHBoxLayout()
        row0.addWidget(QLabel("规划模式:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["两充两放（独立评估）", "一充一放"])
        self.mode_combo.currentIndexChanged.connect(self._on_capacity_mode_changed)
        row0.addWidget(self.mode_combo)
        row0.addWidget(QLabel("  评估策略:"))
        self.est_combo = QComboBox()
        self.est_combo.addItems(["最小可行容量", "满载利用率"])
        self.est_combo.setToolTip(
            "最小可行容量: min(所有充放段) → 全天候可行, 最保守\n"
            "满载利用率: max(min(c₁,d₁), min(c₂,d₂)) → 最苛刻循环跑满"
        )
        self.est_combo.currentIndexChanged.connect(self._on_est_combo_changed)
        row0.addWidget(self.est_combo)
        row0.addStretch()
        planner_layout.addLayout(row0)

        self.charge_option_combo = QComboBox()
        self.charge_option_combo.setVisible(False)
        self.discharge_option_combo = QComboBox()
        self.discharge_option_combo.setVisible(False)
        # 充电/放电选项的动态更新在 _on_capacity_mode_changed 中完成
        row0b = QHBoxLayout()
        row0b.addWidget(QLabel("充电段:"))
        row0b.addWidget(self.charge_option_combo)
        row0b.addWidget(QLabel("放电段:"))
        row0b.addWidget(self.discharge_option_combo)
        row0b.addStretch()
        planner_layout.addLayout(row0b)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("目标天数 (全年基准):"))
        self.target_days_spin = QSpinBox()
        self.target_days_spin.setRange(1, 365)
        self.target_days_spin.setValue(330)
        row1.addWidget(self.target_days_spin)

        self.available_days_label = QLabel(f"实际数据天数: {self.available_days} 天")
        row1.addWidget(self.available_days_label)

        self.effective_days_label = QLabel("等效分析天数: 0 天")
        row1.addWidget(self.effective_days_label)

        self.calc_cap_btn = QPushButton("计算配置容量")
        self.calc_cap_btn.clicked.connect(self._calc_capacity_from_days)
        row1.addWidget(self.calc_cap_btn)
        planner_layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("配置容量 (kWh):"))
        self.config_cap_spin = QDoubleSpinBox()
        self.config_cap_spin.setRange(0.0, 1e9)
        self.config_cap_spin.setDecimals(3)
        self.config_cap_spin.setValue(100.0)
        row2.addWidget(self.config_cap_spin)
        # v3.0: DOD可调(默认95%)
        row2.addWidget(QLabel("  DOD:"))
        self.dod_spin = QSpinBox()
        self.dod_spin.setRange(50, 100)
        self.dod_spin.setValue(int(BESS_DOD_DEFAULT * 100))
        self.dod_spin.setSuffix('%')
        self.dod_spin.setToolTip("放电深度, 90%=SOC区间5%~95%. 推荐容量=日需求/DOD")
        self.dod_spin.valueChanged.connect(lambda: self._update_system_scale())
        row2.addWidget(self.dod_spin)

        self.calc_days_btn = QPushButton("计算满足天数")
        self.calc_days_btn.clicked.connect(self._calc_days_from_capacity)
        row2.addWidget(self.calc_days_btn)
        self.satisfy_days_label = QLabel("满足天数: 0")
        row2.addWidget(self.satisfy_days_label)
        planner_layout.addLayout(row2)

        # v2.3.9fix: 删掉「实际满足天数」。它与上面的「满足天数」同一个变量赋两次值，
        # 永远相等，没有任何口径差异——不是两种统计，只是两行标签。
        self.effective_satisfy_label = QLabel("等效满足天数: 0")
        planner_layout.addWidget(self.effective_satisfy_label)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("单柜功率 (kW):"))
        self.unit_power_spin = QDoubleSpinBox()
        self.unit_power_spin.setRange(0.1, 10000.0)
        self.unit_power_spin.setDecimals(3)
        self.unit_power_spin.setValue(125.0)
        row3.addWidget(self.unit_power_spin)

        row3.addWidget(QLabel("单柜容量 (kWh):"))
        self.unit_cap_spin = QDoubleSpinBox()
        self.unit_cap_spin.setRange(0.1, 10000.0)
        self.unit_cap_spin.setDecimals(3)
        self.unit_cap_spin.setValue(261.248)
        row3.addWidget(self.unit_cap_spin)
        planner_layout.addLayout(row3)

        self.system_result_label = QLabel("系统规模：等待输入")
        planner_layout.addWidget(self.system_result_label)

        self.unit_power_spin.valueChanged.connect(self._update_system_scale)
        self.unit_cap_spin.valueChanged.connect(self._update_system_scale)
        self.config_cap_spin.valueChanged.connect(self._update_system_scale)

        right_layout.addWidget(planner_group)

        btn_layout = QHBoxLayout()
        self.pv_btn = QPushButton("光伏返送详情")
        self.pv_btn.clicked.connect(self._show_pv_feedback)
        btn_layout.addWidget(self.pv_btn)
        btn_layout.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.close)
        btn_layout.addWidget(close_btn)
        right_layout.addLayout(btn_layout)

        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        # 初始化模式选项下拉内容
        self._update_mode_options()
        self._on_capacity_mode_changed(0)

        # 初始刷新
        self._populate_space_table()
        self._populate_daily_month_combo()
        if self.daily_month_combo.count() > 0:
            self._fill_daily_table(self.daily_month_combo.currentText())
        self._update_system_scale()
        self.available_days_label.setText(f"实际数据天数: {self.available_days} 天")
        self._update_effective_days_label()

    def _update_mode_options(self):
        """根据实际充放电段标签更新下拉选项"""
        self.charge_option_combo.clear()
        self.discharge_option_combo.clear()
        # 充电选项
        if len(self.charge_labels) >= 1:
            self.charge_option_combo.addItem(self.charge_labels[0])
        if len(self.charge_labels) >= 2:
            self.charge_option_combo.addItem(self.charge_labels[1])
        if len(self.charge_labels) >= 2:
            self.charge_option_combo.addItem("合并充电段")
        # 放电选项
        if len(self.discharge_labels) >= 1:
            self.discharge_option_combo.addItem(self.discharge_labels[0])
        if len(self.discharge_labels) >= 2:
            self.discharge_option_combo.addItem(self.discharge_labels[1])
        if len(self.discharge_labels) >= 2:
            self.discharge_option_combo.addItem("合并放电段")

    def _on_capacity_mode_changed(self, index):
        if index == 0:  # 两充两放
            self.capacity_mode = "two_two"
            self.charge_option_combo.setVisible(False)
            self.discharge_option_combo.setVisible(False)
        else:  # 一充一放
            self.capacity_mode = "one_one"
            self.charge_option_combo.setVisible(True)
            self.discharge_option_combo.setVisible(True)
        self._calc_capacity_from_days()

    def _on_est_combo_changed(self, index):
        """v3.0: 评估策略切换——最小可行时隐藏充放段选择, 满载利用率尊重模式."""
        is_min = ("最小可行" in self.est_combo.currentText())
        is_two_two = (self.capacity_mode == "two_two")
        if hasattr(self, 'charge_option_combo'):
            self.charge_option_combo.setVisible(not is_min and not is_two_two)
        if hasattr(self, 'discharge_option_combo'):
            self.discharge_option_combo.setVisible(not is_min and not is_two_two)
        self._calc_capacity_from_days()

    def _get_relevant_segments_for_month(self, month):
        """
        返回 (charge_arrays, discharge_arrays) 每个元素为列表，包含参与容量评估的段数组。
        charge_arrays 中每个数组形状为 (n_days,)
        """
        c_segs = self.charge_segments_dict.get(month, [])
        d_segs = self.discharge_segments_dict.get(month, [])
        selected_c = []
        selected_d = []

        if self.capacity_mode == "two_two":
            # 两充两放：所有段都参与
            selected_c = list(c_segs)
            selected_d = list(d_segs)
        else:  # one_one
            # 根据用户选择提取充电段
            charge_text = self.charge_option_combo.currentText()
            if charge_text == "合并充电段" and len(c_segs) >= 2:
                merged = c_segs[0] + c_segs[1]  # 逐日相加
                selected_c = [merged]
            elif charge_text in self.charge_labels:
                idx = self.charge_labels.index(charge_text)
                if idx < len(c_segs):
                    selected_c = [c_segs[idx]]
            else:
                # 回退：使用第一个
                if c_segs:
                    selected_c = [c_segs[0]]

            # 放电段同理
            discharge_text = self.discharge_option_combo.currentText()
            if discharge_text == "合并放电段" and len(d_segs) >= 2:
                merged = d_segs[0] + d_segs[1]
                selected_d = [merged]
            elif discharge_text in self.discharge_labels:
                idx = self.discharge_labels.index(discharge_text)
                if idx < len(d_segs):
                    selected_d = [d_segs[idx]]
            else:
                if d_segs:
                    selected_d = [d_segs[0]]

        # v2.3.8fix: 剔除全零段（占位零）。
        # compute_segmented_spaces 返回的四个平行列表里，每个段在**非本方向**那一侧
        # 是占位零（充电段在放电侧为 0，放电段在充电侧为 0），目的是让四个列表等长。
        # 但下游取 min / 取 max(min(配对)) 时，占位零会把结果直接压成 0：
        # two_two（默认模式）全段参与，于是日需求恒为 0、满足天数恒为 0。
        # 这里是段集合进入计算前的唯一收敛点（_get_all_filtered_daily_mins /
        # _check_day_satisfied / _calc_days_from_capacity 均经此），在此滤掉即全链生效。
        # 两张表（月表/日表）不经过本方法，它们用「充+放相加」的方式已处理好占位零，
        # 所以不受本改动影响。
        def _drop_zero_segments(arrs):
            return [a for a in arrs
                    if float(np.nansum(np.nan_to_num(a, nan=0.0))) != 0.0]

        return _drop_zero_segments(selected_c), _drop_zero_segments(selected_d)

    def _get_all_filtered_daily_mins(self):
        """遍历所有月份，返回每天的最小段值列表（用于容量计算）
        v3.0: 双策略 - 满载利用率 | 最小可行容量
        v3.2fix: 两种策略都走_get_relevant_segments_for_month，尊重1C1D选段.
        """
        use_min = (hasattr(self, 'est_combo') and self.est_combo.currentText() != "满载利用率")
        daily_mins = []
        work_data = self._get_work_data()
        for month in work_data.keys():
            c_arrs, d_arrs = self._get_relevant_segments_for_month(month)
            if not c_arrs and not d_arrs:
                continue
            n_days = len(c_arrs[0]) if c_arrs else (len(d_arrs[0]) if d_arrs else 0)
            if n_days == 0:
                continue
            # v2.3.8fix: 满载分支把「逐段配对 min」改成「当日总充与总放取瓶颈」。
            # 剔除占位零后 c_arrs[i] 与 d_arrs[i] 仍是不同时段的两段，配对无意义。
            _tp = None if use_min else daily_throughput_series(c_arrs, d_arrs)
            for d in range(n_days):
                if use_min:
                    all_vals = [arr[d] for arr in c_arrs if d < len(arr)] + \
                               [arr[d] for arr in d_arrs if d < len(arr)]
                    daily_cap = min(all_vals) if all_vals else 0.0
                else:
                    daily_cap = float(_tp[d]) if _tp is not None and d < len(_tp) else 0.0
                if daily_cap > 0 or (c_arrs or d_arrs):
                    daily_mins.append(daily_cap)
        return daily_mins

    def _check_day_satisfied(self, month, day_index, cap):
        """v3.2fix: 按充放配对判断——应用DOD折算可用容量后比较

        v2.3.8fix: 原来要求「同一索引 i 的充段与放段同时 >= 可用容量」。
        剔除占位零后两个列表已不再逐位对应，这条判据几乎恒假：
        容量 > 0 时「满足天数」全是 0，容量 = 0 时又全真（0 >= 0）。
        两端都错，且都看起来合理。改为当日吞吐量（总充与总放取瓶颈）>= 可用容量。
        """
        dod_val = self.dod_spin.value() if hasattr(self, 'dod_spin') else 95
        usable_cap = cap * dod_val / 100.0
        c_arrs, d_arrs = self._get_relevant_segments_for_month(month)
        _tp = daily_throughput_series(c_arrs, d_arrs)
        if day_index >= len(_tp):
            return False
        return float(_tp[day_index]) >= usable_cap

    def _populate_space_table(self):
        months = sorted(self._get_work_data().keys())
        # 一个段一列：充电1、放电1、充电2、放电2 …（与设计意图一致——
        # 每个段就是一个充放机会，充段显示可充量、放段显示可放量，不拆方向）。
        # charge_labels 与 discharge_labels 内容相同（都装着全部段名，为了让
        # 四个列表等长），所以只取一份，否则每个段名会重复两遍。
        # 同名段（如多个同类型段）加序号区分，否则表头仍会重复。
        _cnt = {}
        for lab in self.charge_labels:
            _cnt[lab] = _cnt.get(lab, 0) + 1
        _seen = {}
        _uniq_labels = []
        for lab in self.charge_labels:
            if _cnt.get(lab, 0) > 1:
                _seen[lab] = _seen.get(lab, 0) + 1
                _uniq_labels.append(f"{lab}{_seen[lab]}")
            else:
                _uniq_labels.append(lab)
        # v2.3.5fix(C4): 全零段（非本方向的占位零）不再占列。
        # 先扫一遍全部月份，只有「所有月份、所有段实例」都为 0 的段才隐藏；
        # 任一月出现非零值就照常显示，避免把真实数据藏掉。
        # 计算层（c_segs/d_segs）完全不动，只是不显示。
        n_segs = len(self.charge_labels)
        _seg_nonzero = [False] * n_segs
        for month in months:
            _c = self.charge_segments_dict.get(month, [])
            _d = self.discharge_segments_dict.get(month, [])
            for i in range(n_segs):
                if _seg_nonzero[i]:
                    continue
                _cv = float(np.sum(np.nan_to_num(_c[i], nan=0.0))) if i < len(_c) else 0.0
                _dv = float(np.sum(np.nan_to_num(_d[i], nan=0.0))) if i < len(_d) else 0.0
                if (_cv + _dv) != 0.0:
                    _seg_nonzero[i] = True
        _keep_segs = [i for i in range(n_segs) if _seg_nonzero[i]]
        _hidden = n_segs - len(_keep_segs)
        if _hidden:
            log.debug(f"隐藏 {_hidden} 个全零段列（所有月份均为 0，即非本方向的占位零）")

        col_labels = ['月份'] + [_uniq_labels[i] for i in _keep_segs] + ['月最小段总和(kWh)']
        self.space_table.setColumnCount(len(col_labels))
        self.space_table.setHorizontalHeaderLabels(col_labels)
        self.space_table.setRowCount(len(months))

        for row, month in enumerate(months):
            c_segs = self.charge_segments_dict.get(month, [])
            d_segs = self.discharge_segments_dict.get(month, [])

            self.space_table.setItem(row, 0, QTableWidgetItem(month))

            seg_values = []
            col = 1
            # 同一个索引 i 在 c_segs 与 d_segs 里指的是同一个段：
            # 充电段在 c_segs 有值、d_segs 为零数组，放电段反之；
            # 非充非放的类型（当前枚举下不存在）两边皆零。
            # 所以两者相加就是该段的空间，不会重复计入。
            for i in _keep_segs:
                c_val = (float(np.sum(np.nan_to_num(c_segs[i], nan=0.0)))
                         if i < len(c_segs) else 0.0)
                d_val = (float(np.sum(np.nan_to_num(d_segs[i], nan=0.0)))
                         if i < len(d_segs) else 0.0)
                val = c_val + d_val
                seg_values.append(val)
                self.space_table.setItem(row, col, QTableWidgetItem(f"{val:.2f}"))
                col += 1
            min_val = min(seg_values) if seg_values else 0.0
            last_col = len(col_labels) - 1
            self.space_table.setItem(row, last_col, QTableWidgetItem(f"{min_val:.2f}"))

        self.space_table.resizeColumnsToContents()
        # v3.2fix: 刷新容量线显示
        self._update_cap_label()

    def _update_cap_label(self):
        """v3.2fix: 优先取当前预览月的billing, 回退到第一个summary."""
        if not hasattr(self, '_cap_label'):
            return
        cap_val = 0
        try:
            # 优先: 当前daily_month_combo选中的月份
            current_month = None
            if hasattr(self, 'daily_month_combo') and self.daily_month_combo.count() > 0:
                current_month = self.daily_month_combo.currentText()
            # 回退: 第一个summary的月份
            if not current_month and self.summaries:
                current_month = self.summaries[0].month_str
            if current_month:
                for s in self.summaries:
                    if s.month_str == current_month:
                        idx = self._summary_idx_map.get((s.asset_value, s.month_str))
                        if idx is not None:
                            bill = self.settings_model.effective_billing(idx)
                            cap_val = bill.get('value', 0) if bill and bill.get('value') else 0
                            break
            # 最终回退
            if cap_val == 0 and self.summaries:
                s = self.summaries[0]
                idx = self._summary_idx_map.get((s.asset_value, s.month_str))
                if idx is not None:
                    bill = self.settings_model.effective_billing(idx)
                    cap_val = bill.get('value', 0) if bill and bill.get('value') else 0
        except Exception:
            log.debug(f"[_update_cap_label] billing lookup failed", exc_info=True)
            pass
        # v2.3.9fix(P2-12): 只做显示，不再自动勾选「需量限制」。
        # 原来只要容量线>0 就把用户上次取消的勾选重新勾上，并触发一次重算。
        # v2.3.9fix(P2-7): 0 与「未设置」分开显示。原来两者都写 "容量线:0.00"，
        # mode A 空间全 0 时用户分不清是没填还是填了 0。
        if cap_val and cap_val > 0:
            self._cap_label.setText(f"容量线:{cap_val:.2f}")
        else:
            self._cap_label.setText("容量线:未设置")

    def _populate_daily_month_combo(self):
        self.daily_month_combo.blockSignals(True)
        self.daily_month_combo.clear()
        self.daily_month_combo.addItems(sorted(self._get_work_data().keys()))
        self.daily_month_combo.blockSignals(False)

    def _fill_daily_table(self, month):
        work_data = self._get_work_data()
        if month not in work_data:
            self.daily_table.setRowCount(0)
            return
        data = work_data[month]
        # v3.0: 切换到当月容量线显示
        try:
            for s in self.summaries:
                if s.month_str == month:
                    idx = self._summary_idx_map.get((s.asset_value, s.month_str))
                    if idx is not None:
                        bill = self.settings_model.effective_billing(idx)
                        self._current_cap = bill.get('value', 0) if bill and bill.get('value') else 0
                        if hasattr(self, '_cap_label'):
                            self._cap_label.setText(f"容量线:{self._current_cap:.2f}")
                        break
        except Exception:
            log.debug(f"[_fill_daily_table] billing lookup failed", exc_info=True)
            pass
        dates = data.get('date_labels', [])
        c_segs = self.charge_segments_dict.get(month, [])
        d_segs = self.discharge_segments_dict.get(month, [])

        cap_val = getattr(self, '_current_cap', 0)
        # v2.3.6fix: 日明细表沿用月表的「一段一列」口径。
        # 原写法 ['日期'] + charge_labels + discharge_labels 会把每个段名印两遍，
        # 且 vals 里混入充段的放电值（恒为 0），导致 min(vals) 永远是 0。
        # 同名的全零段（占位零）与月表一样不显示，两张表列结构保持一致。
        _n = len(self.charge_labels)
        _nz = [False] * _n
        for _m in sorted(self._get_work_data().keys()):
            _c = self.charge_segments_dict.get(_m, [])
            _d = self.discharge_segments_dict.get(_m, [])
            for _i in range(_n):
                if _nz[_i]:
                    continue
                _cv = float(np.sum(np.nan_to_num(_c[_i], nan=0.0))) if _i < len(_c) else 0.0
                _dv = float(np.sum(np.nan_to_num(_d[_i], nan=0.0))) if _i < len(_d) else 0.0
                if (_cv + _dv) != 0.0:
                    _nz[_i] = True
        _keep = [i for i in range(_n) if _nz[i]]

        _cnt = {}
        for lab in self.charge_labels:
            _cnt[lab] = _cnt.get(lab, 0) + 1
        _seen = {}
        _uniq = []
        for lab in self.charge_labels:
            if _cnt.get(lab, 0) > 1:
                _seen[lab] = _seen.get(lab, 0) + 1
                _uniq.append(f"{lab}{_seen[lab]}")
            else:
                _uniq.append(lab)

        col_labels = ['日期'] + [_uniq[i] for i in _keep] + ['日最小值(kWh)']
        self.daily_table.setColumnCount(len(col_labels))
        self.daily_table.setHorizontalHeaderLabels(col_labels)
        n_days = len(dates)
        self.daily_table.setRowCount(n_days)
        # v2.3.8fix: 满载口径改为「当日总充与总放取瓶颈」，
        # 唯一实现见 services/capacity.daily_throughput_series。
        # 原逐段配对 min 因占位零/错位，整列恒为 0.000。
        _thr = daily_throughput_series(c_segs, d_segs)

        for d in range(n_days):
            self.daily_table.setItem(d, 0, QTableWidgetItem(str(dates[d])))
            vals = []
            col = 1
            # 同一索引 i 在 c_segs / d_segs 里是同一个段，一克一放互斥，相加即该段空间。
            for i in _keep:
                c_val = c_segs[i][d] if i < len(c_segs) and d < len(c_segs[i]) else 0.0
                d_val = d_segs[i][d] if i < len(d_segs) and d < len(d_segs[i]) else 0.0
                val = c_val + d_val
                vals.append(val)
                self.daily_table.setItem(d, col, QTableWidgetItem(f"{val:.3f}"))
                col += 1
            min_val = min(vals) if vals else 0.0
            # v3.0: 日需求跟策略走
            est_mode = self.est_combo.currentText() if hasattr(self, 'est_combo') else '最小可行容量'
            if '满载' in est_mode:
                daily_need = float(_thr[d]) if d < len(_thr) else 0.0
            else:
                daily_need = min_val
            # v2.3.9fix(P2-16): 当天所有段都是 0 时显示 —，而不是 0.000。
            # 否则「没数据」与「确实没有可充放空间」长得一模一样。
            _all_zero = (not vals) or all(abs(v) < 1e-12 for v in vals)
            _txt = "—" if _all_zero else f"{daily_need:.3f}"
            self.daily_table.setItem(d, col, QTableWidgetItem(_txt))
        self.daily_table.resizeColumnsToContents()

    def _update_effective_days_label(self):
        input_days = self.target_days_spin.value()
        if self.available_days > 0:
            effective = int(input_days * self.available_days / 365.0)
        else:
            effective = 0
        self.effective_days_label.setText(f"等效分析天数: {effective} 天")

    def _calc_capacity_from_days(self):
        self._update_effective_days_label()
        input_days = self.target_days_spin.value()
        target_days = int(input_days * self.available_days / 365.0) if self.available_days > 0 else 0
        if target_days < 1:
            self.config_cap_spin.setValue(0.0)
            self._update_system_scale()
            return

        daily_mins = self._get_all_filtered_daily_mins()
        if not daily_mins:
            QMessageBox.warning(self, "警告", "无可用数据")
            return
        daily_mins = np.array(daily_mins)
        cap = calc_capacity_for_days(target_days, daily_mins)  # 统一排序取百分位(可用容量)
        # v3.2fix: 可用容量÷DOD=安装容量(电池额定容量必须大于可用容量)
        dod_val = self.dod_spin.value() if hasattr(self, 'dod_spin') else 95
        installed_cap = cap / (dod_val / 100.0) if dod_val > 0 else cap
        self.config_cap_spin.setValue(round(installed_cap, 3))
        self._update_system_scale()

    def _calc_days_from_capacity(self):
        cap = self.config_cap_spin.value()
        days = 0
        work_data = self._get_work_data()
        use_min_all = (hasattr(self, 'est_combo') and "最小可行" in self.est_combo.currentText())
        for month in work_data.keys():
            if use_min_all:
                c_arrs = list(self.charge_segments_dict.get(month, []))
                d_arrs = list(self.discharge_segments_dict.get(month, []))
            else:
                c_arrs, d_arrs = self._get_relevant_segments_for_month(month)
            if not c_arrs and not d_arrs:
                continue
            n_days = 0
            if c_arrs:
                n_days = len(c_arrs[0])
            elif d_arrs:
                n_days = len(d_arrs[0])
            else:
                continue
            for d in range(n_days):
                if self._check_day_satisfied(month, d, cap):
                    days += 1
        self.satisfy_days_label.setText(f"满足天数: {days}")
        effective = int(days * 365.0 / self.available_days) if self.available_days > 0 else 0
        self.effective_satisfy_label.setText(f"等效满足天数: {effective}")

    def _update_system_scale(self):
        cap = self.config_cap_spin.value()
        unit_cap = self.unit_cap_spin.value()
        unit_pow = self.unit_power_spin.value()
        dod_val = self.dod_spin.value() if hasattr(self, 'dod_spin') else 95
        cap_before_dod = cap * dod_val / 100.0 if dod_val > 0 else cap
        dod_info = f" | DOD={dod_val}%(实际可用{cap_before_dod:.1f}kWh)"
        if unit_cap > 0:
            num_units = max(1, int(math.ceil(cap / unit_cap)))
            total_pow = num_units * unit_pow
            total_cap = num_units * unit_cap
            self.system_result_label.setText(
                f"单柜数量: {num_units} 台，系统总功率: {total_pow:.1f} kW，系统总容量: {total_cap:.1f} kWh{dod_info}"
            )
        else:
            self.system_result_label.setText(f"单柜容量必须大于0{dod_info}")

    def _show_pv_feedback(self):
        work_data = self._get_work_data()
        if not work_data:
            return
        month = self.daily_month_combo.currentText()
        key = (self.summaries[0].asset_value if self.summaries else None, month)
        idx = self._summary_idx_map.get(key)
        mult = self.settings_model.effective_multiplier(idx) if idx is not None else self.global_multiplier
        dlg = PVFeedbackDialog(work_data, mult, self)
        dlg.exec()

    def _open_simulation_dialog(self):
        available_months = list(self.monthly_data.keys())
        if not available_months:
            QMessageBox.warning(self, "提示", "无实际数据可供模拟")
            return

        season_map = {}
        if self.province_name and self.province_name != "无":
            season_map = self.config_manager.get_season_map(self.province_name)

        if not self.available_years:
            QMessageBox.warning(self, "警告", "无法从现有数据中提取年份，无法模拟。")
            return

        dlg = SimulationConfigDialog(
            self, self.available_years, self.monthly_data,
            season_map, self.config_manager, self
        )
        if dlg.exec() != QDialog.Accepted or not dlg.result_configs:
            return

        self._execute_simulation(dlg.result_configs)

    def _execute_simulation(self, configs):
        self.simulated_monthly_data = {}
        for key, data in self.monthly_data.items():
            self.simulated_monthly_data[key] = data

        import calendar
        for year, month_cfg in configs.items():
            existing_months = [int(k.split('-')[1]) for k in self.monthly_data if k.startswith(str(year))]
            src_data_map = {}
            for m_int in existing_months:
                src_key = f"{year}-{m_int:02d}"
                if src_key in self.monthly_data:
                    src_data_map[m_int] = self.monthly_data[src_key]

            for month_int, cfg in month_cfg.items():
                target_key = f"{year}-{month_int:02d}"
                src_name = cfg["src"].replace("月", "")
                src_month_int = int(src_name)
                src_data = src_data_map.get(src_month_int)
                if src_data is None:
                    continue

                src_load = np.array(src_data['load_array'])
                n_points = src_load.shape[1]
                target_days = calendar.monthrange(year, month_int)[1]

                if cfg["strategy"] == "平均曲线":
                    avg_curve = np.nanmean(src_load, axis=0)
                    sim_load = np.tile(avg_curve, (target_days, 1))
                else:
                    n_src_days = src_load.shape[0]
                    sim_load = np.zeros((target_days, n_points))
                    for d in range(target_days):
                        sim_load[d] = src_load[d % n_src_days]

                date_labels = [f"{year}-{month_int:02d}-{d+1:02d}" for d in range(target_days)]
                self.simulated_monthly_data[target_key] = {
                    'load_array': sim_load,
                    'time_labels': src_data['time_labels'],
                    'date_labels': date_labels,
                    'month_str': target_key,
                    'table_no': src_data.get('table_no', '未命名'),
                    'asset': src_data.get('asset', '未命名'),
                    'sheet': src_data.get('sheet', ''),
                    'simulated': True
                }

        self._use_simulation = True
        self.available_days = sum(
            d['load_array'].shape[0] for d in self.simulated_monthly_data.values()
            if 'load_array' in d and d['load_array'].ndim == 2
        )
        self._recalc_spaces()
        self._update_mode_options()
        self._populate_space_table()
        self._populate_daily_month_combo()
        if self.daily_month_combo.count() > 0:
            self._fill_daily_table(self.daily_month_combo.currentText())
        self.available_days_label.setText(f"模拟后数据天数: {self.available_days} 天")
        self._update_effective_days_label()
        self._update_system_scale()
        QMessageBox.information(self, "完成", "负荷模拟完成，数据已更新。可点击“恢复原始数据”撤销。")

    def _restore_original(self):
        self.simulated_monthly_data.clear()
        self._use_simulation = False
        self.available_days = sum(
            d['load_array'].shape[0] for d in self.monthly_data.values()
            if 'load_array' in d and d['load_array'].ndim == 2
        )
        self._recalc_spaces()
        self._update_mode_options()
        self._populate_space_table()
        self._populate_daily_month_combo()
        if self.daily_month_combo.count() > 0:
            self._fill_daily_table(self.daily_month_combo.currentText())
        self.available_days_label.setText(f"实际数据天数: {self.available_days} 天")
        self._update_effective_days_label()
        self._update_system_scale()
        QMessageBox.information(self, "已恢复", "已恢复为原始数据。")

    def _on_param_changed(self):
        """v3.2fix: 200ms防抖——快速连击只执行最后一次重算."""
        if not hasattr(self, '_param_debounce_timer'):
            self._param_debounce_timer = QTimer(self)
            self._param_debounce_timer.setSingleShot(True)
            self._param_debounce_timer.setInterval(100)  # v3.2fix: 100ms
            self._param_debounce_timer.timeout.connect(self._do_param_changed)
        self._param_debounce_timer.start()

    def _do_param_changed(self):
        if self.mode in ('B', 'C'):
            self.rated_power = self.rated_power_spin.value()
        self.need_capacity = self.need_cap_cb.isChecked() if self.mode == 'C' else False
        self.ignore_negative = self.ignore_neg_cb.isChecked()
        self._recalc_spaces()
        self._update_mode_options()
        self._populate_space_table()
        if self.daily_month_combo.count() > 0:
            self._fill_daily_table(self.daily_month_combo.currentText())
        self._update_system_scale()
        self._update_cap_label()

    def update_asset_summaries(self, new_summaries, new_province_name, new_plot_options, new_global_multiplier):
        self.summaries = new_summaries
        self.province_name = new_province_name
        if isinstance(new_plot_options, dict):
            self.plot_options = PlotOptions.from_dict(new_plot_options)
        elif isinstance(new_plot_options, PlotOptions):
            self.plot_options = new_plot_options
        else:
            self.plot_options = PlotOptions()
        self.global_multiplier = new_global_multiplier
        self._build_idx_map()
        self._load_all_data()
        self._extract_years()
        self._unify_time_resolution()
        self._recalc_spaces()
        self._update_mode_options()
        self._populate_space_table()
        self._populate_daily_month_combo()
        if self.daily_month_combo.count() > 0:
            self._fill_daily_table(self.daily_month_combo.currentText())
        self.available_days_label.setText(f"实际数据天数: {self.available_days} 天")
        self._update_effective_days_label()
        self._update_system_scale()
        asset = self.summaries[0].asset_value if self.summaries else "未知"
        self.asset_info_label.setText(f"<b>资产：{asset}</b>  模式：{self.mode}")

    def closeEvent(self, event):
        """v3.2fix: 关闭时保存参数和几何,停止定时器,下次打开自动恢复."""
        for timer_attr in ('_cap_sync_timer', '_param_debounce_timer'):
            t = getattr(self, timer_attr, None)
            if t is not None and t.isActive():
                t.stop()
        if self.config_manager:
            self.config_manager.set_app('pw_rated_power', self.rated_power_spin.value() if hasattr(self, 'rated_power_spin') else 500)
            self.config_manager.set_app('pw_dod', self.dod_spin.value() if hasattr(self, 'dod_spin') else 90)
            self.config_manager.set_app('pw_capacity_mode', getattr(self, 'capacity_mode', 'two_two'))
            self.config_manager.set_app('pw_ignore_negative', self.ignore_neg_cb.isChecked() if hasattr(self, 'ignore_neg_cb') else False)
            set_window_geometry(self.config_manager, "ProfessionalWindow", self.saveGeometry())
        super().closeEvent(event)




# ======================= 多进程资源安全上下文管理器 =======================
class _ManagedProcessPool:
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


def _execute_plot_tasks(worker_args, config_manager, signals, cancel_event, progress_queue, total_charts=None, actual_workers=None):
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
            # args = (data, title_tpl, filename_tpl, out_dir, classify, opts, bill, periods, mult)
            # 追加 progress_queue 和 cancel_event
            full_args = args + (progress_queue, cancel_event)
            futures.append(executor.submit(_plot_single_worker, *full_args))

        # v2.3.5fix(K3): 回退 Event 提到循环外。
        # 原实现每轮新建 threading.Event()，新建的永远 unset，
        # is_set() 恒 False——那段「仍可取消」的注释是死代码。
        # 现在 cancel_event 由调用方保证非 None（manager 失败时用 stop_event）。
        _ce = cancel_event if cancel_event is not None else threading.Event()
        for fut in as_completed(futures):
            if _ce.is_set():
                # 取消所有未完成的任务
                for f in futures:
                    f.cancel()
                break
            try:
                ok, msg = fut.result(timeout=120)
                if ok:
                    all_paths.append(msg)
                    signals.log.emit(f"已生成: {os.path.basename(msg)}")
                else:
                    if "已取消" in str(msg):
                        signals.log.emit("绘图任务已取消")
                    else:
                        failed_tasks.append(f"图表失败: {msg}")
                        signals.log.emit(f"图表失败: {msg}")
            except Exception as e:
                friendly = _translate_error(type(e), str(e))
                failed_tasks.append(f"进程异常: {friendly}")
                signals.log.emit(f"图表异常: {friendly}")

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
                except Exception:
                    break

    return all_paths, failed_tasks


# ======================= 导出确认对话框（汉化，增强日志） =======================
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
            geom = get_window_geometry(self.config_manager, "ExportConfirmDialog")
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
            # v2.3.9fix(P2-17): 同名文件实际不会覆盖——_plot_single_worker 会自动加 _1 后缀。
            # 原提示写「将覆盖」，用户以为旧图会被替掉，实际拿到两份。
            status = "同名，将自动加序号" if os.path.exists(full_path) else ""
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
        status = "同名，将自动加序号" if os.path.exists(full_path) else ""
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
            except Exception:
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
            status = "同名，将自动加序号" if os.path.exists(full_path) else ""
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
                except Exception:
                    mult_val = self.global_multiplier
                multipliers[idx] = mult_val
                bill_text = self.table.item(row, 4).text().strip()
                if bill_text:
                    try:
                        bill_val = float(bill_text)
                        billings[idx] = {'type': 'capacity', 'value': bill_val}
                    except Exception:
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
        """v3.2fix: 导出确认对话框关闭."""
        if self.config_manager:
            set_window_geometry(self.config_manager, "ExportConfirmDialog", self.saveGeometry())
        super().closeEvent(event)


# ======================= 处理线程（使用内存自适应，跨进程取消保护） =======================
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

        self.plot_options = PlotOptions(
            show_daily=True,
            show_avg=True,
            fit_types=[],
            show_max_demand=self.config_manager.app_set('show_max_demand', False),
            highlight_mode=self.config_manager.app_set('highlight_mode', 'none'),
            weekday_color=self.config_manager.app_set('weekday_color', DEFAULT_WEEKDAY_COLOR),
            weekend_color=self.config_manager.app_set('weekend_color', DEFAULT_WEEKEND_COLOR),
            holiday_color=self.config_manager.app_set('holiday_color', DEFAULT_HOLIDAY_COLOR),
            weekend_line_width=self.config_manager.app_set('weekend_line_width', DEFAULT_WEEKEND_LINE_WIDTH),
            holiday_line_width=self.config_manager.app_set('holiday_line_width', DEFAULT_HOLIDAY_LINE_WIDTH),
            show_weekend_bg=self.config_manager.app_set('show_weekend_bg', False),
            legend_position=self.config_manager.app_set('legend_position', 'right'),
            mark_anomalies=self.config_manager.app_set('mark_anomalies', False),
        )
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
        self._mp_manager = None
        self._mp_cancel_event = None

    def run(self):
        self._adjust_for_memory()
        try:
            self._mp_manager = mp.Manager()
            self._mp_cancel_event = self._mp_manager.Event()
        except Exception as e:
            log.warning(f"无法创建进程管理器: {e}，取消功能可能失效。")
            self._mp_cancel_event = None

        try:
            if self.stop_event.is_set():
                return
            self.signals.log.emit("阶段1/4：文件解析与映射")
            for fp in self.file_paths:
                try:
                    sz = os.path.getsize(fp)
                    if sz > self._max_file_size:
                        self._max_file_size = sz
                except Exception:
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
            if self._mp_manager:
                try:
                    self._mp_manager.shutdown()
                except Exception:
                    pass
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
            if self._max_file_size > HUGE_FILE_SIZE:
                self._scan_workers = 1
                self.signals.log.emit("超大文件检测，扫描并行度降至1 (psutil未安装，无法根据内存调整)")

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

            # v3.2fix: 原为 emit 后再 clear(竞态窗口) + wait() 无超时(用户不操作则处理线程永久阻塞)。
            # 改为先 clear 再 emit,并加可配置超时(默认60s),超时跳过该文件。
            self.dialog_event.clear()
            self.signals.mapping_request.emit((fp, sheets))
            try:
                _map_timeout = int(self.config_manager.app_set('mapping_timeout', 60) or 60)
            except Exception:
                _map_timeout = 60
            _wait_start = time.time()
            while not self.dialog_event.wait(0.5):
                if self.stop_event.is_set():
                    return
                if time.time() - _wait_start > _map_timeout:
                    # v2.3.8fix: 原行为是 break 后 continue，跳过该文件继续下一个。
                    # 但 UI 线程还阻塞在第一个 dlg.exec() 里，用户随后的「确定」会
                    # 唤醒**下一个文件**的等待（set_mapping_result 没有代次校验），
                    # 于是 b 文件用了 a 文件的列映射——列名对得上就用错列算错数据，
                    # 对不上就报「缺少列」。两种结果都不报真因。
                    # 改为中止整个任务：宁可让用户重来，也不能拿错映射继续算。
                    self.signals.log.emit(
                        f"映射超时({_map_timeout}s)，处理已中止。"
                        f"若要继续请重新开始（映射对话框里可选「跳过」）。"
                    )
                    self.stop_event.set()
                    return
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
                    res = fut.result(timeout=120)
                    if isinstance(res, dict) and 'error' in res:
                        friendly = _translate_error(None, res['error'])
                        self.signals.log.emit(f"扫描错误: {friendly}")
                    elif isinstance(res, dict) and res.get('skipped'):
                        # v2.3.8fix: 扫描层现在会把「整表没拿到有效数据」明确报上来。
                        # 以前这些情况返回空列表，界面与「这个文件本来就没有数据」无法区分。
                        _why = {
                            'empty_sheet': '工作表无数据行（只有表头或完全为空）',
                            'no_valid_rows': '所有行的日期都无法识别',
                        }.get(res['skipped'], res['skipped'])
                        _extra = f"，共 {res.get('dropped')} 行" if res.get('dropped') else ""
                        self.signals.log.emit(f"跳过 {os.path.basename(fp)} / {sheet}：{_why}{_extra}")
                    elif isinstance(res, list):
                        for item in res:
                            # v2.3.8fix: 原为 GroupSummary(**item)。
                            # core.models.GroupSummary 是 __slots__ 定长结构，注释里写明
                            # 「统一从 from_dict 构造，而不是各处写 **d」——但这里没跟上。
                            # 后果：扫描层一旦在 dict 里多带一个字段（如 dropped_rows），
                            # 这里直接 TypeError，整批分组丢失。
                            self.all_summaries.append(GroupSummary.from_dict(item))
                            _dropped = item.get('dropped_rows') or 0
                            if _dropped:
                                self.signals.log.emit(
                                    f"[{os.path.basename(fp)}/{sheet}] {_dropped} 行的日期无法识别，已跳过"
                                )
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
        self.plot_options = PlotOptions(
            show_daily=settings.get('show_daily', True),
            show_avg=settings.get('show_avg', True),
            show_max_demand=settings.get('show_max_demand', False),
            fit_types=[],
            highlight_mode=settings.get('highlight_mode', 'none'),
            weekday_color=settings.get('weekday_color', DEFAULT_WEEKDAY_COLOR),
            weekend_color=settings.get('weekend_color', DEFAULT_WEEKEND_COLOR),
            holiday_color=settings.get('holiday_color', DEFAULT_HOLIDAY_COLOR),
            weekend_line_width=settings.get('weekend_line_width', DEFAULT_WEEKEND_LINE_WIDTH),
            holiday_line_width=settings.get('holiday_line_width', DEFAULT_HOLIDAY_LINE_WIDTH),
            show_weekend_bg=settings.get('show_weekend_bg', False),
            legend_position=settings.get('legend_position', 'right'),
            mark_anomalies=settings.get('mark_anomalies', False),
            dark_theme=settings.get('dark_theme', False),
        )
        self.global_multiplier = settings.get('global_multiplier', self.global_multiplier)
        self.per_group_multiplier = settings.get('per_group_multiplier', {})
        self.per_group_billing = settings.get('per_group_billing', {})
        self.billing_dict = settings.get('billing_dict', {})
        self.title_template = settings.get('title_template', self.title_template)
        self.filename_template = settings.get('filename_template', self.filename_template)
        self.output_dir = settings.get('output_dir', self.output_dir)
        self.classify_by_asset = settings.get('classify_by_asset', False)
        self.price_periods = settings.get('price_periods', {})
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
            opts = self.plot_options  # PlotOptions object
            if not self._highlight_flags.get(orig_idx, True):
                opts = PlotOptions(**{**opts.to_dict(), 'highlight_mode': 'none'})
            worker_args.append((data, self.title_template, self.filename_template,
                                self.output_dir, self.classify_by_asset, opts,
                                bill, self.price_periods, mult))

        cancel_event = self._mp_cancel_event
        manager = self._mp_manager
        if manager is None:
            try:
                manager = mp.Manager()
            except Exception:
                manager = None
            cancel_event = manager.Event() if manager else None
        # v2.3.5fix(K3): 管理器不可用时 cancel_event 会退化成 None，
        # 而 _execute_plot_tasks 里「临时新建 Event」的回退是死代码——
        # 那个 Event 没有任何人能 set，is_set() 恒 False，取消按钮静默失效。
        # 用 stop_event 兜底：cancel() 会同时 set 它。
        if cancel_event is None:
            cancel_event = self.stop_event

        progress_queue = None
        if manager:
            progress_queue = manager.Queue()
        else:
            progress_queue = mp.Queue()

        try:
            all_paths, failed = _execute_plot_tasks(
                worker_args, self.config_manager, self.signals,
                cancel_event,
                progress_queue, total_charts=len(tasks),
                actual_workers=self._plot_worker_count
            )
            self.failed_tasks.extend(failed)
        finally:
            if manager and manager != self._mp_manager:
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
        if self._mp_cancel_event:
            try:
                self._mp_cancel_event.set()
            except (BrokenPipeError, EOFError, FileNotFoundError, ConnectionRefusedError, OSError):
                pass
# -*- coding: utf-8 -*-
# 第五部分：样式表、文件拖拽列表、主窗口、主入口
# 注意：本部分代码紧跟第一至第四部分，共享同一命名空间

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
        except Exception:
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
                    except Exception:
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


# ======================= 主窗口 =======================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{FUSION_APP_NAME} v{FUSION_VERSION}")
        self.setMinimumSize(900, 600)

        self.config_manager = Config()
        self.files = []
        self.output_dir = self.config_manager.app_set('last_output_dir', '')
        if not self.output_dir or not os.path.isdir(self.output_dir):
            self.output_dir = os.path.join(os.path.expanduser("~"), "Desktop", "负荷曲线输出")

        geom = get_window_geometry(self.config_manager, "MainWindow")
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
        self._gen_manager = None
        self._gen_thread = None

        self._active_mapping_dlg = None

        self._taskbar_button = None
        if sys.platform == 'win32' and QWinTaskbarButton:
            try:
                self._taskbar_button = QWinTaskbarButton(self)
            except Exception:
                pass

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
                        except Exception:
                            pass
        except Exception:
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
        # v2.3.4fix(J6): 原实现不过滤文件类型——拖进 .txt / .zip / .exe 也会进列表
        # 并尝试处理（Fusion 里甚至没有 _is_supported_file）。现补文件夹递归 + 扩展名过滤。
        expanded = []
        for p in paths:
            if os.path.isdir(p):
                for root, _, files in os.walk(p):
                    for fn in files:
                        fp = os.path.join(root, fn)
                        if _is_supported_file(fp):
                            expanded.append(fp)
            elif _is_supported_file(p):
                expanded.append(p)
        rejected = len(paths) - len(expanded) if not any(os.path.isdir(x) for x in paths) else 0
        if rejected > 0:
            self._log(f"已忽略 {rejected} 个不支持的文件（仅支持 {', '.join(SUPPORTED_EXTS)}）")
        paths = expanded
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
            except Exception:
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

    # ===================== 处理方法 =====================
    def _reset_processing_state(self):
        if self._thread:
            self._thread.cancel()
            if self._thread.is_alive():
                self._thread.join(timeout=3)
            self._thread = None
        if self._gen_cancel_event:
            try:
                self._gen_cancel_event.set()
            except (BrokenPipeError, EOFError, FileNotFoundError, ConnectionRefusedError, OSError):
                pass
        if self._gen_manager:
            try:
                self._gen_manager.shutdown()
            except Exception:
                pass
        if self._gen_thread and self._gen_thread.is_alive():
            self._gen_thread.join(timeout=3)
        self._gen_thread = None
        self._gen_cancel_event = None
        self._gen_manager = None
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
            except Exception:
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

    @_dialog_handshake('set_mapping_result', 'cancel', '映射对话框')
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

    @_dialog_handshake('set_preview_result', None, '预览对话框')
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
                'dark_theme': self.config_manager.app_set('dark_theme', False),
                'province_name': self.config_manager.app_set('last_province_name'),
                'title_template': self.config_manager.app_set('last_title_template', '{table_no}_{year_month}_负荷曲线'),
                'filename_template': self.config_manager.app_set('last_filename_template', '{table_no}_{year_month}_负荷曲线'),
                'classify_by_asset': self.config_manager.app_set('classify_by_asset', False),
                'billing_dict': {},
                'per_group_multiplier': {},
                'per_group_billing': {},
                'price_periods': {},
                'highlight_flags': {idx: highlight_default for idx in range(len(summaries))}
            }
            prov = settings.get('province_name')
            if prov and prov != "无":
                settings['price_periods'] = self.config_manager.get_periods_for_all_months(prov)
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
                'dark_theme': dlg.result.get('dark_theme', False),
                'province_name': dlg.result.get('province_name'),
                'title_template': dlg.result['title_template'],
                'filename_template': dlg.result['filename_template'],
                'classify_by_asset': dlg.result.get('classify_by_asset', False),
                'billing_dict': dlg.result.get('billing_dict', {}),
                'per_group_multiplier': dlg.result.get('per_group_multiplier', {}),
                'per_group_billing': dlg.result.get('per_group_billing', {}),
                'price_periods': dlg.result.get('price_periods', {}),
                'highlight_flags': dlg.result.get('highlight_flags', {idx: True for idx in dlg.result['selected_indices']})
            }
            self._last_preview_settings = settings
            self._thread.set_preview_result(settings)
            self.phase_label.setText("当前阶段：正在生成图表...")
        else:
            self._thread.set_preview_result(None)
            self._preview_dlg = None
        self._dialog_active = False
        self._set_ui_state('processing')

    @_dialog_handshake('set_export_confirm_result', None, '导出确认对话框')
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

        report_path = self._generate_report(paths, failed)
        self.status_label.setText(f"完成，已生成 {len(paths)} 张图表至 {self.output_dir}")
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("生成完成")
        # v2.3.7fix: _generate_report 失败时返回 None，不能再把 None 写进文案
        # 并挂一个会报错的「打开报告」按钮。
        _tail = f"\n报告: {report_path}" if report_path else "\n（报告生成失败，图表不受影响）"
        msg_box.setText(f"图表已成功生成至:\n{self.output_dir}\n共 {len(paths)} 张{_tail}")
        open_btn = msg_box.addButton("打开文件夹", QMessageBox.ActionRole)
        copy_btn = msg_box.addButton("复制路径", QMessageBox.ActionRole)
        report_btn = msg_box.addButton("打开报告", QMessageBox.ActionRole) if report_path else None
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
            except Exception:
                pass
        elif clicked == return_preview_btn:
            self._return_to_preview()

    def _generate_report(self, paths, failed):
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
<p>输出目录: {html_mod.escape(str(self.output_dir))}</p>
<p>成功 <span class="success">{success_count}</span> 张，失败 <span class="failure">{fail_count}</span> 张，总计 {total} 张。</p>
"""

            if success_count > 0:
                html += "<h2>成功列表</h2><table><tr><th>序号</th><th>文件名</th><th>表号</th><th>月份</th><th>源文件</th></tr>"
                # v2.3.8fix: 原为 startswith(资产名)。资产 `1` 会匹配
                # `10_202601_负荷曲线.png`，报告里那一行的表号/月份/源文件全取错。
                # 改为用同一套模板算出期望文件名再全等比较。
                _tpl = getattr(self._thread, 'filename_template', None) or '{table_no}_{year_month}_负荷曲线'
                for i, p in enumerate(paths, 1):
                    info = {}
                    _base = os.path.basename(p)
                    for s in self._thread.all_summaries if self._thread else []:
                        _ctx = {
                            'year_month': s.month_str or '',
                            'table_no': s.asset_value or '未命名',
                            'asset': s.asset_value or '未命名',
                            'sheet': s.sheet_name or '',
                        }
                        if _base == generate_filename(_tpl, _ctx):
                            info = {'asset': s.asset_value, 'month': s.month_str, 'file': os.path.basename(s.file_path)}
                            break
                    # v2.3.7fix: 文件名/资产号/源文件名都可能含 & < >（如「A&B楼负荷.xlsx」），
                    # 直接拼进 HTML 会破坏页面结构，逐列转义。
                    html += (f"<tr><td>{i}</td>"
                             f"<td>{html_mod.escape(os.path.basename(p))}</td>"
                             f"<td>{html_mod.escape(str(info.get('asset', '?')))}</td>"
                             f"<td>{html_mod.escape(str(info.get('month', '?')))}</td>"
                             f"<td>{html_mod.escape(str(info.get('file', '?')))}</td></tr>")
                html += "</table>"

            if fail_count > 0:
                html += "<h2>失败列表</h2><ul>"
                for f in failed:
                    html += f"<li class='failure'>{html_mod.escape(str(f))}</li>"
                html += "</ul>"

            html += "</body></html>"

            with open(report_path, 'w', encoding='utf-8') as f:
                f.write(html)
            self._log(f"生成报告已保存至: {report_path}")
            return report_path
        except Exception as e:
            # v2.3.7fix: 原来这里也 return report_path，但文件根本没写成——
            # 调用方会显示「报告: <路径>」并挂「打开报告」按钮，点开是空的。
            self._log(f"生成报告失败: {e}")
            return None

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
                'dark_theme': settings.get('dark_theme', False),
                'province_name': settings.get('province_name'),
                'title_template': settings['title_template'],
                'filename_template': settings['filename_template'],
                'classify_by_asset': settings.get('classify_by_asset', False),
                'billing_dict': settings.get('billing_dict', {}),
                'per_group_multiplier': settings.get('per_group_multiplier', {}),
                'per_group_billing': settings.get('per_group_billing', {}),
                'price_periods': settings.get('price_periods', {}),
                'highlight_flags': self._thread._last_highlight_flags if self._thread and self._thread._last_highlight_flags else {idx: True for idx in settings['selected_indices']}
            }
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

        try:
            self._gen_manager = mp.Manager()
            self._gen_cancel_event = self._gen_manager.Event()
        except Exception as e:
            self._log(f"无法创建进程管理器: {e}，取消功能可能失效")
            self._gen_manager = None
            self._gen_cancel_event = None

        self._gen_signals = ProcessingSignals()
        self._gen_signals.log.connect(self._log)
        self._gen_signals.progress.connect(self._on_progress_update)
        self._gen_signals.progress_max.connect(self._set_progress_max)
        self._gen_signals.status.connect(self.status_label.setText)
        self._gen_signals.error.connect(self._on_error)
        self._gen_signals.finished.connect(self._on_gen_finished)
        self._gen_signals.generation_finished.connect(self._on_generation_finished)
        self._gen_signals.eta.connect(self.eta_label.setText)

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
            except Exception:
                pass
            self._gen_signals = None
        self._gen_thread = None
        if self._gen_manager:
            try:
                self._gen_manager.shutdown()
            except Exception:
                pass
        self._gen_manager = None
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
            plot_options = PlotOptions(
                show_daily=settings.get('show_daily', True),
                show_avg=settings.get('show_avg', True),
                show_max_demand=settings.get('show_max_demand', False),
                fit_types=[],
                highlight_mode=settings.get('highlight_mode', 'none'),
                weekday_color=settings.get('weekday_color', DEFAULT_WEEKDAY_COLOR),
                weekend_color=settings.get('weekend_color', DEFAULT_WEEKEND_COLOR),
                holiday_color=settings.get('holiday_color', DEFAULT_HOLIDAY_COLOR),
                weekend_line_width=settings.get('weekend_line_width', DEFAULT_WEEKEND_LINE_WIDTH),
                holiday_line_width=settings.get('holiday_line_width', DEFAULT_HOLIDAY_LINE_WIDTH),
                show_weekend_bg=settings.get('show_weekend_bg', False),
                legend_position=settings.get('legend_position', 'right'),
                mark_anomalies=settings.get('mark_anomalies', False),
                dark_theme=settings.get('dark_theme', False),
            )
            global_mult = settings['global_multiplier']
            per_mult = settings.get('per_group_multiplier', {})
            per_bill = settings.get('per_group_billing', {})
            billing_dict = settings.get('billing_dict', {})
            price_periods = settings.get('price_periods', {})
            highlight_flags = settings.get('highlight_flags', {})

            tasks = []
            for orig_idx in selected_indices:
                if cancel_event and cancel_event.is_set():
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
                opts = plot_options
                if not highlight_flags.get(orig_idx, True):
                    opts = PlotOptions(**{**opts.to_dict(), 'highlight_mode': 'none'})
                worker_args.append((data, title_template, filename_template,
                                    output_dir, classify, opts,
                                    bill, price_periods, mult))

            progress_queue = mp.Queue()
            try:
                all_paths, failed = _execute_plot_tasks(
                    worker_args, self.config_manager, signals, cancel_event,
                    progress_queue, total_charts=len(tasks),
                    actual_workers=getattr(self, '_plot_worker_count', self.config_manager.get_plot_processes())
                )
            finally:
                pass

            signals.generation_finished.emit((all_paths, failed))
        except Exception as e:
            signals.error.emit(_translate_error(type(e), str(e)))
        finally:
            signals.finished.emit()

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
            except Exception:
                pass
        if self._preview_dlg is not None and self._preview_dlg.isVisible():
            try:
                self._preview_dlg.reject()
            except Exception:
                pass
        if self._gen_cancel_event:
            try:
                self._gen_cancel_event.set()
            except (BrokenPipeError, EOFError, FileNotFoundError, ConnectionRefusedError, OSError):
                pass
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
            if not _validate_config_schema(new_cfg):
                QMessageBox.critical(self, "错误", "配置文件结构无效，拒绝导入。")
                return
            if QMessageBox.question(self, "确认", "导入将覆盖当前配置，继续？") == QMessageBox.Yes:
                # v2.3.9fix(P2-18): 替换与迁移放进 Config 的锁里，避免与保存线程竞争。
                # 原实现直接赋值 cfg 并 _migrate()，不持锁。
                with self.config_manager._lock:
                    self.config_manager.cfg = new_cfg
                    self.config_manager._migrate()
                    self.config_manager.save()
                # v2.3.9fix(P2-18): 界面不刷新，用户会以为导入没生效。
                QMessageBox.information(
                    self, "完成",
                    "配置已导入。省份、分时策略与显示设置需重启程序后全部生效。")
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
        psutil_status = "已安装" if importlib.util.find_spec("psutil") else "未安装 (内存自适应不可用)"
        taskbar_status = "支持" if self._taskbar_button else "不支持"
        QMessageBox.information(self, "关于",
            f"{FUSION_APP_NAME} v{FUSION_VERSION}\n\n"
            f"主要更新：\n"
            f"• 策略架构重构：省份 → 策略组 → 月份，支持多季节电价\n"
            f"• 放电空间逻辑修正，等效满足天数\n"
            f"• 负荷模拟（基础版）\n"
            f"• 专业模式资产号实时更新\n"
            f"• 累积示数时间间隔校验\n"
            f"• 暗色主题切换（含图表）\n"
            f"• 生成报告自动输出\n"
            f"• PlotOptions 数据类强化类型安全\n"
            f"• 内存敏感缓存替换固定容量\n"
            f"• 子进程绘图支持可中断取消\n"
            f"• Windows 保留设备名防护\n"
            f"• 空异常块增加日志追踪\n"
            f"• 配置文件结构校验\n"
            f"• MemoryLimitedCache 封装修复\n\n"
            f"依赖状态:\n"
            f"• scipy: {scipy_status}\n"
            f"• chardet: {chardet_status}\n"
            f"• chinesecalendar: {holiday_status}\n"
            f"• psutil: {psutil_status}\n"
        )

    def _gui_exception_handler(self, exc_type, exc_value, exc_tb):
        log.critical(f"GUI异常:\n{''.join(traceback.format_exception(exc_type, exc_value, exc_tb))}")
        QMessageBox.critical(self, "致命错误", f"程序遇到未处理异常:\n{''.join(traceback.format_exception(exc_type, exc_value, exc_tb))}\n建议重启程序。")

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
        set_window_geometry(self.config_manager, "MainWindow", self.saveGeometry())
        self.config_manager.save()
        super().closeEvent(event)


# ======================= 主入口 =======================
def main():
    mp.freeze_support()

    _check_and_install_dependencies()

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

    # 启动提示
    msg_box = QMessageBox()
    msg_box.setWindowTitle(f"欢迎 — {FUSION_APP_NAME} v{FUSION_VERSION}")
    msg_box.setText(f"{FUSION_APP_NAME} v{FUSION_VERSION}\n\n本工具适用于批量生成负荷曲线图表，\n并支持专业储能分析。\n\n请先添加 Excel/CSV 文件开始。")
    msg_box.addButton(QMessageBox.Ok)
    msg_box.exec()

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
