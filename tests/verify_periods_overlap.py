# -*- coding: utf-8 -*-
"""时段切分：重叠、相邻、间隙、跨午夜、零长、越界。

为什么这条重要
--------------
`compute_segmented_spaces` 是「逐段积分再相加」。如果两段在时间上重叠，
重叠区间会被算两次 —— 充电空间虚高，容量反推也就偏大。

原实现只合并「相邻」段（abs(start-end) < 0.01），重叠段各算各的。
实测 Mode A + 容量线 200、负荷恒 100：
    0-8 充            → 充电空间  800
    0-8 充 + 6-12 充  → 充电空间 1400   ← 6-8 这两小时算了两遍

界面保存时段时有 `_valid_periods` 拦，但 `api/routes_calc` 与程序内构造的
时段不走那条路，所以校验必须落在 `_segment_periods` 里。

修法：
- 同方向重叠 → 合并（语义上就是「这些时间都算充电」）
- 不同方向重叠 → 抛 ValueError（同一时刻既充又放是矛盾配置）

跑法：python tests/verify_periods_overlap.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np  # noqa: E402

from core.periods import _segment_periods  # noqa: E402
from core.spaces import compute_segmented_spaces  # noqa: E402
from core.timegrid import _valid_periods  # noqa: E402

PASS = FAIL = 0
TIMES = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 15, 30, 45)]


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}  {detail}")


def spans(periods):
    return [(s["type"], round(s["start"], 2), round(s["end"], 2))
            for s in _segment_periods(periods)]


def charge_space(load_value, periods, **kw):
    load = np.full((1, 96), float(load_value))
    segs = _segment_periods(periods)
    c, d, _, _ = compute_segmented_spaces(load, TIMES, segs, **kw)
    return (sum(float(np.nansum(x)) for x in c),
            sum(float(np.nansum(x)) for x in d))


# ══════════════════════════════════════════════════════════
print("=" * 74)
print("① 基本结构")
print("=" * 74)

s = spans([{"start": 0.0, "end": 8.0, "type": "charge"},
           {"start": 10.0, "end": 12.0, "type": "discharge"},
           {"start": 14.0, "end": 16.0, "type": "charge"},
           {"start": 18.0, "end": 21.0, "type": "discharge"}])
check("两充两放 → 4 段", len(s) == 4, str(s))
check("标签按方向独立编号",
      [x[0] for x in s] == ["charge", "discharge", "charge", "discharge"], str(s))

s = spans([{"start": 0.0, "end": 4.0, "type": "charge"},
           {"start": 4.0, "end": 8.0, "type": "charge"},
           {"start": 10.0, "end": 12.0, "type": "discharge"}])
check("相邻同类型 0-4+4-8 合并成一段",
      len(s) == 2 and s[0][2] == 8.0, str(s))

s = spans([{"start": 0.0, "end": 4.0, "type": "charge"},
           {"start": 5.0, "end": 8.0, "type": "charge"},
           {"start": 10.0, "end": 12.0, "type": "discharge"}])
check("同类型但有间隙 → 保持两段（不越界合并）", len(s) == 3, str(s))

s = spans([{"start": 22.0, "end": 6.0, "type": "charge"},
           {"start": 10.0, "end": 12.0, "type": "discharge"}])
check("跨午夜 22-6 展开后合并回一段",
      len(s) == 2 and s[0][1] == 22.0 and s[0][2] == 6.0, str(s))

check("空输入 → 0 段", spans([]) == [])

# ══════════════════════════════════════════════════════════
print()
print("=" * 74)
print("② 同方向重叠 → 合并（本轮修复的核心）")
print("=" * 74)

s = spans([{"start": 0.0, "end": 8.0, "type": "charge"},
           {"start": 6.0, "end": 12.0, "type": "charge"}])
check("0-8 充 + 6-12 充 → 合并成 0-12 一段",
      len(s) == 1 and s[0] == ("charge", 0.0, 12.0), str(s))

s = spans([{"start": 0.0, "end": 8.0, "type": "charge"},
           {"start": 2.0, "end": 4.0, "type": "charge"},
           {"start": 10.0, "end": 12.0, "type": "discharge"}])
check("完全被包含的段 2-4 不改变外段",
      len(s) == 2 and s[0] == ("charge", 0.0, 8.0), str(s))

s = spans([{"start": 0.0, "end": 4.0, "type": "charge"},
           {"start": 2.0, "end": 10.0, "type": "charge"},
           {"start": 12.0, "end": 14.0, "type": "discharge"}])
check("重叠延伸 0-4 + 2-10 → 合并成 0-10（end 取较大值）",
      len(s) == 2 and s[0] == ("charge", 0.0, 10.0), str(s))

# ══════════════════════════════════════════════════════════
print()
print("=" * 74)
print("③ 不同方向重叠 → 必须报错")
print("=" * 74)

for label, periods in [
    ("0-8 充 + 6-12 放", [{"start": 0.0, "end": 8.0, "type": "charge"},
                          {"start": 6.0, "end": 12.0, "type": "discharge"}]),
    ("2-6 放 被 0-8 充 包住", [{"start": 0.0, "end": 8.0, "type": "charge"},
                               {"start": 2.0, "end": 6.0, "type": "discharge"}]),
]:
    try:
        _segment_periods(periods)
        check(f"{label} → 抛 ValueError", False, "没有抛异常")
    except ValueError as e:
        check(f"{label} → 抛 ValueError", True)
        print(f"       消息: {e}")
    except Exception as e:
        check(f"{label} → 抛 ValueError", False, f"抛的是 {type(e).__name__}")

# ══════════════════════════════════════════════════════════
print()
print("=" * 74)
print("④ 重叠不再导致重复计算（数值验证）")
print("=" * 74)

c_base, d_base = charge_space(100, [
    {"start": 0.0, "end": 8.0, "type": "charge"},
    {"start": 10.0, "end": 12.0, "type": "discharge"},
], mode="A", billing={"value": 200})
check(f"基准：0-8 充 → 充电空间 800（实 {c_base:.0f}）", abs(c_base - 800) < 1e-6)

c_ovl, d_ovl = charge_space(100, [
    {"start": 0.0, "end": 8.0, "type": "charge"},
    {"start": 6.0, "end": 12.0, "type": "charge"},
    {"start": 14.0, "end": 16.0, "type": "discharge"},
], mode="A", billing={"value": 200})
# 合并成 0-12 = 12h × 100 = 1200。若重复计算会是 1400。
check(f"重叠：合并成 0-12 → 充电空间 1200（实 {c_ovl:.0f}）",
      abs(c_ovl - 1200) < 1e-6, "1400 就说明还在重复计算")

c_b, _ = charge_space(-100, [
    {"start": 0.0, "end": 8.0, "type": "charge"},
    {"start": 6.0, "end": 12.0, "type": "charge"},
], mode="B", rated_power=300)
# v2.3.16: 空间不乘效率；12h × min(300, 100) = 1200
check(f"Mode B 重叠也合并（实 {c_b:.0f}，应 1200）", abs(c_b - 1200) < 1.0)

# ══════════════════════════════════════════════════════════
print()
print("=" * 74)
print("⑤ Mode B 的语义确认（避免把正常结果当 bug）")
print("=" * 74)

c_pos, d_pos = charge_space(100, [
    {"start": 0.0, "end": 8.0, "type": "charge"},
    {"start": 10.0, "end": 12.0, "type": "discharge"},
], mode="B", rated_power=500)
check("Mode B：负荷全正 → 充电空间 0（充电来自倒送）", abs(c_pos) < 1e-9, str(c_pos))
check("Mode B：负荷全正 → 放电空间 > 0", d_pos > 0, str(d_pos))

c_neg, d_neg = charge_space(-100, [
    {"start": 0.0, "end": 8.0, "type": "charge"},
    {"start": 10.0, "end": 12.0, "type": "discharge"},
], mode="B", rated_power=500)
check("Mode B：负荷全负 → 放电空间 0", abs(d_neg) < 1e-9, str(d_neg))
check("Mode B：负荷全负 → 充电空间 > 0", c_neg > 0, str(c_neg))

# ══════════════════════════════════════════════════════════
print()
print("=" * 74)
print("⑥ _valid_periods 仍然独立工作（界面层用）")
print("=" * 74)

ok, msg, _ = _valid_periods([{"start": 0.0, "end": 8.0, "type": "charge"},
                             {"start": 6.0, "end": 12.0, "type": "charge"}])
check("重叠时段被 _valid_periods 拦住", ok is False and "重叠" in msg, msg[:50])

ok, msg, _ = _valid_periods([{"start": 8.0, "end": 8.0, "type": "charge"}])
check("零长时段被拦住", ok is False, msg[:50])

ok, msg, _ = _valid_periods([{"start": 0.0, "end": 25.0, "type": "charge"}])
check("越界时段被拦住", ok is False, msg[:50])

ok, _, _ = _valid_periods([{"start": 22.0, "end": 6.0, "type": "charge"},
                           {"start": 10.0, "end": 12.0, "type": "discharge"}])
check("跨午夜合法配置能通过", ok is True)

print()
print("=" * 74)
print(f"结果：PASS {PASS}   FAIL {FAIL}")
print("=" * 74)
sys.exit(1 if FAIL else 0)
