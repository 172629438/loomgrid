# -*- coding: utf-8 -*-
"""预览端点：取某个分组的宽表，供前端画曲线。

对应真值表 ③。

为什么标签放响应头而不是塞进二进制体
------------------------------------
矩阵是数值、标签是字符串。混在一个二进制体里要自己定偏移与编码，
反而容易错；96 个时刻 + 31 个日期约 700 字节，响应头扛得住。
"""
from __future__ import annotations

import io
import json
import logging

import numpy as np
from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from api.schemas import Envelope, ErrorBody
from api.session_data import ResolveError
from api.session_data import load_group as load_group_data

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["preview"])


# ── 响应模型 ─────────────────────────────────────────────
# 为什么要写：Envelope.data 是 Any，OpenAPI 里它的 schema 是空 object，
# 前端只能「实测一次真实响应再照抄」—— 这条路已经错过四次
# （format_type / confidence 层级 / load_col / shape）。
# 声明之后 schema 可查，不靠猜。纯注解，不碰任何计算逻辑与返回结构。

class PreviewGroupInfo(BaseModel):
    asset: str
    month: str


class PreviewData(BaseModel):
    group: PreviewGroupInfo
    date_labels: list[str]
    time_labels: list[str]
    load_array: list[list[float]] = Field(description="二维数组，形状 (天 × 点)")
    shape: list[int] = Field(
        description="[rows, cols]，**是数组不是字符串**。"
                    "响应头 X-Shape（仅 format=bin）才是 '31x96' 字符串，两个不同的东西。")


class PreviewEnvelope(BaseModel):
    """不复用 Envelope：它 data 是 Any，声明不到字段；泛型化会影响全部端点。"""
    ok: bool
    data: PreviewData | None = None
    error: ErrorBody | None = None


def _err(code: str, message: str, detail: dict | None = None) -> dict:
    return Envelope(ok=False, error={"code": code, "message": message,
                                     "detail": detail}).model_dump()


@router.get("/preview", response_model=PreviewEnvelope,
             description=(
                 "session_id + 分组序号 → 宽表。\n\n"
                 "format=json：普通 JSON（shape 是数组 [rows, cols]）\n"
                 "format=bin：uint32 rows + uint32 cols + float32 数据，"
                 "标签在响应头（X-Shape 是字符串 '31x96'，与 json 里的 shape 不是同一个东西）"
             ))
def preview(session_id: str, group: int = 0, format: str = "json"):
    """session_id + 分组序号 → 宽表。

    format=json  普通 JSON（小数据、调试用）
    format=bin   二进制：uint32 rows + uint32 cols + float32 数据，标签在响应头
    """
    # 单入口取数：文件来源走 provider，模拟来源直接取内存里已经生成好的数组。
    # 原实现这里自己查 session + 自己调 provider，与 routes_calc 重复了一份，
    # 靠「两处都改」才不会漏——模拟来源引入后这种重复就会变成「预览不跟着变」。
    try:
        gs, data = load_group_data(session_id, group)
    except ResolveError as e:
        code = e.code if e.code == "PREVIEW_GROUP_NOT_FOUND" else "PREVIEW_NO_DATA"
        return _err(code, e.message)

    arr = np.asarray(data["load_array"], dtype=np.float32)
    date_labels = [str(x) for x in data.get("date_labels", [])]
    time_labels = [str(x) for x in data.get("time_labels", [])]

    if format == "bin":
        buf = io.BytesIO()
        buf.write(np.uint32(arr.shape[0]).tobytes())
        buf.write(np.uint32(arr.shape[1]).tobytes())
        buf.write(np.ascontiguousarray(arr).tobytes())
        return Response(
            content=buf.getvalue(),
            media_type="application/octet-stream",
            headers={
                "X-Date-Labels": json.dumps(date_labels, ensure_ascii=False),
                "X-Time-Labels": json.dumps(time_labels, ensure_ascii=False),
                "X-Shape": f"{arr.shape[0]}x{arr.shape[1]}",
                "X-Dtype": "float32",
                "X-Byte-Order": "little-endian",
            },
        )

    return Envelope(ok=True, data={
        "group": {"asset": gs.asset_value, "month": gs.month_str},
        "date_labels": date_labels,
        "time_labels": time_labels,
        "load_array": arr.tolist(),
        "shape": list(arr.shape),
    }).model_dump()
