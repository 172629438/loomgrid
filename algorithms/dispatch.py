# -*- coding: utf-8 -*-
"""
Dispatch Engines
================

三种调度求解：贪心、逐日仿真、线性规划。

本文件由 Load Curve Studio (LoomGrid) 主程序剥离而来，保留原始逻辑，仅去除 GUI 依赖。
原始出处：负荷曲线批量生成Ultra.py v3.2

依赖：numpy / scipy

"""

import math
import numpy as np
from scipy.optimize import brentq


BESS_SOC_MIN = 0.05


BESS_SOC_MAX = 0.95


BESS_DOD_DEFAULT = 0.90


def greedy_dispatch_annual(
    load_kw: np.ndarray,
    pv_kw: np.ndarray | None = None,
    wind_kw: np.ndarray | None = None,
    batt_kwh: float = 500.0,
    c_rate: float = 0.5,
    eta_roundtrip: float = 0.90,
    soc_init_frac: float = 0.5,
    soc_min_frac: float = 0.1,
    soc_max_frac: float = 0.95,
    dt_hours: float = 0.25,
) -> dict:
    """v2.19 SSS: 纯Python贪心调度引擎 - 逐时遍历,新能源优先.

    调度逻辑(每时步按序执行):
        1) 新能源直供负荷(direct = min(renewable, load))
        2) 多余电优先充电(受SOC上限和Pmax约束)
        3) 缺额优先放电(受SOC下限和Pmax约束)
        4) 未消纳新能源计入弃电
        5) 剩余缺额从电网购电

    Args:
        load_kw: 逐时负荷(kW), shape (n_timesteps,)
        pv_kw: 逐时光伏出力(kW), shape (n_timesteps,), None->全零
        wind_kw: 逐时风电出力(kW), shape (n_timesteps,), None->全零
        batt_kwh: 电池容量(kWh)
        c_rate: 充放电倍率(C-rate), 0.5=2小时充满
        eta_roundtrip: 往返效率, 默认0.90
        soc_init_frac: 初始SOC比例
        soc_min_frac: SOC下限比例
        soc_max_frac: SOC上限比例
        dt_hours: 时间步长(h), 默认0.25=15分钟

    Returns:
        {soc, grid_import, bess_charge, bess_discharge, curtailment,
         direct_use, total_gen, total_load, total_import, total_curtail}

    Note:
        - 仅适配新能源消纳优先场景,峰谷套利请使用 optimal_dispatch_lp()
        - dt_hours=0时自动回退0.25h(v2.19安全修复)
        - SOC约束: 充放电后夹具 soc = max(soc_min, min(soc_max, soc))
    """
    # v2.29fix: dt_hours入口校验,防止SOC更新与功率限制使用不同步长
    if dt_hours <= 0:
        raise ValueError(f"dt_hours 必须 > 0, 当前值 {dt_hours}")
    n = len(load_kw)
    if pv_kw is None:
        pv_kw = np.zeros(n)
    if wind_kw is None:
        wind_kw = np.zeros(n)

    renewable = pv_kw + wind_kw
    E = batt_kwh
    Pmax = c_rate * E

    soc_min = soc_min_frac * E
    soc_max = soc_max_frac * E
    soc = soc_init_frac * E
    eta_c = eta_roundtrip ** 0.5
    eta_d = eta_roundtrip ** 0.5

    soc_arr = np.zeros(n)
    grid_import = np.zeros(n)
    bess_ch = np.zeros(n)
    bess_dis = np.zeros(n)
    curtail = np.zeros(n)

    total_gen = 0.0
    total_load = 0.0
    total_import = 0.0
    total_curtail = 0.0
    direct_use = 0.0

    for t in range(n):
        ld = load_kw[t]
        ren = renewable[t]
        total_load += ld * dt_hours
        total_gen += ren * dt_hours

        # 1) 新能源直供
        direct = min(ren, ld)
        direct_use += direct * dt_hours
        surplus = ren - direct
        deficit = ld - direct

        # 2) 多余电充电
        ch_power = 0.0
        if surplus > 0 and soc < soc_max:
            max_ch_energy = (soc_max - soc) / eta_c
            max_ch_power = min(Pmax, max_ch_energy / dt_hours)
            ch_power = min(surplus, max_ch_power)
            soc += ch_power * eta_c * dt_hours
            bess_ch[t] = ch_power
            if surplus > ch_power:
                curtail[t] = surplus - ch_power
                total_curtail += curtail[t] * dt_hours

        # 3) 缺额放电
        dis_power = 0.0
        if deficit > 0 and soc > soc_min:
            max_dis_energy = (soc - soc_min) * eta_d
            max_dis_power = min(Pmax, max_dis_energy / dt_hours)
            dis_power = min(deficit, max_dis_power)
            soc -= dis_power * dt_hours / eta_d
            bess_dis[t] = dis_power

        # 4) 剩余缺额从电网购电
        net_deficit = deficit - dis_power
        if net_deficit > 0:
            grid_import[t] = net_deficit
            total_import += net_deficit * dt_hours

        # 夹具
        soc = max(soc_min, min(soc_max, soc))
        soc_arr[t] = soc

    return {
        "soc": soc_arr,
        "grid_import": grid_import,
        "bess_charge": bess_ch,
        "bess_discharge": bess_dis,
        "curtailment": curtail,
        "direct_use": direct_use,
        "total_gen": total_gen,
        "total_load": total_load,
        "total_import": total_import,
        "total_curtail": total_curtail,
    }


def simulate_daily_dispatch(load_array, time_labels, periods, capacity_kwh, power_kw,
                             efficiency=0.9, initial_soc=0.2, soc_min=BESS_SOC_MIN, soc_max=BESS_SOC_MAX,
                             transformer_capacity=None):
    """v2.16: 逐日充放电调度仿真 - 含 SOC 上下限保护 + 变压器负载校验.
    默认 DOD=90%, SOC范围10%~90%, 初始SOC=20%.
    v2.16: 新增 transformer_capacity 参数,充电时校验 当前负荷+充电功率 <= 变压器容量x0.9,
    超限则降功率或暂停充电,防止变压器过载.
    """
    n_days, n_points = load_array.shape
    # 零容量保护
    if capacity_kwh <= 0 or power_kw <= 0:
        return {'soc_trajectory': np.zeros((n_days, max(n_points, 2))), 'charge_kwh': np.zeros(n_days),
                'discharge_kwh': np.zeros(n_days), 'final_soc': 0.0,
                'transformer_limit_events': 0}
    if n_points < 2:
        return {'soc_trajectory': np.zeros((n_days, 2)), 'charge_kwh': np.zeros(n_days),
                'discharge_kwh': np.zeros(n_days), 'final_soc': 0.0,
                'transformer_limit_events': 0}
    hours = np.array([_parse_min(_normalize_time_column_name(t)) / 60.0 for t in time_labels])
    soc_trajectory = np.zeros((n_days, n_points + 1))
    charge_kwh = np.zeros(n_days)
    discharge_kwh = np.zeros(n_days)
    soc = initial_soc * capacity_kwh
    soc_low = soc_min * capacity_kwh
    soc_high = soc_max * capacity_kwh
    # v2.16: 变压器安全上限
    xfmr_limit = (transformer_capacity * 0.9) if (transformer_capacity and transformer_capacity > 0) else None
    xfmr_events = 0

    expanded = _expand_overnight(periods) if periods else []
    for d in range(n_days):
        soc_trajectory[d, 0] = soc / capacity_kwh * 100
        for t in range(n_points):
            # 判断当前时刻的充放电类型
            hour = hours[t]
            seg_type = None
            for p in expanded:
                if p['start'] <= hour < p['end'] or (p['start'] > p['end'] and (hour >= p['start'] or hour < p['end'])):
                    seg_type = p['type']
                    break
            load = load_array[d, t]
            if np.isnan(load): continue
            dt = 1.0 / n_points * 24  # 时间步长(小时)
            if seg_type in (PeriodType.CHARGE, PeriodType.CHARGE_LABEL):
                charge_power = min(power_kw, (soc_high - soc) / dt) if dt > 0 and soc < soc_high else 0
                # v2.16: 变压器负载校验 - 当前负荷+充电功率不得超过变压器安全上限
                if xfmr_limit is not None and charge_power > 0:
                    available_headroom = max(0, xfmr_limit - load)
                    if charge_power > available_headroom:
                        charge_power = 0.0 if available_headroom <= 0.01 else min(charge_power, available_headroom)
                        xfmr_events += 1
                soc += charge_power * dt * efficiency
                charge_kwh[d] += charge_power * dt
            elif seg_type in (PeriodType.DISCHARGE, PeriodType.DISCHARGE_LABEL):
                discharge_power = min(power_kw, (soc - soc_low) / dt) if dt > 0 and soc > soc_low else 0
                soc -= discharge_power * dt / efficiency
                discharge_kwh[d] += discharge_power * dt
            soc = max(soc_low, min(soc_high, soc))
            soc_trajectory[d, t + 1] = soc / capacity_kwh * 100

    return {'soc_trajectory': soc_trajectory, 'charge_kwh': charge_kwh,
            'discharge_kwh': discharge_kwh, 'final_soc': soc / capacity_kwh * 100,
            'transformer_limit_events': xfmr_events}


def optimal_dispatch_lp(load_kw, buy_price, sell_price, batt_kwh=500, Pmax=250,
                         eta_c=0.95, eta_d=0.95, soc_min=0.1, soc_max=0.95,
                         soc_init=0.5, dt=0.25, demand_limit=None, demand_rate=0):
    """v2.19 SSS: 线性规划全局最优储能调度.

    使用 scipy.optimize.linprog 求解考虑分时电价+需量约束的最优充放电策略.
    相比贪心算法,LP能保证全局最优解(在连续松弛下).

    决策变量 (3T+1个, T=时间步数):
        ch[t]: 充电功率(kW), dis[t]: 放电功率(kW), soc[t]: 电池SOC(kWh)
        peak: 周期内峰值需量(kW)

    目标: max sum((dis[t]*sell_price[t] - ch[t]*buy_price[t]) * dt) - peak * demand_rate

    约束:
        - 0 <= ch[t], dis[t] <= Pmax
        - soc[t+1] = soc[t] + ch[t]*eta_c*dt - dis[t]*eta_d_inv*dt
        - soc_min*batt_kwh <= soc[t] <= soc_max*batt_kwh
        - soc[0] = soc_init*batt_kwh
        - load[t] - dis[t] + ch[t] <= peak (需量约束, 可选)

    Args:
        load_kw: 逐时负荷(kW), shape (T,)
        buy_price: 购电价格(元/kWh), shape (T,)
        sell_price: 售电/节电价格(元/kWh), shape (T,)
        batt_kwh: 电池容量(kWh)
        Pmax: 最大充放电功率(kW)
        eta_c, eta_d: 充放电效率
        soc_min, soc_max: SOC比例限制
        soc_init: 初始SOC比例
        dt: 时间步长(h)
        demand_limit: 需量上限(kW), None=不约束
        demand_rate: 需量费率(元/kW/周期)

    Returns:
        {ch, dis, soc, revenue, peak_demand, feasible, message}
        若scipy不可用或无可行解,返回 feasible=False
    """
    try:
        from scipy.optimize import linprog
    except ImportError:
        return {'feasible': False, 'message': 'scipy未安装,无法使用MILP调度'}

    import numpy as np
    T = len(load_kw)
    E = batt_kwh

    # 变量: ch[0..T-1], dis[0..T-1], soc[1..T], peak(可选)
    n_demand = 1 if demand_limit is not None else 0
    n_vars = 2*T + T + n_demand  # ch + dis + soc(1..T) + peak

    # 目标系数 (linprog最小化 -> 取负最大化收益)
    c = np.zeros(n_vars)
    for t in range(T):
        c[t] = buy_price[t] * dt        # ch成本
        c[T+t] = -sell_price[t] * dt    # dis收益(负=最大化)
    if n_demand:
        c[-1] = demand_rate  # 需量成本

    # 约束: A_ub @ x <= b_ub
    A_rows, b_rows = [], []

    # SOC递推: soc[t] 定义为"第t个时段结束后的电量"
    # v3.2fix: 原实现 for t in range(T-1) 只约束到 soc[T-2], 末时段 ch[T-1]/dis[T-1]
    # 未进入任何方程, 可在最后一步凭空满功率放电而不付 SOC 代价(收益虚增);
    # 且初值绑在 soc[0] 上造成变量语义错位。现改为覆盖全部 T 个时段。
    inv_eta_d = 1.0 / eta_d if eta_d > 0 else 1.0
    for t in range(T):
        row = np.zeros(n_vars)
        row[t] = -eta_c * dt
        row[T + t] = inv_eta_d * dt
        row[2 * T + t] = 1.0
        if t > 0:
            row[2 * T + t - 1] = -1.0
        base_b = E * soc_init if t == 0 else 0.0
        # 上界: soc[t] - soc[t-1] - eta_c*dt*ch[t] + inv_eta_d*dt*dis[t] <= base_b
        A_rows.append(row.copy()); b_rows.append(base_b)
        # 下界(取负): -soc[t] + soc[t-1] + eta_c*dt*ch[t] - inv_eta_d*dt*dis[t] <= -base_b
        A_rows.append(-row); b_rows.append(-base_b)

    # SOC上下限
    for t in range(T):
        idx = 2*T + t
        # soc <= soc_max * E
        row_ub = np.zeros(n_vars); row_ub[idx] = 1.0
        A_rows.append(row_ub); b_rows.append(soc_max * E)
        # -soc <= -soc_min * E -> soc >= soc_min * E
        row_lb = np.zeros(n_vars); row_lb[idx] = -1.0
        A_rows.append(row_lb); b_rows.append(-soc_min * E)

    # 需量约束: load[t] - dis[t] + ch[t] <= peak
    if n_demand:
        peak_idx = n_vars - 1
        # v2.19fix: 峰值变量上限约束 peak <= demand_limit
        row = np.zeros(n_vars); row[peak_idx] = 1.0
        A_rows.append(row); b_rows.append(demand_limit)
        for t in range(T):
            row = np.zeros(n_vars)
            row[t] = 1.0; row[T+t] = -1.0; row[peak_idx] = -1.0
            A_rows.append(row); b_rows.append(-load_kw[t])

    # 变量上下界
    bounds = [(0, Pmax)] * (2*T)  # ch, dis
    bounds += [(soc_min*E, soc_max*E)] * T  # soc
    if n_demand:
        # v2.19fix: peak上界使用demand_limit而非硬编码
        bounds += [(0, demand_limit)]  # peak

    A = np.array(A_rows) if A_rows else None
    b = np.array(b_rows) if b_rows else None

    result = linprog(c, A_ub=A, b_ub=b, bounds=bounds, method='highs',
                     options={'time_limit': 120})  # v2.22fix: 防止大问题挂死GUI

    if result.success:
        x = result.x
        ch = x[:T]; dis = x[T:2*T]; soc = x[2*T:2*T+T]
        revenue = float(-result.fun)
        peak = x[-1] if n_demand else max(load_kw)
        return {'ch': ch, 'dis': dis, 'soc': soc, 'revenue': round(revenue, 2),
                'peak_demand': round(peak, 1), 'feasible': True,
                'message': f'LP最优解 (收益: {revenue:.2f}元)'}
    return {'feasible': False, 'message': result.message}
