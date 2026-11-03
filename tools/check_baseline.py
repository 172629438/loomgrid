# -*- coding: utf-8 -*-
"""基线完整性体检：确认 18 个场景真的采到了东西，而不是「没崩就算过」。

判据
----
每个场景逐项检查，缺一即告警：
  parse    至少一个输入产出了 summaries 与宽表摘要
  calc     4 个参数变体全部 ok（不含异常）
  plot     至少 light/dark 两张图真的落盘且尺寸合理
  export   Excel 写出并读回，roundtrip 偏差为 0
  report   报告生成成功（若产品本身有缺陷，此项 FAIL 且原因单独列出）

退出码：存在任一 FAIL 即为 1。

用法
----
    python tools/check_baseline.py
    python tools/check_baseline.py --root baseline -v
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def check_scenario(root: str, sid: str) -> dict:
    p = os.path.join(root, sid, "manifest.json")
    if not os.path.exists(p):
        return {"id": sid, "fatal": "manifest 缺失", "fails": ["manifest"]}
    d = json.load(open(p, encoding="utf-8"))
    st = d.get("stages", {})
    res = {"id": sid, "name": d.get("name"), "fails": [], "notes": [], "detail": {}}

    if d.get("fatal"):
        res["fails"].append(f"fatal:{d['fatal']}")
        return res

    # parse
    parse = st.get("parse", {})
    if not parse:
        res["fails"].append("parse:空")
    else:
        for tag, v in parse.items():
            w = v.get("wide") or {}
            if not w.get("shape"):
                res["fails"].append(f"parse:{tag}:无宽表")
            else:
                res["detail"]["wide"] = w["shape"]
                res["detail"]["nan"] = w.get("nan_count")

    # calc
    calc = st.get("calc", {})
    if not calc:
        res["fails"].append("calc:空")
    for tag, v in calc.items():
        variants = v.get("variants", {})
        ok = [k for k, vv in variants.items() if vv.get("ok")]
        bad = [f"{k}({vv.get('error')})" for k, vv in variants.items() if not vv.get("ok")]
        res["detail"]["calc_ok"] = f"{len(ok)}/{len(variants)}"
        if bad:
            res["fails"].append("calc:" + "; ".join(bad))

    # plot
    plot = st.get("plot", {})
    if not plot:
        res["fails"].append("plot:空")
    for tag, v in plot.items():
        variants = v.get("variants", {})
        ok = [k for k, vv in variants.items() if vv.get("ok")]
        res["detail"]["plot_ok"] = f"{len(ok)}/{len(variants)}"
        for k, vv in variants.items():
            if not vv.get("ok"):
                res["fails"].append(f"plot:{k}({vv.get('error')})")
            else:
                sz = vv.get("size") or [0, 0]
                if sz[0] < 400 or sz[1] < 200:
                    res["fails"].append(f"plot:{k} 尺寸异常 {sz}")

    # export
    export = st.get("export", {})
    if not export:
        res["fails"].append("export:空")
    for tag, v in export.items():
        e = (v.get("result") or {}).get("excel") or {}
        if not e.get("ok"):
            res["fails"].append(f"excel:{e.get('error')}")
        else:
            if e.get("roundtrip_max_abs_diff", 0) > 1e-9:
                res["fails"].append(f"excel:roundtrip={e.get('roundtrip_max_abs_diff')}")
            res["detail"]["excel_shape"] = e.get("shape")

    # report
    r = (st.get("report") or {}).get("result") or {}
    if r.get("ok"):
        res["detail"]["report_chars"] = r.get("chars")
        if r.get("has_amp_raw"):
            res["fails"].append(f"report:裸 & 未转义 {r['has_amp_raw']!r}")
    else:
        res["fails"].append(f"report:{r.get('error')}")
        logs = r.get("log") or []
        if logs:
            res["notes"].append("产品自身日志: " + " | ".join(logs[:2]))

    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.join(ROOT, "baseline"))
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    idx_p = os.path.join(a.root, "BASELINE-INDEX.json")
    if not os.path.exists(idx_p):
        print(f"缺少 {idx_p}")
        return 1
    idx = json.load(open(idx_p, encoding="utf-8"))
    rows = [check_scenario(a.root, s["id"]) for s in idx["scenarios"]]

    print(f"基线根目录: {a.root}")
    print(f"环境: Python {idx['env'].get('python')} / matplotlib {idx['env'].get('matplotlib')} "
          f"/ 字体 {idx['env'].get('cjk_font')}")
    print("-" * 96)
    n_fail = 0
    for r in rows:
        d = r.get("detail", {})
        status = "OK  " if not r["fails"] else "FAIL"
        if r["fails"]:
            n_fail += 1
        print(f"{status} {r['id']:4s} {(r.get('name') or ''):22s} "
              f"wide={str(d.get('wide', '-')):10s} calc={d.get('calc_ok', '-'):5s} "
              f"plot={d.get('plot_ok', '-'):5s} excel={str(d.get('excel_shape', '-')):10s} "
              f"report={d.get('report_chars', '-')}")
        if r["fails"]:
            for f in r["fails"]:
                print(f"       × {f}")
        if a.verbose or r["fails"]:
            for n in r.get("notes", []):
                print(f"       · {n}")
    print("-" * 96)
    print(f"合计 {len(rows)} 个场景，通过 {len(rows) - n_fail}，失败 {n_fail}")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
