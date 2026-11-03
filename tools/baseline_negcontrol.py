# -*- coding: utf-8 -*-
"""比对器阴性对照：确认 compare_baseline 真的抓得住差异。

为什么需要这一件
----------------
一个「永远全绿」的比对器，比没有比对器更危险——它让人以为验过了。
所以比对器本身也要被验证：故意制造真实差异，看它报不报警。

做三组差异（都对应重构时最可能发生的偏差）：
  1. 宽表里一个点改 +0.1 kW        —— 数值保真失守
  2. 分段充放空间改一天 +1.0 kWh    —— 测算口径漂移
  3. 时刻标签首项 00:00 → 00:01    —— 解析口径漂移

判据：未改动必须退出码 0；三组差异必须各自退出码 1。

用法
----
    python tools/baseline_negcontrol.py
    python tools/baseline_negcontrol.py --scenario W1
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BASE = os.path.join(ROOT, "baseline")
COMPARE = os.path.join(HERE, "compare_baseline.py")


def run_compare(work: str, sid: str) -> tuple[int, list[str]]:
    r = subprocess.run(
        [sys.executable, COMPARE, "--base", BASE, "--cand", work, "--only", sid],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = r.stdout or ""
    fails = [ln.strip() for ln in out.splitlines() if ln.startswith(("FAIL", "WARN"))]
    return r.returncode, fails


def first_tag(manifest: dict) -> str | None:
    tags = list((manifest.get("stages", {}).get("parse") or {}).keys())
    return tags[0] if tags else None


def main() -> int:
    ap = argparse.ArgumentParser(description="compare_baseline 阴性对照")
    ap.add_argument("--scenario", default=None, help="场景 id，默认取索引里第一个")
    a = ap.parse_args()

    idx_p = os.path.join(BASE, "BASELINE-INDEX.json")
    if not os.path.exists(idx_p):
        print(f"基线缺失: {idx_p}")
        return 2
    idx = json.load(open(idx_p, encoding="utf-8"))
    sid = a.scenario or idx["scenarios"][0]["id"]

    src = os.path.join(BASE, sid)
    mp = os.path.join(src, "manifest.json")
    if not os.path.exists(mp):
        print(f"场景 {sid} 的 manifest 缺失")
        return 2
    tag = first_tag(json.load(open(mp, encoding="utf-8")))
    if not tag:
        print(f"场景 {sid} 没有可用的 parse 记录")
        return 2

    work = tempfile.mkdtemp(prefix="lg_neg_")
    results: dict[str, tuple[int, int]] = {}
    try:
        shutil.copy2(idx_p, work)
        shutil.copytree(src, os.path.join(work, sid))

        rc, f = run_compare(work, sid)
        results["未改动"] = (rc, len(f))

        # 差异 1：宽表单点
        pw = os.path.join(work, sid, tag, "wide.npy")
        aw = np.load(pw)
        keep_w = aw.copy()
        aw.flat[0] = float(aw.flat[0]) + 0.1
        np.save(pw, aw)
        rc, f = run_compare(work, sid)
        results["宽表数值"] = (rc, len(f))
        np.save(pw, keep_w)

        # 差异 2：分段充放空间
        ps = os.path.join(work, sid, tag, "seg_charge_A_cap500.npy")
        if os.path.exists(ps):
            asg = np.load(ps)
            keep_s = asg.copy()
            asg.flat[0] = float(asg.flat[0]) + 1.0
            np.save(ps, asg)
            rc, f = run_compare(work, sid)
            results["分段空间"] = (rc, len(f))
            np.save(ps, keep_s)
        else:
            results["分段空间"] = (-1, -1)

        # 差异 3：时刻标签
        pm = os.path.join(work, sid, "manifest.json")
        m = json.load(open(pm, encoding="utf-8"))
        labels = m["stages"]["parse"][tag]["time_labels"]
        if labels:
            labels[0] = "00:01"
            json.dump(m, open(pm, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            rc, f = run_compare(work, sid)
            results["解析标签"] = (rc, len(f))
        else:
            results["解析标签"] = (-1, -1)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print(f"场景 {sid} / 输入 {tag}\n" + "=" * 60)
    ok = True
    for k, (rc, nfail) in results.items():
        expect_clean = (k == "未改动")
        skipped = (rc == -1)
        good = skipped or ((rc == 0 and nfail == 0) if expect_clean else (rc == 1 and nfail > 0))
        ok = ok and good
        verdict = "跳过（文件缺失）" if skipped else ("期望全绿" if expect_clean else "期望报警")
        print(f"{'✓' if good else '✗'} {k:10s} 退出码={rc:2d} 报警项={nfail:2d}  {verdict}")
    print("=" * 60)
    print("阴性对照通过：比对器对未改动全绿、对真实差异全部报警。"
          if ok else "阴性对照失败：比对器判据不可信，不能用于验收。")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
