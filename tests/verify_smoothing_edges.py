# -*- coding: utf-8 -*-
"""移动平均与高斯平滑的边界/NaN 行为验证。

反向证明：先断言旧写法（convolve mode='same'）确实让平直曲线两端低头，
再断言新写法没有。
"""
import os
import sys

import numpy as np

REPO = r"C:\Users\LENOVO\.hanako\repos\172629438__loomgrid"
sys.path.insert(0, REPO)
sys.stdout.reconfigure(encoding="utf-8")

from plotting.curves import _smooth_edge_safe, _compute_fits

ok, bad = [], []


def check(name, cond, detail=""):
    if cond:
        ok.append(name)
        print(f"  [PASS] {name}")
    else:
        bad.append(name)
        print(f"  [FAIL] {name}   {detail}")


print("=" * 78)
print("① 反向证明：旧写法确实让平直曲线两端低头")
print("=" * 78)
flat = np.full(96, 500.0)
old = np.convolve(flat, np.ones(5) / 5, mode="same")
print(f"  旧写法 96 点 w=5: 首={old[0]:.2f} 尾={old[-1]:.2f}")
check("旧写法首点确实偏低（bug 真实存在）", abs(old[0] - 500) > 1, f"{old[0]}")

print()
print("=" * 78)
print("② 新写法：平直曲线两端保持原值")
print("=" * 78)
for n in (24, 96, 288):
    flat = np.full(n, 500.0)
    w = min(5, n // 4) if n >= 5 else 2
    new = _smooth_edge_safe(flat, w)
    print(f"  {n:3d} 点 w={w}: 首={new[0]:.2f} 中={new[n//2]:.2f} 尾={new[-1]:.2f}")
    check(f"{n} 点平直曲线零偏差", np.allclose(new, 500.0), f"首={new[0]}")

print()
print("=" * 78)
print("③ 新写法：NaN 不扩散")
print("=" * 78)
arr = np.full(20, 500.0)
arr[10] = np.nan
old_nan = np.convolve(arr, np.ones(5) / 5, mode="same")
old_cnt = int(np.isnan(old_nan).sum())
new_out = _smooth_edge_safe(arr, 5)
new_cnt = int(np.isnan(new_out).sum())
print(f"  输入 1 个 NaN")
print(f"  旧写法输出 NaN 个数: {old_cnt}")
print(f"  新写法输出 NaN 个数: {new_cnt}")
check("旧写法扩散 NaN（bug 真实存在）", old_cnt > 1, str(old_cnt))
check("新写法不再扩散", new_cnt == 0, str(new_cnt))
check("新写法在 NaN 位置给出邻值", abs(new_out[10] - 500) < 1e-9, str(new_out[10]))

print()
print("=" * 78)
print("④ 尖峰仍被摊平（这是移动平均本身语义，不该「修」）")
print("=" * 78)
spike = np.full(96, 500.0)
spike[48] = 1000.0
ma = _smooth_edge_safe(spike, 5)
print(f"  峰值 idx48 = {ma[48]:.1f}（原 1000）")
print(f"  邻点 idx47/49 = {ma[47]:.1f} / {ma[49]:.1f}（原 500）")
check("峰值被摊平到 600", abs(ma[48] - 600) < 1e-9, str(ma[48]))
check("邻点被抬起", ma[47] > 500 and ma[49] > 500)

print()
print("=" * 78)
print("⑤ 平滑的能量守恒性（边界效应会带来微小偏差）")
print("=" * 78)
rng = np.random.default_rng(42)
rand = rng.uniform(100, 600, 96)
sm = _smooth_edge_safe(rand, 5)
diff = sm.sum() - rand.sum()
pct = abs(diff) / rand.sum() * 100
print(f"  原总和 {rand.sum():.2f}   平滑后总和 {sm.sum():.2f}")
print(f"  差 {diff:+.2f}（{pct:.3f}%）")
print(f"  边界处理是「复制首尾值」，不是零填充也不是周期边界，")
print(f"  所以总和不严格守恒——这是预期行为，不是 bug。")
print(f"  关键判据：偏差必须很小，且方向不固定（随数据而定）。")
check("平滑不引入显著能量偏移（<1%）", pct < 1.0, f"{pct:.3f}%")

# 反向：旧写法的零填充会系统性偏低（不是随机偏差，方向固定）
old_sm = np.convolve(rand, np.ones(5) / 5, mode="same")
old_diff = old_sm.sum() - rand.sum()
print(f"  对比旧写法（零填充）: {old_sm.sum():.2f}，差 {old_diff:+.2f}")
check("旧写法的偏差是系统性偏低（固定的负号）", old_diff < 0, f"{old_diff}")

print()
print("=" * 78)
print("⑥ _compute_fits 端到端：三种拟合都在，且移动平均不低头")
print("=" * 78)
y = np.full((3, 96), 500.0)     # 3 天 × 96 点，平直
y[:, 40] = 800.0
x = np.arange(96)
fits = _compute_fits(x, y, ["poly2", "poly3", "moving_avg", "gaussian", "spline"])
print(f"  返回的拟合类型: {sorted(fits.keys())}")
check("moving_avg 存在", "moving_avg" in fits)
if "moving_avg" in fits:
    curve, label = fits["moving_avg"]
    print(f"  {label}: 首={curve[0]:.2f} 尾={curve[-1]:.2f}")
    check("moving_avg 首点不低头", abs(curve[0] - 500) < 1, f"{curve[0]}")
    check("moving_avg 尾点不低头", abs(curve[-1] - 500) < 1, f"{curve[-1]}")
if "gaussian" in fits:
    g, glabel = fits["gaussian"]
    print(f"  {glabel}: 首={g[0]:.2f} 尾={g[-1]:.2f}")
    check("gaussian 首点不低头", abs(g[0] - 500) < 20, f"{g[0]}")

print()
print("=" * 78)
print("⑦ _compute_fits：avg 含 NaN 时各拟合不整段失效")
print("=" * 78)
y2 = np.full((3, 24), 500.0)
y2[0, 5] = np.nan          # 只有第 5 点缺
fits2 = _compute_fits(np.arange(24), y2, ["moving_avg", "gaussian"])
for k in ("moving_avg", "gaussian"):
    if k in fits2:
        arr2 = fits2[k][0]
        cnt = int(np.isnan(arr2).sum())
        print(f"  {k}: NaN 个数 {cnt}")
        check(f"{k} 不因单点缺失而整段变 NaN", cnt == 0, str(cnt))

print()
print("=" * 78)
print(f"结果：PASS {len(ok)}   FAIL {len(bad)}")
for b in bad:
    print("  -", b)
print("=" * 78)
sys.exit(1 if bad else 0)
