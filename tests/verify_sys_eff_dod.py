# -*- coding: utf-8 -*-
"""v2.3.16：DOD 与系统效率是两个独立参数，综合系数 = 两者乘积。

业务口径（用户明确，2026-11-04）
--------------------------------
「专业模式下没有那个系统效率只有 DOD 是吧，DOD 只是储能柜的充放电深度，
  而要算系统整体效率就是要 DOD 和充放电效率的乘积。就比如说我们的 261 的
  DOD 是默认 95%，系统充放电效率是 93%，所以计算出整柜的整体效率（即 RTE）
  为 88% 左右。」

所以：
  · DOD = SOC 窗口深度，不是效率；
  · 系统效率 = AC-AC 往返效率（充 + 放，含 PCS / 线损 / 辅助能耗）；
  · 综合可用系数 k = DOD × 系统效率；
  · 261 柜：95% × 93% = 88.35%（行业口径的「整柜整体效率 88%」）。

两条链路必须用同一个 k，否则反推与判定不闭环：
  满足判定：可用容量 = 安装容量 × k
  容量反推：安装容量 = 可搬能量 ÷ k
"""
import os
import re
import sys

REPO = r"C:\Users\LENOVO\.hanako\repos\172629438__loomgrid"
if REPO not in sys.path:
    sys.path.insert(0, REPO)
sys.stdout.reconfigure(encoding="utf-8")

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


def k_of(dod_pct, eff_pct):
    """与主程序 _combined_factor() 同一个公式。"""
    return (dod_pct / 100.0) * (eff_pct / 100.0)


print("=" * 78)
print("① 261 柜实例：DOD 95% × 系统效率 93% = 88.35%")
print("=" * 78)
k261 = k_of(95, 93)
check(f"综合系数 = {k261:.4f}（{k261*100:.2f}%）", close(k261, 0.8835), f"{k261}")
check("与行业口径「整柜整体效率 88%」一致（四舍五入到整数）",
      round(k261 * 100) == 88, f"{k261*100:.2f}% → {round(k261*100)}%")

print()
print("=" * 78)
print("② DOD 不是效率：同 DOD 不同效率，结果必须不同")
print("=" * 78)
a = k_of(95, 93)
b = k_of(95, 88)
check(f"DOD 95% × 效率 93% = {a*100:.2f}% ≠ DOD 95% × 效率 88% = {b*100:.2f}%",
      not close(a, b), f"{a:.4f} vs {b:.4f}")
c = k_of(90, 93)
check(f"DOD 90% × 效率 93% = {c*100:.2f}% ≠ DOD 95% × 效率 93% = {a*100:.2f}%",
      not close(a, c), f"{c:.4f} vs {a:.4f}")

print()
print("=" * 78)
print("③ 两条链路闭环：反推得到安装容量，再判满足天数应等于目标天数")
print("=" * 78)


def installed_from_movable(movable_kwh, dod_pct, eff_pct):
    k = k_of(dod_pct, eff_pct)
    return movable_kwh / k if k > 0 else movable_kwh


def usable_from_installed(installed_kwh, dod_pct, eff_pct):
    return installed_kwh * k_of(dod_pct, eff_pct)


movable = 800.0
inst = installed_from_movable(movable, 95, 93)
back = usable_from_installed(inst, 95, 93)
check(f"可搬 800 kWh → 安装 {inst:.2f} kWh（800 ÷ 0.8835）",
      close(inst, 800.0 / 0.8835, 1e-6), f"{inst:.4f}")
check(f"安装 {inst:.2f} kWh → 可搬 {back:.2f} kWh（应回到 800）",
      close(back, movable, 1e-9), f"{back:.6f}")

# 只除 DOD（旧口径）会得到 842.1，与 905.5 差 7.5%
old_inst = movable / 0.95
check(f"旧口径（只除 DOD）= {old_inst:.2f} kWh，比新口径小 {inst-old_inst:.2f} kWh",
      close(old_inst, 800.0 / 0.95, 1e-6), f"{old_inst:.2f} vs {inst:.2f}")
check("新旧口径比值 = 1/系统效率 = 1/0.93",
      close(old_inst / inst, 0.93, 1e-9), f"{old_inst/inst:.9f}")

print()
print("=" * 78)
print("④ 满足天数闭环：同一批空间，反推再反查要得到目标天数")
print("=" * 78)
# 造 10 天的「每天最小可搬能量」，升序后按业务语义取
daily = [100, 150, 200, 250, 300, 350, 400, 450, 500, 550]
for target in (1, 3, 5, 7, 10):
    # 业务语义：满足 N 天 → 取第 (len-N+1) 小 → sorted[len-N]
    movable_cap = sorted(daily)[len(daily) - target]
    inst = installed_from_movable(movable_cap, 95, 93)
    usable = usable_from_installed(inst, 95, 93)
    days = sum(1 for d in daily if usable <= d + 1e-9)
    check(f"目标 {target} 天 → 可搬 {movable_cap} → 安装 {inst:.1f} → 满足 {days} 天",
          days == target, f"{days}")

print()
print("=" * 78)
print("⑤ 源码契约：主程序三处都用 _combined_factor，没有残留只除 DOD")
print("=" * 78)
main = os.path.join(REPO, "apps", "负荷曲线批量生成工具 Pro 融合版 (v2.3).py")
src = open(main, encoding="utf-8").read()

check("定义了 _combined_factor()", "def _combined_factor(self)" in src)
check("常量 BESS_SYS_EFF_DEFAULT 存在", "BESS_SYS_EFF_DEFAULT" in src)
check("有系统效率输入控件 sys_eff_spin", "sys_eff_spin = QSpinBox()" in src)
check("closeEvent 保存 pw_sys_eff", "'pw_sys_eff'" in src)
check("__init__ 恢复 pw_dod（此前只存不读）",
      "app_set('pw_dod'" in src or 'app_set(\n                \'pw_dod\'' in src)

# 反推处必须用综合系数
m = re.search(r"installed_cap\s*=\s*cap\s*/\s*([^\n]+)", src)
check("容量反推用 k = _combined_factor()",
      bool(m) and "_combined_factor" in src[max(0, m.start() - 300):m.end()],
      (m.group(1).strip()[:50] if m else "未找到"))

# 不得残留「只除 DOD」的写法
bad_patterns = [
    r"cap\s*/\s*\(dod_val\s*/\s*100\.0\)",
    r"cap\s*\*\s*dod_val\s*/\s*100\.0",
]
residual = []
for pat in bad_patterns:
    for m2 in re.finditer(pat, src):
        line_no = src[:m2.start()].count("\n") + 1
        line = src.splitlines()[line_no - 1]
        if line.strip().startswith("#"):
            continue
        residual.append(f"L{line_no}: {line.strip()[:70]}")
check("没有残留「只乘/除 DOD」的计算", not residual, "; ".join(residual[:3]))

print()
print("=" * 76)
print(f"结果：PASS {PASS}   FAIL {FAIL}")
print("=" * 76)
sys.exit(0 if FAIL == 0 else 1)
