# -*- coding: utf-8 -*-
"""LoomGrid 算法模块演示

一次跑完七个模块，看每个模块在做什么、输出长什么样。

用法：
    python demo.py            # 跑全部
    python demo.py periods    # 只跑某一块

不依赖任何外部数据文件，全部现场构造。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def rule(title):
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


# ══════════════════════════════════════════════════════════
def demo_periods():
    """电价时段：三种写法归一化 + 转成逐点价格序列"""
    import periods

    rule("1. periods —— 电价时段归一化")

    # 三种常见写法
    as_dict = [
        {"start": 0, "end": 8, "type": "valley"},
        {"start": 8, "end": 11, "type": "flat"},
        {"start": 11, "end": 13, "type": "peak"},
        {"start": 13, "end": 17, "type": "flat"},
        {"start": 17, "end": 22, "type": "peak"},
        {"start": 22, "end": 24, "type": "valley"},
    ]
    as_tuple = [(0, 8, "valley"), (8, 11, "flat"), (11, 13, "peak"),
                (13, 17, "flat"), (17, 22, "peak"), (22, 24, "valley")]
    as_str = [("00:00", "08:00", "valley"), ("08:00", "11:00", "flat"),
              ("11:00", "13:00", "peak"), ("13:00", "17:00", "flat"),
              ("17:00", "22:00", "peak"), ("22:00", "24:00", "valley")]

    for label, p in [("dict", as_dict), ("tuple", as_tuple), ("'HH:MM'", as_str)]:
        n = periods._normalize_periods(p)
        print(f"  {label:<10} -> {len(n)} 段")

    # 转价格序列（periods 要按类型分组的 dict）
    price_map = {"valley": 0.30, "flat": 0.65, "peak": 1.10}
    grouped = {
        "valley": [("00:00", "08:00"), ("22:00", "24:00")],
        "flat": [("08:00", "11:00"), ("13:00", "17:00")],
        "peak": [("11:00", "13:00"), ("17:00", "22:00")],
    }
    labels = [f"{h:02d}:00" for h in range(24)]
    buy, ptype = periods.periods_to_price_series(labels, grouped, price_map)
    print(f"\n  24 小时价格序列前 10 个: {[round(float(x), 2) for x in list(buy)[:10]]}")
    print(f"  时段类型前 10 个:        {list(ptype)[:10]}")
    print(f"\n  谷段 0.30 元 / 平段 0.65 元 / 峰段 1.10 元 —— 峰谷价差 {1.10-0.30:.2f} 元")


# ══════════════════════════════════════════════════════════
def demo_spaces():
    """充放电空间：容量不是拍出来的，是被曲线和电价挤出来的"""
    import numpy as np
    import spaces
    from periods import _normalize_periods

    rule("2. spaces —— 充放电空间")

    # 造一条典型工业曲线（compute_daily_spaces 要求 2D: n_days x n_points）
    hours = np.arange(24)
    one_day = 500 + 300 * np.sin((hours - 6) / 24 * 2 * np.pi) ** 2
    one_day = np.maximum(one_day, 200)
    load = np.tile(one_day, (7, 1))          # 7 天 × 24 点

    periods = [
        {"start": 0, "end": 8, "type": "valley"},
        {"start": 8, "end": 11, "type": "flat"},
        {"start": 11, "end": 13, "type": "peak"},
        {"start": 13, "end": 17, "type": "flat"},
        {"start": 17, "end": 22, "type": "peak"},
        {"start": 22, "end": 24, "type": "valley"},
    ]
    labels = [f"{h:02d}:00" for h in range(24)]
    billing = {"value": 800}   # 变压器容量 800 kVA

    charge, discharge = spaces.compute_daily_spaces(
        load_array=load, time_labels=labels,
        periods=periods, billing=billing, mode="A",
    )
    print(f"  Mode A（需量/套利）  变压器 800 kVA")
    print(f"    充电空间（7 天均值）{charge.mean():.1f} kWh")
    print(f"    放电空间（7 天均值）{discharge.mean():.1f} kWh")
    print(f"\n  负荷曲线：峰值 {one_day.max():.0f} kW / 谷值 {one_day.min():.0f} kW，铺成 7 天 2D 数组")


# ══════════════════════════════════════════════════════════
def demo_dispatch():
    """三套调度：贪心 / 逐日仿真 / 线性规划"""
    import numpy as np
    import dispatch

    rule("3. dispatch —— 三种调度对照")

    n = 96
    hours = np.arange(n) * 0.25
    load = 500 + 300 * np.sin((hours - 6) / 24 * 2 * np.pi) ** 2
    buy = np.where((hours < 8) | (hours >= 22), 0.30,
                   np.where(((hours >= 11) & (hours < 13)) | ((hours >= 17) & (hours < 22)), 1.10, 0.65))
    sell = np.full(n, 0.0)

    try:
        r = dispatch.optimal_dispatch_lp(
            load_kw=load, buy_price=buy, sell_price=sell,
            batt_kwh=800, Pmax=200,
            eta_c=0.95, eta_d=0.95, soc_min=0.05, soc_max=0.95,
            soc_init=0.10, dt=0.25,
        )
        print(f"  LP：可行={r.get('feasible')}  {r.get('message', '')}")
        if r.get("feasible"):
            print(f"      日收益 {r.get('revenue', 0):.2f} 元   峰值需量 {r.get('peak_demand', 0):.1f} kW")
    except Exception as e:
        print(f"  LP 调用失败: {type(e).__name__}: {e}")

    try:
        r2 = dispatch.greedy_dispatch_annual(
            load_kw=load, pv_kw=None, wind_kw=None,
            batt_kwh=800, c_rate=0.25, eta_roundtrip=0.90,
            soc_init_frac=0.10, soc_min_frac=0.05, soc_max_frac=0.95,
            dt_hours=0.25,
        )
        if isinstance(r2, dict):
            print(f"  贪心：返回 {len(r2)} 个字段")
            for k in ["soc", "grid_import", "bess_charge", "bess_discharge", "energy_cost"]:
                if k in r2:
                    v = r2[k]
                    if hasattr(v, "mean"):
                        print(f"    {k:<16} 均值 {float(np.mean(v)):.2f}   范围 {float(np.min(v)):.1f}~{float(np.max(v)):.1f}")
                    else:
                        print(f"    {k:<16} {v}")
    except Exception as e:
        print(f"  贪心调用失败: {type(e).__name__}: {e}")


# ══════════════════════════════════════════════════════════
def demo_finance():
    """财务：NPV / IRR / 回收期 / LCOE"""
    import finance

    rule("4. finance —— 经济指标")

    investment = 800 * 750          # 800 kWh × 750 元/kWh
    annual = 120000                 # 年节省
    life = 10
    rate = 0.08

    npv = finance.calc_npv(annual, investment, life, rate)
    irr = finance.calc_irr(investment, annual, life)
    pb = finance.calc_simple_payback(investment, annual)

    print(f"  投资 {investment:,.0f} 元   年节省 {annual:,.0f} 元   寿命 {life} 年")
    print(f"  NPV(8%)  = {npv:,.0f} 元")
    print(f"  IRR      = {irr:.2f}%" if isinstance(irr, (int, float)) else f"  IRR      = {irr}")
    print(f"  静态回收 = {pb:.2f} 年")

    # 回收期时间线
    monthly = [annual / 12] * (life * 12)
    tl = finance.calc_payback_timeline(investment, monthly)
    if isinstance(tl, dict):
        cum = tl.get("cumulative_savings", [])
        if cum:
            print(f"  累计节省（第 12/24/36 月）: "
                  f"{cum[11]:,.0f} / {cum[23]:,.0f} / {cum[35]:,.0f} 元")


# ══════════════════════════════════════════════════════════
def demo_loadgen():
    """负荷生成：三层叠加"""
    import loadgen

    rule("5. loadgen —— 三层叠加负荷生成")

    try:
        r = loadgen._generate_load_profile(
            industry="电子制造", daily_kwh=10000, peak_kw=800,
            n_days=3, n_points=96, seed=42,
        )
        if isinstance(r, tuple):
            print(f"  返回 {len(r)} 个元素")
            for i, item in enumerate(r):
                t = type(item).__name__
                try:
                    print(f"    [{i}] {t}  长度/形状 {len(item)}")
                except TypeError:
                    print(f"    [{i}] {t}  {item}")
        else:
            print(f"  返回类型 {type(r).__name__}")
            if hasattr(r, "shape"):
                print(f"    形状 {r.shape}  均值 {r.mean():.1f}")
        print("\n  参数：行业=电子制造, 日电量=10000 kWh, 峰=800 kW, 3 天 × 96 点, seed=42")
    except Exception as e:
        print(f"  调用失败: {type(e).__name__}: {e}")


# ══════════════════════════════════════════════════════════
def demo_diagnostics():
    """诊断：负荷指纹与行业匹配"""
    import numpy as np
    import diagnostics

    rule("6. diagnostics —— 负荷指纹分析")

    n_days = 30
    n_points = 96
    t = np.arange(n_points) * 0.25
    day = 500 + 350 * np.sin((t - 6) / 24 * 2 * np.pi) ** 2
    load = np.tile(np.maximum(day, 200), (n_days, 1))   # (30, 96) 二维

    date_labels = [f"2026-10-{d+1:02d}" for d in range(n_days)]   # 每天一个日期
    time_labels = [f"{int(h):02d}:{int((h % 1) * 60):02d}" for h in t]   # 每天 n_points 个时刻

    try:
        fp = diagnostics.analyze_load_fingerprint(load, date_labels, time_labels)
        if isinstance(fp, dict):
            print(f"  样本：{n_days} 天 × {n_points} 点")
            for k, v in fp.items():
                sv = str(v)
                print(f"    {k:<20} {sv[:60]}")
            m = diagnostics.match_fingerprint_to_industry(fp)
            print(f"\n  行业匹配: {str(m)[:160]}")
    except Exception as e:
        print(f"  调用失败: {type(e).__name__}: {e}")


# ══════════════════════════════════════════════════════════
def demo_milp():
    """MILP 容量规划（需要 ortools，可能较慢）"""
    import numpy as np
    import pandas as pd
    import milp

    rule("7. milp —— 容量规划（跳过重型求解，只验证接口）")

    print("  milp.solve_milp_capacity(df, config) 需要 96 点日曲线 + MilpConfig")
    print("  接口检查：")
    for name in ["solve_milp_capacity", "bridge_ultra_to_milp", "_no_storage_benefit"]:
        print(f"    {'✓' if hasattr(milp, name) else '✗'} {name}")
    print("\n  真实求解请参考 examples/quickstart.py 第 5 步")


DEMOS = {
    "periods": demo_periods,
    "spaces": demo_spaces,
    "dispatch": demo_dispatch,
    "finance": demo_finance,
    "loadgen": demo_loadgen,
    "diagnostics": demo_diagnostics,
    "milp": demo_milp,
}


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    print("LoomGrid 算法模块演示")
    print("每个模块都不依赖外部数据，全部现场构造。")

    todo = DEMOS.items() if which == "all" else [(which, DEMOS[which])]
    for name, fn in todo:
        try:
            fn()
        except Exception as e:
            import traceback
            print(f"\n  [!] {name} 出错: {type(e).__name__}: {e}")
            traceback.print_exc(limit=2)

    print("\n" + "=" * 72)
    print("完成。每个模块的详细说明见 README.md")
    print("=" * 72)


if __name__ == "__main__":
    main()
