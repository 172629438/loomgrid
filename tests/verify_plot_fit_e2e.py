# -*- coding: utf-8 -*-
"""验证 /api/plot/fit 与 plotting.curves._compute_fits 完全同源。

为什么要有这个脚本
------------------
这个端点的存在理由就是「不许出现第二份拟合实现」。所以验收判据不是
「能返回数据」，而是「返回的数据与直接调用同一个函数逐元素相等」。
只测能不能跑，测不出「有人偷偷在路由里重写了一遍拟合」。

判据
----
① 端到端跑通：inspect → mapping → fit，ok=True
② 数值同源：端点每条曲线 == 直接调 _compute_fits 的结果（atol=0，NaN 位置一致）
③ 非法类型 → 结构化错误 PLOT_INVALID_FIT_TYPE，不是 500
④ 状态字段自洽：available ∪ unavailable == requested，且 fits 的 key 集合 == available

用法
----
    python tests/verify_plot_fit_e2e.py
    python tests/verify_plot_fit_e2e.py --scenario W1 L1
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# inspect 的路径白名单：只放开基线输入目录，不放开整个磁盘
os.environ.setdefault("LGM_ALLOW_ROOTS", str(ROOT / "baseline" / "_inputs"))

import numpy as np  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.app import app  # noqa: E402
from api.routes_ingest import get_session  # noqa: E402
from core.models import GroupSummary  # noqa: E402
from plotting.curves import _compute_fits  # noqa: E402
from services.provider import DataProvider  # noqa: E402

CLIENT = TestClient(app)
TYPES = ["poly2", "poly3", "moving_avg", "gaussian", "spline"]

_passed = 0
_failed = 0


def check(ok: bool, label: str, detail: str = "") -> None:
    global _passed, _failed
    if ok:
        _passed += 1
        print(f"  PASS  {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}  {detail}")


def first_input(scenario: str) -> Path | None:
    d = ROOT / "baseline" / "_inputs" / scenario
    if not d.is_dir():
        return None
    for p in sorted(d.glob("*.xlsx")):
        return p
    for p in sorted(d.glob("*.csv")):
        return p
    return None


def run_scenario(scenario: str) -> None:
    print(f"\n[{scenario}]")
    path = first_input(scenario)
    if path is None:
        check(False, "找到基线输入", f"{scenario} 下没有 xlsx/csv")
        return

    # ① inspect
    r = CLIENT.post("/api/ingest/inspect", json={"path": str(path)})
    body = r.json()
    if not body.get("ok"):
        check(False, "POST /api/ingest/inspect", str(body.get("error")))
        return
    sheets = body["data"]["sheets"]
    sheet = next((s for s in sheets if s["suggest"].get("load_col")), sheets[0])
    check(True, f"inspect：{sheet['name']}（置信度 {sheet['confidence']['score']:.2f}）")

    # ② mapping
    r = CLIENT.post("/api/ingest/mapping", json={
        "path": str(path),
        "sheet": sheet["name"],
        "mapping": sheet["suggest"],
    })
    body = r.json()
    if not body.get("ok"):
        check(False, "POST /api/ingest/mapping", str(body.get("error")))
        return
    session_id = body["data"]["session_id"]
    groups = body["data"]["groups"]
    if not groups:
        check(False, "mapping 产出了分组", "groups 为空")
        return
    check(True, f"mapping：{len(groups)} 个分组，session={session_id[:8]}…")

    # ③ fit 端点
    r = CLIENT.post("/api/plot/fit", json={
        "session_id": session_id, "group": 0, "types": TYPES})
    body = r.json()
    if not body.get("ok"):
        check(False, "POST /api/plot/fit", str(body.get("error")))
        return
    data = body["data"]
    check(True, f"fit：available={data['available']} unavailable={data['unavailable']}")

    # ④ 状态字段自洽
    check(set(data["requested"]) | set() == set(TYPES),
          "requested 覆盖请求类型", str(data["requested"]))
    check(set(data["available"]) | set(data["unavailable"]) == set(data["requested"]),
          "available ∪ unavailable == requested",
          f"{data['available']} / {data['unavailable']}")
    check(set(f["key"] for f in data["fits"]) == set(data["available"]),
          "fits 的 key 集合 == available")

    # ⑤ 数值同源：直接调用同一函数对比
    sess = get_session(session_id)
    gs = GroupSummary.from_dict(sess["groups"][0])
    payload = DataProvider().get_group_data(gs)
    load_array = np.asarray(payload["load_array"], dtype=float)
    x = np.arange(load_array.shape[1], dtype=float)
    expected = _compute_fits(x, load_array, data["requested"])

    check(set(expected) == set(data["available"]),
          "直接调用的 key 集合 == 端点的 available",
          f"{sorted(expected)} vs {data['available']}")

    worst = 0.0
    for f in data["fits"]:
        key = f["key"]
        if key not in expected:
            check(False, f"{key} 数值对比", "直接调用的结果里没有这一项")
            continue
        curve, _label = expected[key]
        got = np.array([np.nan if v is None else float(v) for v in f["values"]], dtype=float)
        if got.shape != np.shape(curve):
            check(False, f"{key} 长度", f"{got.shape} vs {np.shape(curve)}")
            continue
        same_nan = np.array_equal(np.isnan(got), np.isnan(curve))
        diff = np.abs(np.nan_to_num(got, nan=0.0) - np.nan_to_num(curve, nan=0.0))
        worst = max(worst, float(diff.max()) if diff.size else 0.0)
        check(same_nan and float(diff.max() if diff.size else 0.0) == 0.0,
              f"{key} 逐元素完全相等",
              f"max|Δ|={diff.max() if diff.size else 0:.3e}, nan_mask_equal={same_nan}")

    print(f"  最差偏差：{worst:.3e}")

    # ⑥ 非法类型
    r = CLIENT.post("/api/plot/fit", json={
        "session_id": session_id, "group": 0, "types": ["not_a_fit"]})
    body = r.json()
    check(r.status_code == 200 and body.get("ok") is False
          and body.get("error", {}).get("code") == "PLOT_INVALID_FIT_TYPE",
          "非法类型返回结构化错误（200 + PLOT_INVALID_FIT_TYPE）",
          f"status={r.status_code} body={body}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", nargs="*", default=["W1", "L1", "S2"])
    args = ap.parse_args()

    print("LGM_ALLOW_ROOTS =", os.environ.get("LGM_ALLOW_ROOTS"))
    for s in args.scenario:
        run_scenario(s)

    print(f"\n合计 PASS {_passed} / FAIL {_failed}")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
