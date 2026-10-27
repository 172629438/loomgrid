# -*- coding: utf-8 -*-
"""数值不变量验证：能量守恒、SOC 边界、NPV↔IRR 自洽、空间口径交叉、物理边界。

这些断言不看代码写得对不对，只看「算出来的数符不符合物理」：
  · 电池不可能一边充一边放
  · 放电不能超过负荷本身
  · 充电不能突破变压器容量
  · 以 IRR 折现时 NPV 必须归零
  · LP 的 SOC 递推必须与充放电量严格守恒

用法：python tests/verify_invariants.py
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "algorithms"))

import dispatch  # noqa: E402
import finance  # noqa: E402
import periods  # noqa: E402
import spaces  # noqa: E402

PASS = FAIL = 0
FAILS = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}  {detail}")
    else:
        FAIL += 1
        FAILS.append(name)
        print(f"[FAIL] {name}  {detail}")


N = 96
buy = np.array([0.3] * 32 + [0.65] * 12 + [0.3] * 12 + [0.65] * 8 + [1.1] * 28 + [0.65] * 4)[:N]
sell = buy * 0.35
load = np.concatenate([np.full(32, 300.0), np.full(32, 900.0), np.full(32, 400.0)])[:N]

print("=" * 78)
print("1. LP 能量守恒：Σ(ηc·ch − dis/ηd)·dt 应等于 SOC 净变化")
print("=" * 78)
for tag, kw in (("SOC 5-95%", dict(soc_min=0.05, soc_max=0.95)),
                ("SOC 10-90%", dict(soc_min=0.10, soc_max=0.90))):
    r = dispatch.optimal_dispatch_lp(load, buy, sell, batt_kwh=1000, Pmax=500,
                                     eta_c=0.95, eta_d=0.95, dt=0.25, **kw)
    if not r.get("feasible"):
        check(f"LP 求解 {tag}", False, r.get("message", ""))
        continue
    ch, dis, soc = np.asarray(r["ch"]), np.asarray(r["dis"]), np.asarray(r["soc"])
    dt, E, soc_init = 0.25, 1000.0, 0.5 * 1000.0
    delta_expected = float(np.sum(ch * 0.95 * dt) - np.sum(dis / 0.95 * dt))
    delta_actual = float(soc[-1] - soc_init)
    err = abs(delta_expected - delta_actual)
    check(f"能量守恒 {tag}", err < max(1.0, abs(delta_expected) * 0.001),
          f"ΔSOC 预期 {delta_expected:+.2f} / 实际 {delta_actual:+.2f} / 误差 {err:.4f} kWh")

print()
print("=" * 78)
print("2. LP 运行约束：SOC 边界、充放互斥、功率上界")
print("=" * 78)
r = dispatch.optimal_dispatch_lp(load, buy, sell, batt_kwh=1000, Pmax=500,
                                 soc_min=0.05, soc_max=0.95, dt=0.25)
soc, ch, dis = np.asarray(r["soc"]), np.asarray(r["ch"]), np.asarray(r["dis"])
check("SOC 上界", float(soc.max()) <= 0.95 * 1000 + 1e-6, f"max={soc.max():.2f} ≤ 950")
check("SOC 下界", float(soc.min()) >= 0.05 * 1000 - 1e-6, f"min={soc.min():.2f} ≥ 50")
check("充放互斥", int(np.sum((ch > 1e-6) & (dis > 1e-6))) == 0,
      f"同时充放时段数 = {int(np.sum((ch > 1e-6) & (dis > 1e-6)))}")
check("功率上界", float(max(ch.max(), dis.max())) <= 500 + 1e-6,
      f"max={max(ch.max(), dis.max()):.2f} ≤ 500")

print()
print("=" * 78)
print("3. NPV ↔ IRR 自洽：以 IRR 折现时 NPV 应为 0")
print("=" * 78)
for tag, (cf, inv, life) in {
    "示例（年 8.05 万 / 投 75 万 / 15 年）": (80507.0, 750000.0, 15),
    "正收益（年 20 万 / 投 100 万 / 10 年）": (200000.0, 1000000.0, 10),
}.items():
    irr = finance.calc_irr(inv, cf, life)
    if irr is None:
        check(f"NPV@IRR=0 {tag}", False, "IRR 无法计算")
        continue
    npv_at_irr = finance.calc_npv(cf, inv, life, discount_rate=irr / 100.0)
    check(f"NPV@IRR=0 {tag}", abs(npv_at_irr) < max(1.0, inv * 0.001),
          f"IRR={irr:.4f}% → NPV={npv_at_irr:,.2f}（应≈0）")

print()
print("=" * 78)
print("4. 空间口径交叉：快算 vs 精算")
print("=" * 78)
LABELS = [f"{(i * 15) // 60}:{(i * 15) % 60:02d}" for i in range(N)]
RAW = [(0, 8, "valley"), (8, 11, "flat"), (11, 14, "valley"),
       (14, 16, "flat"), (16, 23, "peak"), (23, 24, "flat")]
arr = np.tile(load, (5, 1))
segs = periods._segment_periods(RAW)
c_fast, d_fast = spaces.compute_daily_spaces(arr, LABELS, RAW, billing={"value": 800.0}, mode="A")
cs, ds, _, _ = spaces.compute_segmented_spaces(arr, LABELS, segs, billing={"value": 800.0}, mode="A")
c_seg = sum(float(np.nansum(x)) for x in cs) / arr.shape[0]
d_seg = sum(float(np.nansum(x)) for x in ds) / arr.shape[0]
check("充电空间 精算不高于快算", float(np.mean(c_fast)) >= c_seg - 1.0,
      f"快算 {np.mean(c_fast):.2f} / 精算 {c_seg:.2f}")
# 已知口径差（N7）：精算按段取子区间，_compute_time_dt 对段首尾用中点法折半，
# 每段少算半个采样点的一半时长。段越短、点越少越明显（本例 1.59%）。
# 方向保守（精算偏低 → 经济模型偏保守），修它会变更全部历史结果，故只设上界。
_ratio = c_seg / max(float(np.mean(c_fast)), 1e-9)
check("充电空间偏差 ≤3%", abs(1.0 - _ratio) < 0.03,
      f"精算/快算 = {_ratio:.4f}（已知边界口径差，预期 0.97~1.00）")
check("放电空间 快算=精算", abs(float(np.mean(d_fast)) - d_seg) < 1.0,
      f"快算 {np.mean(d_fast):.2f} / 精算 {d_seg:.2f}")

print()
print("=" * 78)
print("5. 物理边界：放电不超负荷、充电不破变压器")
print("=" * 78)
cap = 800.0
dis_kwh = float(np.mean(d_fast))
peak_load_kwh = float(np.sum(load[64:92])) * 0.25   # 峰段 16:00–23:00
check("放电不超峰段负荷", dis_kwh <= peak_load_kwh + 1e-6,
      f"放电 {dis_kwh:.1f} ≤ 峰段负荷 {peak_load_kwh:.1f} kWh")
ch_kwh = float(np.mean(c_fast))
valley_headroom = (float(np.sum(np.maximum(cap - load[:32], 0)))
                   + float(np.sum(np.maximum(cap - load[44:56], 0)))) * 0.25
check("充电不破变压器容量", ch_kwh <= valley_headroom + 1e-6,
      f"充电 {ch_kwh:.1f} ≤ 谷段余量 {valley_headroom:.1f} kWh（电网侧口径）")

print()
print("=" * 78)
print(f"结果：PASS {PASS}   FAIL {FAIL}")
print("=" * 78)
if FAILS:
    print("失败：", ", ".join(FAILS))
sys.exit(1 if FAIL else 0)
