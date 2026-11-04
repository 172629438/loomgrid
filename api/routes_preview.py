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

from api.routes_ingest import get_session
from api.schemas import Envelope, ErrorBody
from core.models import GroupSummary
from services.provider import DataProvider

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["preview"])

# 单进程内复用，避免每次请求重建缓存
_PROVIDER = DataProvider()


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
    sess = get_session(session_id)
    if not sess:
        return _err("PREVIEW_GROUP_NOT_FOUND", f"session 不存在或已过期：{session_id}")

    groups = sess.get("groups", [])
    if not groups or group < 0 or group >= len(groups):
        return _err("PREVIEW_GROUP_NOT_FOUND", f"分组序号越界：{group}（共 {len(groups)} 组）")

    try:
        gs = GroupSummary.from_dict(groups[group])
    except Exception as e:
        return _err("PREVIEW_NO_DATA", f"分组信息不完整：{e}")

    data = _PROVIDER.get_group_data(gs)
    if not data or data.get("load_array") is None:
        return _err("PREVIEW_NO_DATA",
                    _PROVIDER.get_load_error(gs) or "该分组没有可用的宽表数据")

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
