# -*- coding: utf-8 -*-
"""v2.3.17：空间预览的算术必须与表格值背靠背一致。

这张图存在的全部意义是「让用户用眼睛验算程序算得对不对」。
如果预览自己另写一套算法，它就会变成一个看着很专业、实际在骗人的东西——
面积画得好看，数字对不上，用户反而更困惑。

所以核心断言只有一条：**逐点曲线的积分 == compute_segmented_spaces 的返回值**。
两侧用同一个 segment_weights，不是两套实现。
"""
import sys

REPO = r"C:\Users\LENOVO\.hanako\repos\172629438__loomgrid"
if REPO not in sys.path:
    sys.path.insert(0, REPO)
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np  # noqa: E402

import core.spaces as S  # noqa: E402
from core.spaces import (  # noqa: E402
    compute_segmented_spaces, integrate_by_segments, segment_weights,
    space_curves_for_day,
)

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}" + (f"  {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"[FAIL] {name}  {detail}")


def close(a, b, tol=1e-9):
    return abs(float(a) - float(b)) <= tol * max(1.0, abs(float(b)))


TIMES = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 15, 30, 45)]
HOURS = np.array([h * 0.25 for h in range(96)])

print("=" * 78)
print("① segment_weights：与汇总函数共用同一套梯形权重")
print("=" * 78)
w0_8 = segment_weights(HOURS[0:33], 0.0, 8.0)
check(f"0-8 小时（33 点）Σdt = 8.0", close(w0_8.sum(), 8.0), f"{w0_8.sum():.9f}")
w10_12 = segment_weights(HOURS[40:49], 10.0, 12.0)
check(f"10-12 小时（9 点）Σdt = 2.0", close(w10_12.sum(), 2.0), f"{w10_12.sum():.9f}")
check("首点吃半个间隔 = 0.125", close(w0_8[0], 0.125), f"{w0_8[0]}")
check("末点吃半个间隔 = 0.125", close(w0_8[-1], 0.125), f"{w0_8[-1]}")
check("单点时段 Σdt = 时段全长", close(segment_weights(HOURS[0:1], 0.0, 8.0).sum(), 8.0))

print()
print("=" * 78)
print("② Mode A：逐点积分 == 汇总值（三种负荷形态）")
print("=" * 78)
SEGS2 = [{"start": 0.0, "end": 8.0, "type": "charge", "label": "谷"},
         {"start": 10.0, "end": 12.0, "type": "discharge", "label": "峰"}]

cases = {
    "恒负荷 100": np.full(96, 100.0),
    "负荷 100~147.5 递增": np.array([100 + i * 0.5 for i in range(96)]),
    "含负值（光伏返送）": np.array([-50.0 if i < 16 else 120.0 for i in range(96)]),
}
for label, row in cases.items():
    c, d, _, _ = compute_segmented_spaces(row.reshape(1, 96), TIMES, SEGS2,
                                          mode="A", billing={"value": 300.0})
    ch, dis, _pv = space_curves_for_day(row, HOURS, SEGS2, mode="A", cap=300.0)
    a_c = integrate_by_segments(ch, HOURS, SEGS2, "charge")
    a_d = integrate_by_segments(dis, HOURS, SEGS2, "discharge")
    t_c, t_d = float(c[0][0]), float(d[1][0])
    check(f"{label}：充电 图示={a_c:.6f} / 表格={t_c:.6f}", close(a_c, t_c))
    check(f"{label}：放电 图示={a_d:.6f} / 表格={t_d:.6f}", close(a_d, t_d))

print()
print("=" * 78)
print("③ Mode B：逐点积分 == 汇总值（含额定功率封顶）")
print("=" * 78)
neg = np.full(96, -120.0)
c, d, _, _ = compute_segmented_spaces(neg.reshape(1, 96), TIMES, SEGS2,
                                      mode="B", rated_power=300.0)
ch, dis, pv = space_curves_for_day(neg, HOURS, SEGS2, mode="B", rated_power=300.0)
a_c = integrate_by_segments(ch, HOURS, SEGS2, "charge")
a_d = integrate_by_segments(dis, HOURS, SEGS2, "discharge")
check(f"Mode B 充电：图示 {a_c:.6f} / 表格 {float(c[0][0]):.6f}",
      close(a_c, float(c[0][0])))
check(f"Mode B 放电：图示 {a_d:.6f} / 表格 {float(d[1][0]):.6f}",
      close(a_d, float(d[1][0])))
check("全负负荷下放电空间 = 0（没有正负荷可卸）", close(float(d[1][0]), 0.0))

print()
print("=" * 78)
print("④ 三个空间的语义")
print("=" * 78)
row = np.array([-50.0 if i < 16 else 120.0 for i in range(96)])
ch, dis, pv = space_curves_for_day(row, HOURS, SEGS2, mode="A", cap=300.0)
check("pv 只在负荷为负的时段非零", np.all(pv[0:16] == 50.0) and np.all(pv[16:] == 0.0),
      f"前段 {pv[0]:.1f} / 后段 {pv[-1]:.1f}")
_pv_seg = [{"start": 0.0, "end": 24.0, "type": "charge"}]
pv_area_keep = integrate_by_segments(
    space_curves_for_day(row, HOURS, SEGS2, mode="A", cap=300.0,
                         ignore_negative=True)[2], HOURS, _pv_seg, "charge")
pv_area_norm = integrate_by_segments(
    space_curves_for_day(row, HOURS, SEGS2, mode="A", cap=300.0,
                         ignore_negative=False)[2], HOURS, _pv_seg, "charge")
check("pv 用原始负荷，不受 ignore_negative 影响",
      close(pv_area_keep, pv_area_norm) and pv_area_norm > 0,
      f"ignore=True {pv_area_keep:.3f} / ignore=False {pv_area_norm:.3f}")

# Mode A 充电 = 容量线以下余量；容量线抬高，充电空间必须变大
ch_a, _, _ = space_curves_for_day(np.full(96, 100.0), HOURS, SEGS2, mode="A", cap=300.0)
ch_b, _, _ = space_curves_for_day(np.full(96, 100.0), HOURS, SEGS2, mode="A", cap=400.0)
ar_a = integrate_by_segments(ch_a, HOURS, SEGS2, "charge")
ar_b = integrate_by_segments(ch_b, HOURS, SEGS2, "charge")
check(f"容量线 300→400，充电空间 {ar_a:.0f}→{ar_b:.0f}（应变大）", ar_b > ar_a)

# 负荷超过容量线时，充电空间为 0（没有余量）
over = np.full(96, 500.0)
ch_o, dis_o, _ = space_curves_for_day(over, HOURS, SEGS2, mode="A", cap=300.0)
check("负荷 500 > 容量线 300 → 充电空间为 0",
      close(integrate_by_segments(ch_o, HOURS, SEGS2, "charge"), 0.0))

print()
print("=" * 78)
print("⑤ 跨午夜时段也能对上")
print("=" * 78)
SEGS_OVN = [{"start": 22.0, "end": 6.0, "type": "charge", "label": "跨夜谷"}]
c, _, _, _ = compute_segmented_spaces(np.full((1, 96), 50.0), TIMES, SEGS_OVN,
                                      mode="A", billing={"value": 300.0})
ch, _, _ = space_curves_for_day(np.full(96, 50.0), HOURS, SEGS_OVN,
                                mode="A", cap=300.0)
a = integrate_by_segments(ch, HOURS, SEGS_OVN, "charge")
check(f"跨午夜 22-6 充电：图示 {a:.4f} / 表格 {float(c[0][0]):.4f}",
      close(a, float(c[0][0])))
check("跨午夜段覆盖 8 小时 = 8.0", close(a / 250.0, 8.0), f"Σdt={a/250.0:.6f}")

print()
print("=" * 78)
print("⑥ 源码契约：预览不另写一套算法")
print("=" * 78)
main = open(REPO + r"\apps\负荷曲线批量生成工具 Pro 融合版 (v2.3).py",
            encoding="utf-8").read()
check("主程序导入 space_curves_for_day", "space_curves_for_day," in main)
check("主程序导入 integrate_by_segments", "integrate_by_segments," in main)
check("主程序不再自带 _space_curves_for_day 实现",
      "def _space_curves_for_day" not in main)
check("主程序不再自带 _integrate_by_segments 实现",
      "def _integrate_by_segments" not in main)
check("有空间预览 Tab", '"空间预览"' in main)
check("有 space_preview 帮助条目", '"space_preview": {' in main)
check("预览用 Agg 内存图画，不换后端",
      "fig.savefig(buf, format='png'" in main and "matplotlib.use('Agg')" in main)

print()
print("=" * 76)
print(f"结果：PASS {PASS}   FAIL {FAIL}")
print("=" * 76)
sys.exit(0 if FAIL == 0 else 1)
