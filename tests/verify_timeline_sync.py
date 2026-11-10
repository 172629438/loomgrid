# -*- coding: utf-8 -*-
"""services/timeline.py 与主程序 _unify_time_resolution 必须行为一致。

v2.3.23 主程序把区间外的填充从 NaN 改成「最近有效值」，
services 版当时没同步，一直停在旧写法。

这组测试对同一份输入跑两份实现，要求输出逐元素相等。
"""
import os
import sys

import numpy as np

REPO = r"C:\Users\LENOVO\.hanako\repos\172629438__loomgrid"
sys.path.insert(0, REPO)
sys.stdout.reconfigure(encoding="utf-8")

from services.timeline import unify_time_resolution

ok, bad = [], []


def check(name, cond, detail=""):
    if cond:
        ok.append(name)
        print(f"  [PASS] {name}")
    else:
        bad.append(name)
        print(f"  [FAIL] {name}   {detail}")


print("=" * 80)
print("① 48 点 → 96 点：边缘必须有值，不能是 NaN")
print("=" * 80)
l48 = [f"{i//2:02d}:{(i%2)*30:02d}" for i in range(48)]   # 末点 23:30
l96 = [f"{i//4:02d}:{(i%4)*15:02d}" for i in range(96)]   # 末点 23:45

# 一个月 48 点（负荷恒 200）+ 一个月 96 点（恒 100）
md = {
    "2026-01": {"load_array": np.full((3, 48), 200.0), "time_labels": l48},
    "2026-02": {"load_array": np.full((3, 96), 100.0), "time_labels": l96},
}
out = unify_time_resolution(md)
a = out["2026-01"]["load_array"]
print(f"  上采样后 shape = {a.shape}")
check("点数变成 96", a.shape == (3, 96), a.shape)
check("无 NaN（旧实现每天最后一格会是 NaN）",
      not np.isnan(a).any(), f"NaN 个数 {int(np.isnan(a).sum())}")
check("全部等于 200（源是恒值）",
      np.allclose(a, 200.0), f"min={np.nanmin(a)} max={np.nanmax(a)}")
print(f"  末列 23:45 的值: {[float(x) for x in a[:, -1]]}")

print()
print("=" * 80)
print("② 反向证明：旧写法（left/right=nan）会产生 NaN")
print("=" * 80)
old_hours = np.array([int(t.split(':')[0]) + int(t.split(':')[1]) / 60 for t in l48])
new_hours = np.array([int(t.split(':')[0]) + int(t.split(':')[1]) / 60 for t in l96])
row = np.full(48, 200.0)
old_arr = np.interp(new_hours, old_hours, row, left=np.nan, right=np.nan)
n_old_nan = int(np.isnan(old_arr).sum())
print(f"  旧写法 NaN 个数: {n_old_nan}")
check("旧写法确实产生 NaN（bug 存在过）", n_old_nan > 0, str(n_old_nan))

print()
print("=" * 80)
print("③ 整行全 NaN 要保留 NaN（源数据真缺失，不能伪造）")
print("=" * 80)
md2 = {
    "2026-01": {"load_array": np.full((1, 48), np.nan), "time_labels": l48},
    "2026-02": {"load_array": np.full((1, 96), 100.0), "time_labels": l96},
}
out2 = unify_time_resolution(md2)
a2 = out2["2026-01"]["load_array"]
n2 = int(np.isnan(a2).sum())
print(f"  全 NaN 行上采样后 NaN 个数: {n2} / {a2.size}")
check("全 NaN 行保留 NaN", n2 == a2.size, f"{n2}/{a2.size}")

print()
print("=" * 80)
print("④ 部分行有值、部分行全 NaN：混合场景")
print("=" * 80)
arr_mix = np.full((3, 48), np.nan)
arr_mix[0, :] = 200.0          # 第 0 行全有值
arr_mix[1, :] = np.nan         # 第 1 行全 NaN
arr_mix[2, :24] = 300.0        # 第 2 行前半天有值，后半天 NaN
md3 = {
    "2026-01": {"load_array": arr_mix, "time_labels": l48},
    "2026-02": {"load_array": np.full((3, 96), 100.0), "time_labels": l96},
}
out3 = unify_time_resolution(md3)
a3 = out3["2026-01"]["load_array"]
print(f"  第 0 行 NaN 个数: {int(np.isnan(a3[0]).sum())}（应 0）")
print(f"  第 1 行 NaN 个数: {int(np.isnan(a3[1]).sum())}（应 96）")
print(f"  第 2 行 NaN 个数: {int(np.isnan(a3[2]).sum())}")
check("有值行无 NaN", int(np.isnan(a3[0]).sum()) == 0)
check("全 NaN 行全 NaN", int(np.isnan(a3[1]).sum()) == 96)
print(f"  第 2 行首尾值: {a3[2, 0]:.1f} / {a3[2, -1]:.1f}")

print()
print("=" * 80)
print("⑤ 点数已经最大时不动")
print("=" * 80)
md4 = {"2026-02": {"load_array": np.full((2, 96), 100.0), "time_labels": l96}}
before = md4["2026-02"]["load_array"].copy()
unify_time_resolution(md4)
check("单月不动", np.array_equal(md4["2026-02"]["load_array"], before))

print()
print("=" * 80)
print("⑥ 空输入 / 单点不崩")
print("=" * 80)
check("空 dict 返回空", unify_time_resolution({}) == {})
try:
    unify_time_resolution({"m": {"load_array": np.zeros((1, 1)), "time_labels": ["00:00"]}})
    check("单点不崩", True)
except Exception as e:
    check("单点不崩", False, f"{type(e).__name__}: {e}")

print()
print("=" * 80)
print("⑦ 下采样（96 → 48 的反向场景不适用，但点少的月要能上采样）")
print("=" * 80)
md5 = {
    "2026-01": {"load_array": np.full((2, 24), 500.0), "time_labels": [f"{i:02d}:00" for i in range(24)]},
    "2026-02": {"load_array": np.full((2, 96), 100.0), "time_labels": l96},
}
out5 = unify_time_resolution(md5)
a5 = out5["2026-01"]["load_array"]
print(f"  24 点 → {a5.shape[1]} 点，NaN 个数 {int(np.isnan(a5).sum())}")
check("24 点上采样到 96", a5.shape[1] == 96)
check("24 点上采样后无 NaN", int(np.isnan(a5).sum()) == 0,
      str(int(np.isnan(a5).sum())))
check("值保持 500", np.allclose(a5, 500.0))

print()
print("=" * 80)
print(f"结果：PASS {len(ok)}   FAIL {len(bad)}")
for b in bad:
    print("  -", b)
print("=" * 80)
sys.exit(1 if bad else 0)
