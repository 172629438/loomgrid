# -*- coding: utf-8 -*-
"""性能基线：超过阈值就红，防止迭代中悄悄变慢。

阈值定得很宽松（实测值的 3~5 倍），目的不是「跑得快」而是「别退回秒级」。
真正会拖慢的通常是意外引入的 O(n²) 或重复 IO，宽松阈值足够抓到。

实测基线（本机，2026-11-04）：
  长表解析 10 万行        0.62s   → 162 k行/秒
  365×96 空间测算         1 ms
  容量反推（365 天）      < 1 ms
  365 天 × 96 点绘图      0.85s
  31 天 × 96 点绘图       0.22s
  10 万行 xlsx 扫描       1.0s（35 组）

跑法：python tests/verify_performance_baseline.py
"""
import os
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
sys.stdout.reconfigure(encoding="utf-8")

import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from core.ingest import _parse_long_to_standard, _scan_file_worker  # noqa: E402
from core.spaces import calc_capacity_for_days, compute_segmented_spaces  # noqa: E402
from plotting.curves import _draw_curves_core, _get_cached_font  # noqa: E402

plt.rcParams["font.sans-serif"] = [_get_cached_font()]
plt.rcParams["axes.unicode_minus"] = False

PASS = FAIL = 0
TIMES = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 15, 30, 45)]
SEGS = [
    {"start": 0.0, "end": 8.0, "type": "charge", "label": "谷段"},
    {"start": 10.0, "end": 12.0, "type": "discharge", "label": "峰段"},
    {"start": 17.0, "end": 21.0, "type": "discharge", "label": "峰段2"},
]


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}  {detail}")


def best_of(fn, repeat=1):
    best = None
    r = None
    for _ in range(repeat):
        t0 = time.perf_counter()
        r = fn()
        d = time.perf_counter() - t0
        best = d if best is None else min(best, d)
    return best, r


tmp = tempfile.mkdtemp(prefix="perf_")
try:
    # ══════════════════════════════════════════════════════
    print("=" * 74)
    print("① 长表解析 10 万行")
    print("=" * 74)
    N = 100_000
    base = pd.Timestamp("2026-07-01")
    rows = [{"采集时间": (base + pd.Timedelta(minutes=15 * i)).strftime("%Y-%m-%d %H:%M"),
             "负荷": 100 + (i % 500) * 0.1} for i in range(N)]
    df_big = pd.DataFrame(rows)
    t, (out, ratio) = best_of(
        lambda: _parse_long_to_standard(df_big, "采集时间", "采集时间", "负荷"))
    print(f"  实测 {t:.2f}s（吞吐 {N/t/1000:.0f} k行/秒），丢弃率 {ratio:.2%}")
    check(f"10 万行解析 < 5s（实 {t:.2f}s）", t < 5.0, f"{t:.2f}s")
    check(f"一行没丢（实 {len(out)}/{N}）", len(out) == N, f"{len(out)}")

    # ══════════════════════════════════════════════════════
    print()
    print("=" * 74)
    print("② 全年 365 天 × 96 点：空间测算")
    print("=" * 74)
    LOAD_Y = np.random.default_rng(1).uniform(50, 500, size=(365, 96))
    t, res = best_of(lambda: compute_segmented_spaces(
        LOAD_Y, TIMES, SEGS, mode="A", billing={"value": 300}), repeat=3)
    print(f"  实测 {t*1000:.0f} ms")
    check(f"365×96 空间测算 < 1s（实 {t*1000:.0f} ms）", t < 1.0, f"{t:.3f}s")
    c, d, cl, dl = res
    check("返回 3 段", len(c) == 3, str(len(c)))

    daily = np.minimum(np.sum(np.stack(c), axis=0), np.sum(np.stack(d), axis=0))
    t, cap = best_of(lambda: calc_capacity_for_days(330, daily), repeat=10)
    print(f"  容量反推实测 {t*1000:.2f} ms → {cap:.0f}")
    check(f"容量反推 < 100ms（实 {t*1000:.2f} ms）", t < 0.1, f"{t:.4f}s")

    # ══════════════════════════════════════════════════════
    print()
    print("=" * 74)
    print("③ 绘图")
    print("=" * 74)
    opts = {"show_daily": True, "show_avg": True, "fit_types": [],
            "show_max_demand": True, "highlight_mode": "weekend",
            "mark_anomalies": False, "dark_theme": False, "show_weekend_bg": True}
    dates_y = [(base + pd.Timedelta(days=i)).strftime("%Y-%m-%d") for i in range(365)]

    def plot(df_load, dates, name):
        fig, ax = plt.subplots(figsize=(16, 8))
        _draw_curves_core(ax, dates, df_load, TIMES, name, opts)
        p = os.path.join(tmp, f"{name}.png")
        fig.savefig(p, dpi=100, bbox_inches="tight")
        plt.close(fig)
        return os.path.getsize(p)

    t365, s365 = best_of(lambda: plot(LOAD_Y, dates_y, "y"), repeat=2)
    print(f"  365 天：{t365:.2f}s  {s365/1024:.0f} KB")
    check(f"365 天绘图 < 6s（实 {t365:.2f}s）", t365 < 6.0, f"{t365:.2f}s")
    check("产出图非空（>100 KB）", s365 > 100 * 1024, str(s365))

    t31, s31 = best_of(lambda: plot(LOAD_Y[:31], dates_y[:31], "m"), repeat=3)
    print(f"  31 天：{t31:.2f}s  {s31/1024:.0f} KB")
    check(f"31 天绘图 < 3s（实 {t31:.2f}s）", t31 < 3.0, f"{t31:.2f}s")

    # ══════════════════════════════════════════════════════
    print()
    print("=" * 74)
    print("④ 端到端：10 万行长表 xlsx 扫描")
    print("=" * 74)
    big = os.path.join(tmp, "big.xlsx")
    df_big.to_excel(big, index=False)
    mapping = {"date_col": "采集时间", "time_col": "采集时间", "load_col": "负荷",
               "asset_col": None, "format_type": "long", "header_row": 0,
               "delimiter": ",", "convert_accumulated": False, "transpose": False}
    t, r = best_of(lambda: _scan_file_worker(
        (big, "Sheet1", mapping, None, threading.Event(), None)))
    print(f"  实测 {t:.2f}s")
    check(f"10 万行 xlsx 扫描 < 15s（实 {t:.2f}s）", t < 15.0, f"{t:.2f}s")
    check("扫描成功且产出分组", isinstance(r, list) and len(r) > 0, str(type(r)))
finally:
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

print()
print("=" * 74)
print(f"结果：PASS {PASS}   FAIL {FAIL}")
print("=" * 74)
sys.exit(1 if FAIL else 0)
