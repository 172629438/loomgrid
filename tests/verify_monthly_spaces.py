# -*- coding: utf-8 -*-
"""验证 /api/calc/monthly-spaces（V4 月度表的输入）。

判据三条最要紧：
  ① 同月份覆盖 —— 两个文件都是 2026-01 时只出一行（后加载者为准）。
     这是 Fusion 的既有行为，不能「聪明地」改成两行。
  ② min_segment 是数字而不是 NaN/null —— 混合粒度插值会产生 NaN，
     下游若不做 nan 感知，整月会变 NaN。
  ③ 全零段列被隐藏，且 hidden_zero_segments 数得对。

用法：
    python tests/verify_monthly_spaces.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("LGM_ALLOW_ROOTS", str(ROOT / "baseline" / "_inputs"))

from fastapi.testclient import TestClient  # noqa: E402

from api.app import app  # noqa: E402

CLIENT = TestClient(app)
passed = 0
failed = 0


def check(ok: bool, label: str, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}  {detail}")


def make_session(path: Path) -> str | None:
    r = CLIENT.post("/api/ingest/inspect", json={"path": str(path)})
    b = r.json()
    if not b.get("ok"):
        print("  inspect 失败：", b.get("error"))
        return None
    sheet = b["data"]["sheets"][0]
    r = CLIENT.post("/api/ingest/mapping", json={
        "path": str(path), "sheet": sheet["name"], "mapping": sheet["suggest"]})
    b = r.json()
    if not b.get("ok"):
        print("  mapping 失败：", b.get("error"))
        return None
    return b["data"]["session_id"]


def main() -> int:
    segs = [{"start": 0, "end": 8, "type": "charge", "label": "充电1"},
            {"start": 18, "end": 22, "type": "discharge", "label": "放电1"}]
    base = {"segments": segs, "billing": {"value": 800}, "mode": "A",
            "ignore_negative": True}

    w1 = ROOT / "baseline" / "_inputs" / "W1" / "W1_宽表96点.xlsx"
    sid1 = make_session(w1)
    if sid1 is None:
        return 1
    print(f"session1 = {sid1[:8]}…（W1，2026-01，96 点）")

    print("\n[① 单 session → 一月一行]")
    r = CLIENT.post("/api/calc/monthly-spaces", json={**base, "session_ids": [sid1]})
    b = r.json()
    check(b.get("ok") is True, "端点可用", str(b.get("error")))
    data = b.get("data") or {}
    months = data.get("months") or []
    check(len(months) == 1, f"1 行（{len(months)}）", str([m["month"] for m in months]))
    if months:
        m0 = months[0]
        check(m0["month"] == "2026-01", f"月份 = 2026-01（{m0['month']}）", "")
        check(m0["resolution"] == 96, f"resolution = 96（{m0['resolution']}）", "")
        check(m0.get("source_count") == 1, f"source_count = 1（{m0.get('source_count')}）", "")
        check(isinstance(m0.get("min_segment"), (int, float)),
              f"min_segment 是数字（{m0.get('min_segment')}）", str(m0.get("min_segment")))
        check(m0["min_segment"] == m0["min_segment"], "min_segment 不是 NaN", "")
        print(f"    列：{data.get('columns')}")
        print(f"    值：{[s['value'] for s in m0['segments']]} · 最小段 {m0['min_segment']}")

    print("\n[② 同月份覆盖：两个 session 都是 2026-01 → 仍 1 行]")
    tmp = Path(tempfile.mkdtemp(prefix="lg-mon-"))
    dup = tmp / "W1_copy.xlsx"
    shutil.copy2(w1, dup)
    CLIENT.post("/api/ingest/allow", json={"paths": [str(dup)]})
    sid2 = make_session(dup)
    check(sid2 is not None, "第二个 session 建立（同月不同文件）", "")
    if sid2:
        r2 = CLIENT.post("/api/calc/monthly-spaces",
                         json={**base, "session_ids": [sid1, sid2]})
        b2 = r2.json()
        d2 = b2.get("data") or {}
        m2 = d2.get("months") or []
        check(len(m2) == 1, f"仍然 1 行（{len(m2)}）——同月份被覆盖", str([x["month"] for x in m2]))
        if m2:
            check(m2[0].get("source_count") == 2,
                  f"source_count = 2（{m2[0].get('source_count')}）", str(m2[0].get("source_count")))
            srcs = m2[0].get("sources") or []
            check(len(srcs) == 2, f"sources 列出 2 个来源：{srcs}", str(srcs))
        print(f"    两个 session 都是 2026-01，输出 {len(m2)} 行")

    print("\n[③ 全零段隐藏]")
    d = data
    hidden = d.get("hidden_zero_segments", -1)
    check(isinstance(hidden, int) and hidden >= 0,
          f"hidden_zero_segments = {hidden}（非负整数）", str(hidden))
    cols = d.get("columns") or []
    print(f"    保留 {len(cols)} 列，隐藏 {hidden} 列")

    print("\n[④ 空 session_ids / 无时段]")
    r = CLIENT.post("/api/calc/monthly-spaces", json={**base, "session_ids": []})
    check((r.json().get("error") or {}).get("code") == "CALC_INVALID_SEGMENT",
          "空 session_ids → CALC_INVALID_SEGMENT", str(r.json()))
    r = CLIENT.post("/api/calc/monthly-spaces",
                    json={"session_ids": [sid1], "segments": []})
    check((r.json().get("error") or {}).get("code") == "CALC_INVALID_SEGMENT",
          "空 segments → CALC_INVALID_SEGMENT", str(r.json()))

    print("\n[⑤ 不污染 provider 缓存（连调两次结果一致）]")
    a = CLIENT.post("/api/calc/monthly-spaces", json={**base, "session_ids": [sid1]}).json()
    bb = CLIENT.post("/api/calc/monthly-spaces", json={**base, "session_ids": [sid1]}).json()
    va = [(m["month"], m["min_segment"]) for m in (a.get("data") or {}).get("months", [])]
    vb = [(m["month"], m["min_segment"]) for m in (bb.get("data") or {}).get("months", [])]
    check(va == vb, "两次调用结果一致（unify 用的是副本，没改缓存）", f"{va} vs {vb}")

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n合计 PASS {passed} / FAIL {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
