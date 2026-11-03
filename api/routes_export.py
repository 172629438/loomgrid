# -*- coding: utf-8 -*-
"""导出端点：批量出图（异步）+ 取报告。

对应真值表 ⑥ ⑦。

异步形态
--------
POST /api/export/run 提交任务后**立即返回 task_id**，实际执行在后台线程，
进度通过 WS /ws/progress?task_id=... 推送。

四阶段事件与 ProcessingThread 的顺序一致：
  stage.parse → stage.scan → stage.preload → stage.generate

⚠️ 调用方注意
-------------
task_id 拿到不代表导出成功。要看：
  · WebSocket 的 done 事件里的 succeeded / failed
  · 或轮询 GET /api/export/status?task_id=...
HTTP 200 只表示任务已受理。
"""
from __future__ import annotations

import logging
import os
import threading

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from api.routes_ingest import get_session
from api.schemas import Envelope
from core.models import GroupSummary
from services.export import (
    DEFAULT_FILENAME_TEMPLATE, DEFAULT_TITLE_TEMPLATE,
    export_midwide, generate_report, plot_group,
)
from services.provider import DataProvider
from services.tasks import TASK_STORE

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/export", tags=["export"])

_PROVIDER = DataProvider()


def _err(code: str, message: str, detail: dict | None = None) -> dict:
    return Envelope(ok=False, error={"code": code, "message": message,
                                     "detail": detail}).model_dump()


class ExportRunRequest(BaseModel):
    session_id: str
    output_dir: str = Field(description="输出目录，不存在会自动创建")
    groups: list[int] | None = Field(default=None, description="要导出的分组序号；省略=全部")
    title_template: str = Field(
        default=DEFAULT_TITLE_TEMPLATE,
        description="标题模板，可用占位符：{table_no} {year_month} {asset} {sheet}")
    filename_template: str = Field(
        default=DEFAULT_FILENAME_TEMPLATE,
        description="文件名模板，同上；未知占位符会原样保留")
    multiplier: float = 1.0
    plot_options: dict | None = None
    billing: dict | None = None
    price_periods: list | None = None
    export_midwide: bool = False


def _execute(tid: str, req: ExportRunRequest, summaries_raw: list[dict], idxs: list[int]):
    """后台线程里跑的实际导出，按四阶段推进。"""
    TASK_STORE.set_status(tid, "running")
    files: list[str] = []
    failed: list[dict] = []
    summaries: list[GroupSummary] = []

    try:
        total = len(idxs)

        # 阶段 1：解析与映射（本任务里是取分组摘要）
        TASK_STORE.emit(tid, "stage.parse", {"stage": "parse", "index": 1, "total": 4})
        TASK_STORE.emit(tid, "progress", {"current": 0, "total": total})

        # 阶段 2：扫描（取宽表数据）
        TASK_STORE.emit(tid, "stage.scan", {"stage": "scan", "index": 2, "total": 4})
        prepared: list[tuple[GroupSummary, dict]] = []
        for i in idxs:
            try:
                # session 里的分组 dict 会随 core 新增字段而变多（如 dates），
                # 直接用 ** 展开会撞上 GroupSummary 的固定参数列表。
                # 按 GroupSummary 的字段白名单过滤，新增字段不影响这里。
                gs = GroupSummary.from_dict(summaries_raw[i])
            except Exception as e:
                failed.append({"group": i, "error": f"分组信息不完整：{e}"})
                continue
            data = _PROVIDER.get_group_data(gs)
            if not data:
                # 诊断字段全带上：这类「未找到月份」的报错，光看一句 message
                # 无法判断是月份错了、资产错了，还是日期列根本没解析出来。
                failed.append({
                    "group": i,
                    "error": _PROVIDER.get_load_error(gs) or "无数据",
                    "diag": {
                        "month_str": gs.month_str,
                        "asset_value": gs.asset_value,
                        "file_path": gs.file_path,
                        "sheet_name": gs.sheet_name,
                        "date_count": gs.date_count,
                        "time_points": gs.time_points,
                        "date_col": (gs.mapping or {}).get("date_col"),
                        "asset_col": (gs.mapping or {}).get("asset_col"),
                        "format_type": (gs.mapping or {}).get("format_type"),
                    },
                })
                continue
            summaries.append(gs)
            prepared.append((gs, data))

        # 阶段 3：预加载（这里已完成，作为阶段标记）
        TASK_STORE.emit(tid, "stage.preload", {"stage": "preload", "index": 3, "total": 4})

        # 阶段 4：图表生成
        TASK_STORE.emit(tid, "stage.generate", {"stage": "generate", "index": 4, "total": 4})
        for n, (gs, data) in enumerate(prepared, 1):
            try:
                p = plot_group(gs, data, req.output_dir,
                               title_template=req.title_template,
                               filename_template=req.filename_template,
                               plot_options=req.plot_options,
                               billing=req.billing,
                               price_periods=req.price_periods,
                               multiplier=req.multiplier)
                files.append(p)
                if req.export_midwide:
                    files.append(export_midwide(gs, data, req.output_dir))
                TASK_STORE.emit(tid, "log", {"level": "info",
                                             "message": f"已生成 {os.path.basename(p)}"})
            except Exception as e:
                log.exception("出图失败 group=%s", gs.asset_value)
                failed.append({"group": gs.asset_value, "error": f"{type(e).__name__}: {e}"})
            TASK_STORE.emit(tid, "progress", {"current": n, "total": len(prepared)})

        report_path = None
        try:
            report_path = generate_report(files, failed, summaries, req.output_dir)
        except Exception as e:
            log.exception("报告生成失败")
            failed.append({"group": -1, "error": f"报告生成失败：{e}"})

        result = {
            "task_id": tid,
            "total": total,
            "succeeded": len(files),
            "failed": failed,
            "files": [os.path.basename(f) for f in files],
            "output_dir": req.output_dir,
            "report": os.path.basename(report_path) if report_path else None,
        }
        TASK_STORE.finish(tid, result)
    except Exception as e:
        log.exception("导出任务异常")
        TASK_STORE.fail(tid, f"{type(e).__name__}: {e}", failed)


@router.post("/run", response_model=Envelope,
             description=(
                 "提交批量出图任务，**立即返回 task_id**（异步）。\n\n"
                 "进度通过 `WS /ws/progress?task_id=...` 推送，四阶段事件：\n"
                 "`stage.parse` → `stage.scan` → `stage.preload` → `stage.generate`，\n"
                 "结束发 `done`，出错发 `stage.error`（payload 里带 failed 明细）。\n\n"
                 "**HTTP 200 只表示任务已受理，不代表导出成功。**\n"
                 "要看 done 事件里的 succeeded / failed，或查 GET /api/export/status。"
             ))
def run(req: ExportRunRequest):
    sess = get_session(req.session_id)
    if not sess:
        return _err("EXPORT_TASK_NOT_FOUND", f"session 不存在：{req.session_id}")

    summaries_raw = sess.get("groups", [])
    if not summaries_raw:
        return _err("EXPORT_PARTIAL_FAILED", "该 session 没有分组")

    try:
        os.makedirs(req.output_dir, exist_ok=True)
        probe = os.path.join(req.output_dir, ".write_probe")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
    except Exception as e:
        return _err("EXPORT_DIR_UNWRITABLE", f"输出目录不可写：{e}",
                    {"output_dir": req.output_dir})

    idxs = req.groups if req.groups is not None else list(range(len(summaries_raw)))
    bad = [i for i in idxs if i < 0 or i >= len(summaries_raw)]
    if bad:
        return _err("EXPORT_PARTIAL_FAILED", f"分组序号越界：{bad}")

    tid = TASK_STORE.create("export")
    th = threading.Thread(target=_execute, args=(tid, req, summaries_raw, idxs),
                          daemon=True, name=f"export-{tid[:8]}")
    th.start()

    return Envelope(ok=True, data={
        "task_id": tid,
        "accepted": True,
        "total": len(idxs),
        "ws": f"/ws/progress?task_id={tid}",
        "status_url": f"/api/export/status?task_id={tid}",
    }).model_dump()


@router.get("/status", response_model=Envelope)
def status(task_id: str):
    """任务状态与结果（WS 不便使用时的兜底）。"""
    t = TASK_STORE.get(task_id)
    if not t:
        return _err("EXPORT_TASK_NOT_FOUND", f"任务不存在或已过期：{task_id}")
    return Envelope(ok=True, data={
        "task_id": task_id,
        "status": t["status"],
        "result": t["result"],
        "error": t["error"],
        "events": len(t["events"]),
    }).model_dump()


@router.get("/report", response_class=HTMLResponse)
def report(task_id: str):
    """取某个任务的 HTML 报告。"""
    t = TASK_STORE.get(task_id)
    if not t or not t.get("result"):
        return HTMLResponse(content="<h1>报告不存在</h1><p>任务未完成或 task_id 无效。</p>",
                            status_code=404)
    rpt = t["result"].get("report")
    outdir = t["result"].get("output_dir")
    if not rpt or not outdir:
        return HTMLResponse(content="<h1>报告不存在</h1><p>该任务没有产出报告。</p>",
                            status_code=404)
    path = os.path.join(outdir, rpt)
    if not os.path.exists(path):
        return HTMLResponse(content="<h1>报告文件已丢失</h1>", status_code=404)
    with open(path, encoding="utf-8") as f:
        return HTMLResponse(content=f.read())
