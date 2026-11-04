# -*- coding: utf-8 -*-
"""模拟 session 的端到端验证：apply → preview / calc → 重复 apply → revert。

这一组判据针对四个容易「静默错」的点
--------------------------------------
① 模拟数据真的能被 `/api/preview` 取到（不是仍读原始文件）
② 模拟数据真的能被 `/api/calc/spaces` 算（不是返回原值）
③ 连点「应用」不留多个模拟 session（同一来源只保留一个）
④ 「恢复原始」后当前 session 换回原值，且模拟 session 被回收

跑法：python tests/verify_simulate_apply.py
"""
import os
import sys

import numpy as np
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from api.app import app            # noqa: E402
from api.routes_ingest import get_session  # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}  {detail}")


client = TestClient(app)

# ── 准备一个真实 session ──
W1 = os.path.join(ROOT, "baseline", "_inputs", "W1", "W1_宽表96点.xlsx")
r = client.post("/api/ingest/allow", json={"paths": [W1]})
assert r.status_code == 200, r.text
r = client.post("/api/ingest/inspect", json={"path": W1})
insp = r.json()["data"]
sh = insp["sheets"][0]
sid_real = client.post("/api/ingest/mapping", json={
    "path": W1, "mapping": sh["suggest"], "sheet": sh["name"],
}).json()["data"]["session_id"]
print(f"[0] 真实 session = {sid_real}")

# 原始数据的统计，稍后用于对照
pre0 = client.get(f"/api/preview?session_id={sid_real}&group=0&format=json").json()["data"]
arr0 = np.array(pre0["load_array"])
print(f"    原始 shape={arr0.shape} mean={arr0.mean():.1f}")

# ── 生成一条明显不同的模拟曲线（平均值放大 10 倍，便于区分）──
sim = client.post("/api/simulate/load", json={
    "industry": "电解铝", "avg_power_kw": 5000.0, "n_days": arr0.shape[0],
    "n_points": arr0.shape[1], "noise_std": 0.0, "seed": 424242,
}).json()["data"]

print("[1] apply")
ap = client.post("/api/simulate/apply", json={
    "original_session_id": sid_real,
    "load_array": sim["load_array"],
    "time_labels": sim["time_labels"],
    "date_labels": sim["date_labels"],
    "industry": "电解铝",
    "seed": sim["applied_seed"],
}).json()
check("apply 成功", ap["ok"] is True, ap.get("error"))
sid_sim = ap["data"]["session_id"]
check("group_count = 1", ap["data"]["group_count"] == 1, str(ap["data"]["group_count"]))
check("origin 带回 industry/seed",
      ap["data"]["origin"]["industry"] == "电解铝"
      and ap["data"]["origin"]["seed"] == 424242,
      str(ap["data"]["origin"]))

sess = get_session(sid_sim)
check("session.source = simulated", sess.get("source") == "simulated", sess.get("source"))

print("[2] preview 取到的是模拟数据，不是原文件")
pre1 = client.get(f"/api/preview?session_id={sid_sim}&group=0&format=json").json()["data"]
arr1 = np.array(pre1["load_array"])
check("行数与模拟一致", arr1.shape == arr0.shape, f"{arr1.shape}")
check("数值等于模拟曲线（逐元素）",
      np.allclose(arr1, np.array(sim["load_array"]), atol=1e-6),
      f"mean {arr0.mean():.1f} -> {arr1.mean():.1f}")

print("[3] calc 也算的是模拟数据")
PERIODS = [{"start": 0, "end": 8, "type": "charge"},
           {"start": 18, "end": 22, "type": "discharge"}]

calc = client.post("/api/calc/capacity", json={
    "session_id": sid_sim, "group": 0, "billing": {"type": "capacity", "value": 20000},
    "mode": "A", "basis": "min_feasible", "periods": PERIODS,
}).json()
check("容量测算能跑", calc["ok"] is True, calc.get("error"))
sim_demand = calc["data"]["daily_demand"]["value_kwh"]

calc0 = client.post("/api/calc/capacity", json={
    "session_id": sid_real, "group": 0, "billing": {"type": "capacity", "value": 20000},
    "mode": "A", "basis": "min_feasible", "periods": PERIODS,
}).json()
real_demand = calc0["data"]["daily_demand"]["value_kwh"]
check("模拟与原始的测算结果不同（证明取数没走错）",
      abs(sim_demand - real_demand) > 1.0,
      f"模拟 {sim_demand:.0f} vs 原始 {real_demand:.0f}")

print("[4] 连点三次「应用」只留一个模拟 session")
ids = []
for _ in range(3):
    j = client.post("/api/simulate/apply", json={
        "original_session_id": sid_real,
        "load_array": sim["load_array"],
        "time_labels": sim["time_labels"],
        "date_labels": sim["date_labels"],
        "industry": "电解铝", "seed": 424242,
    }).json()
    ids.append(j["data"]["session_id"])
alive = [s for s in ids if get_session(s) is not None]
check("旧模拟被清掉，只剩最后一个", len(alive) == 1 and alive[0] == ids[-1],
      f"活着 {len(alive)} 个 / 共建 {len(ids)} 个")
check("最后一次 dropped_previous = 1", j["data"]["dropped_previous"] == 1,
      str(j["data"]["dropped_previous"]))
sid_latest = ids[-1]

print("[5] revert")
rv = client.post("/api/simulate/revert", json={"original_session_id": sid_real}).json()
check("revert 成功", rv["ok"] is True, rv.get("error"))
check("清掉 1 个模拟 session", rv["data"]["dropped"] == 1, str(rv["data"]["dropped"]))
check("模拟 session 已回收", get_session(sid_latest) is None, "")
check("原始 session 仍在", get_session(sid_real) is not None, "")
pre2 = client.get(f"/api/preview?session_id={sid_real}&group=0&format=json").json()["data"]
check("原始 session 的数据没被污染",
      np.allclose(np.array(pre2["load_array"]), arr0, atol=1e-6), "")

print("[6] 边界")
bad = client.post("/api/simulate/apply", json={
    "original_session_id": "不存在的session",
    "load_array": [[1.0, 2.0]], "time_labels": ["00:00", "00:15"],
    "date_labels": ["2025-01-01"],
}).json()
check("原始 session 不存在 → 明确错误码",
      bad["ok"] is False and bad["error"]["code"] == "SIM_ORIGINAL_NOT_FOUND",
      str(bad.get("error")))

bad2 = client.post("/api/simulate/apply", json={
    "original_session_id": sid_real,
    "load_array": [[1.0, 2.0]], "time_labels": ["00:00"],   # 长度不匹配
    "date_labels": ["2025-01-01"],
}).json()
check("列数与 time_labels 不匹配 → SIM_INVALID_ARRAY",
      bad2["ok"] is False and bad2["error"]["code"] == "SIM_INVALID_ARRAY",
      str(bad2.get("error")))

print()
print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
if FAIL:
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
