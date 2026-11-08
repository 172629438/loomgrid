# -*- coding: utf-8 -*-
"""绘图内容：轴范围、峰谷配色、需量线、容量线、图例、周末底色。

为什么这条重要
--------------
`_plot_curves` 被多个测试调用，但此前只验证「文件生成且非空」——
图上有没有峰谷底色、容量线画没画、图例对不对，一个都没验。
那些恰好是用户每天在看的东西。

做法：不调 `_plot_curves`（它会 close fig），直接调 `_draw_curves_core(ax, ...)`，
然后检查 matplotlib 的 artist（lines / patches / texts / legend）。
纯数据断言，不需要人眼看图。

x 轴语义（容易搞错的地方）：
    x = np.arange(n_times)，即「一天内第几个采样点」，0..95。
    多天曲线**叠在同一张图**上对比形状，不是连续时间轴。
    实测 3 天 96 点 → xlim ≈ (-4.8, 99.8)，3 条日曲线 + 1 条平均线。

跑法：python tests/verify_plot_content.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
sys.stdout.reconfigure(encoding="utf-8")

import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from plotting.curves import _draw_curves_core, _get_cached_font  # noqa: E402

plt.rcParams["font.sans-serif"] = [_get_cached_font()]
plt.rcParams["axes.unicode_minus"] = False

PASS = FAIL = 0
TIMES = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 15, 30, 45)]
DATES = ["2026-07-03", "2026-07-04", "2026-07-05"]     # 周五 / 周六 / 周日
LOAD = np.array([[100 + p * 0.5 for p in range(96)] for _ in range(3)], dtype=float)
BASE = {
    "show_daily": True, "show_avg": True, "fit_types": [],
    "show_max_demand": False, "highlight_mode": "none",
    "mark_anomalies": False, "dark_theme": False,
}


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}  {detail}")


def draw(load=None, **kw):
    opts = dict(BASE)
    bill = kw.pop("bill", None)
    periods = kw.pop("periods", None)
    opts.update(kw)
    fig, ax = plt.subplots(figsize=(16, 8))
    _draw_curves_core(ax, DATES, LOAD if load is None else load, TIMES,
                      "测试标题", opts, bill, periods)
    return fig, ax


def hlines(ax):
    """所有水平线（y 值相同的 line）"""
    out = []
    for ln in ax.lines:
        y = ln.get_ydata()
        if len(y) >= 2 and abs(float(y[0]) - float(y[-1])) < 1e-9:
            out.append((float(y[0]), ln.get_label(), ln.get_color()))
    return out


# ══════════════════════════════════════════════════════════
print("=" * 74)
print("① 基础结构：x 轴是天内 0..95，多天曲线叠加")
print("=" * 74)
fig, ax0 = draw()
n_base_lines = len(ax0.lines)
n_base_patches = len(ax0.patches)
xl = tuple(float(v) for v in ax0.get_xlim())
yl = tuple(float(v) for v in ax0.get_ylim())
check(f"3 条日曲线 + 1 平均线 = 4（实 {n_base_lines}）", n_base_lines == 4, str(n_base_lines))
check(f"x 轴覆盖一天内 0..95（实 {xl[0]:.1f}~{xl[1]:.1f}）",
      xl[0] < 5 and 95 <= xl[1] <= 105, str(xl))
check(f"y 轴包住负荷 {LOAD.min():.0f}~{LOAD.max():.1f}",
      yl[0] <= LOAD.min() and yl[1] >= LOAD.max(), str(yl))
check("基础图无底色 patch", n_base_patches == 0, str(n_base_patches))
check("有图例", ax0.get_legend() is not None)
plt.close(fig0 := fig)

# ══════════════════════════════════════════════════════════
print()
print("=" * 74)
print("② 峰谷配色：2 段 → 天内 2 个 axvspan")
print("=" * 74)
PERIODS = [
    {"start": 0.0, "end": 8.0, "type": "charge", "label": "谷段"},
    {"start": 10.0, "end": 12.0, "type": "discharge", "label": "峰段"},
]
fig, ax = draw(periods=PERIODS)
check(f"patches {n_base_patches} → {len(ax.patches)}（预期 +2，天内不按天展开）",
      len(ax.patches) == n_base_patches + 2, str(len(ax.patches)))
if len(ax.patches) >= 2:
    spans = sorted(ax.patches, key=lambda p: p.get_x())
    p1, p2 = spans[0], spans[1]
    check(f"第一段 x 范围 {p1.get_x():.0f}~{p1.get_x()+p1.get_width():.0f}"
          "（0-8 小时 = 0~32 点）",
          abs(p1.get_x() - 0) < 1e-6 and abs(p1.get_width() - 32) < 1e-6,
          f"{p1.get_x()} 宽 {p1.get_width()}")
    check(f"第二段 x 范围 {p2.get_x():.0f}~{p2.get_x()+p2.get_width():.0f}"
          "（10-12 小时 = 40~48 点）",
          abs(p2.get_x() - 40) < 1e-6 and abs(p2.get_width() - 8) < 1e-6,
          f"{p2.get_x()} 宽 {p2.get_width()}")
    c1 = p1.get_facecolor()[:3]
    c2 = p2.get_facecolor()[:3]
    check("充电段与放电段颜色不同", c1 != c2, f"{c1} vs {c2}")
plt.close(fig)

# ══════════════════════════════════════════════════════════
print()
print("=" * 74)
print("③ 需量线画的是实际最大值；容量线只在 type='capacity' 时画")
print("=" * 74)
fig, ax = draw(show_max_demand=True)
hs = hlines(ax)
check(f"show_max_demand 多出一条水平线（{len(hs)}）", len(hs) == 1, str(hs))
if hs:
    check(f"数值 = 数据最大值 {LOAD.max():.2f}（实 {hs[0][0]:.2f}）",
          abs(hs[0][0] - float(LOAD.max())) < 1e-6, str(hs[0][0]))
    check("标签写明是最大需量", "最大需量" in hs[0][1], hs[0][1])
plt.close(fig)

fig, ax = draw(bill={"type": "capacity", "value": 150})
hs = hlines(ax)
check(f"type='capacity' → 画出容量线（{len(hs)}）", len(hs) == 1, str(hs))
if hs:
    check(f"数值 = 配置值 150（实 {hs[0][0]:.2f}）", abs(hs[0][0] - 150) < 1e-6, str(hs[0][0]))
    check("标签写明是容量线", "容量线" in hs[0][1], hs[0][1])
plt.close(fig)

fig, ax = draw(bill={"value": 150})          # 漏传 type
check(f"漏传 type → 不画容量线（{len(hlines(ax))} 条水平线）",
      len(hlines(ax)) == 0, str(hlines(ax)))
plt.close(fig)

# ══════════════════════════════════════════════════════════
print()
print("=" * 74)
print("④ 周末底色需要 show_weekend_bg=True")
print("=" * 74)
fig, ax = draw(highlight_mode="weekend", show_weekend_bg=True)
check(f"开背景 → +1 个 patch（实 {len(ax.patches)}）",
      len(ax.patches) == n_base_patches + 1, str(len(ax.patches)))
plt.close(fig)

fig, ax = draw(highlight_mode="weekend", show_weekend_bg=False)
check(f"关背景 → 不加 patch（实 {len(ax.patches)}）",
      len(ax.patches) == n_base_patches, str(len(ax.patches)))
plt.close(fig)

# ══════════════════════════════════════════════════════════
print()
print("=" * 74)
print("⑤ 图例内容随模式变化")
print("=" * 74)
fig, ax = draw()
labels = [t.get_text() for t in ax.get_legend().get_texts()]
check("基础图例含 3 个日期 + 平均负荷", len(labels) == 4, str(labels))
check("含「平均负荷」", any("平均" in l for l in labels), str(labels))
plt.close(fig)

fig, ax = draw(highlight_mode="weekend", show_weekend_bg=True)
labels = [t.get_text() for t in ax.get_legend().get_texts()]
check("周末模式图例含「周末背景」", any("周末背景" in l for l in labels), str(labels))
plt.close(fig)

fig, ax = draw(mark_anomalies=True)
labels = [t.get_text() for t in ax.get_legend().get_texts()]
# 这批数据没有 0 / 负值 → 没有异常点 → 图例不加「异常值」。这是对的。
check("无异常点时图例不加「异常值」", not any("异常值" in l for l in labels), str(labels))
plt.close(fig)

# 有异常点时应该加
bad = LOAD.copy()
bad[0, 10] = 0.0
bad[0, 20] = -50.0
fig, ax = draw(load=bad, mark_anomalies=True)
labels = [t.get_text() for t in ax.get_legend().get_texts()]
check("有异常点时图例含「异常值」", any("异常值" in l for l in labels), str(labels))
plt.close(fig)

# ══════════════════════════════════════════════════════════
print()
print("=" * 74)
print("⑥ 深色主题")
print("=" * 74)
fig, ax = draw(dark_theme=True)
face = ax.get_facecolor()[:3]
check(f"ax 背景变深（{face}）", sum(face) / 3 < 0.4, str(face))
plt.close(fig)

# ══════════════════════════════════════════════════════════
print()
print("=" * 74)
print("⑦ 边界")
print("=" * 74)
fig, ax = plt.subplots()
try:
    _draw_curves_core(ax, ["2026-07-03"], np.array([[100.0]]), ["00:00"], "1点", dict(BASE))
    check("只有 1 个时间点 → 抛 ValueError", False, "没抛")
except ValueError as e:
    check("只有 1 个时间点 → 抛 ValueError", True)
    print(f"       消息: {e}")
except Exception as e:
    check("只有 1 个时间点 → 抛 ValueError", False, f"{type(e).__name__}: {e}")
plt.close(fig)

fig, ax = plt.subplots()
_draw_curves_core(ax, ["2026-07-03"], np.array([[100.0, 110.0]]), ["00:00", "00:15"],
                  "1天2点", dict(BASE))
check(f"1 天 2 点能画（lines={len(ax.lines)}）", len(ax.lines) >= 1, str(len(ax.lines)))
plt.close(fig)

print()
print("=" * 74)
print(f"结果：PASS {PASS}   FAIL {FAIL}")
print("=" * 74)
sys.exit(1 if FAIL else 0)
