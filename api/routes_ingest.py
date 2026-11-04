# -*- coding: utf-8 -*-
"""摄入端点：文件长什么样、映射生效后扫出什么。

对应真值表 ① ②。
"""
from __future__ import annotations

import logging
import os
import tempfile
import threading
import uuid

import pandas as pd
from fastapi import APIRouter, File, UploadFile

from api.schemas import (
    Confidence, Envelope, GroupInfo, InspectData, InspectMappingData,
    MappingRequest, MappingSuggest, SheetInfo,
)
from core.timegrid import _get_day_type
from core.ingest import (
    _detect_file_format_type, _looks_yearless, _read_file_full, _safe_excel_sheets,
    _scan_file_worker, _smart_classify_columns, get_columns_fast,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ingest", tags=["ingest"])

# 会话状态：session_id -> {"path","sheet","mapping","groups"}
# 单进程内存即可；重启丢失属预期（阶段 2 不做持久化）。
_SESSIONS: dict[str, dict] = {}
_SESSION_LOCK = threading.Lock()

MAX_ROWS_SAMPLE = 200


def _err(code: str, message: str, detail: dict | None = None) -> dict:
    return Envelope(ok=False, error={"code": code, "message": message,
                                     "detail": detail}).model_dump()


def assess_confidence(mapping: dict, df: pd.DataFrame) -> Confidence:
    """推断映射可信度。

    reasons 的措辞直接面向用户——前端会原样展示，所以不写内部标识符。
    这个字段的存在理由：A2 那种「高信心地错」是最危险的，
    让人有机会在低信心时手动确认。
    """
    score = 0.0
    reasons: list[str] = []

    if mapping.get("date_col"):
        score += 0.40
    else:
        reasons.append("没有识别到日期列，需手动指定")

    lc = mapping.get("load_col")
    if lc:
        score += 0.35
    else:
        reasons.append("没有识别到负荷列，需手动指定")

    fmt = mapping.get("format_type")
    if fmt in ("wide", "wide_transposed", "accumulated"):
        score += 0.15
        if fmt == "wide_transposed":
            reasons.append("按转置宽表处理：时刻在行、日期在列，请确认方向没弄反")
        elif fmt == "accumulated":
            reasons.append("按累积示数表处理：会做相邻差分还原成功率")
    else:
        reasons.append("表型判定不明确，按长表处理")

    # 数值密度：宽表/转置表的大部分单元格应该是数字
    if fmt in ("wide", "wide_transposed") and df is not None and len(df):
        sample = df.head(MAX_ROWS_SAMPLE)
        numeric_ratio = sample.apply(pd.to_numeric, errors="coerce").notna().mean().mean()
        if numeric_ratio >= 0.9:
            score += 0.10
        else:
            reasons.append(f"表格里能转成数字的单元格只有 {numeric_ratio:.0%}，"
                           "可能有表头或说明行混在数据里")

    score = min(score, 1.0)

    # 时刻点数量：15 分钟粒度应该是 96 点。48 点意味着 30 分钟粒度 ——
    # 系统能算（seg_dt 按实际时刻算，数值是对的），但用户不知道自己这份数据
    # 被当成了 30 分钟一粒。这是「静默降精度」：没丢数据，精度变了，一声不响。
    # 不扣分（能算就是能算），但必须说出来。
    if lc:
        pts = len(lc)
        if pts != 96:
            step = round(1440 / pts) if pts else 0
            reasons.append(
                "时刻点 %d 个（约 %d 分钟粒度），非标准 15 分钟（96 个）。"
                "测算按实际粒度进行，结果与 15 分钟数据不可直接比较。" % (pts, step))

    if score >= 0.85:
        reasons.append("识别结果看起来可靠")
    elif score < 0.6:
        reasons.append("建议逐项核对映射后再继续")
    return Confidence(score=round(score, 2), reasons=reasons)


def _cleanup_stale_uploads(max_age_hours: int = 24) -> int:
    """清理过期的上传临时目录。

    在创建本进程的目录之前调用。只删确实老了的，避免误伤同时运行的另一个实例。
    返回清理数量。
    """
    import shutil
    import time

    base = tempfile.gettempdir()
    cutoff = time.time() - max_age_hours * 3600
    removed = 0
    try:
        for name in os.listdir(base):
            if not name.startswith("loomgrid-upload-"):
                continue
            full = os.path.join(base, name)
            try:
                if os.path.isdir(full) and os.path.getmtime(full) < cutoff:
                    shutil.rmtree(full, ignore_errors=True)
                    removed += 1
            except OSError:
                continue
    except OSError:
        pass
    return removed


_STALE = _cleanup_stale_uploads()
_UPLOAD_DIR = tempfile.mkdtemp(prefix="loomgrid-upload-")
if _STALE:
    logging.getLogger(__name__).info("清理了 %d 个过期上传目录", _STALE)


@router.post("/upload", response_model=Envelope)
async def upload(file: UploadFile = File(...)):
    """接收上传的表格文件，落到临时目录，返回可给 /inspect 用的路径。

    浏览器拿不到本地文件路径（<input type="file"> 只给 File 对象），
    所以「真实文件跑通全链路」必须先经过这里。
    """
    name = file.filename or "upload.xlsx"
    ext = os.path.splitext(name)[1].lower()
    if ext not in (".xlsx", ".xlsm", ".xls", ".csv", ".xlsb"):
        return _err("INGEST_UNSUPPORTED_TYPE", f"不支持的扩展名：{ext}")

    try:
        data = await file.read()
    except Exception as e:
        return _err("INGEST_READ_FAILED", f"读取上传内容失败：{e}")

    if not data:
        return _err("INGEST_READ_FAILED", "上传内容为空")

    # 保留原始文件名（去路径），便于 inspect 里 _detect_file_format_type 判断
    safe_name = os.path.basename(name).replace("/", "_").replace("\\", "_")
    dest = os.path.join(_UPLOAD_DIR, safe_name)
    try:
        with open(dest, "wb") as f:
            f.write(data)
    except Exception as e:
        return _err("INGEST_READ_FAILED", f"写入临时文件失败：{e}")

    return Envelope(ok=True, data={
        "path": dest,
        "filename": safe_name,
        "size": len(data),
    })

# ── 路径白名单 ─────────────────────────────────────────────
# inspect / mapping 接受文件路径。若不限制，任何网页都能让本机 API
# 去读 C:\\Windows\\win.ini 这类文件并回列名（信息泄露）。
# 正常路径都经过 /upload（落在 _UPLOAD_DIR 内），
# 额外的可信根用环境变量显式声明，默认空。
_EXTRA_ROOTS = [p for p in (os.environ.get("LGM_ALLOW_ROOTS") or "").split(os.pathsep) if p.strip()]

# 用户显式选择/拖入的文件。
# 桌面壳在调 /inspect 之前先登记——「用户选中这个文件」本身就是授权动作，
# 但它只授权这一个文件：不授权所在目录，不授权整个盘，系统目录一律拒绝。
# 只存在进程内存，后端重启即失效（下次再选重新授权）。
_ALLOWED_FILES: set[str] = set()
_ALLOWED_LOCK = threading.Lock()


def _norm(path: str) -> str:
    try:
        return os.path.normcase(os.path.realpath(path))
    except Exception:
        return ""


def _forbidden_roots() -> list[str]:
    out = []
    for key in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
        v = os.environ.get(key)
        if v:
            out.append(v)
    return out


def _path_allowed(path: str) -> bool:
    """文件路径是否在允许的根内。"""
    real = _norm(path)
    if not real:
        return False

    with _ALLOWED_LOCK:
        if real in _ALLOWED_FILES:
            return True

    roots = [_UPLOAD_DIR] + _EXTRA_ROOTS
    for r in roots:
        rr = _norm(r)
        if not rr:
            continue
        if real == rr or real.startswith(rr + os.sep):
            return True
    return False

@router.post("/allow", response_model=Envelope,
             description=(
                 "登记「用户显式选择/拖入」的文件路径。\n\n"
                 "桌面壳在调 /inspect 之前先调这里。理由：桌面程序里「用户选中这个文件」"
                 "本身就是授权动作，而浏览器上传那套白名单在本地应用里会把用户自己的文件拦在外面。\n\n"
                 "**授权粒度是单个文件**：不授权所在目录、不授权整个盘；"
                 "只收存在的文件（不收目录），系统目录与程序目录一律拒绝。\n\n"
                 "登记只存在进程内存，后端重启即失效。"
             ))
def allow_paths(payload: dict):
    """把用户选中的文件加入本次进程的可读白名单。"""
    raw_list = (payload or {}).get("paths") or []
    if not isinstance(raw_list, list):
        return _err("INGEST_ALLOW_INVALID", "paths 必须是字符串数组")

    added: list[str] = []
    existing: list[str] = []
    rejected: list[str] = []
    forbidden = [_norm(f) for f in _forbidden_roots() if _norm(f)]

    for raw in raw_list:
        if not isinstance(raw, str) or not raw.strip():
            continue
        real = _norm(raw)
        if not real or not os.path.exists(real):
            rejected.append(raw)
            continue
        if not os.path.isfile(real):
            rejected.append(raw)
            continue
        if any(real == f or real.startswith(f + os.sep) for f in forbidden):
            rejected.append(raw)
            continue

        with _ALLOWED_LOCK:
            if real in _ALLOWED_FILES:
                existing.append(raw)
            else:
                _ALLOWED_FILES.add(real)
                added.append(raw)

    log.info("allow: added=%d existing=%d rejected=%d", len(added), len(existing), len(rejected))
    return Envelope(ok=True, data={
        "added": added, "existing": existing, "rejected": rejected,
    }).model_dump()


@router.post("/inspect", response_model=Envelope,
             description=(
                 "给文件路径，返回每个工作表的列清单与建议映射。\n\n"
                 "**path 必须落在允许的根内**：\n"
                 "· 本进程通过 /api/ingest/upload 创建的临时目录（正常路径都走这里）\n"
                 "· 或环境变量 LGM_ALLOW_ROOTS 声明的目录（默认空）\n\n"
                 "不满足时返回 **HTTP 200 + error.code = INGEST_PATH_FORBIDDEN** ——\n"
                 "本 API 的错误统一走 200 + error.code，安全拒绝也不破例。\n"
                 "**调用方必须以 error.code 判断，不能只看 HTTP 状态码。**"
             ))
def inspect(payload: dict):
    """给文件路径，返回每个工作表的列清单与建议映射。"""
    path = (payload or {}).get("path")
    if not path:
        return _err("INGEST_MAPPING_INVALID", "缺少 path")
    if not _path_allowed(path):
        return _err("INGEST_PATH_FORBIDDEN",
                    "该路径不在允许的范围内。请通过 /api/ingest/upload 上传文件，"
                    "或用 LGM_ALLOW_ROOTS 环境变量声明可信目录。",
                    {"path": path})
    if not os.path.exists(path):
        return _err("INGEST_READ_FAILED", f"文件不存在：{path}")

    ext = os.path.splitext(path)[1].lower()
    if ext not in (".xlsx", ".xlsm", ".xls", ".csv", ".xlsb"):
        return _err("INGEST_UNSUPPORTED_TYPE", f"不支持的扩展名：{ext}")

    try:
        sheets = [None] if ext == ".csv" else (_safe_excel_sheets(path) or [None])
    except Exception as e:
        return _err("INGEST_READ_FAILED", f"读工作表列表失败：{e}")

    out: list[SheetInfo] = []
    _fname = os.path.basename(path)
    for sh in sheets:
        # 对 CSV 用「CSV文件」当虚拟表名；对 Excel 不能这么叫——
        # 用户的文件叫「假.xlsx」，提示却说「读 CSV文件 失败」，他会以为程序
        # 认错了文件类型（虽然从内容上看它确实不是 xlsx）。
        sheet_name = sh or ("CSV文件" if ext == ".csv" else "工作表")
        try:
            df = _read_file_full(path, None if ext == ".csv" else sh)
        except Exception as e:
            # 带文件名：用户一次拖十个文件时要知道是哪个坏了。
            # 底层异常原话保留（排障要看原话），但补一句人话在前。
            _raw = str(e)
            if "Cannot detect file format" in _raw or "format cannot be determined" in _raw:
                _msg = (f"读「{_fname}」失败：文件格式无法识别。"
                        "可能是文件损坏，或扩展名与实际内容不符（比如把文本文件改名成 .xlsx）。")
            else:
                _msg = f"读「{_fname}」失败：{_raw}"
            return _err("INGEST_READ_FAILED", _msg,
                        {"sheet": sheet_name, "file": _fname, "raw": _raw})
        if df is None or len(df) == 0:
            continue
        cols = [str(c) for c in df.columns]
        mapping = _smart_classify_columns(cols, df)
        mapping["format_type"] = _detect_file_format_type(path, sheet_name)
        mapping.setdefault("header_row", 0)
        suggest = MappingSuggest(**{k: v for k, v in mapping.items()
                                    if k in MappingSuggest.model_fields})
        # 样本行与无年份检测：V2 要用它们显示真实样本表 + C8 提示。
        # 只取前 6 列、前 5 行 —— 96 列的宽表全传会把响应撑到几 MB。
        _smp_cols = cols[:6]
        _smp_rows = [[("" if pd.isna(v) else str(v)) for v in row]
                     for row in df[_smp_cols].head(5).itertuples(index=False, name=None)] if _smp_cols else []

        _yl = []
        _dc = mapping.get("date_col")
        if _dc and _dc in df.columns:
            for _v in df[_dc].head(200):
                if _looks_yearless(_v):
                    _sv = str(_v).strip()
                    if _sv not in _yl:
                        _yl.append(_sv)
                    if len(_yl) >= 5:
                        break

        out.append(SheetInfo(name=sheet_name, columns=cols,
                             rows_sampled=min(len(df), MAX_ROWS_SAMPLE),
                             suggest=suggest,
                             confidence=assess_confidence(mapping, df),
                             sample_columns=[str(c) for c in _smp_cols],
                             sample_rows=_smp_rows,
                             yearless_samples=_yl))

    if not out:
        return _err("INGEST_READ_FAILED", "文件里没有可用数据")
    _yl_all = []
    for _si in out:
        for _v in (getattr(_si, "yearless_samples", None) or []):
            if _v not in _yl_all:
                _yl_all.append(_v)
    _hints = {}
    if _yl_all:
        import datetime as _dt
        _y = _dt.date.today().year
        _hints["guessed_year"] = {
            "value": _y, "count": len(_yl_all), "samples": _yl_all[:5],
            "message": "有 %d 个日期没有年份，已按 %d 年处理。如数据属于往年，请手动指定年份。"
                       % (len(_yl_all), _y),
        }

    return Envelope(ok=True, data={**InspectData(path=path, sheets=out).model_dump(),
                                   "hints": _hints}).model_dump()


def _build_days(month_str: str, dates: list) -> list:
    """把这个月的每一天列出来，标上类型与有没有数据。

    日期类型用 core.timegrid._get_day_type —— 与扫描阶段同一套判定，
    不在这里另写规则，否则前后端两份日历必然漂移。
    """
    if not month_str or len(month_str) != 7:
        return []
    try:
        import calendar
        y, m = int(month_str[:4]), int(month_str[5:7])
        ndays = calendar.monthrange(y, m)[1]
    except Exception:
        return []
    have = set(dates or [])
    out = []
    for d in range(1, ndays + 1):
        ds = "%s-%02d" % (month_str, d)
        try:
            t = _get_day_type(ds)
        except Exception:
            t = "weekday"
        out.append({"date": ds, "type": t, "has_data": ds in have})
    return out


@router.post("/mapping", response_model=Envelope)
def apply_mapping(req: MappingRequest):
    """确认映射后真正扫描，产出分组清单与 session_id。"""
    if not _path_allowed(req.path):
        return _err("INGEST_PATH_FORBIDDEN",
                    "该路径不在允许的范围内。", {"path": req.path})
    if not os.path.exists(req.path):
        return _err("INGEST_READ_FAILED", f"文件不存在：{req.path}")

    mapping = req.mapping.model_dump(exclude_none=True)
    if not mapping.get("date_col"):
        return _err("INGEST_NO_DATE_COL", "映射里缺少 date_col")

    sheet = req.sheet
    try:
        res = _scan_file_worker((req.path, sheet, mapping, None, threading.Event(), None))
    except Exception as e:
        return _err("INGEST_SCAN_ERROR", f"扫描失败：{e}",
                    {"type": type(e).__name__})

    if isinstance(res, dict):
        return _err("INGEST_SCAN_ERROR", res.get("error", "扫描返回错误"), res)
    if not res:
        return _err("INGEST_SCAN_ERROR", "扫描没有产出任何分组")

    groups = [GroupInfo(asset=g.get("asset_value", "未命名"),
                        month=g.get("month_str", ""),
                        date_count=int(g.get("date_count", 0)),
                        time_points=int(g.get("time_points", 0)),
                        min_val=g.get("min_val"), max_val=g.get("max_val"),
                        dates=_build_days(g.get("month_str", ""), g.get("dates") or []),
                        yearless_samples=g.get("yearless_samples") or [])
              for g in res]

    sid = uuid.uuid4().hex
    with _SESSION_LOCK:
        _SESSIONS[sid] = {"path": req.path, "sheet": sheet,
                          "mapping": res[0].get("mapping", mapping), "groups": res}
    log.info(f"session {sid} 建立：{len(groups)} 个分组，来源 {os.path.basename(req.path)}")

    _yl = []
    for g in groups:
        for v in (getattr(g, "yearless_samples", None) or []):
            if v not in _yl:
                _yl.append(v)
    hints = {}
    if _yl:
        hints["guessed_year"] = {
            "value": __import__("datetime").date.today().year,
            "count": len(_yl),
            "samples": _yl[:5],
            "message": "有 %d 个日期没有年份，已按 %d 年处理。如数据属于往年，请手动指定年份。"
                       % (len(_yl), __import__("datetime").date.today().year),
        }

    return Envelope(ok=True,
                    data={**InspectMappingData(groups=groups, session_id=sid).model_dump(),
                          "hints": hints}).model_dump()


def _hints_from_groups(groups) -> dict:
    yl = []
    for g in groups:
        for v in (getattr(g, "yearless_samples", None) or []):
            if v not in yl:
                yl.append(v)
    if not yl:
        return {}
    import datetime as _dt
    y = _dt.date.today().year
    return {"guessed_year": {"value": y, "count": len(yl), "samples": yl[:5],
                             "message": "有 %d 个日期没有年份，已按 %d 年处理。"
                                        "如数据属于往年，请手动指定年份。" % (len(yl), y)}}

@router.get("/groups", response_model=Envelope)
def list_groups(session_id: str):
    """列出某个 session 的分组摘要（含日期类型与是否有数据）。

    V4 的月度表按分组循环，V3 的分组树也用这个。
    """
    sess = get_session(session_id)
    if not sess:
        return _err("INGEST_SESSION_NOT_FOUND", f"session 不存在或已过期：{session_id}")
    raw = sess.get("groups") or []
    out = [GroupInfo(asset=g.get("asset_value", "未命名"),
                     month=g.get("month_str", ""),
                     date_count=int(g.get("date_count", 0)),
                     time_points=int(g.get("time_points", 0)),
                     min_val=g.get("min_val"), max_val=g.get("max_val"),
                     dates=_build_days(g.get("month_str", ""), g.get("dates") or []))
           for g in raw]
    return Envelope(ok=True, data={
        "session_id": session_id,
        "path": os.path.basename(sess.get("path") or ""),
        "groups": [g.model_dump() for g in out],
        "hints": _hints_from_groups(out),
    })


def get_session(sid: str) -> dict | None:
    with _SESSION_LOCK:
        return _SESSIONS.get(sid)
