# -*- coding: utf-8 -*-
"""FastAPI 应用：阶段 2 的 API 层。

分层规则：api/ 不得 import 任何 UI 框架（PySide6 等）。
它只做「把 services 与 core 的能力暴露成 HTTP」，不含业务逻辑。

启动：
    python -m uvicorn api.app:app --reload --port 8000

验收（硬闸门）：不用前端，纯 curl 跑通完整流程。
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from api.routes_calc import router as calc_router
from api.routes_export import router as export_router
from api.routes_ingest import router as ingest_router
from api.routes_preview import router as preview_router
from api.schemas import Envelope

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(
    title="LoomGrid Fusion API",
    version="3.0.0",
    description=(
        "负荷曲线批量生成工具的后端接口。\n\n"
        "**量纲警告**：/api/calc/capacity 同时返回 `daily_demand`（kWh，电池该装多大）"
        "与 `daily_throughput_kwh_per_day`（kWh/天，一天能搬多少能量）——"
        "两者不可互换、不可比较。"
    ),
)

app.include_router(ingest_router)
app.include_router(preview_router)
app.include_router(calc_router)
app.include_router(export_router)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    """未预期异常也走统一包络，不把堆栈直接吐给前端。"""
    logging.getLogger(__name__).exception("unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content=Envelope(ok=False, error={
            "code": "INTERNAL_ERROR",
            "message": f"服务端异常：{type(exc).__name__}",
            "detail": {"path": request.url.path},
        }).model_dump(),
    )


@app.get("/api/health", response_model=Envelope, tags=["meta"])
def health():
    """存活探针：同时确认核心模块可导入（分层破了这里会红）。"""
    from core.ingest import _smart_classify_columns  # noqa: F401
    from services.capacity import DemandBasis  # noqa: F401
    from services.config import Config  # noqa: F401
    from services.provider import DataProvider  # noqa: F401
    return Envelope(ok=True, data={
        "status": "ok",
        "modules": ["core.ingest", "services.config", "services.provider", "services.capacity"],
        "demand_basis": [b.value for b in DemandBasis],
        "endpoints": [
            "POST /api/ingest/inspect", "POST /api/ingest/mapping",
            "GET /api/preview", "POST /api/calc/spaces", "POST /api/calc/capacity",
            "POST /api/export/run", "GET /api/export/report",
        ],
    }).model_dump()
