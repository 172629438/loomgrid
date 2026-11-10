# -*- coding: utf-8 -*-
"""中文字体探测与 matplotlib 字体设置。

本模块从 Fusion v2.3.9 单文件原样搬出，函数体一字未改。
搬移依据：`tools/analyze_deps.py --group plotting` 的依赖闭包切片。

字体回退链：_CJK_FONT_CANDIDATES 逐个尝试 → 关键词兜底 → DejaVu + 明确 warning。
这条链路 2026-10 的审查确认三层完整，搬移时勿改判定顺序。
"""
from __future__ import annotations

import logging
import os
import re
import sys
import threading

import matplotlib.pyplot as plt
log = logging.getLogger(__name__)

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
# v2.3.21：True 表示没找到中文字体，图表会显示方块。
# 主窗口启动后读这个标记，弹一次提示。
CJK_FONT_MISSING = False


def _font_dirs():
    """字体可能放的位置：程序目录 + 程序目录下的 fonts/ 子目录。

    v2.3.21：打包后程序目录是 exe 所在目录；源码运行时是仓库根。
    用户把字体文件丢进这两处之一，程序就能用上——不需要装到系统里。
    这对「没有管理员权限」「不想污染系统字体」的场景很实用。
    """
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return [base, os.path.join(base, "fonts")]


def _register_local_fonts():
    """把程序目录里的字体文件注册给 matplotlib。

    返回注册成功的字体名列表。找不到文件不算错，返回空列表。
    """
    added = []
    for d in _font_dirs():
        if not os.path.isdir(d):
            continue
        try:
            for fn in os.listdir(d):
                if not fn.lower().endswith((".ttf", ".otf", ".ttc")):
                    continue
                fp = os.path.join(d, fn)
                try:
                    fontManager.addfont(fp)
                    added.append(fn)
                    log.info(f"已加载程序目录字体: {fp}")
                except Exception as e:
                    log.debug(f"字体加载失败 {fp}: {e}")
        except OSError:
            continue
    if added:
        try:
            fontManager.__init__()      # 重建索引
        except Exception:
            pass
    return added


def _find_cjk_font():
    """查找可用的中文字体。

    顺序：① 系统已安装的（fontManager.ttflist）
          ② 程序目录里放的字体文件（自动注册后重查）
    两处都没有才降级成 DejaVu Sans 并打标记，由主窗口提示用户。
    """
    try:
        fontManager.__init__()
    except Exception:
        log.debug("fontManager 初始化异常，继续尝试查找字体。", exc_info=True)

    name = _pick_from_installed()
    if name:
        return name

    # 系统里没有 → 看看程序目录里有没有自带
    local = _register_local_fonts()
    if local:
        name = _pick_from_installed()
        if name:
            log.info(f"使用程序目录自带字体: {name}")
            return name
        # 注册了但名字不在候选表里，也接受（至少有个 CJK 字体）
        for f in fontManager.ttflist:
            if any(k in f.name.lower() for k in
                   ('cjk', 'chinese', 'hei', 'song', 'yahei', 'yuan', 'kai')):
                log.info(f"使用程序目录自带字体（非候选表）: {f.name}")
                return f.name

    global CJK_FONT_MISSING
    CJK_FONT_MISSING = True
    log.warning("未找到任何中文字体！图表中文将显示为方框。"
                "把任意 .ttf/.otf 中文字体放到程序目录或程序目录的 fonts/ 下即可。")
    return 'DejaVu Sans'


def _pick_from_installed():
    """从已注册字体里按候选表挑一个中文字体，找不到返回 None。"""
    available = {f.name for f in fontManager.ttflist}
    for name in _CJK_FONT_CANDIDATES:
        if name in available:
            return name
    for f in fontManager.ttflist:
        if any(k in f.name.lower() for k in
               ['cjk', 'chinese', 'simhei', 'simsun', 'yahei', 'songti', 'hei', 'kai']):
            return f.name
    return None

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

