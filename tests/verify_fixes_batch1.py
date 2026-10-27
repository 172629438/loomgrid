# -*- coding: utf-8 -*-
"""修复后综合自测：语法编译 + P-11 / P-03 / P-13 / P-14 / P-17 复测"""
import os as _os
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
import importlib.util, sys, types, py_compile, io, contextlib
import numpy as np

TARGET = _os.path.join(_ROOT, "负荷曲线批量生成Ultra.py")
results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("[PASS] " if ok else "[FAIL] ") + name + ("  " + detail if detail else ""))


# ---------- 0. 语法编译 ----------
try:
    py_compile.compile(TARGET, doraise=True)
    check("语法编译 py_compile", True)
except Exception as e:
    check("语法编译 py_compile", False, repr(e))
    print("\n编译失败，后续测试跳过")
    for n, o, d in results:
        print(n, o, d)
    sys.exit(1)

# ---------- 导入模块 ----------
spec = importlib.util.spec_from_file_location("ultra_fix", TARGET)
mod = importlib.util.module_from_spec(spec)
sys.modules["ultra_fix"] = mod
buf = io.StringIO()
try:
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        spec.loader.exec_module(mod)
    check("模块导入", True)
except Exception as e:
    check("模块导入", False, repr(e))
    sys.exit(1)

n_pts = 96
tl = [f"{(i*15)//60:02d}:{(i*15)%60:02d}" for i in range(n_pts)]
arr = np.full((2, n_pts), 500.0)
arr[:, 36:60] = 800.0
monthly = {"2026-06": {"load_array": arr, "time_labels": tl,
                       "date_labels": ["2026-06-01", "2026-06-02"]}}

# ---------- P-11: dict 时段不再崩 + 电价正确 ----------
try:
    cd_periods = {"6": [{"start": 0.0, "end": 8.0, "type": "充电"},
                        {"start": 9.0, "end": 15.0, "type": "放电"}]}
    tariff = {"6": [(0, 8, 'valley'), (8, 11, 'flat'), (11, 14, 'valley'),
                    (14, 16, 'flat'), (16, 23, 'peak'), (23, 24, 'flat')]}
    df_cd = mod.bridge_ultra_to_milp(monthly, strategy_periods=cd_periods,
                                     peak_price=1.16, valley_price=0.28, flat_price=0.67)
    check("P-11a dict 时段不再抛 TypeError", True, f"行数={len(df_cd)}")
    uniq_cd = sorted(df_cd['price'].unique().tolist())
    check("P-11b 仅充放策略时电价不越界", all(p in (1.16, 0.28, 0.67) for p in uniq_cd), f"取值={uniq_cd}")

    df_t = mod.bridge_ultra_to_milp(monthly, strategy_periods=cd_periods,
                                    tariff_periods=tariff,
                                    peak_price=1.16, valley_price=0.28, flat_price=0.67)
    n_peak = int((df_t['price'] == 1.16).sum())
    n_val = int((df_t['price'] == 0.28).sum())
    n_flat = int((df_t['price'] == 0.67).sum())
    check("P-11c 传电价时段后峰谷都出现", n_peak > 0 and n_val > 0,
          f"峰={n_peak} 谷={n_val} 平={n_flat} 总={len(df_t)}")

    # 跨午夜电价时段
    tariff_on = {"6": [(23, 6, 'valley'), (8, 22, 'peak')]}
    df_on = mod.bridge_ultra_to_milp(monthly, tariff_periods=tariff_on,
                                     peak_price=1.16, valley_price=0.28, flat_price=0.67)
    n_val_on = int((df_on['price'] == 0.28).sum())
    check("P-11d 跨午夜电价时段生效", n_val_on > 0, f"谷价行数={n_val_on}")
except Exception as e:
    check("P-11 修复验证", False, repr(e))

# ---------- P-03: 套利收益口径 ----------
class FakeSpin:
    def __init__(self, v): self._v = v
    def value(self): return self._v

fake = types.SimpleNamespace()
CAP = 1000.0
fake.config_cap_spin = FakeSpin(CAP)
fake.charge_segments_dict = {"2026-06": [np.array([1000.0])]}
fake.discharge_segments_dict = {"2026-06": [np.array([900.0])]}
fake.EFF_CHARGE = mod.BESS_EFF_CHARGE
fake.EFF_DISCHARGE = mod.BESS_EFF_DISCHARGE
fake.mode = 'A'
fake.rated_power = 500.0
fake.province_name = None
fake._storage_type = 'user_side'
fake._use_net_demand = False
fake.monthly_data = {"2026-06": {"load_array": arr, "time_labels": tl,
                                 "date_labels": ["2026-06-01", "2026-06-02"]}}
fake._get_work_data = lambda: fake.monthly_data

res = mod.ProfessionalWindow.calc_economic_summary(
    fake, peak_price=1.0, valley_price=0.3, flat_price=0.6, demand_charge=30.0,
    system_cost_per_kwh=750.0, project_years=10, discount_rate=0.06,
    rated_cycle_life=6000, dod=0.90)
expected = 900.0 * 1.0 - 1000.0 * 0.3
check("P-03 ModeA 套利=600.0", abs(res['annual_arbitrage'] - expected) < 1.0,
      f"实际={res['annual_arbitrage']} 期望={expected}")

# Mode B：充电为电池侧，需 ÷η
fake_b = types.SimpleNamespace(**{k: getattr(fake, k) for k in
                                 ('config_cap_spin', 'EFF_CHARGE', 'EFF_DISCHARGE',
                                  'rated_power', 'province_name', 'monthly_data')})
fake_b.mode = 'B'
fake_b._storage_type = 'user_side'
fake_b._use_net_demand = False
fake_b.charge_segments_dict = {"2026-06": [np.array([930.0])]}   # 电池侧存入 930
fake_b.discharge_segments_dict = {"2026-06": [np.array([900.0])]}  # 电网侧供出 900
fake_b._get_work_data = lambda: fake_b.monthly_data
res_b = mod.ProfessionalWindow.calc_economic_summary(
    fake_b, peak_price=1.0, valley_price=0.3, flat_price=0.6, demand_charge=30.0,
    system_cost_per_kwh=750.0, project_years=10, discount_rate=0.06,
    rated_cycle_life=6000, dod=0.90)
check("P-03 ModeB 光伏模式套利=900.0(充电零成本)", abs(res_b['annual_arbitrage'] - 900.0) < 1.0,
      f"实际={res_b['annual_arbitrage']}")

# ---------- P-17: 月度结算量纲 ----------
settle = mod.calc_monthly_settlement(
    month_str="2026-06",
    load_kwh_daily=[10000.0] * 30,
    price_daily=[1.0] * 10 + [0.3] * 20,
    demand_before_kw=800.0, demand_after_kw=600.0, demand_price=30.0,
    bess_throughput_kwh=3000.0, bess_capacity_kwh=1000.0)
energy_without = sum(10000.0 * p for p in [1.0] * 10 + [0.3] * 20)
spread = 1.0 - 0.3
expected_with = energy_without - 3000.0 * spread
check("P-17 量纲：节省=吞吐×价差", abs(settle.energy_cost_with_bess - expected_with) < 1.0,
      f"实际={settle.energy_cost_with_bess:.0f} 期望={expected_with:.0f}")
check("P-17b 扣减额未超过电费", settle.energy_cost_with_bess >= 0,
      f"电费={settle.energy_cost_with_bess:.0f}")

# ---------- P-13: 报告文案 ----------
src = open(TARGET, encoding="utf-8").read()
p13_ok = ("P10 IRR(保守)" in src and "P90 IRR(乐观)" in src
          and "P10 IRR(乐观)" not in src and "P90 IRR(保守)" not in src)
check("P-13 P10/P90 标签已纠正", p13_ok)

# ---------- P-14: 死调用清除 ----------
p14_ok = ("self._apply_multiplier_to_all()" not in src) and ("self.per_group_multiplier[k] = 1.0" not in src)
check("P-14 死调用已移除", p14_ok,
      "仍存在" if not p14_ok else "")

print("\n================ 汇总 ================")
ok_n = sum(1 for _, o, _ in results if o)
print(f"通过 {ok_n}/{len(results)}")
sys.exit(0 if ok_n == len(results) else 2)
