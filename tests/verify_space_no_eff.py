# -*- coding: utf-8 -*-
"""v2.3.16 业务口径：充放电空间不含效率。

业务定义（用户口径，2026-11-04 明确）
--------------------------------------
「计算这三块空间的时候别算效率，就是负荷曲线里有多少空间你就显示多少空间，
  然后在专业模式的窗口里计算实际配储容量的时候不是会引入一个效率进行计算的吗，
  那个效率还能自定义呢。」

所以：
  · 空间 = 纯物理量：负荷曲线在充/放时段里还剩多少能量可搬。
            充电空间就是「还能往上装多少」，放电空间就是「还能往下卸多少」。
  · 效率 = 另一层的事，在专业模式窗口以 DOD（50%~100% 可自定义）引入，
            作用于容量：可用容量 = 安装容量 × DOD。

为什么这条测试必须存在
----------------------
`compute_segmented_spaces` 里曾经在 4 个分支上都乘了 0.93（充放各一档），
看起来"更符合物理"，但那让空间和容量两层都掺了效率，用户看到的数字
既不是纯空间、也不是纯容量，而是两者的混合。改动方向容易反复，
所以这里把「不许乘效率」写成可执行断言：谁加回去，谁就红。
"""
import sys

REPO = r"C:\Users\LENOVO\.hanako\repos\172629438__loomgrid"
if REPO not in sys.path:
    sys.path.insert(0, REPO)
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np  # noqa: E402

import core.spaces as S  # noqa: E402
from core.spaces import compute_segmented_spaces  # noqa: E402

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

# 单段、单天，方便手算：0-8 充（Σdt=8.0）、10-12 放（Σdt=2.0）
SEGS = [
    {"start": 0.0, "end": 8.0, "type": "charge", "label": "谷段"},
    {"start": 10.0, "end": 12.0, "type": "discharge", "label": "峰段"},
]

print("=" * 78)
print("① 四个分支逐一验证：结果 = 纯几何值，不含 0.93")
print("=" * 78)

# ── Mode A 充电：(容量线 − 负荷) × dt ─────────────────────────
load = np.full((1, 96), 100.0)
c, d, _, _ = compute_segmented_spaces(load, TIMES, SEGS, mode="A",
                                      billing={"value": 300.0})
want = (300.0 - 100.0) * 8.0            # 1600
got = float(c[0][0])
check(f"Mode A 充电 = (300−100)×8h = {want:.1f}（不含效率）",
      close(got, want), f"实得 {got}")

# ── Mode A 放电：min(负荷, 容量线) × dt ───────────────────────
want = min(100.0, 300.0) * 2.0          # 200
got = float(d[1][0])
check(f"Mode A 放电 = min(100,300)×2h = {want:.1f}（不含效率）",
      close(got, want), f"实得 {got}")

# ── Mode B 充电：min(额定, −负荷) × dt ────────────────────────
neg = np.full((1, 96), -120.0)
c, d, _, _ = compute_segmented_spaces(neg, TIMES, SEGS, mode="B",
                                      rated_power=300.0)
want = min(300.0, 120.0) * 8.0          # 960
got = float(c[0][0])
check(f"Mode B 充电 = min(300,120)×8h = {want:.1f}（不含效率）",
      close(got, want), f"实得 {got}")

# ── Mode B 放电：min(额定, 负荷) × dt ─────────────────────────
c, d, _, _ = compute_segmented_spaces(load, TIMES, SEGS, mode="B",
                                      rated_power=300.0)
want = min(300.0, 100.0) * 2.0          # 200
got = float(d[1][0])
check(f"Mode B 放电 = min(300,100)×2h = {want:.1f}（不含效率）",
      close(got, want), f"实得 {got}")

print()
print("=" * 78)
print("② 与「乘了效率」的旧口径明确区分（差值必须正好 0.93 倍）")
print("=" * 78)

EFF = 0.93
c, _d, _, _ = compute_segmented_spaces(np.full((1, 96), 0.0), TIMES, SEGS,
                                       mode="A", billing={"value": 100.0})
pure = float(c[0][0])                    # 100 × 8 = 800
check("负荷 0 / 容量线 100 → 充电空间 = 800（若乘 0.93 会是 744）",
      close(pure, 800.0), f"实得 {pure}")
check(f"800 与旧口径 744 的比值 = 1/0.93 = {1/EFF:.6f}",
      close(800.0 / 744.0, 1 / EFF, 1e-9), f"{800.0/744.0:.9f}")

print()
print("=" * 78)
print("③ 常量保留（api/ 与其他调用方仍可能引用），但空间计算不再使用")
print("=" * 78)
check("CHARGING_EFFICIENCY 常量仍存在（兼容）",
      hasattr(S, "CHARGING_EFFICIENCY"), str(getattr(S, "CHARGING_EFFICIENCY", None)))
check("DISCHARGING_EFFICIENCY 常量仍存在（兼容）",
      hasattr(S, "DISCHARGING_EFFICIENCY"), str(getattr(S, "DISCHARGING_EFFICIENCY", None)))

src = open(REPO + r"\core\spaces.py", encoding="utf-8").read()
# 只检查 compute_segmented_spaces 函数体内的代码行（去掉注释）
func_src = src.split("def compute_segmented_spaces", 1)[-1]
if "def " in func_src[1:]:
    # 截到下一个顶层 def
    body_lines = []
    for line in func_src.splitlines()[1:]:
        if line.startswith("def "):
            break
        body_lines.append(line)
    body = "\n".join(body_lines)
else:
    body = func_src
code_only = "\n".join(
    ln.split("#", 1)[0] for ln in body.splitlines()
)
check("函数体内不出现 CHARGING_EFFICIENCY",
      "CHARGING_EFFICIENCY" not in code_only,
      "出现即为口径回退")
check("函数体内不出现 DISCHARGING_EFFICIENCY",
      "DISCHARGING_EFFICIENCY" not in code_only,
      "出现即为口径回退")

print()
print("=" * 78)
print("④ 效率转移到容量层：DOD 语义验证（可用容量 = 安装 × DOD）")
print("=" * 78)
for dod in (50, 90, 95, 100):
    cap = 1000.0
    usable = cap * dod / 100.0
    back = usable / (dod / 100.0)
    check(f"DOD {dod}%：可用 {usable:.1f} → 反推安装 {back:.1f} = 1000",
          close(back, 1000.0), f"{back}")

print()
print("=" * 76)
print(f"结果：PASS {PASS}   FAIL {FAIL}")
print("=" * 76)
sys.exit(0 if FAIL == 0 else 1)
