# -*- coding: utf-8 -*-
"""纯数据模型：零 Qt、零 matplotlib、零 IO。

本模块从 Fusion v2.3.9 单文件（`负荷曲线批量生成工具 Pro 融合版 (v2.3).py`）
原样搬出，逻辑一字未改，只把散落在模块级的两类东西收拢到这里：

1. 五个纯数据类：PeriodType / CurveLabel / ColorConst / PlotOptions / GroupSummary
2. 五个绘制默认值常量（DEFAULT_*）—— 原来与 PlotOptions 分居两处，
   搬出后这里是唯一来源，主程序从这里 import 回去。

铁律（方案 D 第 3 节）
----------------------
core/ 不许 import PySide6、不许 import matplotlib、不许 import fastapi。
可机械检查：

    grep -rn "PySide6\\|matplotlib\\|fastapi" core/

搬出依据：这五个类经 AST 扫描确认「零 Qt 引用、零文件 IO」，
是整份单文件中耦合最低的一块，因此作为阶段 1 的第一块。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List

__all__ = [
    "DEFAULT_WEEKDAY_COLOR",
    "DEFAULT_WEEKEND_COLOR",
    "DEFAULT_HOLIDAY_COLOR",
    "DEFAULT_WEEKEND_LINE_WIDTH",
    "DEFAULT_HOLIDAY_LINE_WIDTH",
    "PeriodType",
    "CurveLabel",
    "ColorConst",
    "PlotOptions",
    "GroupSummary",
]

# ── 绘制默认值（原主程序 L192-196）──────────────────────────────
DEFAULT_WEEKDAY_COLOR = '#1f77b4'
DEFAULT_WEEKEND_COLOR = '#FF8C00'
DEFAULT_HOLIDAY_COLOR = '#DC143C'
DEFAULT_WEEKEND_LINE_WIDTH = 1.8
DEFAULT_HOLIDAY_LINE_WIDTH = 2.0


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


# ======================= 绘图选项数据类（唯一定义） =======================
@dataclass
class PlotOptions:
    show_daily: bool = True
    show_avg: bool = True
    show_max_demand: bool = False
    fit_types: List[str] = field(default_factory=list)
    highlight_mode: str = 'none'
    weekday_color: str = DEFAULT_WEEKDAY_COLOR
    weekend_color: str = DEFAULT_WEEKEND_COLOR
    holiday_color: str = DEFAULT_HOLIDAY_COLOR
    weekend_line_width: float = DEFAULT_WEEKEND_LINE_WIDTH
    holiday_line_width: float = DEFAULT_HOLIDAY_LINE_WIDTH
    show_weekend_bg: bool = False
    legend_position: str = 'right'
    mark_anomalies: bool = False
    dark_theme: bool = False

    def to_dict(self):
        return {
            'show_daily': self.show_daily,
            'show_avg': self.show_avg,
            'show_max_demand': self.show_max_demand,
            'fit_types': self.fit_types,
            'highlight_mode': self.highlight_mode,
            'weekday_color': self.weekday_color,
            'weekend_color': self.weekend_color,
            'holiday_color': self.holiday_color,
            'weekend_line_width': self.weekend_line_width,
            'holiday_line_width': self.holiday_line_width,
            'show_weekend_bg': self.show_weekend_bg,
            'legend_position': self.legend_position,
            'mark_anomalies': self.mark_anomalies,
            'dark_theme': self.dark_theme,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class GroupSummary:
    __slots__ = ('file_path', 'sheet_name', 'asset_value', 'month_str', 'date_count',
                 'time_points', 'min_val', 'max_val', 'mapping')

    def __init__(self, file_path, sheet_name, asset_value, month_str, date_count,
                 time_points, min_val, max_val, mapping):
        self.file_path = file_path
        self.sheet_name = sheet_name
        self.asset_value = asset_value
        self.month_str = month_str
        self.date_count = date_count
        self.time_points = time_points
        self.min_val = min_val
        self.max_val = max_val
        self.mapping = mapping

    def __eq__(self, other):
        if not isinstance(other, GroupSummary):
            return NotImplemented
        return (self.file_path == other.file_path and
                self.sheet_name == other.sheet_name and
                self.asset_value == other.asset_value and
                self.month_str == other.month_str)

    def __hash__(self):
        return hash((self.file_path, self.sheet_name, self.asset_value, self.month_str))
