# -*- coding: utf-8 -*-
"""阶段 0 · 行为基线采集器。

记录 v2.3.8 在 18 个场景下的真实输出，形成重构后的比对金标准。

驱动层次（关键设计）
-------------------
不模拟点击，也不重写逻辑，而是调用**产品自己的代码**：

    解析   _detect_file_format_type → _read_file_full → _smart_classify_columns
           → _scan_file_worker → GroupSummary → DataProvider.get_group_data
    测算   compute_segmented_spaces / calc_capacity_for_days / calc_days_for_capacity
    出图   _plot_curves（matplotlib 真渲染）
    报告   MainWindow._generate_report（用 __new__ 绑定属性，绕过 __init__ 的 UI 构建）

报告层之所以能这样驱动：_generate_report(paths, failed) 只依赖 self.output_dir、
self._thread.all_summaries、self._log 三个东西 —— 全部可以外部绑定，
方法体本身一字不改地执行。

判据分层（比对时用）
-------------------
strict  必须逐字/逐值一致   解析结果、测算数字、宽表数值、Excel 单元格、报告文本
loose   只要求形态一致       PNG 像素哈希（版本/字体敏感，重构后若改前端渲染必然变）
info    仅记录不判等         控件状态快照

用法
----
    python tools/capture_baseline.py                     # 全量，写 baseline/
    python tools/capture_baseline.py --only W1 L1        # 指定场景
    python tools/capture_baseline.py --out <目录>
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import platform
import shutil
import sys
import threading
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_OUT = os.path.join(ROOT, "baseline")
FUSION = os.path.join(ROOT, "负荷曲线批量生成工具 Pro 融合版 (v2.3).py")
INPUT_ROOT = os.path.join(DEFAULT_OUT, "_inputs")

sys.path.insert(0, HERE)
import baseline_scenarios as scen  # noqa: E402


# ────────────────────────────────────────────────────────────
# 通用工具
# ────────────────────────────────────────────────────────────
def sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def arr_digest(a) -> dict:
    """数组的结构化摘要：形状 + 全量 sha256 + 统计量。"""
    a = np.asarray(a, dtype=np.float64)
    nan_mask = np.isnan(a)
    payload = {
        "shape": list(a.shape),
        "sha256": sha_bytes(np.ascontiguousarray(a).tobytes()),
        "nan_count": int(nan_mask.sum()),
        "sum": float(np.nansum(a)),
        "min": float(np.nanmin(a)) if a.size and not nan_mask.all() else None,
        "max": float(np.nanmax(a)) if a.size and not nan_mask.all() else None,
        "mean": float(np.nanmean(a)) if a.size and not nan_mask.all() else None,
    }
    return payload


def jsonable(o):
    """把产品返回的结构压成可 JSON 化的形态。"""
    if isinstance(o, dict):
        return {str(k): jsonable(v) for k, v in o.items() if not callable(v)}
    if isinstance(o, (list, tuple)):
        return [jsonable(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        v = float(o)
        return None if (v != v) else v
    if isinstance(o, np.ndarray):
        return arr_digest(o)
    if isinstance(o, (str, int, float, bool)) or o is None:
        if isinstance(o, float) and o != o:
            return None
        return o
    return f"<{type(o).__name__}>"


def load_fusion():
    spec = importlib.util.spec_from_file_location("lg_baseline", FUSION)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["lg_baseline"] = mod
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        spec.loader.exec_module(mod)
    return mod, buf.getvalue()


def env_fingerprint(mod) -> dict:
    import matplotlib
    import pandas
    fp = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "numpy": np.__version__,
        "pandas": pandas.__version__,
        "matplotlib": matplotlib.__version__,
        "fusion_file_sha256": sha_file(FUSION),
        "fusion_lines": len(open(FUSION, encoding="utf-8").read().splitlines()),
    }
    for pkg in ("scipy", "openpyxl", "loguru", "chardet", "chinese_calendar", "PySide6"):
        try:
            m = __import__(pkg)
            fp[pkg] = getattr(m, "__version__", "?")
        except Exception:
            fp[pkg] = None
    try:
        fp["cjk_font"] = mod._get_cached_font()
    except Exception as e:
        fp["cjk_font"] = f"<err {e}>"
    return fp


# ────────────────────────────────────────────────────────────
# 单场景采集
# ────────────────────────────────────────────────────────────
def run_pipeline(mod, fp: str, sheet: str, override: dict | None):
    """走产品自己的读取→分类→扫描→取数链路。返回 (summaries, data, err)。"""
    try:
        fmt = mod._detect_file_format_type(fp, sheet)
        df = mod._read_file_full(fp, sheet if sheet != "CSV文件" else None)
        if df is None or len(df) == 0:
            return None, None, "read_failed"
        mapping = mod._smart_classify_columns(list(df.columns), df)
        mapping["format_type"] = fmt
        mapping.setdefault("header_row", 0)
        if override:
            mapping.update(override)
        args = (fp, sheet, mapping, None, threading.Event(), None, False)
        try:
            res = mod._scan_file_worker(args)
        except ValueError:
            res = mod._scan_file_worker(args[:6])
        if isinstance(res, dict):
            return None, None, f"scan_error:{res.get('error', '')}"
        if not res:
            return [], None, "no_summary"
        gs = mod.GroupSummary(**res[0])
        provider = mod.DataProvider()
        data = provider.get_group_data(gs)
        if not data:
            return res, None, f"load_error:{provider.get_load_error(gs)}"
        return res, data, None
    except Exception as e:
        return None, None, f"{type(e).__name__}: {e}"


SEGMENTS_TEMPLATE = [
    {"name": "上午充", "start": 0.0, "end": 8.0, "kind": "c"},
    {"name": "午间放", "start": 8.0, "end": 11.0, "kind": "d"},
    {"name": "下午充", "start": 11.0, "end": 14.0, "kind": "c"},
    {"name": "晚间放", "start": 14.0, "end": 20.0, "kind": "d"},
]


def build_segments(mod) -> list[dict]:
    """补齐 seg['type'] 与 seg['label']。

    compute_segmented_spaces 按 type 分流充放（L3219/L3238/L3263），
    并直接拿 seg['label'] 当作列名写入四个平行列表（L3266+）。
    """
    return [
        {"name": s["name"], "label": s["name"], "start": s["start"], "end": s["end"],
         "type": mod.PeriodType.CHARGE if s["kind"] == "c" else mod.PeriodType.DISCHARGE}
        for s in SEGMENTS_TEMPLATE
    ]


def stage_calc(mod, load_array, time_labels, outdir_npy: str) -> dict:
    """测算层：多组参数下的逐日逐段空间 + 容量换算。"""
    out = {}
    segments = build_segments(mod)
    variants = {
        "A_no_bill": dict(segments=segments, billing=None, mode="A", rated_power=None, ignore_negative=False),
        "A_cap500": dict(segments=segments, billing={"value": 500.0}, mode="A", rated_power=None, ignore_negative=False),
        "A_cap500_ignneg": dict(segments=segments, billing={"value": 500.0}, mode="A", rated_power=None, ignore_negative=True),
        "B_cap500": dict(segments=segments, billing={"value": 500.0}, mode="B", rated_power=None, ignore_negative=False),
    }
    for name, kw in variants.items():
        try:
            ch, dis, clab, dlab = mod.compute_segmented_spaces(load_array, time_labels, **kw)
            c_arr = np.asarray([np.asarray(a, dtype=float).ravel() for a in ch], dtype=float)
            d_arr = np.asarray([np.asarray(a, dtype=float).ravel() for a in dis], dtype=float)
            out[name] = {
                "ok": True,
                "charge": arr_digest(ch),
                "discharge": arr_digest(dis),
                "charge_daily": [float(x) for x in np.asarray(ch, dtype=float).ravel()],
                "discharge_daily": [float(x) for x in np.asarray(dis, dtype=float).ravel()],
                "charge_labels": list(clab),
                "discharge_labels": list(dlab),
            }
            out[name]["seg_npy"] = {"charge": f"seg_charge_{name}.npy",
                                    "discharge": f"seg_discharge_{name}.npy"}
            np.save(os.path.join(outdir_npy, f"seg_charge_{name}.npy"), c_arr)
            np.save(os.path.join(outdir_npy, f"seg_discharge_{name}.npy"), d_arr)
            # 容量换算（两条互逆路径）
            # 口径说明：这里用「各段逐日求和」作为充/放总空间的基线口径，
            # 不是产品 UI 的日最小段口径（_get_all_filtered_daily_mins 的两种策略）。
            # 目的只是把两个纯函数的数学行为钉住，8 周后验证重构未改公式；
            # UI 口径的等价核对在阶段 4 逐屏进行。
            if c_arr.ndim == 2 and c_arr.shape[1] > 0:
                c_tot = c_arr.sum(axis=0)
                d_tot = d_arr.sum(axis=0)
                n = c_tot.size
                td = max(1, min(2, n))
                cap = float(mod.calc_capacity_for_days(td, c_tot, d_tot))
                days = int(mod.calc_days_for_capacity(cap, c_tot, d_tot))
                out[name]["daily_total"] = {
                    "charge": [round(float(x), 6) for x in c_tot],
                    "discharge": [round(float(x), 6) for x in d_tot],
                }
                out[name]["capacity_calc"] = {
                    "note": "口径=各段逐日求和（纯函数验证用，非 UI 日最小段口径）",
                    "target_days": td,
                    "capacity": round(cap, 6),
                    "days_for_capacity": days,
                }
        except Exception as e:
            out[name] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return out


def stage_plot(mod, dates, load_array, time_labels, outdir: str, tag: str) -> dict:
    """出图层：真渲染 PNG，记像素哈希与尺寸。"""
    out = {}
    variants = {
        "light_default": dict(show_daily=True, show_avg=True, fit_types=[],
                              show_max_demand=False, highlight_mode="none",
                              mark_anomalies=False, dark_theme=False),
        "dark_default": dict(show_daily=True, show_avg=True, fit_types=[],
                             show_max_demand=False, highlight_mode="none",
                             mark_anomalies=False, dark_theme=True),
    }
    for name, opts in variants.items():
        p = os.path.join(outdir, f"plot_{name}.png")
        try:
            ok = mod._plot_curves(list(dates), np.asarray(load_array, dtype=float),
                                  list(time_labels), f"基线-{tag}", p,
                                  opts=opts, bill=None, periods=None)
            if ok and os.path.exists(p):
                from PIL import Image  # matplotlib 依赖链里通常已有
                with Image.open(p) as im:
                    size = list(im.size)
                out[name] = {"ok": True, "sha256": sha_file(p), "size": size,
                             "bytes": os.path.getsize(p), "file": os.path.basename(p)}
            else:
                out[name] = {"ok": False, "error": "返回 False 或文件不存在"}
        except Exception as e:
            out[name] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return out


def stage_export(mod, data, summary, outdir: str) -> dict:
    """导出层：宽表 DataFrame → xlsx，记逐单元格。"""
    import pandas as pd
    out = {}
    try:
        df = pd.DataFrame(data["load_array"], index=data["date_labels"], columns=data["time_labels"])
        df.index.name = "日期"
        p = os.path.join(outdir, "midwide.xlsx")
        df.to_excel(p)
        # 读回，逐单元格比对（验证写出去的文件而不是内存里的对象）
        back = pd.read_excel(p, index_col=0)
        a = np.asarray(df.values, dtype=float)
        b = np.asarray(back.values, dtype=float)
        out["excel"] = {
            "ok": True,
            "file": os.path.basename(p),
            "sha256_bytes": sha_file(p),
            "shape": list(a.shape),
            "values_sha256": sha_bytes(np.ascontiguousarray(a).tobytes()),
            "roundtrip_max_abs_diff": float(np.nanmax(np.abs(a - b))) if a.size else 0.0,
            "index": [str(x) for x in df.index],
            "columns": [str(x) for x in df.columns],
        }
    except Exception as e:
        out["excel"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return out


def stage_report(mod, summaries, outdir: str, chart_paths: list[str]) -> dict:
    """报告层：直接调 MainWindow._generate_report，方法体一字不改。

    依赖 self.output_dir / self._thread.all_summaries / self._log，
    用 __new__ 绕过 __init__ 的 UI 构建后外部绑定。
    """
    from types import SimpleNamespace
    logs: list[str] = []
    try:
        w = mod.MainWindow.__new__(mod.MainWindow)
        w.output_dir = outdir
        w._thread = SimpleNamespace(all_summaries=summaries or [])
        w._log = lambda m: logs.append(str(m))
        path = mod.MainWindow._generate_report(w, chart_paths, [])
        if path and os.path.exists(path):
            text = open(path, encoding="utf-8").read()
            # 报告含「生成时间: <now>」，每次必不同 —— 哈希前先规范化，否则比对无意义。
            import re
            normalized = re.sub(r"生成时间:\s*[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9:]{8}",
                                "生成时间: <TS>", text)
            return {
                "ok": True,
                "file": os.path.basename(path),
                "sha256": sha_bytes(text.encode("utf-8")),
                "sha256_normalized": sha_bytes(normalized.encode("utf-8")),
                "normalized": normalized,
                "chars": len(text),
                "has_amp_raw": bool(_raw_amp(text)),
                "text": text,
                "log": logs,
            }
        return {"ok": False, "error": "未返回路径", "log": logs}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "log": logs}


def _raw_amp(text: str) -> str:
    """检查报告里是否残留未转义的裸 & < >（转义正确时不该命中）。"""
    import re
    m = re.search(r"&(?!amp;|lt;|gt;|quot;|#)", text)
    return m.group(0) if m else ""


# 配置里这些键装的是用户本机状态（历史目录、最近文件），既泄隐私又不可复现
_PATHY_KEY_HINTS = ("dir", "path", "file", "recent", "history", "last_", "output")


def _safe_conf_value(k: str, v):
    """路径类配置值一律遮掉；其余原值保留（默认值本身是有效的基线信息）。"""
    kl = str(k).lower()
    if any(h in kl for h in _PATHY_KEY_HINTS):
        return "<hidden>"
    if isinstance(v, str):
        if "\\" in v or "/" in v or ":" in v:
            return "<hidden>"
        return v
    if isinstance(v, list):
        return ["<hidden>" if isinstance(x, str) and ("\\" in x or "/" in x or ":" in x) else x
                for x in v[:8]]
    if isinstance(v, dict):
        return {kk: _safe_conf_value(kk, vv) for kk, vv in list(v.items())[:16]}
    return v


def stage_widgets(mod, summaries, data) -> dict:
    """控件状态：只记录不判等（info）。构造轻量替身读取产品默认值。"""
    out = {}
    try:
        out["plot_options_default"] = {
            k: jsonable(v) for k, v in mod.PlotOptions().__dict__.items()
        }
    except Exception as e:
        out["plot_options_default"] = f"<err {type(e).__name__}: {e}>"
    try:
        c = mod.Config()
        out["config_keys"] = sorted(c.__dict__.keys())
        out["config_default"] = {
            k: _safe_conf_value(k, jsonable(v))
            for k, v in c.__dict__.items()
            if isinstance(v, (str, int, float, bool, type(None), list, dict))
        }
    except Exception as e:
        out["config_default"] = f"<err {type(e).__name__}: {e}>"
    try:
        out["period_types"] = [p.name for p in mod.PeriodType]
    except Exception as e:
        out["period_types"] = f"<err {type(e).__name__}: {e}>"
    return out


def scrub_paths(obj, root: str = ROOT):
    """把本机绝对路径换成 <REPO> 前缀。

    基线会随仓库公开提交，manifest 里带着 C:\\Users\\<用户名>\\...
    等于把个人目录结构一起发布。所有路径在写盘前统一脱敏。
    """
    if isinstance(obj, str):
        s = obj.replace(root, "<REPO>")
        s = s.replace(root.replace("\\", "/"), "<REPO>")
        s = s.replace(root.replace("\\", "\\\\"), "<REPO>")
        return s
    if isinstance(obj, dict):
        # abs 是运行期临时字段（拼好的绝对路径），不参与脱敏也不写盘
        return {k: (v if k == "abs" else scrub_paths(v, root)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub_paths(v, root) for v in obj]
    return obj


def capture_scenario(mod, sc: dict, entry_inputs: list[dict], outdir: str) -> dict:
    """采集一个场景的全部层。"""
    rec = {"id": sc["id"], "name": sc["name"], "group": sc["group"],
           "notes": sc["notes"], "covers": sc["covers"],
           "known_defects": sc.get("known_defects") or [],
           "inputs": [], "stages": {}}
    os.makedirs(outdir, exist_ok=True)

    per_input = []
    for inp in entry_inputs:
        r = run_pipeline(mod, inp.get("abs") or inp["path"], inp["sheet"], inp["mapping_override"])
        summaries, data, err = r
        per_input.append({
            "input": os.path.basename(inp["path"]),
            "sheet": inp["sheet"],
            "error": err,
            "summaries": jsonable(summaries) if summaries else None,
        })
        rec["inputs"].append(os.path.basename(inp["path"]))

        if not data:
            continue

        tag = os.path.splitext(os.path.basename(inp["path"]))[0]
        sdir = os.path.join(outdir, tag)
        os.makedirs(sdir, exist_ok=True)

        rec["stages"].setdefault("parse", {})[tag] = {
            "grade": "strict",
            "summaries": jsonable(summaries),
            "wide": arr_digest(data["load_array"]),
            "wide_npy": "wide.npy",
            "date_labels": [str(x) for x in data.get("date_labels", [])],
            "time_labels": [str(x) for x in data.get("time_labels", [])],
        }
        # 全量数值必须落盘 —— 只存哈希的话，重构后浮点运算顺序微变就无法判定
        # 是「真变了」还是「噪声」，比对器只能用哈希硬砸，产生假失败。
        np.save(os.path.join(sdir, "wide.npy"),
                np.asarray(data["load_array"], dtype=np.float64))

        load = np.asarray(data["load_array"], dtype=float)
        tl = [str(x) for x in data["time_labels"]]
        rec["stages"].setdefault("calc", {})[tag] = {
            "grade": "strict",
            "variants": stage_calc(mod, load, tl, sdir),
        }
        rec["stages"].setdefault("plot", {})[tag] = {
            "grade": "loose",
            "variants": stage_plot(mod, data["date_labels"], load, tl, sdir, tag),
        }
        rec["stages"].setdefault("export", {})[tag] = {
            "grade": "strict",
            "result": stage_export(mod, data, summaries[0] if summaries else None, sdir),
        }

    # 报告层：用第一个成功输入的图当素材
    charts = []
    for root, _dirs, files in os.walk(outdir):
        for f in sorted(files):
            if f.startswith("plot_") and f.endswith(".png"):
                charts.append(os.path.join(root, f))
    all_summaries = []
    for inp in per_input:
        if inp["summaries"]:
            for s in inp["summaries"]:
                if isinstance(s, dict):
                    try:
                        all_summaries.append(mod.GroupSummary(**{k: v for k, v in s.items()
                                                                 if k in mod.GroupSummary._fields}))
                    except Exception:
                        pass
    rec["stages"]["report"] = {
        "grade": "strict",
        "charts": [os.path.basename(c) for c in charts],
        "result": stage_report(mod, all_summaries, outdir, charts),
    }
    rec["stages"]["widgets"] = {"grade": "info", "result": stage_widgets(mod, all_summaries, None)}
    rec["per_input"] = per_input
    return scrub_paths(rec)


# ────────────────────────────────────────────────────────────
# 主流程
# ────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="采集阶段 0 行为基线")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--only", nargs="*", help="只跑指定场景 id")
    ap.add_argument("--keep-inputs", action="store_true", help="不重新生成输入样本")
    a = ap.parse_args()

    out = a.out
    in_root = os.path.join(out, "_inputs")

    if not a.keep_inputs:
        print(f"[1/4] 生成场景输入 -> {in_root}")
        manifest = scen.generate(in_root, a.only)
    else:
        with open(os.path.join(in_root, "scenarios.json"), encoding="utf-8") as f:
            manifest = json.load(f)
    # 输入清单里存的是相对路径，这里还原成本机绝对路径供读取
    for sc_meta in manifest["scenarios"]:
        for e in sc_meta.get("inputs", []):
            e["abs"] = os.path.join(in_root, e["path"].replace("/", os.sep))
    manifest = scrub_paths(manifest)

    print("[2/4] 加载 Fusion v2.3.8（offscreen）…")
    mod, boot_log = load_fusion()
    env = env_fingerprint(mod)
    print(f"      Python {env['python']} / matplotlib {env['matplotlib']} / "
          f"字体 {env['cjk_font']}")
    for k, v in env.items():
        print(f"      {k:20s} {v}")

    with open(os.path.join(out, "env.json"), "w", encoding="utf-8") as f:
        json.dump(scrub_paths({"env": env, "import_log_tail": boot_log[-2000:]}),
                  f, ensure_ascii=False, indent=2)

    print("[3/4] 逐场景采集…")
    results = []
    for sc_meta in manifest["scenarios"]:
        sc = next(s for s in scen.SCENARIOS if s["id"] == sc_meta["id"])
        sdir = os.path.join(out, sc_meta["id"])
        if os.path.isdir(sdir):
            shutil.rmtree(sdir)
        os.makedirs(sdir, exist_ok=True)
        try:
            rec = capture_scenario(mod, sc, sc_meta["inputs"], sdir)
        except Exception as e:
            rec = {"id": sc_meta["id"], "name": sc_meta["name"], "fatal":
                   f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()}
            print(f"  {sc_meta['id']:3s} FATAL {e}")
        else:
            ok_stages = [k for k, v in rec["stages"].items()
                         if isinstance(v, dict) and v.get("grade")]
            errs = [i for i in rec.get("inputs", []) if i]
            n_err = sum(1 for i in rec["inputs"] if False)
            print(f"  {sc_meta['id']:3s} {sc_meta['name']:24s} 阶段 {','.join(ok_stages)}")
        with open(os.path.join(sdir, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=2)
        results.append(rec)

    print("[4/4] 汇总")
    summary = {
        "captured_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "env": env,
        "scenarios": [{"id": r["id"], "name": r.get("name"),
                       "fatal": r.get("fatal"),
                       "stages": list(r.get("stages", {}).keys())} for r in results],
    }
    with open(os.path.join(out, "BASELINE-INDEX.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    n_fatal = sum(1 for r in results if r.get("fatal"))
    print(f"\n完成：{len(results)} 个场景，{n_fatal} 个致命错误 -> {out}")


if __name__ == "__main__":
    main()
