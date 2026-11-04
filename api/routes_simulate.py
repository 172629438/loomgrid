# -*- coding: utf-8 -*-
"""负荷模拟端点：25 个行业模板 + 参数化曲线生成。

对应 Fusion 的「负荷模拟」对话框（`_open_simulation_dialog`）。

⚠️ 名字陷阱（务必看这里）
------------------------
本端点用的 25 个模板来自 ``services/loadsim.py``（原样搬自 Fusion 主程序）。
``algorithms/loadgen.py`` 里另有一套 **10 个**行业的 ``INDUSTRY_PARAMS``
（par / lf / cv / base_load + 省份基线），**是另一个引擎**，曲线形状不同。
混用会得到「有曲线但不像真实负荷」的结果，且不会报错。

seed 语义
--------
- 请求 ``seed=null``：端点**当场生成一个随机 seed** 并回显为 ``applied_seed``，
  再以该 seed 生成。这样「应用」后用户想复现同一条曲线时，凭 ``applied_seed`` 就能重来。
- 请求带 ``seed``：原样使用，``applied_seed`` 与它一致。
- 前端必须保证「预览」与「应用」传同一个 seed，否则两条曲线不一致 —— 用户会以为程序飘了。

服务层 ``generate_load_profile(cfg, seed=None)`` 在 ``seed=None`` 时仍走全局
``np.random``（与 Fusion 完全一致）；本端点不依赖那个分支，永远显式传 seed。
"""
from __future__ import annotations

import logging

import numpy as np
from fastapi import APIRouter
from pydantic import BaseModel, Field

from api.schemas import Envelope
from services.loadsim import INDUSTRY_TEMPLATES, generate_load_profile

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/simulate", tags=["simulate"])


def _err(code: str, message: str, detail: dict | None = None) -> dict:
    return Envelope(ok=False, error={"code": code, "message": message,
                                     "detail": detail}).model_dump()


class LoadSimRequest(BaseModel):
    industry: str = Field(description="行业模板名（GET /templates 的 key）；未知名称回退到电子制造")
    avg_power_kw: float = Field(default=500.0, gt=0, description="平均功率（kW）")
    daily_load_factor: float | None = Field(
        default=None, gt=0, le=1,
        description="日负荷率（平均/最大）。省略则用模板值")
    peak_valley_ratio: float | None = Field(
        default=None, gt=0, le=1,
        description="峰谷差率。省略则用模板值")
    weekend_factor: float | None = Field(
        default=None, gt=0, le=1,
        description="周末系数。省略则用模板值")
    noise_std: float = Field(default=0.05, ge=0, le=0.5,
                             description="随机噪声标准差比例；预览建议传 0")
    pv_kw: float = Field(default=0.0, ge=0, description="叠加光伏容量（kW），0=不叠加")
    n_days: int = Field(default=365, ge=1, le=366)
    n_points: int = Field(default=96, ge=24, le=288,
                          description="每日点数：24=小时，48=半小时，96=15 分钟")
    seed: int | None = Field(
        default=None,
        description="噪声随机种子。不传则端点生成一个并回显 applied_seed。"
                    "预览与应用必须传同一个 seed，否则两条曲线不一致。")


class TemplateItem(BaseModel):
    key: str
    name: str
    daily_load_factor: float
    peak_valley_ratio: float
    weekend_factor: float
    schedule: str
    morning_peak: list
    evening_peak: list
    noon_valley: list
    description: str = Field(description="数据来源标注（模板可信度依据）")


@router.get("/templates")
def list_templates():
    """25 个行业模板的展示用参数。不含生成逻辑。"""
    items = []
    for key, t in INDUSTRY_TEMPLATES.items():
        items.append(TemplateItem(
            key=key,
            name=t.get("name", key),
            daily_load_factor=float(t["daily_load_factor"]),
            peak_valley_ratio=float(t["peak_valley_ratio"]),
            weekend_factor=float(t["weekend_factor"]),
            schedule=str(t["schedule"]),
            morning_peak=list(t["morning_peak"]),
            evening_peak=list(t["evening_peak"]),
            noon_valley=list(t["noon_valley"]),
            description=str(t.get("source", "")),
        ).model_dump())
    return {"ok": True, "data": {"count": len(items), "templates": items}, "error": None}


@router.post("/load")
def simulate_load(req: LoadSimRequest):
    """按模板生成负荷曲线。

    返回 ``applied_seed``：本次实际使用的随机种子。
    同参数 + 同 applied_seed 可完整复现这条曲线。
    """
    if req.industry not in INDUSTRY_TEMPLATES:
        log.info("模拟：未知行业 %r，回退到 电子制造", req.industry)
    if req.n_points % 1 != 0:
        return _err("SIM_INVALID_POINTS", "n_points 必须是整数")

    applied_seed = int(req.seed) if req.seed is not None else int(np.random.randint(0, 2**31 - 1))

    cfg = {
        "industry": req.industry,
        "avg_power_kw": float(req.avg_power_kw),
        "noise_std": float(req.noise_std),
        "pv_kw": float(req.pv_kw),
        "n_days": int(req.n_days),
        "n_points": int(req.n_points),
    }
    # 只有显式传了才覆盖模板默认值 —— 传 None 表示「用模板自己的参数」。
    if req.daily_load_factor is not None:
        cfg["daily_load_factor"] = float(req.daily_load_factor)
    if req.peak_valley_ratio is not None:
        cfg["peak_valley_ratio"] = float(req.peak_valley_ratio)
    if req.weekend_factor is not None:
        cfg["weekend_factor"] = float(req.weekend_factor)

    try:
        data = generate_load_profile(cfg, seed=applied_seed)
    except Exception as exc:  # noqa: BLE001 - 生成失败要回可读原因，不是 500
        log.exception("模拟生成失败")
        return _err("SIM_GENERATE_FAILED", f"生成失败：{exc}")

    arr = np.asarray(data["load_array"], dtype=float)
    return {
        "ok": True,
        "data": {
            "industry": req.industry if req.industry in INDUSTRY_TEMPLATES else "电子制造",
            "requested_industry": req.industry,
            "load_array": arr.tolist(),
            "time_labels": list(data["time_labels"]),
            "date_labels": list(data["date_labels"]),
            "shape": list(arr.shape),
            "applied_seed": applied_seed,
            "config": cfg,
            "stats": {
                "min": float(np.min(arr)),
                "max": float(np.max(arr)),
                "mean": float(np.mean(arr)),
            },
        },
        "error": None,
    }
