# -*- coding: utf-8 -*-
"""中文字体探测与 matplotlib 字体设置。

本模块从 Fusion v2.3.9 单文件原样搬出，函数体一字未改。
搬移依据：`tools/analyze_deps.py --group plotting` 的依赖闭包切片。

字体回退链：_CJK_FONT_CANDIDATES 逐个尝试 → 关键词兜底 → DejaVu + 明确 warning。
这条链路 2026-10 的审查确认三层完整，搬移时勿改判定顺序。
"""
from __future__ import annotations

import re
import threading

import matplotlib.pyplot as plt
from loguru import logger as log

# 字体管理器。原主程序在模块级用 try-import 引入，形如：
#     try: from matplotlib.font_manager import fontManager
#     except ImportError: sys.exit(1)
# 闭包分析器当时只扫顶层 Import、漏了 Try 里的 import（已修），
# 导致搬移后 _find_cjk_font 直接 NameError。
# 这里改为抛异常而非 sys.exit：plotting 是被 import 的库，不应该杀掉进程。
try:
    from matplotlib.font_manager import fontManager
except ImportError as _e:  # pragma: no cover
    raise ImportError("matplotlib 未安装，绘图功能不可用") from _e

__all__ = ["_find_cjk_font", "_get_cached_font", "_setup_font"],


_CJK_FONT_CANDIDATES = [
    'SimHei', 'Microsoft YaHei', 'STHeiti', 'STSong', 'SimSun',
    'FangSong', 'KaiTi', 'YouYuan', 'NSimSun',
    'Noto Sans CJK SC', 'Noto Sans CJK TC', 'WenQuanYi Micro Hei',
    'WenQuanYi Zen Hei', 'Source Han Sans SC', 'Source Han Sans CN',
    'AR PL UMing CN', 'AR PL UKai CN', 'Droid Sans Fallback'
]


_FONT_LOCK = threading.Lock()
# 字体名缓存。原在主程序 L263，闭包分析器把 `global` 声明的变量当成局部变量、
# 漏算了它；实际搬移时报 NameError 才发现。缓存函数与字面常量必须同居。
_FONT_NAME = None


def _find_cjk_font():
    """查找系统中可用的中文字体"""
    try:
        fontManager.__init__()
    except Exception:
        log.debug("fontManager 初始化异常，继续尝试查找字体。", exc_info=True)
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

