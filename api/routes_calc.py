# -*- coding: utf-8 -*-
"""测算端点：分充放空间、容量与吞吐量。

对应真值表 ④ ⑤。

⚠️ 量纲警告（必须同时出现在 OpenAPI description 里）
--------------------------------------------------
本文件返回两个**不可互换**的量：

  daily_demand.value_kwh            单位 kWh      回答「电池该装多大」
  daily_throughput_kwh_per_day      单位 kWh/天   回答「一天能搬多少能量」

前者是单次容量，后者是一天累计吞吐。字段名里带量纲，不靠文档提醒。
"""
from __future__ import annotations

import logging

import numpy as np
from fastapi import APIRouter
from pydantic import BaseModel, Field

from api.routes_ingest import get_session
from api.schemas import (
    CapacityData, DailyDemand, DailyThroughput, Envelope, SegmentDemand,
)
from core.models import GroupSummary, PeriodType
from core.periods import _segment_periods
from core.spaces import compute_segmented_spaces
from services.capacity import (
    DemandBasis, daily_demand_series, summarize_daily_demand, throughput_breakdown,
)
from services.provider import DataProvider

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/calc", tags=["calc"])

_PROVIDER = DataProvider()


def _err(code: str, message: str, detail: dict | None = None) -> dict:
    return Envelope(ok=False, error={"code": code, "message": message,
                                     "detail": detail}).model_dump()


class Segment(BaseModel):
    start: float
    end: float
    type: str = Field(description="charge 或 discharge")
    label: str | None = None


class SpacesRequest(BaseModel):
    session_id: str
    group: int = 0
    segments: list[Segment] | None = Field(
        default=None, description="已切好的段；与 periods 二选一")
    periods: list[dict] | None = Field(
        default=None, description="原始时段 [{start,end,type}]，服务端自动切分")
    billing: dict | None = None
    mode: str = "A"
    rated_power: float | None = None
    ignore_negative: bool = False


def _load_group(session_id: str, group: int):
    sess = get_session(session_id)
    if not sess:
        return None, None, _err("PREVIEW_GROUP_NOT_FOUND", f"session 不存在：{session_id}")
    groups = sess.get("groups", [])
    if not groups or group < 0 or group >= len(groups):
        return None, None, _err("PREVIEW_GROUP_NOT_FOUND", f"分组序号越界：{group}")
    gs = GroupSummary(**groups[group])
    data = _PROVIDER.get_group_data(gs)
    if not data or data.get("load_array") is None:
        return None, None, _err("CALC_NO_DATA", "该分组没有可用数据")
    return gs, data, None


def _to_internal_segments(req: SpacesRequest):
    """把请求里的段转成 compute_segmented_spaces 要的结构（含 type 与 label）。"""
    if req.segments:
        out = []
        for i, s in enumerate(req.segments):
            t = s.type.lower()
            ptype = PeriodType.CHARGE if t.startswith("c") else PeriodType.DISCHARGE
            out.append({"start": s.start, "end": s.end, "type": ptype,
                        "label": s.label or f"{'充电' if ptype is PeriodType.CHARGE else '放电'}{i + 1}",
                        "sub_periods": []})
        return out
    if req.periods:
        # 原始时段用产品自己的切分逻辑，保持与界面一致
        return _segment_periods(req.periods)
    return []


@router.post("/spaces", response_model=Envelope)
def spaces(req: SpacesRequest):
    """按段算逐日充放电空间。

    返回按段的对象数组，**不暴露内部的四个平行列表**——
    那是 compute_segmented_spaces 的实现约定（等长、按索引对齐），
    固化进 API 会把实现细节变成协议。
    """
    segs = _to_internal_segments(req)
    if not segs:
        return _err("CALC_INVALID_SEGMENT", "没有有效时段，请传 segments 或 periods")

    gs, data, err = _load_group(req.session_id, req.group)
    if err:
        return err

    load = np.asarray(data["load_array"], dtype=float)
    labels = [str(x) for x in data["time_labels"]]
    try:
        ch, dis, cl, dl = compute_segmented_spaces(
            load, labels, segs, billing=req.billing, mode=req.mode,
            rated_power=req.rated_power, ignore_negative=req.ignore_negative)
    except Exception as e:
        return _err("CALC_INVALID_SEGMENT", f"测算失败：{e}", {"type": type(e).__name__})

    by_segment = []
    for i, lab in enumerate(cl):
        c = np.asarray(ch[i], dtype=float)
        d = np.asarray(dis[i], dtype=float)
        by_segment.append({
            "label": lab,
            "type": "charge" if float(np.sum(c)) != 0 else "discharge",
            "charge": [round(float(x), 6) for x in c],
            "discharge": [round(float(x), 6) for x in d],
        })

    return Envelope(ok=True, data={
        "group": {"asset": gs.asset_value, "month": gs.month_str},
        "date_labels": [str(x) for x in data.get("date_labels", [])],
        "segments": by_segment,
    }).model_dump()


class CapacityCalcRequest(BaseModel):
    session_id: str
    group: int = 0
    segments: list[Segment] | None = None
    periods: list[dict] | None = None
    billing: dict | None = None
    mode: str = "A"
    rated_power: float | None = None
    ignore_negative: bool = False
    basis: str = Field(default="min_feasible",
                       description="min_feasible（最小可行容量）或 full_load（满载利用率）")
    target_days: int | None = None
    dod_percent: float = 95.0


@router.post("/capacity", response_model=Envelope,
             description=(
                 "容量与吞吐量测算。\n\n"
                 "**量纲警告**：返回两个不同量纲的量，不可互换、不可比较。\n\n"
                 "· `daily_demand.value_kwh` —— 单位 kWh，回答「电池该装多大」\n"
                 "· `daily_throughput_kwh_per_day` —— 单位 kWh/天，回答「一天能搬多少能量」\n\n"
                 "单段配置（只有充或缺放）时吞吐量按 0 计算，属正常，会带 hints 说明。"
             ))
def capacity(req: CapacityCalcRequest):
    segs = _to_internal_segments(SpacesRequest(**req.model_dump()))
    if not segs:
        return _err("CALC_INVALID_SEGMENT", "没有有效时段，请传 segments 或 periods")

    gs, data, err = _load_group(req.session_id, req.group)
    if err:
        return err

    load = np.asarray(data["load_array"], dtype=float)
    labels = [str(x) for x in data["time_labels"]]
    try:
        ch, dis, cl, dl = compute_segmented_spaces(
            load, labels, segs, billing=req.billing, mode=req.mode,
            rated_power=req.rated_power, ignore_negative=req.ignore_negative)
    except Exception as e:
        return _err("CALC_INVALID_SEGMENT", f"测算失败：{e}", {"type": type(e).__name__})

    try:
        basis = DemandBasis(req.basis)
    except ValueError:
        return _err("CALC_INVALID_SEGMENT", f"未知口径：{req.basis}",
                    {"allowed": [b.value for b in DemandBasis]})

    # ⚠️ drop_zero_placeholders=True 必须传：
    # compute_segmented_spaces 的四个平行列表里，每个段在非本方向那一侧是占位零，
    # 不过滤的话 min_feasible 与 full_load 都恒为 0（v2.3.10 修的那个缺陷）。
    series = daily_demand_series(ch, dis, basis, drop_zero_placeholders=True)
    if series.size == 0:
        return _err("CALC_NO_DATA", "没有可用的逐日需求数据")

    summary = summarize_daily_demand(ch, dis, basis,
                                     target_days=req.target_days)
    # summarize 内部未传 drop 标志，这里用已过滤的 series 重算一次，保持口径一致
    if req.target_days and 0 < req.target_days <= series.size:
        value = float(np.sort(series)[series.size - req.target_days])
    else:
        value = float(np.max(series))

    dod = req.dod_percent if req.dod_percent and req.dod_percent > 0 else 95.0
    installed = value / (dod / 100.0) if value > 0 else 0.0

    by_segment = []
    for i, lab in enumerate(cl):
        c_sum = float(np.sum(np.asarray(ch[i], dtype=float)))
        d_sum = float(np.sum(np.asarray(dis[i], dtype=float)))
        by_segment.append(SegmentDemand(label=lab, charge_kwh=round(c_sum, 6),
                                        discharge_kwh=round(d_sum, 6)))

    # 吞吐量：逐日 min(当日总充, 当日总放)。
    # 旧版用「逐段 min 再求和」——充放通常在不同时段，那样算恒为 0，已改掉。
    tp = throughput_breakdown(ch, dis)
    hints: list[str] = []
    if tp["min_side"] == "charge" and tp["total_charge_kwh"] <= 0:
        hints.append("所有充电段的空间都是 0，一整天充不进去——请检查容量线（billing.value）")
    elif tp["min_side"] == "discharge" and tp["total_discharge_kwh"] <= 0:
        hints.append("所有放电段的空间都是 0，一整天放不出来——请检查负载与容量线")
    elif tp["min_side"] == "charge":
        hints.append("充电是瓶颈：总充电空间小于总放电空间，日吞吐受充电侧限制")
    elif tp["min_side"] == "discharge":
        hints.append("放电是瓶颈：总放电空间小于总充电空间，日吞吐受放电侧限制")
    if value == 0:
        hints.append("日需求为 0，请检查容量线（billing.value）是否已设置；"
                     "Mode A 必须有容量线才有充电空间")

    payload = CapacityData(
        daily_demand=DailyDemand(basis=basis.value, value_kwh=round(value, 6),
                                 by_segment=by_segment),
        daily_throughput=DailyThroughput(**tp),
        capacity_suggestion_installed_kwh=round(installed, 6),
        hints=hints,
    )
    return Envelope(ok=True, data={
        **payload.model_dump(),
        "group": {"asset": gs.asset_value, "month": gs.month_str},
        "days": int(series.size),
        "note": summary.get("note", ""),
    }).model_dump()
