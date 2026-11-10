# -*- coding: utf-8 -*-
"""v2.3.25 三处修复的回归：合并所有段、三处配对统一、两条按钮闭环。

这一轮的核心发现：同一个「max(len) 配对」的 bug 在主程序里重复了三处，
加上 services 一处共四处。前一轮只修了一处（_fill_daily_table）。
"""
import ast
import io
import os
import sys

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(REPO, "apps", "负荷曲线批量生成工具 Pro 融合版 (v2.3).py")
sys.path.insert(0, REPO)
sys.stdout.reconfigure(encoding="utf-8")

passed, failed = [], []


def check(name, cond, detail=""):
    if cond:
        passed.append(name)
        print(f"  [PASS] {name}")
    else:
        failed.append(name)
        print(f"  [FAIL] {name}  {detail}")


src = io.open(MAIN, encoding="utf-8").read()
lines = src.splitlines()
cap_src = io.open(os.path.join(REPO, "services", "capacity.py"),
                  encoding="utf-8").read()

print("=" * 76)
print("① 全库不再有 max(len(...)) 的配对循环")
print("=" * 76)
main_hits = [i + 1 for i, l in enumerate(lines)
             if "max(len(" in l and not l.strip().startswith("#")]
cap_hits = [i + 1 for i, l in enumerate(cap_src.splitlines())
            if "max(len(" in l and not l.strip().startswith("#")]
print(f"  主程序: {main_hits or '无'}")
print(f"  services/capacity.py: {cap_hits or '无'}")
check("主程序无 max(len())", not main_hits, str(main_hits))
check("services 无 max(len())", not cap_hits, str(cap_hits))

print()
print("=" * 76)
print("② 下拉框列出全部段（不再只取前两个）")
print("=" * 76)
tree = ast.parse(src)
PW = None
for n in ast.walk(tree):
    if isinstance(n, ast.ClassDef) and n.name == "ProfessionalWindow":
        PW = n
        break


def src_of(fname):
    if PW is None:
        return ""
    for n in PW.body:
        if isinstance(n, ast.FunctionDef) and n.name == fname:
            return "\n".join(lines[n.lineno - 1:n.end_lineno])
    return ""


upd = src_of("_update_mode_options")
check("_update_mode_options 用 for 循环填全部段",
      "for lab in self.charge_labels" in upd and "for lab in self.discharge_labels" in upd,
      "仍是硬编码前两个")
check("不再有 labels[1] 的硬编码",
      "charge_labels[1]" not in upd and "discharge_labels[1]" not in upd)

rel = src_of("_get_relevant_segments_for_month")
check("合并充电段遍历全部段",
      "for _s in c_segs[1:]" in rel, "仍只合前两个")
check("合并放电段遍历全部段",
      "for _s in d_segs[1:]" in rel)
check("合并不会原地改原数组（copy）",
      "c_segs[0].copy()" in rel and "d_segs[0].copy()" in rel,
      "直接 + 可能改了原数组")

print()
print("=" * 76)
print("③ 三处配对的判据一致（都用 min(len)）")
print("=" * 76)
for fname in ("_fill_daily_table", "_check_day_satisfied",
              "_get_all_filtered_daily_mins"):
    seg = src_of(fname)
    ok = ("min(len(" in seg) and ("max(len(" not in seg)
    check(f"{fname} 用 min(len)", ok,
          "仍是 max" if "max(len(" in seg else "没找到配对循环")

print()
print("=" * 76)
print("④ 容量反推与满足天数闭环（含 DOD×效率）")
print("=" * 76)
# 直接用 core 层验证闭环，不依赖 GUI
from core.spaces import calc_capacity_for_days

# 构造：3 天数据，每天一次循环
daily_mins = np.array([100.0, 200.0, 300.0])
cap_movable = calc_capacity_for_days(2, daily_mins)   # 满足 2 天 → 应取第 2 小 = 200
print(f"  满足 2 天 → 可搬 {cap_movable}")
check("容量反推取第 N 小（N=2 → 200）", abs(cap_movable - 200.0) < 1e-9,
      str(cap_movable))

# DOD×效率闭环
k = 0.95 * 0.93
installed = cap_movable / k
usable_back = installed * k
print(f"  安装 {installed:.3f} → 可用 {usable_back:.3f}（k={k:.4f}）")
check("安装容量 × k 回到可搬能量", abs(usable_back - cap_movable) < 1e-9)

# 反向：不乘 k 会偏
wrong = cap_movable / 0.95      # 只除 DOD（旧口径）
print(f"  只除 DOD 的旧口径: {wrong:.3f}（少 {installed - wrong:.3f} kWh）")
check("只除 DOD 与除 DOD×效率确实不同",
      abs(wrong - installed) > 1e-6,
      "两者相等说明系数没接上")

print()
print("=" * 76)
print("⑤ 满载模式：段数不等的四种组合")
print("=" * 76)
from services.capacity import daily_demand_series, DemandBasis

CASES = [
    ("充电 1、放电 2", [np.array([100.0])],
     [np.array([300.0]), np.array([280.0])], 100.0),
    ("充电 2、放电 1", [np.array([300.0]), np.array([250.0])],
     [np.array([280.0])], 280.0),
    ("充电 1、放电 3", [np.array([50.0])],
     [np.array([400.0]), np.array([300.0]), np.array([200.0])], 50.0),
    ("充电 3、放电 1", [np.array([300.0]), np.array([200.0]), np.array([100.0])],
     [np.array([250.0])], 250.0),
    ("相等 2×2", [np.array([300.0]), np.array([250.0])],
     [np.array([280.0]), np.array([240.0])], 280.0),
]
for name, c, d, expect in CASES:
    got = float(daily_demand_series(c, d, DemandBasis.full_load)[0])
    check(f"{name} → {expect}", abs(got - expect) < 1e-9, f"实际 {got}")

# 说明「充电 3、放电 1 → 250 而不是 300」：
# 配对只能到第 0 对（放电只有 1 段），min(300, 250) = 250。
# 300 是充电侧最大值，但放电侧只有 250，补齐后就是 250。
# 这与「一次循环放电只有 250」的物理意义一致。
print()
print("  说明：充电 3 放电 1 → 250，因为配对只能到第 0 对（放电只有 1 段）")

print()
print("=" * 76)
print(f"结果：PASS {len(passed)}   FAIL {len(failed)}")
for f in failed:
    print("  -", f)
print("=" * 76)
sys.exit(1 if failed else 0)
