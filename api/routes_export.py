# -*- coding: utf-8 -*-
"""导出端点：批量出图、取报告。

对应真值表 ⑥ ⑦。

当前是**同步执行**——请求返回时图已经出完。真值是异步（立刻返回 task_id，
进度走 WebSocket），但异步需要任务队列与 ws-progress 配合，那一步还没做。
同步版能跑通完整链路，先让 curl 验收过闸门，异步化留到 ws-progress 那一步。
"""
from __future__ import annotations

import logging
import os
import uuid

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from api.routes_ingest import get_session
from api.schemas import Envelope
from core.models import GroupSummary
from services.export import export_midwide, generate_report, plot_group
from services.provider import DataProvider

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/export", tags=["export"])

_PROVIDER = DataProvider()

# task_id -> {"output_dir","files","failed","report","groups"}
_TASKS: dict[str, dict] = {}


def _err(code: str, message: str, detail: dict | None = None) -> dict:
    return Envelope(ok=False, error={"code": code, "message": message,
                                     "detail": detail}).model_dump()


class ExportRunRequest(BaseModel):
    session_id: str
    output_dir: str = Field(description="输出目录，不存在会自动创建")
    groups: list[int] | None = Field(default=None, description="要导出的分组序号；省略=全部")
    title_template: str = "{asset} {month}"
    filename_template: str = "{asset}_{month}"
    multiplier: float = 1.0
    plot_options: dict | None = None
    billing: dict | None = None
    price_periods: list | None = None
    export_midwide: bool = False


@router.post("/run", response_model=Envelope)
def run(req: ExportRunRequest):
    """批量出图（同步）。返回文件清单与 task_id。"""
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

    files: list[str] = []
    failed: list[dict] = []
    summaries: list[GroupSummary] = []

    for i in idxs:
        try:
            gs = GroupSummary(**summaries_raw[i])
        except Exception as e:
            failed.append({"group": i, "error": f"分组信息不完整：{e}"})
            continue
        summaries.append(gs)
        data = _PROVIDER.get_group_data(gs)
        if not data:
            failed.append({"group": i, "error": _PROVIDER.get_load_error(gs) or "无数据"})
            continue
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
        except Exception as e:
            log.exception("出图失败 group=%s", i)
            failed.append({"group": i, "error": f"{type(e).__name__}: {e}"})

    report_path = None
    try:
        report_path = generate_report(files, failed, summaries, req.output_dir)
    except Exception as e:
        log.exception("报告生成失败")
        failed.append({"group": -1, "error": f"报告生成失败：{e}"})

    tid = uuid.uuid4().hex
    _TASKS[tid] = {"output_dir": req.output_dir, "files": files,
                   "failed": failed, "report": report_path, "groups": idxs}

    return Envelope(ok=True, data={
        "task_id": tid,
        "total": len(idxs),
        "succeeded": len(files),
        "failed": failed,
        "files": [os.path.basename(f) for f in files],
        "output_dir": req.output_dir,
        "report": os.path.basename(report_path) if report_path else None,
    }).model_dump()


@router.get("/report", response_class=HTMLResponse)
def report(task_id: str):
    """取某个任务的 HTML 报告。"""
    task = _TASKS.get(task_id)
    if not task or not task.get("report") or not os.path.exists(task["report"]):
        return HTMLResponse(
            content="<h1>报告不存在</h1><p>task_id 无效或报告尚未生成。</p>",
            status_code=404)
    with open(task["report"], encoding="utf-8") as f:
        return HTMLResponse(content=f.read())
