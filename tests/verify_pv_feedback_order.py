# -*- coding: utf-8 -*-
"""光伏返送：时刻乱序时的处理。

`_dt_weights` 末尾有 `np.maximum(dt, 0.0)`——时刻乱序时 diff 为负，
被抹成 0，那部分时长就丢了。实测 96 点全程倒送 100 kW：

    正常顺序   2400.0 kWh
    乱序       2325.0 kWh     少 3.12%
"""
import sys

import numpy as np

REPO = r"C:\Users\LENOVO\.hanako\repos\172629438__loomgrid"
sys.path.insert(0, REPO)
sys.stdout.reconfigure(encoding="utf-8")

from services.pvfeedback import _dt_weights, _is_sorted_hours, pv_feedback

ok, bad = [], []


def check(name, cond, detail=""):
    if cond:
        ok.append(name)
        print(f"  [PASS] {name}")
    else:
        bad.append(name)
        print(f"  [FAIL] {name}   {detail}")


T96 = [f"{i//4:02d}:{(i%4)*15:02d}" for i in range(96)]

print("=" * 78)
print("① 正常顺序：Σ dt = 24 小时")
print("=" * 78)
dt = _dt_weights(T96)
print(f"  Σ dt = {dt.sum():.4f}，dt==0 的点 {int((dt == 0).sum())}")
check("Σ dt = 24", abs(dt.sum() - 24.0) < 1e-9, dt.sum())
check("无零权重", int((dt == 0).sum()) == 0)

print()
print("=" * 78)
print("② 反向证明：乱序时 dt 出现 0（旧行为）")
print("=" * 78)
SHUF = ["23:45", "23:30"] + T96[:94]
dt_bad = _dt_weights(SHUF)
print(f"  乱序 Σ dt = {dt_bad.sum():.4f}，dt==0 的点 {int((dt_bad == 0).sum())}")
check("乱序时 Σ dt 小于 24（旧实现）", dt_bad.sum() < 24.0, dt_bad.sum())
check("乱序时出现零权重", int((dt_bad == 0).sum()) > 0)

print()
print("=" * 78)
print("③ 新行为：pv_feedback 内部排序，结果与正常顺序一致")
print("=" * 78)
load = np.full((1, 96), -100.0)
r_ok = pv_feedback(load, T96, ["2026-01-01"])
r_shuf = pv_feedback(load, SHUF, ["2026-01-01"])
print(f"  正常: {r_ok['total_energy_kwh']:.1f} kWh")
print(f"  乱序: {r_shuf['total_energy_kwh']:.1f} kWh")
check("乱序结果与正常一致",
      abs(r_ok["total_energy_kwh"] - r_shuf["total_energy_kwh"]) < 0.1,
      f"{r_ok['total_energy_kwh']} vs {r_shuf['total_energy_kwh']}")
check("乱序也得到 2400", abs(r_shuf["total_energy_kwh"] - 2400.0) < 0.1,
      r_shuf["total_energy_kwh"])

print()
print("=" * 78)
print("④ 排序后 load 数据要跟着一起排（不能只排时刻）")
print("=" * 78)
# 造一个有区分的负荷：只有 12:00 那个点倒送 500，其余 0
load2 = np.zeros((1, 96))
i_noon = T96.index("12:00")
load2[0, i_noon] = -500.0
r_noon = pv_feedback(load2, T96, ["2026-01-01"])
print(f"  正常顺序：12:00 倒送 500 → {r_noon['total_energy_kwh']:.1f} kWh"
      f"，最大功率 {r_noon['max_power_kw']:.0f} kW")

# 打乱后：把 12:00 挪到第 0 位
idx_shuf = [i_noon] + [i for i in range(96) if i != i_noon]
T_SHUF2 = [T96[i] for i in idx_shuf]
load2_shuf = load2[:, idx_shuf]
r_noon2 = pv_feedback(load2_shuf, T_SHUF2, ["2026-01-01"])
print(f"  乱序后  ：{r_noon2['total_energy_kwh']:.1f} kWh"
      f"，最大功率 {r_noon2['max_power_kw']:.0f} kW")
check("乱序后能量一致",
      abs(r_noon["total_energy_kwh"] - r_noon2["total_energy_kwh"]) < 0.1,
      f"{r_noon['total_energy_kwh']} vs {r_noon2['total_energy_kwh']}")
check("乱序后最大功率一致",
      abs(r_noon["max_power_kw"] - r_noon2["max_power_kw"]) < 1e-6)

print()
print("=" * 78)
print("⑤ 无倒送 / 全倒送 / 倍率")
print("=" * 78)
r_pos = pv_feedback(np.full((1, 96), 200.0), T96, ["d1"])
print(f"  全正负荷: total {r_pos['total_energy_kwh']}, has_negative {r_pos['has_negative']}")
check("无倒送时 total=0", r_pos["total_energy_kwh"] == 0.0)
check("无倒送时 has_negative=False", r_pos["has_negative"] is False)

r_mul = pv_feedback(np.full((1, 96), -100.0), T96, ["d1"], multiplier=2.0)
print(f"  倍率 2.0: total {r_mul['total_energy_kwh']}（应 4800）")
check("倍率生效", abs(r_mul["total_energy_kwh"] - 4800.0) < 0.1,
      r_mul["total_energy_kwh"])

print()
print("=" * 78)
print("⑥ _is_sorted_hours 辅助函数")
print("=" * 78)
check("正常顺序判为有序", _is_sorted_hours(T96))
check("乱序判为无序", not _is_sorted_hours(SHUF))
check("单点判为有序", _is_sorted_hours(["00:00"]))
check("空列表判为有序", _is_sorted_hours([]))

print()
print("=" * 78)
print(f"结果：PASS {len(ok)}   FAIL {len(bad)}")
for b in bad:
    print("  -", b)
print("=" * 78)
sys.exit(1 if bad else 0)
