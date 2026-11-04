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
import os

import numpy as np
from fastapi import APIRouter
from pydantic import BaseModel, Field

from api.routes_ingest import get_session
from api.session_data import ResolveError
from api.session_data import load_group as load_group_data
from api.schemas import (
    CapacityData, DailyDemand, DailyThroughput, Envelope, SegmentDemand,
)
from core.models import PeriodType
from core.periods import _segment_periods
from core.spaces import calc_days_for_capacity, compute_segmented_spaces
from services.timeline import unify_time_resolution
from services.capacity import (
    DemandBasis, daily_demand_series, summarize_daily_demand, throughput_breakdown,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/calc", tags=["calc"])


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
    """取数转发到 api.session_data.load_group（模拟/文件两种来源的统一入口）。

    保留本函数名与返回形态：routes_plot 直接 import 了它，契约由
    tests/verify_plot_fit_e2e.py 的 [S2] 组守着。
    错误码仍用原来的两个，调用方（routes_plot / 本模块）不需要改。
    """
    try:
        gs, data = load_group_data(session_id, group)
    except ResolveError as e:
        code = "CALC_NO_DATA" if e.code in ("CALC_NO_DATA", "PREVIEW_NO_DATA") else e.code
        return None, None, _err(code, e.message)
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


@router.post("/spaces", response_model=Envelope,
    description=(
        "按段算逐日充放电空间。\n\n"
        "**segments[].start / end 是 float 小时**（0.0 / 8.0），不是 '00:00' 字符串。\n\n"
        "**mode 决定充电空间怎么算**：\n"
        "· A（需量模式）充电 = Σ max(billing.value − 负荷, 0) × dt —— 与负值无关，缺 billing.value 则为 0\n"
        "· B / C（功率模式）充电 = Σ min(rated_power, max(−负荷,0)) × dt × η —— 只吃负负荷，缺 rated_power 则为 0\n\n"
        "**缺对应参数时该方向的空间为 0，不报错** —— 这是最容易误判的地方：\n"
        "看起来像「算对了结果就是 0」，实际是参数没给。\n\n"
        "ignore_negative=True 会把负负荷清零，于是 B/C 的充电空间归 0。\n\n"
        "seg_dt 用梯形权重：N 个采样点的时段总时长 = (N-2)×0.25 + 2×0.125，\n"
        "即「3 小时时段」实际 2.875 小时 —— 手算核对必须用这个值。"
    ),
             )
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


class SatisfiedDaysRequest(BaseModel):
    session_id: str
    group: int = 0
    segments: list[Segment] | None = None
    periods: list[dict] | None = None
    billing: dict | None = None
    mode: str = "A"
    rated_power: float | None = None
    ignore_negative: bool = False
    capacity_kwh: float = Field(description="安装容量（kWh）")
    dod_percent: float = 95.0


@router.post("/satisfied-days", response_model=Envelope,
             description=(
                 "给定安装容量，算能满足多少天。\n\n"
                 "**两套口径都返回，因为它们本来就不是同一个算法**：\n\n"
                 "· `satisfied_days` —— 与 Fusion 界面一致：\n"
                 "  先按 DOD 折算可用容量（cap×DOD），再**逐对**判定：\n"
                 "  任一对充放段的当日空间都 ≥ 可用容量，这一天就算满足。\n\n"
                 "· `satisfied_days_core` —— core.calc_days_for_capacity 的结果：\n"
                 "  **不折算 DOD**，把各段当日空间相加后与安装容量比。\n\n"
                 "两者可能差很多（前者严、后者松）。需要哪个由调用方定，\n"
                 "不要拿一个的语义去解释另一个的数。\n\n"
                 "`equivalent_days = int(satisfied × 365 / total_days)`，\n"
                 "total_days 是**有数据的天数**，不是日历天数。"
             ))
def satisfied_days(req: SatisfiedDaysRequest):
    sp = SpacesRequest(session_id=req.session_id, group=req.group,
                       segments=req.segments, periods=req.periods,
                       billing=req.billing, mode=req.mode,
                       rated_power=req.rated_power, ignore_negative=req.ignore_negative)
    segs = _to_internal_segments(sp)
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

    n_days = len(ch[0]) if ch else (len(dis[0]) if dis else 0)
    if n_days == 0:
        return _err("CALC_NO_DATA", "没有可用的逐日数据")

    # ⚠️ 必须先剔除占位零段（与 Fusion 的 `_get_relevant_segments_for_month`
    # 里的 `_drop_zero_segments` 一致）：
    # compute_segmented_spaces 返回的四个列表里，每个段在非本方向那一侧是占位零。
    # 不过滤的话「同一对里 cv 和 dv 都 ≥ 容量」永远不成立（总有一侧是 0），
    # 满足天数恒为 0——这正是实测到的那个 0。
    ch_nz = [np.asarray(a, dtype=float) for a in ch if float(np.nansum(a)) != 0.0]
    dis_nz = [np.asarray(a, dtype=float) for a in dis if float(np.nansum(a)) != 0.0]

    dod = req.dod_percent if req.dod_percent and req.dod_percent > 0 else 95.0
    usable_cap = req.capacity_kwh * dod / 100.0

    # 口径①：与 Fusion 界面一致（DOD 折算 + 逐对；任一对充放都够就算这天满足）
    satisfied = 0
    n_pairs = max(len(ch_nz), len(dis_nz))
    for d in range(n_days):
        day_ok = False
        for i in range(n_pairs):
            cv = float(ch_nz[i][d]) if i < len(ch_nz) and d < len(ch_nz[i]) else float("inf")
            dv = float(dis_nz[i][d]) if i < len(dis_nz) and d < len(dis_nz[i]) else float("inf")
            if cv >= usable_cap and dv >= usable_cap:
                day_ok = True
                break
        if day_ok:
            satisfied += 1

    # 口径②：core 的 calc_days_for_capacity（不折算，各段当日相加后比安装容量）
    try:
        charge_daily = (np.sum(np.vstack(ch_nz), axis=0) if ch_nz
                        else np.zeros(n_days, dtype=float))
        discharge_daily = (np.sum(np.vstack(dis_nz), axis=0) if dis_nz
                           else np.zeros(n_days, dtype=float))
        satisfied_core = int(calc_days_for_capacity(
            req.capacity_kwh, charge_daily, discharge_daily))
    except Exception:
        satisfied_core = -1

    equivalent = int(satisfied * 365.0 / n_days) if n_days > 0 else 0

    return Envelope(ok=True, data={
        "capacity_kwh": req.capacity_kwh,
        "usable_kwh": round(usable_cap, 3),
        "dod_percent": dod,
        "satisfied_days": satisfied,
        "satisfied_days_core": satisfied_core,
        "total_days": n_days,
        "equivalent_days": equivalent,
        "by_month": [{"month": gs.month_str or "—",
                       "satisfied": satisfied, "total": n_days}],
        "note": "satisfied_days 按 DOD 折算+逐对判定（同界面）；"
                "satisfied_days_core 不折算、各段相加后再比（同 core）。两者语义不同。",
    }).model_dump()


class MonthlySpacesRequest(BaseModel):
    session_ids: list[str] = Field(default_factory=list,
                                  description="全部已加载的 session；跨分组按月份聚合")
    segments: list[Segment] | None = None
    periods: list[dict] | None = None
    billing: dict | None = None
    mode: str = "A"
    rated_power: float | None = None
    ignore_negative: bool = False


@router.post("/monthly-spaces", response_model=Envelope,
             description=(
                 "跨分组按**月份**聚合的逐段空间（V4 月度表的输入）。\n\n"
                 "**同月份会被后来者覆盖** —— 与 Fusion 的 `monthly_data[month_str] = data`\n"
                 "一致。两个文件都是 2026-01 时，表里只有一行（后加载的那个）。\n"
                 "这不是 bug，是既有行为；不要「聪明地」改成两行。\n\n"
                 "粒度归一：内部调 `services/timeline.unify_time_resolution`，\n"
                 "把各月插值到点数最多的那个粒度。每行带 `resolution`（**原始**点数），\n"
                 "用来回答「这月和那月可比吗」。\n\n"
                 "`min_segment` 口径：本月各段中最小的那一段（充+放），与列名「最小段空间」一致。\n"
                 "全零段列会被隐藏，隐藏数量在 `hidden_zero_segments`。"
             ))
def monthly_spaces(req: MonthlySpacesRequest):
    sp = SpacesRequest(session_id="", group=0, segments=req.segments,
                       periods=req.periods, billing=req.billing, mode=req.mode,
                       rated_power=req.rated_power, ignore_negative=req.ignore_negative)
    segs = _to_internal_segments(sp)
    if not segs:
        return _err("CALC_INVALID_SEGMENT", "没有有效时段，请传 segments 或 periods")

    if not req.session_ids:
        return _err("CALC_INVALID_SEGMENT", "session_ids 不能为空")

    # ① 逐 session 逐 group 收集，按 month_str 建 dict（后加载覆盖前者，同 Fusion）
    #    注意：拷贝 load_array。unify_time_resolution 是原地修改，
    #    直接引用 provider 的返回会污染它的缓存，影响后续调用。
    monthly: dict[str, dict] = {}
    raw_resolution: dict[str, int] = {}
    # 来源追踪：同月份赋值是静默覆盖，痕迹不记就彻底没了。
    # 用户看到「2 个文件同月」后第一个问题一定是「哪两个」，所以连文件名一起存。
    sources: dict[str, list[str]] = {}
    for sid in req.session_ids:
        sess = get_session(sid)
        if not sess:
            continue
        for gi, gd in enumerate(sess.get("groups", [])):
            try:
                # 走统一取数口：模拟 session 的数据在内存里，文件 session 走 provider。
                # 这里不直接调 provider——否则模拟数据进不了月度表，而且不会报错。
                gs, data = load_group_data(sid, gi)
            except ResolveError:
                continue
            _ = gd  # 只为枚举下标；数据从统一入口取
            if not data or data.get("load_array") is None:
                continue
            arr = np.asarray(data["load_array"], dtype=float)
            if arr.ndim != 2:
                continue
            key = gs.month_str or "—"
            monthly[key] = {
                "load_array": np.array(arr, dtype=float, copy=True),
                "time_labels": [str(x) for x in data.get("time_labels", [])],
                "month_str": gs.month_str,
                "asset": gs.asset_value,
            }
            raw_resolution[key] = int(arr.shape[1])
            # 用**文件名**而不是 asset_value 做来源标识：
            # 没有资产列时 asset_value 恒为「未命名」，两个来源会看不出区别。
            # 用户要回答的是「哪两个文件同月」，文件名才是答案。
            fname = os.path.basename(getattr(gs, "file_path", "") or "")
            label = fname or str(gs.asset_value or key)
            sources.setdefault(key, []).append(label)

    if not monthly:
        return _err("CALC_NO_DATA", "没有可用的月度数据")

    # ② 粒度归一（各月插到最细）
    unify_time_resolution(monthly)

    # ③ 逐月算段空间
    per_month: list[dict] = []
    labels: list[str] = []
    for month in sorted(monthly.keys()):
        data = monthly[month]
        load = data["load_array"]
        tl = data["time_labels"]
        try:
            ch, dis, cl, dl = compute_segmented_spaces(
                load, tl, segs, billing=req.billing, mode=req.mode,
                rated_power=req.rated_power, ignore_negative=req.ignore_negative)
        except Exception as e:
            log.warning("月度空间：%s 测算失败 %s", month, e)
            continue

        use_labels = cl if len(cl) >= len(dl) else dl
        if not labels:
            labels = list(use_labels)

        n = max(len(ch), len(dis))
        totals: list[float] = []
        for i in range(n):
            c_sum = (float(np.nansum(np.nan_to_num(np.asarray(ch[i], dtype=float), nan=0.0)))
                     if i < len(ch) else 0.0)
            d_sum = (float(np.nansum(np.nan_to_num(np.asarray(dis[i], dtype=float), nan=0.0)))
                     if i < len(dis) else 0.0)
            totals.append(round(c_sum + d_sum, 2))

        per_month.append({
            "month": month,
            "resolution": raw_resolution.get(month, 0),
            "asset": data.get("asset"),
            "totals": totals,
            "sources": sources.get(month, []),
        })

    if not per_month:
        return _err("CALC_NO_DATA", "所有月份的测算都失败了")

    # ④ 去全零段列（与 Fusion 的 C4 一致：该段在所有月份都为 0 则隐藏）
    n_seg = max(len(m["totals"]) for m in per_month)
    keep = []
    for i in range(n_seg):
        if any(i < len(m["totals"]) and m["totals"][i] != 0.0 for m in per_month):
            keep.append(i)
    hidden = n_seg - len(keep)

    months_out = []
    for m in per_month:
        vals = [m["totals"][i] if i < len(m["totals"]) else 0.0 for i in keep]
        months_out.append({
            "month": m["month"],
            "resolution": m["resolution"],
            "asset": m.get("asset"),
            "sources": m["sources"],
            # 同月份后者覆盖前者：>1 时 UI 用副标「N 个文件同月，本行取后加载的那个」。
            # 措辞不叫「合并」——用户会以为数据相加。
            "source_count": len(m["sources"]),
            "segments": [
                {"label": labels[i] if i < len(labels) else f"段{i+1}", "value": v}
                for i, v in zip(keep, vals)
            ],
            # 口径：本月各段中最小的那一段（充+放）
            "min_segment": min(vals) if vals else 0.0,
        })

    return Envelope(ok=True, data={
        "months": months_out,
        "columns": [labels[i] if i < len(labels) else f"段{i+1}" for i in keep],
        "hidden_zero_segments": hidden,
        "min_segment_label": "最小段空间",
        "note": "同月份以后加载者为准（与 Fusion 一致）；"
                "source_count>1 表示该月有多个文件，本行取后加载的那个；"
                "resolution 是该月原始点数，插值后已统一到最细粒度。",
    }).model_dump()


@router.post("/capacity", response_model=Envelope,
             description=(
                 "容量与吞吐量测算。\n\n"
                 "**量纲警告**：返回两个不同量纲的量，不可互换、不可比较。\n\n"
                 "· `daily_demand.value_kwh` —— 单位 kWh，回答「电池该装多大」\n"
                 "· `daily_throughput_kwh_per_day` —— 单位 kWh/天，回答「一天能搬多少能量」\n\n"
                 "单段配置（只有充或缺放）时吞吐量按 0 计算，属正常，会带 hints 说明。\n\n"
                 "**入参与 /spaces 同构**：session_id / group / segments / mode / rated_power / ignore_negative，\n"
                 "额外多 basis（min_feasible | full_load）、target_days、dod_percent。\n\n"
                 "**segments[].start / end 是 float 小时**（0.0 / 8.0），不是 '00:00' 字符串。\n"
                 "**mode A 要 billing.value、B / C 要 rated_power** ——\n"
                 "缺参数时该方向的空间为 0 而不报错，容易让人误以为「算对了结果就是 0」。"
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
