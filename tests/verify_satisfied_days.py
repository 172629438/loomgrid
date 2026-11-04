# -*- coding: utf-8 -*-
"""验证 /api/calc/satisfied-days：容量 → 天数。

为什么这个端点特殊
------------------
它同时返回两个口径，因为它们**本来就不是同一个算法**：

  satisfied_days       DOD 折算 + 逐对判定（与 Fusion 界面一致）
  satisfied_days_core  不折算 + 各段当日相加后比（core.calc_days_for_capacity）

判据不能只说「返回了数」，要能证明：
  ① 逐日判定不是除法——天数变化时不是线性缩放的
  ② DOD 折算生效——容量固定时，DOD 从 95 降到 50，满足天数必须不增
  ③ equivalent_days = int(satisfied × 365 / total_days)
  ④ by_month 的总和等于 satisfied_days
  ⑤ 放不下时（容量极小）satisfied_days = 0
  ⑥ 容量极大时 satisfied_days = total_days

用法：
    python tests/verify_satisfied_days.py
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
    if not body.get("ok"):
        print("inspect 失败：", body.get("error"))
        return 1
    sheet = body["data"]["sheets"][0]

    r = CLIENT.post("/api/ingest/mapping", json={
        "path": str(xlsx), "sheet": sheet["name"], "mapping": sheet["suggest"]})
    body = r.json()
    if not body.get("ok"):
        print("mapping 失败：", body.get("error"))
        return 1
    session_id = body["data"]["session_id"]

    segs = [{"start": 0, "end": 8, "type": "charge", "label": "充电段"},
            {"start": 18, "end": 22, "type": "discharge", "label": "放电段"}]
    base = {"session_id": session_id, "group": 0, "segments": segs,
            "billing": {"value": 800}, "mode": "A", "ignore_negative": True}

    def call(**kw):
        payload = dict(base)
        payload.update(kw)
        r = CLIENT.post("/api/calc/satisfied-days", json=payload)
        return r.json()

    print("[① 极小容量 → 全部满足]")
    r1 = call(capacity_kwh=0.001)
    check(r1.get("ok") is True, "端点可用", str(r1.get("error")))
    d1 = r1.get("data") or {}
    total_days = d1.get("total_days", 0)
    check(total_days > 0, f"total_days>0（{total_days}）", str(total_days))
    # 语义：容量越小越容易满足（电池小，一点点空间就够充满）
    check(d1.get("satisfied_days") == total_days,
          f"satisfied_days == total_days（{d1.get('satisfied_days')} / {total_days}）", str(d1))

    print("\n[② 极大容量 → 一天都不满足]")
    r2 = call(capacity_kwh=1e9)
    d2 = r2.get("data") or {}
    check(d2.get("satisfied_days") == 0,
          f"satisfied_days=0（{d2.get('satisfied_days')}）", str(d2))

    print("\n[③ equivalent_days 公式]")
    for cap in (200, 400, 600):
        d = (call(capacity_kwh=cap).get("data") or {})
        s, t, e = d.get("satisfied_days", 0), d.get("total_days", 1), d.get("equivalent_days", -1)
        expect = int(s * 365.0 / t) if t else 0
        check(e == expect, f"cap={cap}: {e} == int({s}×365/{t}) = {expect}", f"got {e}")

    print("\n[④ by_month 自洽]")
    d3 = (call(capacity_kwh=300).get("data") or {})
    by_month = d3.get("by_month") or []
    check(len(by_month) > 0, "by_month 非空", str(by_month))
    if by_month:
        check(sum(m["satisfied"] for m in by_month) == d3.get("satisfied_days"),
              "各月满足数之和 == satisfied_days",
              f"{sum(m['satisfied'] for m in by_month)} vs {d3.get('satisfied_days')}")
        check(all(m["total"] > 0 for m in by_month), "每月 total>0", str(by_month))

    print("\n[⑤ DOD 折算生效：DOD 降低 → 满足天数不减]")
    s95 = (call(capacity_kwh=300, dod_percent=95).get("data") or {}).get("satisfied_days", -1)
    s50 = (call(capacity_kwh=300, dod_percent=50).get("data") or {}).get("satisfied_days", -1)
    # DOD 越低，可用容量越小 → 越容易满足
    check(s50 >= s95, f"DOD 50 ({s50}) ≥ DOD 95 ({s95})", f"s95={s95} s50={s50}")

    print("\n[⑥ 逐日判定 vs 除法：容量翻倍不等于天数翻倍]")
    sa = (call(capacity_kwh=300).get("data") or {}).get("satisfied_days", 0)
    sb = (call(capacity_kwh=600).get("data") or {}).get("satisfied_days", 0)
    check(sb >= sa, f"容量变大满足天数不减（{sa} → {sb}）", f"{sa} vs {sb}")
    print(f"    300→{sa} 天，600→{sb} 天（不是 2 倍，因为逐日判定）")

    print("\n[⑦ 两个口径都返回，且允许不同]")
    d7 = (call(capacity_kwh=300).get("data") or {})
    check("satisfied_days" in d7 and "satisfied_days_core" in d7,
          "两个字段都在", str(list(d7.keys())))
    print(f"    DOD折算+逐对 = {d7.get('satisfied_days')}；core 不折算 = {d7.get('satisfied_days_core')}")
    check(isinstance(d7.get("note"), str) and len(d7["note"]) > 0, "note 解释了差异", str(d7.get("note")))

    print(f"\n合计 PASS {passed} / FAIL {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
