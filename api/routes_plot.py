# -*- coding: utf-8 -*-
"""绘图辅助端点：把 plotting/ 的纯计算能力暴露给前端。

为什么有这个文件
----------------
CurveViewer 要在界面上画拟合曲线，而 ScottPlot 只自带线性回归。
如果 C# 侧再实现一份多项式拟合，项目里就有第三份同逻辑实现——
「界面预览」与「导出 PNG」可能给出两条略有差异的曲线，用户不知道该信哪张。

所以：拟合一律由 plotting.curves._compute_fits 计算，前端只负责画。

约束
----
· 本文件不含任何算法，只做「取数据 → 调现有函数 → 包 Envelope」
· _compute_fits 的输入形态 (x, y2d, types) 与 _draw_curves_core 内部完全一致，
  这里不改变它的调用方式
· types 里 scipy 相关的两项（gaussian / spline）在缺 scipy 时会被静默跳过——
  这里必须把它说出来（available / unavailable / hints），不让前端看见空结果
"""
from __future__ import annotations

import logging

import numpy as np
from fastapi import APIRouter
from pydantic import BaseModel, Field

from api.routes_calc import _err, _load_group
from api.schemas import Envelope
from plotting.curves import _compute_fits

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/plot", tags=["plot"])

# _compute_fits 支持的全部类型（顺序即推荐展示顺序）
_SUPPORTED = ("poly2", "poly3", "moving_avg", "gaussian", "spline")
# 这两项依赖 scipy
_SCIPY_TYPES = ("gaussian", "spline")


class FitRequest(BaseModel):
    session_id: str
    group: int = 0
    types: list[str] = Field(
        default_factory=lambda: ["poly2"],
        description=f"拟合类型，可选：{', '.join(_SUPPORTED)}")


class FitCurve(BaseModel):
    key: str = Field(description="poly2 / poly3 / moving_avg / gaussian / spline")
    label: str = Field(description="给人看的名称，如「二次拟合」")
    values: list[float | None] = Field(
        description="与 x 等长；null 表示该点为断点（原数据缺失，曲线应断开）")


class FitData(BaseModel):
    x: list[float] = Field(description="横轴（点序号，与绘图时 arange 一致）")
    fits: list[FitCurve] = Field(default_factory=list)
    requested: list[str] = Field(default_factory=list)
    available: list[str] = Field(default_factory=list)
    unavailable: list[str] = Field(
        default_factory=list,
        description="请求了但没算出来的类型——数据不足或依赖缺失，原因见 hints")
    hints: list[str] = Field(default_factory=list)


def _nan_to_none(arr) -> list[float | None]:
    """numpy 数组转 JSON 友好列表：NaN → null（JSON 不支持 NaN 字面量）。"""
    out: list[float | None] = []
    for v in np.asarray(arr, dtype=float).ravel():
        out.append(None if np.isnan(v) else float(v))
    return out


@router.post(
    "/fit",
    response_model=Envelope,
    description=(
        "计算负荷曲线的拟合线。\n\n"
        "**算法不在这里**：直接调用 `plotting.curves._compute_fits`，"
        "与导出 PNG 用的是同一份实现——保证界面预览和导出图一致。\n\n"
        "**数据来自服务端会话**（session_id + group），不接受前端上传大数组："
        "这样与 /api/calc/* 取的是同一份 load_array，不会出现「预览用的是前端缓存、"
        "导出用的是服务端重读」的偏差。\n\n"
        "**返回三组类型状态**：requested（请求的）/ available（算出来的）/ "
        "unavailable（没算出来的）。unavailable 不为空时看 hints，"
        "里面会写明是「天数不足」还是「scipy 未安装」。\n\n"
        "值为 null 表示该点原数据缺失，画线时应断开而不是连成直线。"
    ),
)
def fit(req: FitRequest):
    unknown = [t for t in req.types if t not in _SUPPORTED]
    if unknown:
        return _err(
            "PLOT_INVALID_FIT_TYPE",
            f"不支持的拟合类型：{unknown}",
            {"supported": list(_SUPPORTED)})

    types = [t for t in _SUPPORTED if t in req.types]  # 去重并按固定顺序
    if not types:
        return _err("PLOT_INVALID_FIT_TYPE", "types 不能为空",
                    {"supported": list(_SUPPORTED)})

    gs, data, err = _load_group(req.session_id, req.group)
    if err:
        return err

    load_array = np.asarray(data["load_array"], dtype=float)
    if load_array.ndim != 2 or load_array.shape[1] < 2:
        return _err("PLOT_NO_DATA", "该分组数据不足以计算拟合",
                    {"shape": list(load_array.shape)})

    x = np.arange(load_array.shape[1], dtype=float)
    raw = _compute_fits(x, load_array, types)

    fits = [
        FitCurve(key=key, label=label, values=_nan_to_none(curve))
        for key, (curve, label) in raw.items()
    ]
    available = [f.key for f in fits]
    unavailable = [t for t in types if t not in available]

    hints: list[str] = []
    if unavailable:
        try:
            import scipy  # noqa: F401
            scipy_ok = True
        except ImportError:
            scipy_ok = False

        missing_scipy = [t for t in unavailable if t in _SCIPY_TYPES]
        if missing_scipy and not scipy_ok:
            hints.append(
                f"未安装 scipy，{'、'.join(missing_scipy)} 不可用；"
                "其余拟合正常。安装 scipy 后这些类型会自动出现。")

        n_dates = int(load_array.shape[0])
        for t in unavailable:
            if t == "poly2" and n_dates < 3:
                hints.append(f"二次拟合至少需要 3 天数据，当前 {n_dates} 天。")
            elif t == "poly3" and n_dates < 4:
                hints.append(f"三次拟合至少需要 4 天数据，当前 {n_dates} 天。")
            elif t == "spline" and n_dates < 4:
                hints.append(f"样条插值至少需要 4 天数据，当前 {n_dates} 天。")
        if not hints:
            hints.append("部分拟合类型未产生结果，通常是该列有效数据点不足。")

    return Envelope(ok=True, data=FitData(
        x=x.tolist(),
        fits=fits,
        requested=types,
        available=available,
        unavailable=unavailable,
        hints=hints,
    ).model_dump()).model_dump()
