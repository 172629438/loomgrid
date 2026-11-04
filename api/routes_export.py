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

from api.routes_ingest import _forbidden_roots, _norm, get_session
from api.schemas import Envelope
from core.models import GroupSummary
from services.export import (
    DEFAULT_FILENAME_TEMPLATE, DEFAULT_TITLE_TEMPLATE,
    export_midwide, generate_report, plot_group,
)
from services.provider import DataProvider
from services.tasks import TASK_STORE
from plotting.naming import generate_filename, parse_title_template

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/export", tags=["export"])

_PROVIDER = DataProvider()


def _err(code: str, message: str, detail: dict | None = None) -> dict:
    return Envelope(ok=False, error={"code": code, "message": message,
                                     "detail": detail}).model_dump()


class GroupOverride(BaseModel):
    """单个分组的参数覆盖。字段为 None 时用请求里的全局值。"""
    index: int = Field(description="分组序号，与 groups 里的值对应")
    multiplier: float | None = Field(default=None, description="None = 用全局 multiplier")
    billing: dict | None = Field(default=None, description="None = 用全局 billing")
    plot_options: dict | None = Field(default=None, description="None = 用全局 plot_options")


class ExportRunRequest(BaseModel):
    """批量导出请求。

    ⚠️ group_overrides 是**可选扩展**：
      不传 → 行为与加这个字段之前一字不差（整批用全局 multiplier/billing/plot_options）
      传了 → 按 index 覆盖；不在列表里的分组仍用全局值；
             列表里出现 groups 之外的 index → 忽略（宽容，不报错）
    为什么需要它：Fusion 的导出对话框能逐行改倍率并批量编辑，
    而 multiplier 是标量——不提供分组级覆盖就是功能倒退。
    """
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
    group_overrides: list[GroupOverride] | None = Field(
        default=None,
        description="分组级参数覆盖（可选）；不传则完全等同原有的整批行为")


def _execute(tid: str, req: ExportRunRequest, summaries_raw: list[dict], idxs: list[int]):
    """后台线程里跑的实际导出，按四阶段推进。"""
    TASK_STORE.set_status(tid, "running")
    files: list[str] = []
    # 显式产物清单：kind 由生产方标注，不让下游靠扩展名或顺序猜。
    # files 保持原样（向后兼容），artifacts 是新增的并行结构。
    artifacts: list[dict] = []
    failed: list[dict] = []
    summaries: list[GroupSummary] = []
    # 成功的**分组数**。与 succeeded（产物数）不同量纲，带宽表时两者不等。
    n_ok = 0

    try:
        total = len(idxs)

        # 阶段 1：解析与映射（本任务里是取分组摘要）
        TASK_STORE.emit(tid, "stage.parse", {"stage": "parse", "index": 1, "total": 4})
        TASK_STORE.emit(tid, "progress", {"current": 0, "total": total})

        # 阶段 2：扫描（取宽表数据）
        TASK_STORE.emit(tid, "stage.scan", {"stage": "scan", "index": 2, "total": 4})
        prepared: list[tuple[int, GroupSummary, dict]] = []
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
            prepared.append((i, gs, data))

        # 阶段 3：预加载（这里已完成，作为阶段标记）
        TASK_STORE.emit(tid, "stage.preload", {"stage": "preload", "index": 3, "total": 4})

        # 阶段 4：图表生成
        TASK_STORE.emit(tid, "stage.generate", {"stage": "generate", "index": 4, "total": 4})

        # group_overrides 按 index 查找；未命中或字段为 None 时回落到全局值。
        # 找不到就返回 None，不报错——宽松处理，不让一个多余的 index 毁掉整批。
        overrides = {o.index: o for o in (req.group_overrides or [])}

        for n, (idx, gs, data) in enumerate(prepared, 1):
            ov = overrides.get(idx)
            mult = ov.multiplier if ov and ov.multiplier is not None else req.multiplier
            bill = ov.billing if ov and ov.billing is not None else req.billing
            opts = ov.plot_options if ov and ov.plot_options is not None else req.plot_options
            p = None  # 供 except 判断「PNG 是否已落盘」
            try:
                p = plot_group(gs, data, req.output_dir,
                               title_template=req.title_template,
                               filename_template=req.filename_template,
                               plot_options=opts,
                               billing=bill,
                               price_periods=req.price_periods,
                               multiplier=mult)
                files.append(p)
                artifacts.append({"name": os.path.basename(p), "kind": "chart"})
                if req.export_midwide:
                    _mw = export_midwide(gs, data, req.output_dir)
                    files.append(_mw)
                    artifacts.append({"name": os.path.basename(_mw), "kind": "midwide"})
                n_ok += 1
                TASK_STORE.emit(tid, "log", {"level": "info",
                                             "message": f"已生成 {os.path.basename(p)}"})
            except Exception as e:
                log.exception("出图失败 group=%s", gs.asset_value)
                # 宽表或后续步骤失败时，PNG 可能已经落盘。不说清楚的话，
                # 运维会看到「有图却说失败」，不知道该删哪一个。
                hint = "（图表已生成并落盘，但后续产物失败）" if p else ""
                failed.append({"group": gs.asset_value,
                               "error": f"{type(e).__name__}: {e}{hint}"})
            TASK_STORE.emit(tid, "progress", {"current": n, "total": len(prepared)})

        report_path = None
        try:
            report_path = generate_report(files, failed, summaries, req.output_dir)
        except Exception as e:
            log.exception("报告生成失败")
            failed.append({"group": -1, "error": f"报告生成失败：{e}"})

        result = {
            "task_id": tid,
            "total": total,                     # 本次要处理的**分组数**
            # 产物数（PNG + 宽表合计），不是分组数。
            # 分组口径看 groups_succeeded / groups_failed —— 两个量纲不同，不要相除。
            "succeeded": len(files),
            "groups_succeeded": n_ok,
            "groups_failed": len(failed),
            "failed": failed,
            "files": [os.path.basename(f) for f in files],
            # 显式产物类型：chart / midwide。下游按 kind 分流，不看扩展名、不看顺序。
            "artifacts": artifacts,
            "output_dir": req.output_dir,
            "report": os.path.basename(report_path) if report_path else None,
        }
        TASK_STORE.finish(tid, result)
    except Exception as e:
        log.exception("导出任务异常")
        TASK_STORE.fail(tid, f"{type(e).__name__}: {e}", failed)


# 拒绝往系统目录写 —— output_dir 不限死（用户要能选任意目录），
# 但 Windows / Program Files 这类位置写进去毫无用处，且风险更大。
_FORBIDDEN_DIRS = [
    os.environ.get("SystemRoot", r"C:\Windows"),
    r"C:\Program Files",
    r"C:\Program Files (x86)",
    r"C:\ProgramData",
]


def _output_dir_allowed(d: str) -> bool:
    try:
        real = os.path.realpath(d)
    except Exception:
        return False
    low = real.lower()
    for f in _FORBIDDEN_DIRS:
        if not f:
            continue
        try:
            ff = os.path.realpath(f).lower()
        except Exception:
            continue
        if low == ff or low.startswith(ff + os.sep):
            return False
    return True

class PreviewRequest(BaseModel):
    session_id: str
    groups: list[int] | None = Field(default=None, description="要预览的分组序号；省略=全部")
    title_template: str = DEFAULT_TITLE_TEMPLATE
    filename_template: str = DEFAULT_FILENAME_TEMPLATE


@router.post("/preview", response_model=Envelope,
             description=(
                 "预览每个分组将生成的标题与文件名。\n\n"
                 "**命名逻辑不在这里实现**：直接调 `plotting.naming` 的\n"
                 "`parse_title_template` 与 `generate_filename`，与真正落盘时同一份代码。\n\n"
                 "占位符：`{table_no}` `{year_month}` `{asset}` `{sheet}`；\n"
                 "未知占位符原样保留；文件名会过 `_sanitize_name`\n"
                 "（非法字符转下划线、Windows 保留名加后缀、超长截断）。\n\n"
                 "前端只负责展示，不得在 C# 里重写一遍清洗逻辑。"
             ))
def preview(req: PreviewRequest):
    sess = get_session(req.session_id)
    if not sess:
        return _err("EXPORT_SESSION_NOT_FOUND", f"session 不存在：{req.session_id}")

    raw_groups = sess.get("groups", [])
    idxs = req.groups if req.groups is not None else list(range(len(raw_groups)))

    items = []
    for i in idxs:
        if i < 0 or i >= len(raw_groups):
            continue
        gs = GroupSummary.from_dict(raw_groups[i])
        ctx = {
            "year_month": gs.month_str or "",
            "table_no": gs.asset_value or "未命名",
            "asset": gs.asset_value or "未命名",
            "sheet": gs.sheet_name or "",
        }
        items.append({
            "group_index": i,
            "asset": gs.asset_value or "未命名",
            "month": gs.month_str or "",
            "title": parse_title_template(req.title_template, ctx),
            "filename": generate_filename(req.filename_template, ctx),
        })

    return Envelope(ok=True, data={"items": items, "total": len(items)}).model_dump()


# ── 开发工具：从已有目录反推结果（默认不注册）─────────────
# 用途：V6 调样式时反复重跑导出又慢又不稳定，adopt 把已有产物读成一个 task。
# 定位：它是「识别已有产物的读取器」，**不伪造导出结果**——adopted 标记就是为这个。
# 门控：LGM_DEV_TOOLS=1 才注册；没设时这个路由**根本不存在**（不是返回 403）。
# 这样生产包被试探时是 404，而不是一个能扫描磁盘的隐藏端点。
if os.environ.get("LGM_DEV_TOOLS") == "1":

    @router.post("/adopt", response_model=Envelope,
                 description=(
                     "【开发工具，需 LGM_DEV_TOOLS=1 才注册】\n\n"
                     "扫描一个已有输出目录，把里面的 png / xlsx 读成一个 status=done 的临时 task。\n\n"
                     "**它会扫描目录，所以和 /api/ingest/allow 同级**：\n"
                     "系统目录与程序目录一律拒绝。\n\n"
                     "返回的 result 带 `adopted: true` —— 调用方必须让用户知道\n"
                     "「这是从目录读的，不是本次导出的结果」。"
                 ))
    def adopt(payload: dict):
        raw = (payload or {}).get("output_dir")
        if not raw:
            return _err("EXPORT_ADOPT_INVALID", "缺少 output_dir")

        real = os.path.realpath(raw)
        if not os.path.isdir(real):
            return _err("EXPORT_ADOPT_INVALID", f"不是目录：{raw}")

        forbidden = [_norm(f) for f in _forbidden_roots() if _norm(f)]
        real_n = os.path.normcase(real)
        if any(real_n == f or real_n.startswith(f + os.sep) for f in forbidden):
            return _err("EXPORT_ADOPT_FORBIDDEN", "不能在系统目录或程序目录里扫描",
                        {"path": real})

        artifacts: list[dict] = []
        files: list[str] = []
        report_name = None
        for name in sorted(os.listdir(real)):
            p = os.path.join(real, name)
            if not os.path.isfile(p):
                continue
            low = name.lower()
            if low.endswith(".png"):
                kind = "chart"
            elif low.endswith(".xlsx"):
                kind = "midwide"
            elif low.endswith(".html"):
                report_name = name
                continue
            else:
                continue
            files.append(name)
            artifacts.append({"name": name, "kind": kind})

        tid = TASK_STORE.create("export")
        result = {
            "adopted": True,
            "task_id": tid,
            "total": len(files),
            "succeeded": len(files),
            "failed": [],
            "files": files,
            "artifacts": artifacts,
            "output_dir": real,
            "report": report_name,
        }
        TASK_STORE.finish(tid, result)
        log.info("adopt: %s → %d 个产物（%d chart / %d midwide）",
                 real, len(artifacts),
                 sum(1 for a in artifacts if a["kind"] == "chart"),
                 sum(1 for a in artifacts if a["kind"] == "midwide"))
        return Envelope(ok=True, data=result).model_dump()


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
    if not _output_dir_allowed(req.output_dir):
        return _err("EXPORT_OUTPUT_FORBIDDEN",
                    "不能写入系统目录：" + req.output_dir,
                    {"output_dir": req.output_dir})

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
