# -*- coding: utf-8 -*-
"""端点级验证：/api/simulate/templates 与 /api/simulate/load。

重点判据（不是「能返回数据」，而是「语义对」）
------------------------------------------------
① templates 返回 25 项，字段齐全，description 非空
② 同 seed 两次调用 → 逐元素完全一致（预览=应用的前提）
③ seed=None 两次调用 → applied_seed 不同，且都能用回显的 seed 复现
④ 未知行业 → 回退到电子制造，但 requested_industry 保留用户输入（不静默改口）
⑤ 只传 avg_power_kw，不传 daily_load_factor → 用模板值（不被默认值盖掉）
⑥ n_points = 24/48/96 → shape 与 time_labels 长度一致
⑦ 非法参数（avg_power_kw=0）→ 422，不是 500

跑法：python tests/verify_simulate_api.py
"""
import os
import sys

import numpy as np
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from api.app import app  # noqa: E402

PASS = []
FAIL = []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}  {detail}")


client = TestClient(app)

print("[1] GET /api/simulate/templates")
r = client.get("/api/simulate/templates")
j = r.json()
check("HTTP 200 + ok", r.status_code == 200 and j["ok"] is True, f"{r.status_code}")

items = j["data"]["templates"]
check("25 个模板", len(items) == 25, f"实际 {len(items)}")
check("字段齐全",
      all(k in items[0] for k in
          ("key", "name", "daily_load_factor", "peak_valley_ratio",
           "weekend_factor", "schedule", "morning_peak", "evening_peak",
           "noon_valley", "description")),
      "")
check("description 全部非空", all(t["description"] for t in items),
      f"空的有 {sum(1 for t in items if not t['description'])} 个")
check("含电解铝且 schedule=three_shift",
      any(t["key"] == "电解铝" and t["schedule"] == "three_shift" for t in items), "")

print("[2] 同 seed 两次调用完全一致")
body = {"industry": "造纸", "avg_power_kw": 600.0, "n_days": 10, "n_points": 96,
        "noise_std": 0.05, "seed": 20261104}
a = client.post("/api/simulate/load", json=body).json()["data"]
b = client.post("/api/simulate/load", json=body).json()["data"]
check("两次 load_array 逐元素相等",
      np.array_equal(np.array(a["load_array"]), np.array(b["load_array"])), "")
check("applied_seed 回显等于请求 seed", a["applied_seed"] == 20261104,
      f"{a['applied_seed']}")

print("[3] seed=None → 随机，但可用回显复现")
x = client.post("/api/simulate/load",
                json={**body, "seed": None}).json()["data"]
y = client.post("/api/simulate/load",
                json={**body, "seed": None}).json()["data"]
check("两次 applied_seed 不同", x["applied_seed"] != y["applied_seed"],
      f"{x['applied_seed']} vs {y['applied_seed']}")
check("两次曲线不同",
      not np.array_equal(np.array(x["load_array"]), np.array(y["load_array"])), "")

rep = client.post("/api/simulate/load",
                  json={**body, "seed": x["applied_seed"]}).json()["data"]
check("用回显 seed 能复现同一条",
      np.array_equal(np.array(x["load_array"]), np.array(rep["load_array"])), "")

print("[4] 未知行业回退")
u = client.post("/api/simulate/load",
                json={**body, "industry": "不存在的行业"}).json()["data"]
ref = client.post("/api/simulate/load",
                  json={**body, "industry": "电子制造"}).json()["data"]
check("回退到电子制造",
      np.array_equal(np.array(u["load_array"]), np.array(ref["load_array"])), "")
check("requested_industry 保留用户输入",
      u["requested_industry"] == "不存在的行业" and u["industry"] == "电子制造",
      f"{u['requested_industry']} -> {u['industry']}")

print("[5] 不传 daily_load_factor → 用模板值")
# 电解铝 daily_load_factor=0.96（接近平线），商业楼宇 0.40（峰谷明显）
alu = client.post("/api/simulate/load",
                  json={"industry": "电解铝", "avg_power_kw": 500.0, "n_days": 3,
                        "n_points": 96, "noise_std": 0.0, "seed": 1}).json()["data"]
com = client.post("/api/simulate/load",
                  json={"industry": "商业楼宇", "avg_power_kw": 500.0, "n_days": 3,
                        "n_points": 96, "noise_std": 0.0, "seed": 1}).json()["data"]
alu_pv = alu["stats"]["max"] / max(alu["stats"]["min"], 1e-9)
com_pv = com["stats"]["max"] / max(com["stats"]["min"], 1e-9)
check("电解铝比商业楼宇平（模板参数生效）", alu_pv < com_pv,
      f"电解铝 peak/valley={alu_pv:.2f} vs 商业楼宇 {com_pv:.2f}")
check("config 里没有凭空补的 daily_load_factor",
      "daily_load_factor" not in alu["config"], f"{list(alu['config'])}")

print("[6] n_points 一致性")
for npnt in (24, 48, 96):
    d = client.post("/api/simulate/load",
                    json={"industry": "医院", "avg_power_kw": 300.0, "n_days": 2,
                          "n_points": npnt, "noise_std": 0.0, "seed": 5}).json()["data"]
    check(f"n_points={npnt} shape 与 labels 一致",
          d["shape"] == [2, npnt] and len(d["time_labels"]) == npnt,
          f"shape={d['shape']} labels={len(d['time_labels'])}")

print("[7] 非法参数")
bad = client.post("/api/simulate/load",
                  json={"industry": "造纸", "avg_power_kw": 0})
check("avg_power_kw=0 → 422", bad.status_code == 422, f"{bad.status_code}")
bad2 = client.post("/api/simulate/load",
                   json={"industry": "造纸", "avg_power_kw": 100, "n_points": 5})
check("n_points=5 → 422（低于下限）", bad2.status_code == 422, f"{bad2.status_code}")

print()
print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
if FAIL:
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
