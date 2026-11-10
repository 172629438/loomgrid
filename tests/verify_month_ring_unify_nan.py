# -*- coding: utf-8 -*-
"""v2.3.23 两处修复的回归：月份环形距离 + 归一化不产生假 NaN。

两处都是「之前没系统审过的区域」里挖出来的：
  · _find_best_source 的月份距离用线性差，跨年边界选错源月份
  · _unify_time_resolution 的插值区间外填 NaN，被 nansum 当 0 吞掉
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
tree = ast.parse(src)


def cls(name):
    for n in ast.walk(tree):
        if isinstance(n, ast.ClassDef) and n.name == name:
            return n
    return None


def method(clazz, fname):
    if clazz is None:
        return None
    for n in clazz.body:
        if isinstance(n, ast.FunctionDef) and n.name == fname:
            return n
    return None


# ══════════════════════════════════════════════════════════════════
print("=" * 76)
print("① 月份环形距离")
print("=" * 76)
Sim = cls("SimulationConfigDialog")
check("SimulationConfigDialog 存在", Sim is not None)

md = method(Sim, "_month_distance")
check("有 _month_distance（抽出来的环形距离）", md is not None)
if md is None:
    # 没抽出来说明还是旧实现
    print("  !! 仍是旧实现，检查 _find_best_source")

# 真跑：导入模块实际调用
import importlib.util
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
spec = importlib.util.spec_from_file_location("fm_2323", MAIN)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

if hasattr(mod.SimulationConfigDialog, "_month_distance"):
    D = mod.SimulationConfigDialog._month_distance
    CASES = [
        (1, 12, 1), (2, 12, 2), (1, 11, 2), (3, 12, 3),
        (1, 2, 1), (6, 7, 1), (12, 1, 1), (2, 11, 3),
    ]
    ok = True
    for a, b, expect in CASES:
        got = D(a, b)
        if got != expect:
            ok = False
            print(f"    dist({a},{b}) = {got}，应为 {expect}")
    check("环形距离全部正确", ok)
    print(f"    dist(1,12)={D(1,12)}  dist(2,12)={D(2,12)}  dist(1,11)={D(1,11)}")

# 源码契约：不能用裸 abs
fs = method(Sim, "_find_best_source")
if fs:
    body = "\n".join(lines[fs.lineno - 1:fs.end_lineno])
    check("_find_best_source 不再用裸 abs 算距离",
          "abs(x - target_month)" not in body,
          "线性差在跨年边界会选错")
    check("_find_best_source 用 _month_distance",
          "_month_distance" in body)

# ══════════════════════════════════════════════════════════════════
print()
print("=" * 76)
print("② 归一化不产生假 NaN")
print("=" * 76)
un = None
PW = cls("ProfessionalWindow")
if PW:
    un = method(PW, "_unify_time_resolution")
check("有 _unify_time_resolution", un is not None)

if un:
    body = "\n".join(lines[un.lineno - 1:un.end_lineno])
    check("不再用 left=np.nan, right=np.nan",
          "left=np.nan" not in body and "right=np.nan" not in body,
          "区间外填 NaN 会被 nansum 当 0")
    check("用最近有效值填充边缘",
          "valid[0]" in body and "valid[-1]" in body)
    check("整行全 NaN 时保留 NaN（不伪造）",
          "valid.size == 0" in body and "np.nan" in body)

# 真跑：模拟三种分辨率混合
def fmt_min(m):
    h, mm = divmod(int(m), 60)
    return f"{h:02d}:{mm:02d}"


def parse_min(t):
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def labels_n(n, step):
    return [fmt_min(i * step) for i in range(n)]


def unify_impl(mdict):
    """按修复后的逻辑跑一遍。"""
    max_points = max(v["arr"].shape[1] for v in mdict.values())
    ref = None
    for v in mdict.values():
        if v["arr"].shape[1] == max_points:
            ref = v["labels"]
            break
    nh = np.array([parse_min(t) / 60.0 for t in ref])
    out = {}
    for month, d in mdict.items():
        arr = d["arr"]
        if arr.shape[1] == max_points:
            out[month] = {"arr": arr, "labels": ref}
            continue
        oh = np.array([parse_min(t) / 60.0 for t in d["labels"]])
        na = np.zeros((arr.shape[0], max_points))
        for i in range(arr.shape[0]):
            row = arr[i, :]
            valid = row[~np.isnan(row)]
            if valid.size == 0:
                na[i, :] = np.nan
                continue
            na[i, :] = np.interp(nh, oh, row,
                                 left=float(valid[0]), right=float(valid[-1]))
        out[month] = {"arr": na, "labels": ref}
    return out


mdict = {
    "A96": {"arr": np.full((2, 96), 200.0), "labels": labels_n(96, 15)},
    "B288": {"arr": np.full((2, 288), 200.0), "labels": labels_n(288, 5)},
    "C24": {"arr": np.full((2, 24), 200.0), "labels": labels_n(24, 60)},
}
r = unify_impl({k: dict(v) for k, v in mdict.items()})
total_nan = sum(int(np.isnan(v["arr"]).sum()) for v in r.values())
check("96/288/24 混合归一后零 NaN", total_nan == 0, f"{total_nan} 个")

ok_val = all(
    abs(np.nanmin(v["arr"]) - 200.0) < 1e-6 and abs(np.nanmax(v["arr"]) - 200.0) < 1e-6
    for m, v in r.items() if m != "B288"
)
check("常数负荷归一后仍是常数（没引入畸变）", ok_val)

# 整行真缺失
m2 = {
    "A96": {"arr": np.full((2, 96), 200.0), "labels": labels_n(96, 15)},
    "B288": {"arr": np.full((2, 288), 200.0), "labels": labels_n(288, 5)},
}
m2["B288"]["arr"][1, :] = np.nan
r2 = unify_impl({k: {"arr": v["arr"].copy(), "labels": v["labels"]}
                 for k, v in m2.items()})
a = r2["B288"]["arr"]
check("有数据行零 NaN", int(np.isnan(a[0]).sum()) == 0)
check("整行缺失仍保留全 NaN（不伪造数据）",
      int(np.isnan(a[1]).sum()) == 288, str(int(np.isnan(a[1]).sum())))

# ══════════════════════════════════════════════════════════════════
print()
print("=" * 76)
print("③ 修复对空间计算的实际影响")
print("=" * 76)
from core.spaces import compute_segmented_spaces

labels = labels_n(96, 15)
segs = [
    {"start": 0.0, "end": 8.0, "type": "charge", "label": "充电1"},
    {"start": 18.0, "end": 21.0, "type": "discharge", "label": "放电1"},
]

load_before = np.full((2, 96), 200.0)
load_before[:, 75:] = np.nan      # 18:45 后缺失（unify 的旧产物）
load_after = np.full((2, 96), 200.0)

res_b = compute_segmented_spaces(load_before, labels, segs, billing=None,
                                 mode="B", rated_power=1000.0)
res_a = compute_segmented_spaces(load_after, labels, segs, billing=None,
                                 mode="B", rated_power=1000.0)
dis_b = float(np.nansum(res_b[1]))
dis_a = float(np.nansum(res_a[1]))
print(f"  修复前（尾部 NaN）放电空间: {dis_b:.1f}")
print(f"  修复后（边缘补齐）放电空间: {dis_a:.1f}")
check("修复前确实被 NaN 压成 0（证明这个 bug 真实存在）", dis_b < 1e-6, f"{dis_b}")
check("修复后放电空间恢复正常", dis_a > 1000.0, f"{dis_a}")

print()
print("=" * 76)
print(f"结果：PASS {len(passed)}   FAIL {len(failed)}")
for f in failed:
    print("  -", f)
print("=" * 76)
sys.exit(1 if failed else 0)
