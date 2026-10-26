# -*- coding: utf-8 -*-
"""
MILP Capacity Planning
======================

基于 OR-Tools CBC 的混合整数线性规划容量规划，支持多设备选型、需量电费与更换周期。

本文件由 Load Curve Studio (LoomGrid) 主程序剥离而来，保留原始逻辑，仅去除 GUI 依赖。
原始出处：负荷曲线批量生成Ultra.py v3.2

依赖：numpy / pandas

"""

import math
import numpy as np
from typing import List, Dict, Optional, Tuple
import pandas as pd


class MilpDevice:
    """储能设备参数."""
    capacity: float          # kWh
    max_c_rate: float = 0.5  # 最大充放电倍率
    price: float = 0         # 单价(元)
    eta_ch: float = 0.95     # 充电效率
    eta_dis: float = 0.95


class MilpConfig:
    """MILP 规划配置."""
    devices: List[MilpDevice] = None       # 可选设备列表
    demands_price: dict = None             # {月份: 元/kW/月}
    discount_rate: float = 0.06            # 折现率(≥0→NPV模式,<0→净利润模式)
    lifecycle: int = 25                    # 项目寿命(年)
    soc_min: float = 0.05                  # 最小SOC
    soc_max: float = 0.95                  # 最大SOC
    soc_initial: float = 0.1               # 初始SOC
    device_count: int = 0                  # 固定设备数(>0则跳过选型)
    demand_limit: float = 0                # 电网功率上限(kW)
    pack_replace_months: int = 300         # Pack更换周期(月)
    cell_replace_months: int = 96          # 电芯更换周期(月)

    def __post_init__(self):
        """v3.2fix(P-31): devices/demands_price 默认是 None，但 solve_milp_capacity
        直接做 devices[j] 下标与 config.demands_price.get(...)，用默认配置调用必崩。
        这里统一兜底为空容器；devices 为空时由求解入口提前返回错误状态。"""
        if self.devices is None:
            self.devices = []
        if self.demands_price is None:
            self.demands_price = {}


class MilpResult:
    """MILP 求解结果."""
    objective: float = 0           # 目标值(NPV或净利润)
    capacity_kwh: float = 0        # 推荐容量
    power_kw: float = 0            # 推荐功率
    device_count: int = 0          # 设备台数
    demands: list = None           # 每月需量
    purchase: np.ndarray = None    # 逐时买电
    export: np.ndarray = None      # 逐时卖电
    charge: np.ndarray = None      # 逐时充电
    discharge: np.ndarray = None   # 逐时放电
    soc: np.ndarray = None         # 逐时SOC(%)
    status: str = 'unsolved'       # optimal/feasible/infeasible/error
    solve_time_s: float = 0


def _no_storage_benefit(df: pd.DataFrame, demands_price: dict) -> float:
    """无储能基准收益: 卖电-买电-需量费."""
    purchase_df = df.query("consume > produce")
    export_df = df.query("produce >= consume")
    return (
        (export_df["FiT"] * (export_df["produce"] - export_df["consume"])).sum()
        - (purchase_df["price"] * (purchase_df["consume"] - purchase_df["produce"])).sum()
        - ((df.groupby('month')['consume']
            .apply(lambda x: (x - df.loc[x.index, 'produce']).max())) * pd.Series(demands_price))
        .fillna(0).sum()
    )


def bridge_ultra_to_milp(monthly_data: dict, province: str = None,
                          strategy_periods: dict = None,
                          peak_price: float = 1.1, valley_price: float = 0.27,
                          flat_price: float = 0.7, fit_price: float = 0.41,
                          pv_data: dict = None,
                          tariff_periods: dict = None) -> pd.DataFrame:
    """
    将 Ultra 月度负荷数据桥接为 MILP 要求的 8760h DataFrame.

    monthly_data: {month_str: {'load_array': (n_days, n_pts), 'time_labels': [...], 'date_labels': [...]}}
    strategy_periods: {month: [(start_h, end_h, type), ...]} 分时电价时段
    pv_data: {month_str: array(n_days, n_pts)} 光伏发电数据(kW), None→全零
    返回: 含 produce/consume/FiT/price/month/hour 列的 DataFrame
    """
    rows = []
    for month_str, data in sorted(monthly_data.items()):
        arr = np.asarray(data.get('load_array', []))
        dates = data.get('date_labels', [])
        if arr.size == 0:
            continue
        m = int(month_str.split('-')[1]) if '-' in month_str else 1
        n_pts = arr.shape[1]
        n_days = arr.shape[0]

        # 获取当月光伏数据
        pv_arr = None
        if pv_data:
            pv_key = month_str
            if pv_key not in pv_data:
                # 尝试匹配月份数字
                for k in pv_data:
                    if str(m) in str(k):
                        pv_key = k
                        break
            if pv_key in pv_data:
                pv_arr = np.asarray(pv_data[pv_key])

        # 获取当月时段: tariff_periods(尖峰平谷,优先) / strategy_periods(充放电,兼容)
        month_periods = strategy_periods.get(str(m), []) if strategy_periods else []
        month_tariff = tariff_periods.get(str(m), []) if tariff_periods else []

        def _iter_period_slots(items):
            """v3.2fix: 兼容 (start,end,type) 元组与 {'start','end','type'} 字典两种时段格式."""
            for _it in items or []:
                if isinstance(_it, dict):
                    yield _it.get('start', 0.0), _it.get('end', 0.0), _it.get('type', '')
                else:
                    try:
                        yield _it[0], _it[1], _it[2]
                    except (TypeError, IndexError):
                        continue

        def _price_of_type(p_type) -> float:
            ts = str(getattr(p_type, 'value', p_type)).lower()
            if 'peak' in ts or '尖' in ts or ts == '2':
                return peak_price
            if 'valley' in ts or '谷' in ts or ts == '4':
                return valley_price
            return flat_price

        def _get_price(hour: float) -> float:
            """根据分时电价时段确定该小时的电价. v3.2fix: 支持字典/元组与跨午夜时段."""
            src = month_tariff or month_periods
            if not src:
                return peak_price if (9 <= hour < 15 or 17 <= hour < 22) else valley_price
            for p_start, p_end, p_type in _iter_period_slots(src):
                try:
                    s, e = float(p_start), float(p_end)
                except (TypeError, ValueError):
                    continue
                if s <= e:
                    if s <= hour < e:
                        return _price_of_type(p_type)
                else:  # 跨午夜时段, 如 23:00-06:00
                    if hour >= s or hour < e:
                        return _price_of_type(p_type)
            return flat_price  # 默认平段

        for d_idx in range(n_days):
            # 日期解析
            raw_date = dates[d_idx] if d_idx < len(dates) else None
            if raw_date is None:
                dt_str = f"2024-{m:02d}-{d_idx+1:02d}"
            elif hasattr(raw_date, 'strftime'):
                dt_str = raw_date.strftime('%Y-%m-%d')
            else:
                dt_str = str(raw_date)[:10]

            for t_idx in range(n_pts):
                h = t_idx * (24.0 / n_pts)
                load = float(arr[d_idx, t_idx])
                if np.isnan(load):
                    load = 0.0
                # 光伏: 优先用pv_data, 其次从负负荷提取
                pv = 0.0
                if pv_arr is not None and d_idx < pv_arr.shape[0] and t_idx < pv_arr.shape[1]:
                    pv = float(pv_arr[d_idx, t_idx])
                    if np.isnan(pv):
                        pv = 0.0
                elif load < 0:
                    pv = -load
                    load = 0.0  # 负值全部归光伏

                rows.append({
                    'datetime': dt_str,
                    'consume': max(0, load),
                    'produce': max(0, pv),
                    'FiT': fit_price,
                    'price': _get_price(h),
                    'month': m,
                    'hour': int(h),
                })
    df = pd.DataFrame(rows)
    if 'datetime' in df.columns and len(df) > 0:
        df['datetime'] = pd.to_datetime(df['datetime'])
    return df


def solve_milp_capacity(df: pd.DataFrame, config: MilpConfig) -> MilpResult:
    """
    MILP 8760h 全局最优容量规划.

    df 列要求: datetime, produce(光伏kW), consume(负荷kW), FiT(上网电价),
               price(市电价), month(月), hour(时)
    可选: charge_sign(1充电/0静置/-1放电, 用于固定充放策略)

    v3.1fix (相对planning.py):
      - 放电约束: SOC*eta_dis (原为 SOC/eta_dis, 会超放5%)
      - 折现率: config.discount_rate (原错误使用device_count)
      - 三份重复代码 → 一份
    """
    import time; t0 = time.time()
    from ortools.linear_solver import pywraplp

    devices = config.devices or [MilpDevice(capacity=261.248, max_c_rate=0.5)]
    device_n = len(devices)
    # 默认效率取第一台,多设备备选时各台效率可不同(当前用于SOC约束,单设备场景无影响)
    eta_ch = devices[0].eta_ch
    eta_dis = devices[0].eta_dis
    months = sorted(df["month"].unique())
    n = len(df)
    n_to_M = df["month"].tolist()
    UB = float(df[["consume", "produce"]].max().max())  # 决策变量上界
    Z_UB = 1000  # 最大设备数

    solver = pywraplp.Solver.CreateSolver('SCIP')
    if not solver:
        solver = pywraplp.Solver.CreateSolver('CBC')
    if not solver:
        return MilpResult(status='error')
    solver.SuppressOutput()

    # ── 决策变量 ──
    D = {m: solver.NumVar(0, UB, f"d_{m}") for m in months}
    X_IN = [solver.NumVar(0, UB, f"xi_{i}") for i in range(n)]
    X_OUT = [solver.NumVar(0, UB, f"xo_{i}") for i in range(n)]
    Y_CH = [solver.NumVar(0, UB, f"yc_{i}") for i in range(n)]
    Y_DIS = [solver.NumVar(0, UB, f"yd_{i}") for i in range(n)]
    SOC = [solver.NumVar(0, solver.infinity(), f"s_{i}") for i in range(n)]
    Z = [solver.IntVar(0, Z_UB, f"z_{j}") for j in range(device_n)]
    B_Z = [solver.BoolVar(f"bz_{j}") for j in range(device_n)]

    # ── 储能容量/功率表达式 ──
    C_expr = solver.Sum(Z[j] * devices[j].capacity for j in range(device_n))
    P_expr = solver.Sum(Z[j] * devices[j].max_c_rate * devices[j].capacity for j in range(device_n))

    benefit0 = _no_storage_benefit(df, config.demands_price)

    export_income = solver.Sum(float(df.iloc[i]["FiT"]) * X_OUT[i] for i in range(n))
    purchase_cost = solver.Sum(float(df.iloc[i]["price"]) * X_IN[i] for i in range(n))
    demand_cost = solver.Sum(D[m] * config.demands_price.get(m, 0) for m in months)
    shell_cost = solver.Sum(Z[j] * devices[j].price * 0.4 for j in range(device_n))  # 壳体≈40%总价
    cell_cost = solver.Sum(Z[j] * devices[j].price * 0.6 for j in range(device_n))   # 电芯≈60%总价

    # ── 目标函数 ──
    if config.discount_rate >= 0:
        # 最大化NPV
        npv = -(shell_cost + cell_cost)
        for yr in range(config.lifecycle):
            cf = export_income - purchase_cost - demand_cost
            # v3.2fix: 更换年份落在项目期内才计提。原式 pack 300月//12=25, 而 range(lifecycle=25) 只到 24,
            # 判定恒假→最长寿命里最贵的一次更换被静默忽略, NPV 偏高。
            _pack_yr = max(1, int(round(config.pack_replace_months / 12.0)))
            _cell_yr = max(1, int(round(config.cell_replace_months / 12.0)))
            if _pack_yr < config.lifecycle and yr == _pack_yr:
                cf -= (shell_cost + cell_cost)
            if _cell_yr < config.lifecycle and yr == _cell_yr:
                cf -= cell_cost
            npv += cf / ((1 + config.discount_rate) ** (yr + 1))  # v3.1fix: discount_rate
        npv0 = sum(benefit0 / ((1 + config.discount_rate) ** (yr + 1)) for yr in range(config.lifecycle))
        solver.Maximize(npv - npv0)
    else:
        # 最大化净利润
        investment = (shell_cost * 12 / (config.pack_replace_months or 300)
                      + cell_cost * 12 / (config.cell_replace_months or 96))
        solver.Maximize(export_income - purchase_cost - demand_cost - investment - benefit0)

    # ── 需量帽 ──
    if config.demand_limit > 0:
        for m in months:
            solver.Add(D[m] <= config.demand_limit)

    # ── 约束条件 ──
    for i in range(n):
        row = df.iloc[i]
        prod = float(row["produce"])
        cons = float(row["consume"])

        # 功率平衡
        solver.Add(prod + X_IN[i] - X_OUT[i] + Y_DIS[i] - Y_CH[i] == cons)
        # 电网约束
        solver.Add(X_IN[i] <= D[n_to_M[i]])
        solver.Add(X_OUT[i] <= prod)
        # v3.2fix: 买电不得超过自身负荷——堵住"买谷电转卖"的账面套利(原仅令 X_OUT≤prod, 无耦合)
        solver.Add(X_IN[i] <= cons)
        # 储能功率约束
        solver.Add(Y_CH[i] <= P_expr)
        solver.Add(Y_DIS[i] <= P_expr)
        # v3.1fix: 放电≤SOC*eta_dis (原bug: 除eta_dis会高估5%)
        if i == 0:
            solver.Add(SOC[i] == config.soc_initial * C_expr)
        else:
            solver.Add(SOC[i] == SOC[i-1]
                        + eta_ch * Y_CH[i-1]
                        - Y_DIS[i-1] / eta_dis)
        solver.Add(SOC[i] >= config.soc_min * C_expr)
        solver.Add(SOC[i] <= config.soc_max * C_expr)
        solver.Add(Y_DIS[i] <= SOC[i] * eta_dis)  # v3.1fix
        # 充放电来源约束
        solver.Add(Y_CH[i] <= prod + X_IN[i])
        # 强制充放标记
        cs = row.get("charge_sign") if "charge_sign" in df.columns else None
        if cs is not None and not pd.isna(cs):
            if cs < 0:
                solver.Add(Y_CH[i] == 0)
            elif cs > 0:
                solver.Add(Y_DIS[i] == 0)
            else:
                solver.Add(Y_CH[i] == 0)
                solver.Add(Y_DIS[i] == 0)

    # 循环约束: 末态SOC=初始SOC
    solver.Add(SOC[n-1]
               + eta_ch * Y_CH[n-1]
               - Y_DIS[n-1] / eta_dis
               == config.soc_initial * C_expr)

    # 选型约束
    if config.device_count > 0:
        # 固定台数: 首台设备=指定数量, 其余为0
        solver.Add(Z[0] == config.device_count)
        for j in range(1, device_n):
            solver.Add(Z[j] == 0)
    else:
        for j in range(device_n):
            solver.Add(Z[j] <= Z_UB * B_Z[j])
            solver.Add(Z[j] >= B_Z[j])
        solver.Add(solver.Sum(B_Z[j] for j in range(device_n)) == 1)

    # ── 求解 ──
    status = solver.Solve()
    result = MilpResult(solve_time_s=time.time() - t0)

    if status in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE):
        result.objective = solver.Objective().Value()
        result.capacity_kwh = C_expr.solution_value()
        result.power_kw = P_expr.solution_value()
        result.device_count = int(round(sum(Z[j].solution_value() for j in range(device_n))))
        result.demands = [D[m].solution_value() for m in months]
        result.purchase = np.array([x.solution_value() for x in X_IN])
        result.export = np.array([x.solution_value() for x in X_OUT])
        result.charge = np.array([y.solution_value() for y in Y_CH])
        result.discharge = np.array([y.solution_value() for y in Y_DIS])
        soc_raw = np.array([s.solution_value() for s in SOC])
        result.soc = _safe_soc_normalize(soc_raw, result.capacity_kwh)
        result.status = 'optimal' if status == pywraplp.Solver.OPTIMAL else 'feasible'
    else:
        result.status = 'infeasible'

    return result
