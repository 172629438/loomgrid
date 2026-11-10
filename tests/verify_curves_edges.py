# -*- coding: utf-8 -*-
"""出图边界与图例内容验证。

这一轮在 plotting/curves.py 挖到三处：
  ① 移动平均两端零填充（已在 verify_smoothing_edges.py 覆盖，这里不重复）
  ② 图例把 PeriodType 成员直接当 label，界面显示英文类名
  ③ time_labels 长度与数据点数不一致时 set_xticklabels 崩
  ④ 无条件列「充电」「放电」两个图例项，只有一侧时列了不存在的东西
"""
import io
import os
import re
import sys

import numpy as np

REPO = r"C:\Users\LENOVO\.hanako\repos\172629438__loomgrid"
MPL = r"C:\Users\LENOVO\.hanako\_tmp\data\mpl2326"
os.makedirs(MPL, exist_ok=True)
os.environ["MPLCONFIGDIR"] = MPL
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, REPO)
sys.stdout.reconfigure(encoding="utf-8")

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from core.models import PeriodType
from plotting.curves import _draw_curves_core

ok, bad = [], []


def check(name, cond, detail=""):
    if cond:
        ok.append(name)
        print(f"  [PASS] {name}")
    else:
        bad.append(name)
        print(f"  [FAIL] {name}   {detail}")


def draw(load, opts, labels=None, periods=None):
    n = load.shape[1]
    if labels is None:
        labels = [f"{i//4:02d}:{(i%4)*15:02d}" for i in range(n)]
    fig, ax = plt.subplots(figsize=(10, 5))
    try:
        _draw_curves_core(ax, ["2026-01-01"] * load.shape[0], load, labels,
                          "测试", opts, periods=periods)
        return fig, ax, None
    except Exception as e:
        return fig, ax, e


BASE = {"show_daily": True, "show_avg": False, "highlight_mode": "none"}

print("=" * 78)
print("① 图例文案不能出现英文类名")
print("=" * 78)
src = io.open(os.path.join(REPO, "plotting", "curves.py"), encoding="utf-8").read()
# 只看代码行，排除注释（注释里会引用旧写法作为说明）
code_only = "\n".join(l for l in src.splitlines()
                       if not l.strip().startswith("#"))
# 正则要注意回溯：\w+ 可以退回一个字符让 (?!\.value) 通过，得加 \b 锁住结尾
bad_label = re.findall(r"label\s*=\s*PeriodType\.\w+\b(?!\.value)", code_only)
print(f"  源码里 label=PeriodType.X（缺 .value）: {len(bad_label)} 处")
check("源码不再有 label=PeriodType.X 的裸写法", not bad_label, str(bad_label))

per_both = [{"start": 0.0, "end": 6.0, "type": "charge"},
            {"start": 8.0, "end": 12.0, "type": "discharge"}]
fig, ax, err = draw(np.full((1, 96), 500.0), BASE, periods=per_both)
if err:
    check("充放都有时能画", False, str(err))
else:
    labels = [t.get_text() for t in ax.get_legend().get_texts()]
    print(f"  图例: {labels}")
    check("图例里是「充电(低谷)」而非 PeriodType.X",
          any("充电(低谷)" == l for l in labels), str(labels))
    check("图例里没有 PeriodType 字样",
          not any("PeriodType" in l for l in labels), str(labels))
plt.close(fig)

print()
print("=" * 78)
print("② 只有一侧时段时，图例只列那一侧")
print("=" * 78)
per_d_only = [{"start": 8.0, "end": 12.0, "type": "discharge"}]
fig, ax, err = draw(np.full((1, 96), 500.0), BASE, periods=per_d_only)
labels = [t.get_text() for t in ax.get_legend().get_texts()]
print(f"  只有放电段 → 图例: {labels}")
check("只有放电时不列「充电」", not any("充电" in l for l in labels), str(labels))
check("只有放电时列了「放电」", any("放电" in l for l in labels), str(labels))
plt.close(fig)

per_c_only = [{"start": 0.0, "end": 6.0, "type": "charge"}]
fig, ax, err = draw(np.full((1, 96), 500.0), BASE, periods=per_c_only)
labels = [t.get_text() for t in ax.get_legend().get_texts()]
print(f"  只有充电段 → 图例: {labels}")
check("只有充电时不列「放电」", not any("放电" in l for l in labels), str(labels))
check("只有充电时列了「充电」", any("充电" in l for l in labels), str(labels))
plt.close(fig)

print()
print("=" * 78)
print("③ time_labels 与点数不一致时不崩")
print("=" * 78)
# 反向证明：先看旧写法会怎样
try:
    fig_t = plt.subplots()[0]
    ax_t = fig_t.add_subplot(111)
    ax_t.set_xticks(range(2))
    ax_t.set_xticklabels([f"t{i}" for i in range(96)])
    plt.close(fig_t)
    old_crashed = False
except ValueError as e:
    old_crashed = True
    print(f"  旧写法确实崩: {str(e)[:70]}")
check("旧写法确实崩（bug 真实存在）", old_crashed)

for n_pts, n_labels, desc in ((2, 96, "2 点 + 96 标签"),
                              (2, 2, "2 点 + 2 标签"),
                              (96, 96, "96 点 + 96 标签"),
                              (24, 24, "24 点 + 24 标签"),
                              (5, 0, "5 点 + 0 标签")):
    load = np.full((1, n_pts), 500.0)
    labels = [f"t{i}" for i in range(n_labels)] if n_labels else []
    fig, ax, err = draw(load, BASE, labels=labels)
    if err:
        print(f"  {desc:16s} → 崩: {type(err).__name__}")
        check(f"{desc} 不崩", False, str(err)[:60])
    else:
        n_xt = len(ax.get_xticklabels())
        print(f"  {desc:16s} → OK，刻度 {n_xt} 个")
        check(f"{desc} 不崩", True)
    plt.close(fig)

print()
print("=" * 78)
print("④ 全 NaN 不刷屏")
print("=" * 78)
import warnings
with warnings.catch_warnings(record=True) as w:
    warnings.simplefilter("always")
    fig, ax, err = draw(np.full((2, 96), np.nan),
                        {"show_daily": True, "show_avg": True,
                         "show_max_demand": True, "mark_anomalies": True,
                         "highlight_mode": "none"})
    msgs = [str(x.message) for x in w]
print(f"  警告 {len(msgs)} 条: {[m[:40] for m in msgs]}")
check("全 NaN 不抛异常", err is None, str(err))
check("警告 ≤ 4 条", len(msgs) <= 4, f"{len(msgs)} 条")
plt.close(fig)

print()
print("=" * 78)
print("⑤ 1 点数据必须明确报错（不是静默画一条线）")
print("=" * 78)
fig, ax, err = draw(np.array([[100.0]]), BASE)
print(f"  结果: {type(err).__name__ if err else '没报错'}  {err}")
check("1 点抛 ValueError", isinstance(err, ValueError), str(err))
plt.close(fig)

print()
print("=" * 78)
print(f"结果：PASS {len(ok)}   FAIL {len(bad)}")
for b in bad:
    print("  -", b)
print("=" * 78)
sys.exit(1 if bad else 0)
