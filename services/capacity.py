# -*- coding: utf-8 -*-
"""容量与吞吐量测算：把「日需求」和「日吞吐量」两个量纲分开。

为什么这个模块存在
------------------
原来这两件事都埋在 ProfessionalWindow 里，靠 `est_combo.currentText()` 的分支区分，
且其中一个（日吞吐）根本没实现——C3 那条待确认项说的「各段 min 之和」被记在
「月最小段总和」这个列名下，量纲对不上。

本模块把口径显式化：

    daily_demand      单位 kWh      回答「电池该装多大」
    daily_throughput  单位 kWh/天   回答「一天能搬多少能量」

**两者不可互换、不可比较。** 前者是单次容量，后者是一天累计吞吐。

口径定义（与界面现有两项一致，行为不变）
----------------------------------------
min_feasible  最小可行容量：min(所有段当日的值)
              每个时段都能操作所需的最小容量——瓶颈法
full_load     满载利用率：max(min(每个配对的充、放))
              挑一个时段让它跑满所需的容量——挑选法

段的选择逻辑
------------
原实现依赖下拉框文字（"合并充电段" / 段名 / 回退第一个），这里改成参数：
    mode="all"      所有段参与（对应界面 two_two）
    pick=<段名>     只取该段
    pick="merge"    合并前两段
    pick=None       回退到第一段（与界面默认分支一致）
"""
from __future__ import annotations

from enum import Enum

import numpy as np

__all__ = [
    "DemandBasis",
    "relevant_segments",
    "daily_demand_series",
    "daily_throughput_series",
    "daily_throughput_kwh_per_day",
    "summarize_daily_demand",
]


class DemandBasis(str, Enum):
    """日需求容量的口径。两个都是 kWh，回答「装多大」。"""
    min_feasible = "min_feasible"   # 最小可行容量：min(所有段的值)
    full_load = "full_load"         # 满载利用率：max(min(每段的充,放))


def relevant_segments(charge_segs, discharge_segs, charge_labels, discharge_labels,
                      mode="all", charge_pick=None, discharge_pick=None):
    """按选择逻辑挑出参与容量评估的段。

    charge_segs / discharge_segs: list[np.ndarray]，每项形状 (n_days,)
    返回 (selected_charge, selected_discharge)，同样是 list[np.ndarray]

    mode="all" 且两个 pick 都为 None 时返回全部（对应界面 two_two）；
    只要给了 pick 就走选择分支，不会被 mode="all" 吞掉。
    """
    if mode == "all" and charge_pick is None and discharge_pick is None:
        return list(charge_segs), list(discharge_segs)

    def pick(segs, labels, choice):
        if not segs:
            return []
        if choice == "merge" and len(segs) >= 2:
            return [segs[0] + segs[1]]
        if choice in labels:
            idx = labels.index(choice)
            if idx < len(segs):
                return [segs[idx]]
        return [segs[0]]      # 回退：与界面默认分支一致

    return pick(charge_segs, charge_labels, charge_pick), \
        pick(discharge_segs, discharge_labels, discharge_pick)


def _n_days(charge_segs, discharge_segs) -> int:
    if charge_segs:
        return len(charge_segs[0])
    if discharge_segs:
        return len(discharge_segs[0])
    return 0


def daily_demand_series(charge_segs, discharge_segs,
                        basis: DemandBasis | str = DemandBasis.min_feasible,
                        drop_zero_placeholders: bool = False) -> np.ndarray:
    """逐日的需求容量（kWh）。口径见模块文档。

    drop_zero_placeholders 默认 False —— 与现有界面行为一致。

    ⚠️ 但这意味着：compute_segmented_spaces 返回的四个列表里，每个段在非本
    方向那一侧是**占位零**，不过滤的话 min_feasible 的 min(所有段值) 恒为 0，
    full_load 的 max(min(每对充放)) 也恒为 0——因为每对里总有一侧是占位零。
    这是 C3 待确认口径问题的实质，已在阶段 2 报告里单独列出。
    传 True 时会先剔除全零段再算，供对比验证用。
    """
    basis = DemandBasis(basis) if not isinstance(basis, DemandBasis) else basis
    if drop_zero_placeholders:
        charge_segs = [a for a in charge_segs if float(np.nansum(a)) != 0.0]
        discharge_segs = [a for a in discharge_segs if float(np.nansum(a)) != 0.0]
    n = _n_days(charge_segs, discharge_segs)
    out = np.zeros(n, dtype=float)
    if n == 0:
        return out

    for d in range(n):
        if basis is DemandBasis.min_feasible:
            vals = [float(arr[d]) for arr in charge_segs if d < len(arr)]
            vals += [float(arr[d]) for arr in discharge_segs if d < len(arr)]
            out[d] = min(vals) if vals else 0.0
        else:  # full_load
            n_pairs = max(len(charge_segs), len(discharge_segs))
            best = 0.0
            for i in range(n_pairs):
                cv = float(charge_segs[i][d]) if i < len(charge_segs) and d < len(charge_segs[i]) else float('inf')
                dv = float(discharge_segs[i][d]) if i < len(discharge_segs) and d < len(discharge_segs[i]) else float('inf')
                pair_min = min(cv, dv)
                if pair_min != float('inf'):
                    best = max(best, pair_min)
            out[d] = best
    return out


def summarize_daily_demand(charge_segs, discharge_segs,
                           basis: DemandBasis | str = DemandBasis.min_feasible,
                           target_days: int | None = None) -> dict:
    """把逐日需求汇总成一个数（供容量测算用）。

    target_days 给定时按「挑最好的 N 天」取值（与 calc_capacity_for_days 一致），
    否则给全期最大值。
    """
    series = daily_demand_series(charge_segs, discharge_segs, basis)
    if series.size == 0:
        return {"basis": DemandBasis(basis).value, "daily": [], "value_kwh": 0.0,
                "days": 0, "note": "无有效数据"}
    if target_days is not None and 0 < target_days <= series.size:
        sorted_vals = np.sort(series)
        value = float(sorted_vals[max(0, series.size - target_days)])
        note = f"取最好的 {target_days} 天"
    else:
        value = float(np.max(series))
        note = "取全期最大值"
    return {
        "basis": DemandBasis(basis).value,
        "daily": [round(float(x), 6) for x in series],
        "value_kwh": round(value, 6),
        "days": int(series.size),
        "note": note,
    }


def daily_throughput_series(charge_segs, discharge_segs) -> np.ndarray:
    """逐日的吞吐量（kWh/天）= 各段 min(充电空间, 放电空间) 之和。

    这是「一天能搬多少能量」，不是「电池该装多大」。
    单段配置（只有充电段或只有放电段）恒为 0——没有充放配对就不构成循环，
    这不是 bug，调用方需要据此给提示。
    """
    n = _n_days(charge_segs, discharge_segs)
    out = np.zeros(n, dtype=float)
    if n == 0:
        return out
    n_pairs = max(len(charge_segs), len(discharge_segs))
    for i in range(n_pairs):
        c = np.asarray(charge_segs[i], dtype=float) if i < len(charge_segs) else np.zeros(n)
        d = np.asarray(discharge_segs[i], dtype=float) if i < len(discharge_segs) else np.zeros(n)
        out += np.minimum(c, d)
    return out


def daily_throughput_kwh_per_day(charge_segs, discharge_segs) -> float:
    """日吞吐量的典型值：逐日吞吐量的平均值（kWh/天）。"""
    series = daily_throughput_series(charge_segs, discharge_segs)
    if series.size == 0:
        return 0.0
    return round(float(np.mean(series)), 6)
