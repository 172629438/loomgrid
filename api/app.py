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
import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from api.routes_calc import router as calc_router
from api.routes_export import router as export_router
from api.routes_ingest import router as ingest_router
from api.routes_plot import router as plot_router
from api.routes_preview import router as preview_router
from api.routes_simulate import router as simulate_router
from api.ws_progress import router as ws_router
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

# CORS：前端页面跑在 http://127.0.0.1:8899（静态服务）或 file://，
# 而 API 跑在 8000 —— 跨源。允许本机任意端口，不开放给外部来源。
# 只允许开发期静态服务与桌面壳（pywebview 的 file:// origin 是 "null"）。
# 打包后如果壳改用 http://127.0.0.1:<port>，把那个来源加进这个列表。
ALLOWED_ORIGINS = [
    "http://127.0.0.1:8899",
    "http://localhost:8899",
    "null",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)

app.include_router(ingest_router)
app.include_router(preview_router)
app.include_router(calc_router)
app.include_router(plot_router)
app.include_router(export_router)
app.include_router(simulate_router)
app.include_router(ws_router)



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
            "POST /api/ingest/allow", "POST /api/ingest/inspect", "POST /api/ingest/mapping",
            "GET /api/preview", "POST /api/calc/spaces", "POST /api/calc/capacity",
            "POST /api/plot/fit",
            "POST /api/export/run", "GET /api/export/status", "GET /api/export/report",
            "GET /api/simulate/templates", "POST /api/simulate/load",
            "POST /api/simulate/apply",
            "WS /ws/progress",
        ],
    }).model_dump()


# ── 根路径 ──────────────────────────────────────────────────
# 这里曾经 307 跳到 /views/v1-workbench.html —— 那个页面随 web/ 一起删了，
# 留着就成了死链接：调试时访问 8000/ 会 404，WebView2 导航到根也会撞墙。
# 改成一个自描述的 JSON：说清「界面在哪、后端在哪」，不给 api 加行为。
@app.get("/", include_in_schema=False)
def root():
    return {
        "app": "LoomGrid Pro backend",
        "note": "界面在 WinUI 3 应用中；这里是后端 API",
        "health": "/api/health",
        "docs": "/docs",
    }


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    """空 favicon：避免控制台一条 404。真图标以后再补。"""
    from fastapi.responses import Response
    return Response(status_code=204)


# ── 前端静态文件 ────────────────────────────────────────────
# 开发期用 python -m http.server 跑 web/，但打包成 exe 后没有那个服务 ——
# 必须由 FastAPI 自己提供。**挂载必须放在所有路由定义之后**：
# app.mount("/") 按注册顺序匹配，挂在前面会把 /api/health 这类路由盖住。
# 目录来源：环境变量 LGM_WEB_DIR（launcher 会设），否则回落到仓库里的 web/。
_WEB_DIR = os.environ.get("LGM_WEB_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
if os.path.isdir(_WEB_DIR):
    app.mount("/", StaticFiles(directory=_WEB_DIR, html=True), name="web")
