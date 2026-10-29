# -*- coding: utf-8 -*-
"""三个调度引擎的统一测量口径。

## 为什么需要这个文件

`engines_data.py`（三引擎对拍图）和 `compare_data.py`（落地页对照器）
原本各写一套，结果同一个工况算出不同数字。三处不一致：

1. **仿真的效率处理**：engines_data 用 `dsoc/DT` 当功率，没算效率损失，
   充电成本被低估 5%、放电收入被高估 5%，收益虚高。
2. **贪心的收益**：engines_data 用 `放电量×最高价 − 充电量×最低价`，
   等于假设所有动作都发生在极端价上。实际每 15 分钟价格都不同。
3. **时段的粒度**：一个用 4 个大时段，一个用 96 个 15 分钟时段，
   仿真器按不同规则动作。

三处叠加，¥1258 vs ¥1196、¥46 vs ¥11 这种差异就出现了。

## 统一口径

    基准成本 = Σ load × buy_price × dt
    实际成本 = Σ (load − dis + ch) × buy_price × dt
    日省     = 基准 − 实际

其中 ch / dis 都是**电网侧**功率（交流侧）：
    充电：电网取 p_ch，进电池 p_ch × η        → SOC 增 p_ch × η × dt / cap
    放电：电池放 p_dis / η，电网得 p_dis      → SOC 减 p_dis / η × dt / cap
反过来从 SOC 差分推功率：
    dE > 0（充）：p_ch  = dE / η / dt
    dE < 0（放）：p_dis = −dE × η / dt
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

import numpy as np  # noqa: E402

N, DT = 96, 0.25
HOURS = np.arange(N) * DT

ETA = 0.95
SOC_MIN, SOC_MAX, SOC_INIT = 0.08, 0.95, 0.10
CAP_DEFAULT, PMAX_DEFAULT = 2000.0, 400.0

# 基准分时电价：谷 0.30 / 平 0.65 / 峰 1.05 / 尖 1.20（价差 0.90）
PRICE_BASE = 0.30
PRICE_MID = 0.65
PRICE_PEAK = 1.05
PRICE_TIP = 1.20


def load_day() -> np.ndarray:
    """商业综合体一天，96 点，固定种子。三处图/表共用这一条。"""
    def load_at(h):
        g = lambda c, s, a: a * np.exp(-((h - c) ** 2) / (2 * s * s))
        v = (150 + g(10.2, 2.6, 780) + g(12.4, 0.8, -220)
             + g(16.4, 2.4, 880) + g(19.6, 1.6, 460) + g(3.5, 2.0, -55))
        return float(max(v, 90.0))

    base = np.array([load_at(h) for h in HOURS])
    rng = np.random.default_rng(20261029)
    return np.clip(base + rng.normal(0, 6.0, N), 80, None)


def price_day(spread: float | None = None) -> np.ndarray:
    """分时电价。spread=None 用基准序列；给定 spread 时谷价固定、其余按比例爬。

    系数 0.39 / 0.83 由基准序列反推：spread=0.90 时平=0.651、峰=1.047，
    与基准 0.65 / 1.05 相差 0.003 以内，保证对照器默认点与首页图对得上。
    """
    if spread is None:
        mid, peak, tip = PRICE_MID, PRICE_PEAK, PRICE_TIP
    else:
        # 0.3889 / 0.8333 是精确反推：spread=0.90 时平=0.65、峰=1.05，
        # 与基准序列逐点相同。这样对照器默认点和首页那张图的 ¥1676 对得上，
        # 读者不会在两个地方看到两个数。
        mid = PRICE_BASE + spread * (0.35 / 0.90)
        peak = PRICE_BASE + spread * (0.75 / 0.90)
        tip = PRICE_BASE + spread

    out = np.full(N, mid)
    for i, h in enumerate(HOURS):
        if h < 7 or h >= 23:
            out[i] = PRICE_BASE
        elif (10 <= h < 12) or (17 <= h < 22):
            out[i] = peak
        elif 14 <= h < 16:
            out[i] = tip
    return out


def periods_15min() -> list:
    """96 个 15 分钟电价时段，用电价词表（valley/flat/peak）。

    仿真器按电价类型判断该充还是该放，这比直接告诉它「这段充电」更接近
    它在 Fusion 里的真实用法——那里时段是从省级电价库读出来的。
    """
    out = []
    for i, h in enumerate(HOURS):
        if h < 7 or h >= 23:
            t = "valley"
        elif (10 <= h < 12) or (17 <= h < 22) or (14 <= h < 16):
            t = "peak"
        else:
            t = "flat"
        out.append((float(h), float(min(h + DT, 24.0)), t))
    return out


def time_labels() -> list:
    return [f"{int(h):02d}:{int(round((h % 1) * 60)):02d}" for h in HOURS]


def baseline_cost(load, price) -> float:
    return float(np.sum(load * price * DT))


def saving(load, price, ch, dis) -> float:
    """电网侧充放功率 → 相对「不装储能」省了多少钱。逐点算，不取极端价。"""
    ch = np.asarray(ch, float)
    dis = np.asarray(dis, float)
    net = load - dis + ch
    return baseline_cost(load, price) - float(np.sum(net * price * DT))


def soc_to_power(soc_pct: np.ndarray, cap: float) -> tuple[np.ndarray, np.ndarray]:
    """从 SOC 百分比轨迹反推电网侧充放功率。

    仿真器只回总量和 SOC 轨迹，不回逐点功率。差分是唯一能拿到逐点值的路。
    已核对：反推的累计量与仿真器自报的 charge_kwh / discharge_kwh 一致
    （2189.5 vs 2189.47，800.0 vs 800.0）。
    """
    soc = np.asarray(soc_pct, float).reshape(-1)
    if soc.size == N + 1:          # 含初始点的 n+1 序列
        soc = soc[1:]
    d_e = np.diff(np.concatenate([[SOC_INIT * 100.0], soc])) / 100.0 * cap
    ch = np.where(d_e > 0, d_e / ETA / DT, 0.0)
    dis = np.where(d_e < 0, -d_e * ETA / DT, 0.0)
    return ch, dis


# ── 三个引擎的统一封装 ────────────────────────────────────────────────

def run_lp(load, price, cap=CAP_DEFAULT, pmax=PMAX_DEFAULT) -> dict:
    from algorithms.dispatch import optimal_dispatch_lp

    r = optimal_dispatch_lp(
        load_kw=load, buy_price=price, sell_price=price,
        batt_kwh=float(cap), Pmax=float(pmax), eta_c=ETA, eta_d=ETA,
        soc_min=SOC_MIN, soc_max=SOC_MAX, soc_init=SOC_INIT, dt=DT)
    if not r or "ch" not in r:
        return {"ok": False, "saving": 0.0}
    ch = np.asarray(r["ch"], float)
    dis = np.asarray(r["dis"], float)
    soc_kwh = np.asarray(r.get("soc", []), float)
    soc = np.clip(soc_kwh / float(cap), 0.0, 1.0) if soc_kwh.size == N else \
        np.clip(np.cumsum((ch * ETA - dis / ETA) * DT) / cap + SOC_INIT, 0, 1)
    return {"ok": True, "ch": ch, "dis": dis, "soc": soc,
            "saving": saving(load, price, ch, dis)}


def run_sim(load, price, cap=CAP_DEFAULT, pmax=PMAX_DEFAULT) -> dict:
    from algorithms.dispatch import simulate_daily_dispatch

    r = simulate_daily_dispatch(
        load_array=np.asarray(load, float).reshape(1, -1),
        time_labels=np.array(time_labels()), periods=periods_15min(),
        capacity_kwh=float(cap), power_kw=float(pmax), efficiency=ETA,
        initial_soc=SOC_INIT, soc_min=SOC_MIN, soc_max=SOC_MAX)
    if not r:
        return {"ok": False, "saving": 0.0}
    raw = np.asarray(r["soc_trajectory"], float).reshape(-1)
    ch, dis = soc_to_power(raw, float(cap))
    soc = (raw[1:] if raw.size == N + 1 else raw) / 100.0
    return {"ok": True, "ch": ch, "dis": dis, "soc": soc,
            "saving": saving(load, price, ch, dis),
            "ch_kwh": float(np.sum(r["charge_kwh"])),
            "dis_kwh": float(np.sum(r["discharge_kwh"]))}


def run_greedy(load, price, cap=CAP_DEFAULT, pmax=PMAX_DEFAULT) -> dict:
    from algorithms.dispatch import greedy_dispatch_annual

    r = greedy_dispatch_annual(
        load_kw=np.asarray(load, float), pv_kw=None, wind_kw=None,
        batt_kwh=float(cap), c_rate=float(pmax) / float(cap),
        eta_roundtrip=ETA * ETA, soc_init_frac=SOC_INIT,
        soc_min_frac=SOC_MIN, soc_max_frac=SOC_MAX, dt_hours=DT)
    if not r:
        return {"ok": False, "saving": 0.0}
    ch = np.asarray(r["bess_charge"], float)
    dis = np.asarray(r["bess_discharge"], float)
    # greedy 返回的 soc 单位不可靠（实测 1.6 量级，容量 2000 kWh），
    # 用充放积分一条同口径的
    soc = np.empty(N)
    s = SOC_INIT
    for i in range(N):
        s += (ch[i] * ETA - dis[i] / ETA) * DT / float(cap)
        soc[i] = float(np.clip(s, SOC_MIN, SOC_MAX))
    return {"ok": True, "ch": ch, "dis": dis, "soc": soc,
            "saving": saving(load, price, ch, dis)}


def run_all(load=None, price=None, cap=CAP_DEFAULT, pmax=PMAX_DEFAULT) -> dict:
    load = load_day() if load is None else load
    price = price_day() if price is None else price
    return {
        "lp": run_lp(load, price, cap, pmax),
        "sim": run_sim(load, price, cap, pmax),
        "greedy": run_greedy(load, price, cap, pmax),
    }


if __name__ == "__main__":
    load, price = load_day(), price_day()
    print(f"负荷峰 {load.max():.0f} kW  电价 {price.min():.2f}~{price.max():.2f}  "
          f"基准成本 ¥{baseline_cost(load, price):.0f}")
    for name, r in run_all(load, price).items():
        if not r["ok"]:
            print(f"  {name:<7} 求解失败")
            continue
        print(f"  {name:<7} 充 {np.sum(r['ch'])*DT:>6.0f}  放 {np.sum(r['dis'])*DT:>6.0f}  "
              f"省 ¥{r['saving']:>7.0f}")
