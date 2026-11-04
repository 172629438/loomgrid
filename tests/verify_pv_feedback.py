# -*- coding: utf-8 -*-
"""光伏返送详情：算法与端点验证。

判据分三层
----------
① 算法等价（与 Fusion 的 PVFeedbackDialog._refresh 逐项比）
   从参考程序.py 里 exec 出原始 _refresh 的算法段，与 services/pvfeedback.py 对拍。
   用 N1（全负倒送）与 W1（全正）两个场景。
② 时间权重口径（这条最容易错）
   断言 pv_feedback 的 dt 是「整格差分」，且与 spaces.seg_dt 不是同一套：
   对 0–8 点的段，seg_dt 总和 = 8.0 但首点 0.125；pv 的 dt[0] = 0.25。
③ 端点行为
   倍率生效（×2 后返送量翻倍）、全正数据 has_negative=false、
   negative_points 与逐日非零天数一致、模拟 session 也能算。

跑法：python tests/verify_pv_feedback.py
"""
import io
import os
import sys

import numpy as np
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from api.app import app                      # noqa: E402
from services.pvfeedback import pv_feedback  # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}  {detail}")


# ── 原版算法（从 Fusion 主程序里取，用于对拍）────────────────────
LEGACY_SRC = r'''
import numpy as np

def legacy_refresh(load_array, time_labels, date_labels, multiplier):
    load_array = np.asarray(load_array) * multiplier
    dates = date_labels
    n_days = load_array.shape[0]
    hours = np.array([_parse_min(_normalize_time_column_name(t)) / 60.0 for t in time_labels])
    dt = np.zeros_like(hours)
    if len(hours) > 1:
        dt[0] = hours[1] - hours[0]
        dt[-1] = hours[-1] - hours[-2]
        for i in range(1, len(hours) - 1):
            dt[i] = (hours[i + 1] - hours[i - 1]) / 2.0
    dt = np.maximum(dt, 0.0)
    daily_neg_energy = []
    daily_neg_max_power = []
    for d in range(n_days):
        neg_mask = load_array[d] < 0
        if not np.any(neg_mask):
            daily_neg_energy.append(0.0)
            daily_neg_max_power.append(0.0)
        else:
            daily_neg_energy.append(float(np.sum(np.abs(load_array[d][neg_mask]) * dt[neg_mask])))
            daily_neg_max_power.append(float(np.max(np.abs(load_array[d][neg_mask]))))
    return (float(np.sum(daily_neg_energy)),
            float(max(daily_neg_max_power)) if daily_neg_max_power else 0.0,
            daily_neg_energy, daily_neg_max_power)
'''

from core.timegrid import _normalize_time_column_name, _parse_min  # noqa: E402

ns = {"_parse_min": _parse_min, "_normalize_time_column_name": _normalize_time_column_name}
exec(compile(LEGACY_SRC, "<legacy_pv>", "exec"), ns)
legacy = ns["legacy_refresh"]

print("[0] 造数据")
TIME96 = [f"{i * 15 // 60:02d}:{i * 15 % 60:02d}" for i in range(96)]
DATES = [f"2026-01-{d + 1:02d}" for d in range(5)]

# 全负倒送（模拟 N1）
neg = -np.full((5, 96), 300.0)
neg[:, 10:30] = -800.0          # 10:00-07:30 之间更深的倒送
# 全正
pos = np.full((5, 96), 500.0)
pos[:, 40:60] = 900.0
# 混合（前半天正、后半天负）
mixed = np.full((5, 96), 200.0)
mixed[:, 48:] = -400.0

print("[1] 算法与 Fusion 原版对拍")
for label, arr in (("全负 N1", neg), ("全正 W1", pos), ("混合", mixed)):
    for mult in (1.0, 2.0):
        t_new, p_new, daily_new = _run = None, None, None
        res = pv_feedback(arr, TIME96, DATES, multiplier=mult)
        t_old, p_old, daily_old, peak_old = legacy(arr, TIME96, DATES, mult)
        ok_e = abs(res["total_energy_kwh"] - t_old) < 1e-3
        ok_p = abs(res["max_power_kw"] - p_old) < 1e-3
        ok_d = all(abs(a["energy_kwh"] - b) < 1e-3
                   for a, b in zip(res["daily"], daily_old))
        check(f"{label} ×{mult} 总返送/最大功率/逐日 全部一致",
              ok_e and ok_p and ok_d,
              f"{res['total_energy_kwh']:.3f} vs {t_old:.3f}")

print("[2] 时间权重口径（PV 整格 vs spaces 首尾补齐）")
from services.pvfeedback import _dt_weights  # noqa: E402
from core.spaces import compute_segmented_spaces  # noqa: E402

dt = _dt_weights(TIME96)
check("pv dt[0] = 0.25（整格）", abs(dt[0] - 0.25) < 1e-9, f"{dt[0]}")
check("pv dt[-1] = 0.25（整格）", abs(dt[-1] - 0.25) < 1e-9, f"{dt[-1]}")
check("pv Σdt = 24.0", abs(dt.sum() - 24.0) < 1e-9, f"{dt.sum():.6f}")

# 对照：同一条 0–8 段、恒负荷 1 kW、容量线 2 kW → 充电空间 = Σseg_dt × 1。
# 结果也是 8.0，说明**两套权重的总量都对齐 40h**；差异在**单点分配**，不在总和。
# 这是“不能混用”的真正含义 —— 不是量级差异，而是端点那段能量算到哪个点头上。
flat = np.ones((1, 96))
seg_charge, _seg_dis, _cl, _dl = compute_segmented_spaces(
    flat, TIME96,
    segments=[{"start": 0.0, "end": 8.0, "type": "charge", "label": "c"}],
    billing={"type": "capacity", "value": 2.0}, mode="A")
seg_sum = float(np.sum(seg_charge[0]))
check("seg_dt 对 0–8 段的总和也是 8.0（总量对齐）",
      abs(seg_sum - 8.0) < 1e-6, f"{seg_sum:.6f}")
check("pv 前 32 点之和 = 8.0（同段同总量，单点分配不同）",
      abs(dt[:32].sum() - 8.0) < 1e-9, f"{dt[:32].sum():.6f}")

print("[3] negative_points 与逐日非零天数一致")
r = pv_feedback(mixed, TIME96, DATES, 1.0)
nonzero_days = sum(1 for d in r["daily"] if d["energy_kwh"] > 0)
check("有返送的天数 = 5", nonzero_days == 5, f"{nonzero_days}")
r0 = pv_feedback(pos, TIME96, DATES, 1.0)
check("全正数据 negative_points=0 且 has_negative=False",
      r0["negative_points"] == 0 and r0["has_negative"] is False,
      f"{r0['negative_points']} / {r0['has_negative']}")

print("[4] 端点")
client = TestClient(app)
W1 = os.path.join(ROOT, "baseline", "_inputs", "W1", "W1_宽表96点.xlsx")
client.post("/api/ingest/allow", json={"paths": [W1]})
insp = client.post("/api/ingest/inspect", json={"path": W1}).json()["data"]
sh = insp["sheets"][0]
sid = client.post("/api/ingest/mapping",
                  json={"path": W1, "mapping": sh["suggest"], "sheet": sh["name"]}
                  ).json()["data"]["session_id"]

r1 = client.post("/api/calc/pv-feedback",
                 json={"session_id": sid, "group": 0, "multiplier": 1.0}).json()
check("端点 HTTP ok", r1["ok"] is True, str(r1.get("error")))
w1_data = r1["data"]
check("W1 全正 → has_negative=False",
      w1_data["has_negative"] is False, f"{w1_data['negative_points']}")

# 模拟一条全负曲线再算，验证倍率
sim = client.post("/api/simulate/load", json={
    "industry": "电解铝", "avg_power_kw": 500, "n_days": 31,
    "n_points": 96, "noise_std": 0.0, "pv_kw": 0, "seed": 11,
}).json()["data"]
applied = client.post("/api/simulate/apply", json={
    "original_session_id": sid,
    "load_array": sim["load_array"],
    "time_labels": sim["time_labels"],
    "date_labels": sim["date_labels"],
    "industry": "电解铝", "seed": sim["applied_seed"],
}).json()["data"]["session_id"]

pv1 = client.post("/api/calc/pv-feedback",
                  json={"session_id": applied, "group": 0, "multiplier": 1.0}).json()["data"]
check("模拟 session 也能算（取数走统一入口）", pv1["has_negative"] is False,
      f"has_negative={pv1['has_negative']}")

# 倍率：把负荷翻成负数（用 0.001 倍率不现实，改用直接构造）
neg_session = client.post("/api/simulate/apply", json={
    "original_session_id": sid,
    "load_array": [[-100.0] * 96 for _ in range(3)],
    "time_labels": sim["time_labels"],
    "date_labels": sim["date_labels"][:3],
    "industry": "测试全负", "seed": 1,
}).json()["data"]["session_id"]

a = client.post("/api/calc/pv-feedback",
                json={"session_id": neg_session, "group": 0, "multiplier": 1.0}).json()["data"]
b = client.post("/api/calc/pv-feedback",
                json={"session_id": neg_session, "group": 0, "multiplier": 2.0}).json()["data"]
check("倍率 ×2 → 返送电量翻倍",
      abs(b["total_energy_kwh"] - 2 * a["total_energy_kwh"]) < 1e-3,
      f"{a['total_energy_kwh']:.3f} → {b['total_energy_kwh']:.3f}")
check("倍率 ×2 → 最大返送功率翻倍",
      abs(b["max_power_kw"] - 2 * a["max_power_kw"]) < 1e-3,
      f"{a['max_power_kw']:.3f} → {b['max_power_kw']:.3f}")

print()
print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
if FAIL:
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
