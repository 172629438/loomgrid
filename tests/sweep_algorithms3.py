# -*- coding: utf-8 -*-
"""算法链路巡检 v3：按真实签名调用 + 验证 P-30 时段格式兼容"""
import os as _os
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import importlib.util, sys, io, contextlib, traceback, warnings
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
TARGET = _os.path.join(_ROOT, "负荷曲线批量生成Ultra.py")
R = []


def run(name, fn, validator=None):
    try:
        out = fn()
        ok, detail = (validator(out) if validator else (True, ""))
        R.append((name, ok, detail))
        print(("[PASS] " if ok else "[WARN] ") + name + ("  " + detail if detail else ""), flush=True)
        return out
    except Exception as e:
        tb = traceback.format_exc().splitlines()
        R.append((name, False, f"EXC {type(e).__name__}: {e}"))
        print(f"[FAIL] {name}  EXC {type(e).__name__}: {e}", flush=True)
        print(f"        {tb[-3] if len(tb) > 3 else tb[-1]}", flush=True)
        return None


spec = importlib.util.spec_from_file_location("us3", TARGET)
m = importlib.util.module_from_spec(spec)
sys.modules["us3"] = m
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    spec.loader.exec_module(m)
print("[load] OK\n", flush=True)

prof = m.generate_load_profile({'industry': '电子制造', 'n_days': 30, 'n_points': 96, 'avg_power_kw': 500})
arr = np.asarray(prof['load_array'], float)
times, dates = prof['time_labels'], prof['date_labels']
print(f"[data] {arr.shape} peak={arr.max():.0f} mean={arr.mean():.0f}\n", flush=True)


def periods_for(prov='浙江'):
    db = m.PROVINCE_TARIFF_DB[prov]
    p = db.get('periods') if isinstance(db, dict) else db
    if isinstance(p, list) and p and isinstance(p[0], (tuple, list)) and len(p[0]) == 2 \
            and isinstance(p[0][1], list):
        return p[0][1]
    return p


P = periods_for()          # tuple 格式 [(0,8,'valley'), ...]
P_dict = [{'start': s, 'end': e, 'type': t} for s, e, t in P]
P_str = [(f"{int(s):02d}:00", f"{int(e):02d}:00", t) for s, e, t in P]
price_periods = {}
for _s, _e, _t in P:
    price_periods.setdefault(_t, []).append((f"{int(_s):02d}:00", f"{int(_e):02d}:00"))
print(f"[tariff] {len(P)} 段 | price 类型={list(price_periods.keys())}\n", flush=True)

# 1 负荷生成
def sweep_ind():
    bad = []
    for ind in m.INDUSTRY_TEMPLATES:
        for npts in (24, 48, 96):
            try:
                r = m.generate_load_profile({'industry': ind, 'n_days': 7, 'n_points': npts, 'avg_power_kw': 400})
                a = np.asarray(r['load_array'], float)
                if a.shape != (7, npts) or not np.isfinite(a).all() or a.min() < -1e-9:
                    bad.append((ind, npts, a.shape, float(np.nanmin(a))))
            except Exception as e:
                bad.append((ind, npts, type(e).__name__, str(e)[:40]))
    return bad
run("25 行业 x 3 分辨率（75 组）", sweep_ind,
    lambda o: (not o, f"异常 {len(o)} 组 {o[:3]}" if o else "75/75 形状/数值均正常"))
run("三层引擎", lambda: m._generate_load_profile('化工', 12000, 900, 10, 96, province='浙江', seed=7),
    lambda o: (np.asarray(o[0]).shape == (10, 96), f"{np.asarray(o[0]).shape}"))
run("电价库", lambda: len(m.PROVINCE_TARIFF_DB), lambda o: (o >= 30, f"{o} 省"))

# 2 时段兼容（P-30 修复验证）
run("_segment_periods 吃 dict 格式", lambda: m._segment_periods(P_dict),
    lambda o: (isinstance(o, list) and len(o) > 0, f"{len(o)} 段"))
run("_segment_periods 吃 tuple 格式（P-30）", lambda: m._segment_periods(P),
    lambda o: (isinstance(o, list) and len(o) > 0, f"{len(o)} 段"))
run("_segment_periods 吃 'HH:MM' 字符串端点（P-30）", lambda: m._segment_periods(P_str),
    lambda o: (isinstance(o, list) and len(o) > 0, f"{len(o)} 段"))
run("_valid_periods 吃 tuple 格式（P-30）", lambda: m._valid_periods(P),
    lambda o: (isinstance(o, tuple) and o[0] is True, f"valid={o[0]}"))

segs = run("_segment_periods（dict）", lambda: m._segment_periods(P_dict), lambda o: (isinstance(o, list), f"{len(o) if o else 0} 段"))
run("compute_daily_spaces", lambda: m.compute_daily_spaces(arr, times, P_dict, mode='B', rated_power=500),
    lambda o: (isinstance(o, tuple) and len(o) >= 2, f"{len(o)} 返回值"))
segs_arg = segs if segs else []
run("compute_segmented_spaces", lambda: m.compute_segmented_spaces(arr, times, segs_arg, mode='B', rated_power=500),
    lambda o: (isinstance(o, tuple) and len(o) == 4, f"{len(o)} 返回值"))

# 3 价格序列
price = run("periods_to_price_series", lambda: m.periods_to_price_series(times, price_periods),
            lambda o: (isinstance(o, tuple) and len(o) == 2 and len(o[0]) == len(times),
                       f"buy {len(o[0])} 点 {float(o[0].min()):.2f}~{float(o[0].max()):.2f}"))
buy = np.asarray(price[0], float) if price is not None else np.full(len(times), 0.8)
sell = buy * 0.35

# 4 调度
run("greedy_dispatch_annual", lambda: m.greedy_dispatch_annual(arr.flatten(), batt_kwh=1000, c_rate=0.5),
    lambda o: (o is not None, str(type(o).__name__)))
run("simulate_daily_dispatch", lambda: m.simulate_daily_dispatch(arr, times, segs_arg, 1000, 500),
    lambda o: (isinstance(o, dict) and 'soc_trajectory' in o, str(list(o.keys())[:6]) if isinstance(o, dict) else type(o).__name__))
run("optimal_dispatch_lp", lambda: m.optimal_dispatch_lp(arr[0], buy, sell, batt_kwh=1000, Pmax=500),
    lambda o: (isinstance(o, dict), str(list(o.keys())[:6]) if isinstance(o, dict) else type(o).__name__))

# 5 MILP
def _milp():
    cfg = m.MilpConfig(devices=[m.MilpDevice(capacity=261.0, max_c_rate=0.5, price=261000.0)], demands_price={'2025-01': 40.0})
    df = pd.DataFrame({'month': ['2025-01'] * int(arr.size), 'consume': arr.flatten(),
                       'produce': np.zeros(int(arr.size)), 'price': 0.8, 'FiT': 0.35})
    return m.solve_milp_capacity(df, cfg)
run("solve_milp_capacity", _milp, lambda o: (o is not None and getattr(o, 'status', 'ok') != 'error',
        f"status={getattr(o,'status',None)} cap={getattr(o,'capacity_kwh',None)} power={getattr(o,'power_kw',None)} obj={getattr(o,'objective',None)}"))
run("bridge_ultra_to_milp", lambda: m.bridge_ultra_to_milp(
        {'2025-01': {'load_array': arr[:7], 'time_labels': times, 'date_labels': dates[:7]}}, P),
    lambda o: (o is not None, str(type(o).__name__)))

# 6 容量扫描
run("scan_capacity", lambda: m.scan_capacity(list(arr.flatten()), 0.7),
    lambda o: (o is not None and len(o) > 0, f"{len(o)} 方案"))
run("scan_capacity_multi_scenario", lambda: m.scan_capacity_multi_scenario(list(arr.flatten()), 0.7),
    lambda o: (isinstance(o, dict) and len(o) > 0, f"{len(o)} 场景"))

# 7 财务
run("FinanceEngine.npv", lambda: m.FinanceEngine.npv(1_000_000, 250_000), lambda o: (isinstance(o, (int, float)), f"{o:,.0f}"))
run("FinanceEngine.irr", lambda: m.FinanceEngine.irr(1_000_000, 250_000), lambda o: (o is not None, str(o)))
run("calc_npv", lambda: m.calc_npv(1_000_000, 250_000, 10, 0.06), lambda o: (isinstance(o, (int, float)), f"{o:,.0f}"))
run("calc_irr", lambda: m.calc_irr(1_000_000, 250_000, 10), lambda o: (o is not None, str(o)))
run("calc_simple_payback", lambda: m.calc_simple_payback(1_000_000, 250_000), lambda o: (o is not None, str(o)))

# 8 优化 / 诊断
run("StorageOptimizer.optimize", lambda: m.StorageOptimizer(arr, times, dates).optimize(), lambda o: (o is not None, str(type(o).__name__)))
run("EnergyStorageEngineer.diagnose", lambda: m.EnergyStorageEngineer.diagnose(arr, times, dates, province='浙江', billing=None),
    lambda o: (isinstance(o, dict) and len(o) > 0, f"{len(o)} 键 {list(o.keys())[:5]}"))

# 9 月度结算
run("calc_monthly_settlement", lambda: m.calc_monthly_settlement('2025-01', [500.0] * 30, [0.8] * 30, 600, 480, 40.0, 9000, 1000),
    lambda o: (o is not None, str(o)[:90]))

# 10 数据卫生
run("_compute_data_health_score", lambda: m._compute_data_health_score({
        'load_array': arr, 'time_labels': times, 'date_labels': dates}),
    lambda o: (isinstance(o, (int, float, dict, tuple)), str(o)[:80]))
run("analyze_load_fingerprint", lambda: m.analyze_load_fingerprint(arr, dates, times), lambda o: (o is not None, str(o)[:70]))
run("_estimate_degradation", lambda: m._estimate_degradation(365), lambda o: (o is not None, str(o)[:80]))
run("match_fingerprint_to_industry", lambda: m.match_fingerprint_to_industry(m.analyze_load_fingerprint(arr, dates, times)),
    lambda o: (o is not None, str(o)[:70]))

print("\n" + "=" * 72)
fails = [r for r in R if not r[1]]
print(f"巡检 {len(R)} 项 | 通过 {len(R)-len(fails)} | 失败 {len(fails)}")
if fails:
    print("\n失败清单：")
    for n, _, d in fails:
        print(f"  x {n}\n    {d}")
os._exit(0 if not fails else 3)



