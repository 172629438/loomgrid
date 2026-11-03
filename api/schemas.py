# -*- coding: utf-8 -*-
"""API 契约：请求 / 响应模型与统一包络。

约定（与《阶段 2 · API 真值表》一致）
------------------------------------
成功：{"ok": true, "data": {...}}
失败：{"ok": false, "error": {"code": "...", "message": "...", "detail": {...}}}

code 用大写下划线，前端可按码分支；message 是给人看的中文；
detail 放原始上下文（列名、文件、行号），便于排障。

⚠️ 量纲警告
------------
/api/calc/capacity 同时返回两个**不可互换**的量：

  daily_demand.value_kwh            单位 kWh      回答「电池该装多大」
  daily_throughput_kwh_per_day      单位 kWh/天   回答「一天能搬多少能量」

前者是单次容量，后者是一天累计吞吐。字段名里带量纲，不靠文档提醒。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# ── 统一包络 ──────────────────────────────────────────────


class ErrorBody(BaseModel):
    code: str = Field(description="大写下划线错误码，供前端分支")
    message: str = Field(description="给人看的中文说明")
    detail: dict[str, Any] | None = Field(default=None, description="原始上下文")


class Envelope(BaseModel):
    ok: bool
    data: Any | None = None
    error: ErrorBody | None = None


# ── ① /api/ingest/inspect ─────────────────────────────────


class Confidence(BaseModel):
    """映射可信度。

    reasons 的措辞要能直接摆给用户看，不要写内部标识符——前端会原样展示，
    那就是产品文案。
    """
    score: float = Field(ge=0.0, le=1.0, description="0-1，越低越需要人工确认")
    reasons: list[str] = Field(default_factory=list,
                               description="为什么是这个分数，中文、面向用户")


class MappingSuggest(BaseModel):
    date_col: str | None = None
    time_col: str | None = None
    load_col: list[str] | str | None = None
    asset_col: str | None = None
    format_type: Literal["wide", "wide_transposed", "long", "accumulated"] | None = None
    header_row: int = 0
    transpose: bool = False


class SheetInfo(BaseModel):
    name: str
    columns: list[str]
    rows_sampled: int
    suggest: MappingSuggest
    confidence: Confidence


class InspectData(BaseModel):
    path: str
    sheets: list[SheetInfo]


# ── ② /api/ingest/mapping ─────────────────────────────────


class MappingRequest(BaseModel):
    path: str
    sheet: str = "Sheet1"
    mapping: MappingSuggest


class GroupInfo(BaseModel):
    asset: str
    month: str
    date_count: int
    time_points: int
    min_val: float | None = None
    max_val: float | None = None


class InspectMappingData(BaseModel):
    groups: list[GroupInfo]
    session_id: str | None = Field(default=None, description="后续端点用它引用这批数据")


# ── ⑤ /api/calc/capacity（量纲在这里分开）─────────────────


class SegmentDemand(BaseModel):
    label: str
    charge_kwh: float
    discharge_kwh: float
    min_kwh: float


class DailyDemand(BaseModel):
    """单位 kWh —— 回答「电池该装多大」。"""
    basis: Literal["min_feasible", "full_load"]
    value_kwh: float
    by_segment: list[SegmentDemand] = Field(default_factory=list)


class CapacityData(BaseModel):
    daily_demand: DailyDemand
    daily_throughput_kwh_per_day: float = Field(
        description="单位 kWh/天 —— 回答「一天能搬多少能量」。"
                    "与 daily_demand 不可互换、不可比较。"
                    "单段配置（缺充或缺放）时为 0，属正常。")
    capacity_suggestion_installed_kwh: float | None = None
    hints: list[str] = Field(default_factory=list)


class CapacityRequest(BaseModel):
    daily_charge: list[float]
    daily_discharge: list[float]
    basis: Literal["min_feasible", "full_load"] = "min_feasible"
    target_days: int | None = None
    dod_percent: float = 95.0
