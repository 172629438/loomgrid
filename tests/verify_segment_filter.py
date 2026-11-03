# -*- coding: utf-8 -*-
"""占位零过滤验证：段集合进入计算前是否已剔除占位零。

背景
----
compute_segmented_spaces 返回的四个平行列表里，每个段在**非本方向**那一侧是
占位零（充电段在放电侧 0，放电段在充电侧 0，目的是让四个列表等长）。
下游取 min / 取 max(min(配对)) 时，占位零会把结果直接压成 0：
two_two（默认模式）全段参与，于是日需求恒为 0、满足天数恒为 0。

修复点在 _get_relevant_segments_for_month 返回之前——那是段集合进入所有计算前
的唯一收敛点（_get_all_filtered_daily_mins / _check_day_satisfied /
_calc_days_from_capacity 均经此）。

覆盖
----
1 two_two 过滤后只剩真实段
2 两种口径（min_feasible / full_load）都不再为 0
3 one_one 的值不被过滤破坏
4 选错方向（下拉框里选到反方向的段名）时被过滤为空而不是留下占位零

需要 Qt（ProfessionalWindow 是 UI 类），用 offscreen。
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import importlib.util  # noqa: E402

import numpy as np  # noqa: E402
from PySide6.QtWidgets import QApplication, QComboBox  # noqa: E402

from services.capacity import DemandBasis, daily_demand_series  # noqa: E402

FUSION = os.path.join(ROOT, "负荷曲线批量生成工具 Pro 融合版 (v2.3).py")
spec = importlib.util.spec_from_file_location("lg_segfilter", FUSION)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

VERBOSE = "-v" in sys.argv
PASS = FAIL = 0
FAILS = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        if VERBOSE:
            print(f"[PASS] {name}")
    else:
        FAIL += 1
        FAILS.append((name, detail))
        print(f"[FAIL] {name}  {detail}")


app = QApplication.instance() or QApplication([])

N = 96
LABELS = [f"{(i * 15) // 60:02d}:{(i * 15) % 60:02d}" for i in range(N)]
SEGS = [
    {"start": 0, "end": 8, "label": "充电1", "type": mod.PeriodType.CHARGE},
    {"start": 8, "end": 11, "label": "放电1", "type": mod.PeriodType.DISCHARGE},
    {"start": 11, "end": 14, "label": "充电2", "type": mod.PeriodType.CHARGE},
    {"start": 14, "end": 20, "label": "放电2", "type": mod.PeriodType.DISCHARGE},
]
load = np.full((3, N), 300.0)
CH, DIS, CL, DL = mod.compute_segmented_spaces(load, LABELS, SEGS,
                                               billing={"value": 500.0}, mode="A")


def make_window(mode, charge_text=None, discharge_text=None):
    """绕过 __init__ 只绑定该方法需要的属性，避免建整套 UI。"""
    w = mod.ProfessionalWindow.__new__(mod.ProfessionalWindow)
    w.capacity_mode = mode
    w.charge_segments_dict = {"2026-01": CH}
    w.discharge_segments_dict = {"2026-01": DIS}
    w.charge_labels = list(CL)
    w.discharge_labels = list(DL)
    w.charge_option_combo = QComboBox()
    w.charge_option_combo.addItems(list(CL))
    if charge_text:
        w.charge_option_combo.setCurrentText(charge_text)
    w.discharge_option_combo = QComboBox()
    w.discharge_option_combo.addItems(list(DL))
    if discharge_text:
        w.discharge_option_combo.setCurrentText(discharge_text)
    return w


# ── 0. 先确认原始数据确实含占位零 ──
zero_in_raw = (any(float(np.sum(a)) == 0 for a in CH) and
               any(float(np.sum(a)) == 0 for a in DIS))
check("0) 原始数据含占位零（前提成立）", zero_in_raw,
      f"c={[round(float(np.sum(a))) for a in CH]} d={[round(float(np.sum(a))) for a in DIS]}")

# ── 1. two_two 过滤 ──
w = make_window("two_two")
c2, d2 = w._get_relevant_segments_for_month("2026-01")
check("1) two_two 过滤后充电段数 = 2", len(c2) == 2, f"{len(c2)}")
check("2) two_two 过滤后放电段数 = 2", len(d2) == 2, f"{len(d2)}")
check("3) 过滤后无全零段",
      all(float(np.sum(a)) != 0 for a in c2) and all(float(np.sum(a)) != 0 for a in d2))

# ── 2. 两种口径不再为 0 ──
minf = daily_demand_series(c2, d2, DemandBasis.min_feasible)
full = daily_demand_series(c2, d2, DemandBasis.full_load)
check("4) 修复后 min_feasible 非零", float(np.max(minf)) > 0, f"max={float(np.max(minf)):.2f}")
check("5) 修复后 full_load 非零", float(np.max(full)) > 0, f"max={float(np.max(full)):.2f}")

# ── 3. one_one 不被破坏 ──
w2 = make_window("one_one", charge_text="充电1", discharge_text="放电1")
c1, d1 = w2._get_relevant_segments_for_month("2026-01")
check("6) one_one 选充1放1 各返回 1 段", len(c1) == 1 and len(d1) == 1,
      f"{len(c1)}/{len(d1)}")
check("7) one_one 的值未被过滤破坏",
      abs(float(np.sum(c1[0])) - float(np.sum(CH[0]))) < 1e-9,
      f"{float(np.sum(c1[0])):.1f} vs {float(np.sum(CH[0])):.1f}")

# ── 4. 选错方向的边界 ──
w3 = make_window("one_one", charge_text="放电1", discharge_text="充电1")
c3, d3 = w3._get_relevant_segments_for_month("2026-01")
check("8) 选错方向时过滤为空，不留占位零", len(c3) == 0 and len(d3) == 0,
      f"{len(c3)}/{len(d3)}")

print()
if FAILS:
    print("失败项：")
    for n, d in FAILS:
        print(f"  - {n}  {d}")
print(f"结果：PASS {PASS}   FAIL {FAIL}")
sys.exit(1 if FAIL else 0)
