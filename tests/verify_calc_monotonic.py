# -*- coding: utf-8 -*-
"""用 check_monotonic 扫容量，验证 satisfied-days 的中间态。

为什么点值测试不够
------------------
之前五个测试点全落在两端（全满足 / 全不满足），中间那一段没覆盖。
「差一位」的错（>= vs >、int vs round）只在中间暴露。

三条判据（用 tools/check_monotonic.py）：
  ① 单调     容量 ↑ → 满足天数不增
  ② 中间态   区间内既不是 0 也不是 total
  ③ 端点     极小容量给 total，极大容量给 0

用法：
    python tests/verify_calc_monotonic.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("LGM_ALLOW_ROOTS", str(ROOT / "baseline" / "_inputs"))

from fastapi.testclient import TestClient  # noqa: E402

from api.app import app  # noqa: E402
from tools.check_monotonic import assert_monotonic, format_table, scan  # noqa: E402

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


def main() -> int:
    xlsx = next(iter(sorted((ROOT / "baseline" / "_inputs" / "W1").glob("*.xlsx"))), None)
    if xlsx is None:
        print("找不到 W1 输入")
        return 1

    r = CLIENT.post("/api/ingest/inspect", json={"path": str(xlsx)})
    body = r.json()
    sheet = body["data"]["sheets"][0]
    r = CLIENT.post("/api/ingest/mapping", json={
        "path": str(xlsx), "sheet": sheet["name"], "mapping": sheet["suggest"]})
    session_id = r.json()["data"]["session_id"]

    segs = [{"start": 0, "end": 8, "type": "charge", "label": "充电段"},
            {"start": 18, "end": 22, "type": "discharge", "label": "放电段"}]
    base = {"session_id": session_id, "group": 0, "segments": segs,
            "billing": {"value": 800}, "mode": "A", "ignore_negative": True,
            "dod_percent": 95}

    def days_for(cap: float) -> int:
        payload = dict(base)
        payload["capacity_kwh"] = cap
        resp = CLIENT.post("/api/calc/satisfied-days", json=payload)
        b = resp.json()
        if not b.get("ok"):
            raise RuntimeError(f"端点失败 cap={cap}: {b.get('error')}")
        return int((b.get("data") or {}).get("satisfied_days", -1))

    # 先探一次拿 total_days
    probe = CLIENT.post("/api/calc/satisfied-days",
                        json={**base, "capacity_kwh": 1}).json()
    total_days = (probe.get("data") or {}).get("total_days", 0)
    print(f"total_days = {total_days}\n")

    xs = [50, 200, 400, 600, 800, 1000, 1200, 1500, 1800, 2000,
          2100, 2200, 2300, 2400, 2500, 2600, 2700, 2800, 2900, 3000,
          5000, 1e6]
    rep = scan(days_for, xs, expect="nonincreasing", label="capacity→days")
    print("扫描结果：")
    print(format_table(rep))
    print(f"  distinct = {rep.distinct}")
    print(f"  interior = {rep.interior}")
    print()

    print("[① 单调性]")
    check(rep.monotonic, "容量递增 → 满足天数不增", str(rep.violations[:3]))

    print("\n[③ 端点]")
    check(rep.first == total_days, f"极小容量 → total（{rep.first}）", str(rep.first))
    check(rep.last == 0, f"极大容量 → 0（{rep.last}）", str(rep.last))

    print("\n[② 中间态]")
    if rep.has_interior:
        check(True, f"存在中间态：{rep.interior}", "")
    else:
        # 数据分布导致没有中间态时，如实报告而不是硬凑
        print(f"  未观察到中间态（distinct={rep.distinct}）。"
              f"这批数据在容量 {xs[0]}–{xs[-1]} 之间直接跳变。")
        check(False, "存在中间态", f"distinct={rep.distinct}")

    # 用工具的统一断言再跑一遍，确保两边口径一致
    print("\n[工具断言一致性]")
    try:
        assert_monotonic(rep, require_interior=rep.has_interior)
        check(True, "assert_monotonic 通过", "")
    except AssertionError as e:
        check(False, "assert_monotonic 报错", str(e))

    print(f"\n合计 PASS {passed} / FAIL {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
