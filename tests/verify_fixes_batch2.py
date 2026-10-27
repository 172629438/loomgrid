# -*- coding: utf-8 -*-
"""修复后综合自测 Batch2：P-15 / P-16 / P-19 / P-20 / P-21 / P-22"""
import os as _os
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
import importlib.util, sys, py_compile, io, contextlib
import numpy as np

TARGET = _os.path.join(_ROOT, "负荷曲线批量生成Ultra.py")
results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("[PASS] " if ok else "[FAIL] ") + name + ("  " + detail if detail else ""))


try:
    py_compile.compile(TARGET, doraise=True)
    check("语法编译 py_compile", True)
except Exception as e:
    check("语法编译 py_compile", False, repr(e))
    sys.exit(1)

spec = importlib.util.spec_from_file_location("ultra_fix2", TARGET)
mod = importlib.util.module_from_spec(spec)
sys.modules["ultra_fix2"] = mod
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    spec.loader.exec_module(mod)

src = open(TARGET, encoding="utf-8").read()

# ---------- P-15: LP 末时段不再逃逸 ----------
T = 8
load = np.full(T, 50.0)
buy = np.full(T, 10.0)    # 充电极贵 → 不应充电
sell = np.full(T, 100.0)  # 放电极贵 → 应该放光
E, P = 100.0, 100.0
soc_min, soc_max = 0.1, 0.9
r = mod.optimal_dispatch_lp(load, buy, sell, batt_kwh=E, Pmax=P,
                            eta_c=0.95, eta_d=0.95,
                            soc_min=soc_min, soc_max=soc_max,
                            soc_init=0.5, dt=1.0)
if not r.get('feasible'):
    check("P-15 LP 求解", False, r.get('message', ''))
else:
    dis_total = float(np.sum(r['dis']) * 1.0)
    ch_total = float(np.sum(r['ch']) * 1.0)
    usable = (soc_max - soc_min) * E
    cap_energy = usable + ch_total * 0.95
    check("P-15 末时段未逃逸(总放电≤可用能量)", dis_total <= cap_energy + 1e-6,
          f"总放={dis_total:.1f}kWh 允许上限={cap_energy:.1f}kWh 可用={usable:.1f}")
    soc_arr = np.asarray(r['soc'])
    check("P-15b SOC 全程在区间内",
          float(soc_arr.min()) >= soc_min * E - 1e-6 and float(soc_arr.max()) <= soc_max * E + 1e-6,
          f"SOC范围=[{soc_arr.min():.1f}, {soc_arr.max():.1f}] kWh")

# ---------- P-19: 无 0.25 硬编码 ----------
check("P-19 0.25h 硬编码已移除", "solution_value() for i in range(n_pts)) * 0.25" not in src
      and "* _dt_hours" in src)

# ---------- P-20: 买电受负荷约束 / 更换年不再死分支 ----------
check("P-20a 买电上限约束已加", "solver.Add(X_IN[i] <= cons)" in src)
check("P-20b 更换年份按项目期判定", "_pack_yr < config.lifecycle" in src and "_cell_yr < config.lifecycle" in src)

# ---------- P-21: 健康分不崩（多资产长表） ----------
try:
    n_rows, n_cols = 300, 24          # 行(资产×日) 远大于列
    arr_long = np.random.RandomState(0).rand(n_rows, n_cols) * 100 + 50
    dates = [f"2026-06-{(i % 30) + 1:02d}" for i in range(n_rows)]
    tl = [f"{i:02d}:00" for i in range(n_cols)]
    out = mod._compute_data_health_score({'load_array': arr_long, 'date_labels': dates, 'time_labels': tl})
    check("P-21 多资产长表不再越界", isinstance(out, dict) and 'score' in out,
          f"score={out.get('score')} grade={out.get('grade')}")
except Exception as e:
    check("P-21 多资产长表不再越界", False, repr(e))

# 正常 96 点数据仍工作
try:
    arr96 = np.random.RandomState(1).rand(30, 96) * 100 + 50
    out2 = mod._compute_data_health_score({'load_array': arr96,
                                           'date_labels': [f"2026-06-{i+1:02d}" for i in range(30)],
                                           'time_labels': [f"{(i*15)//60:02d}:{(i*15)%60:02d}" for i in range(96)]})
    check("P-21b 正常 96 点数据评分", out2.get('grade') in 'ABCDF', f"score={out2.get('score')} grade={out2.get('grade')}")
except Exception as e:
    check("P-21b 正常 96 点数据评分", False, repr(e))

# ---------- P-16: 30 天循环已折算 ----------
check("P-16 年循环已按观测天数折算",
      "_rf_cycles * (365.0 / _obs_days)" in src and "min(int(arr.shape[0]), 30)" in src)

# ---------- P-22: 指纹不再用 id() ----------
check("P-22 指纹键已改为内容元组", "cache_key = id(periods)" not in src)

# ---------- P-23: 不改数值, 但要有显式说明 ----------
check("P-23 SOC 口径已统一（5%~95%, DOD=0.90）",
      "BESS_SOC_MIN = 0.05" in src and "BESS_DOD_DEFAULT = 0.90" in src
      and "soc_min = 0.10 * capacity_kwh" not in src)

# ── P-28：自定义统计改读数据源，不再依赖背景色 ──
_us = src.split("def _update_stats(self):")[1][:1600]
check("P-28 自定义统计不再依赖背景色",
      "_i3.data(Qt.UserRole) in self.per_group_multiplier" in _us)
check("P-28 自定义筛选同样改读数据源",
      "_i3.data(Qt.UserRole) in self.per_group_multiplier" in src.split("def _toggle_filter_custom")[1][:900])
print("\n================ 汇总 ================")
ok_n = sum(1 for _, o, _ in results if o)
print(f"通过 {ok_n}/{len(results)}")
for n, o, d in results:
    if not o:
        print("  未通过:", n, d)
sys.exit(0 if ok_n == len(results) else 2)



