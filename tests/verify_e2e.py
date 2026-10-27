# -*- coding: utf-8 -*-
"""端到端测试：真实文件 → 扫描 → 宽表 → 图表数据。

与 verify_ingest.py 的分工
--------------------------
verify_ingest 测「列分类/日期解析/示数转换」这些单点函数；
本文件测**整条链**：把 xlsx/csv 落盘，走程序自己的读取→探测→分类→扫描→取数路径，
最后断言「宽表里的每个数，都等于源文件里对应那个数」。

为什么必须做这层
----------------
单点函数全对，链路仍可能错——比如扫描阶段把 15 分钟降成小时、把负值裁掉、
把两天的数据合并成一天。这类错误只有拿源数据逐点比对才看得见。

覆盖
----
A 长表形态（6）  B 宽表（3）  C 转置宽表（2）  D 示数表（3）
E 多资产（2）    F 多月（1）   G 数值保真（4）   H 脏数据（3）   I 双端一致（4）
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ULTRA = os.path.join(ROOT, "负荷曲线批量生成Ultra.py")
FUSION = os.path.join(ROOT, "负荷曲线批量生成工具 Pro 融合版 (v2.3).py")

VERBOSE = "-v" in sys.argv
PASS = FAIL = 0
FAILS = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        if VERBOSE:
            print(f"[PASS] {name}")
    else:
        FAIL += 1
        FAILS.append((name, detail))
        print(f"[FAIL] {name}  {detail}")


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


print("加载主程序…", flush=True)
U = load_module(ULTRA, "lg_e2e")
print("加载 Fusion…", flush=True)
F = load_module(FUSION, "lgf_e2e")

TMP = tempfile.mkdtemp(prefix="lg_e2e_")

# ────────────────────────────────────────────────────────────
# 负荷形态
# ────────────────────────────────────────────────────────────
def shape_value(kind, i, day, n_pts=96):
    hour = i * 24.0 / n_pts
    rng = np.random.default_rng(1000 + day)
    if kind == "industrial":       # 双峰：8-11、18-21
        v = 400 + 520 * np.exp(-((hour - 9.5) ** 2) / 2.0) + 460 * np.exp(-((hour - 19.5) ** 2) / 2.0)
    elif kind == "commercial":     # 单峰：午后
        v = 120 + 780 * np.exp(-((hour - 14.0) ** 2) / 9.0)
    elif kind == "pv":             # 光伏倒送：中午负
        v = 260 - 620 * np.exp(-((hour - 12.5) ** 2) / 6.0)
    elif kind == "flat":           # 连续生产
        v = 600 + 8 * np.sin(i / 6.0)
    elif kind == "spiky":          # 尖峰冲击
        v = 300 + (900 if i in (40, 41) else 0) + rng.normal(0, 5)
    elif kind == "seasonal":       # 季节差异（按月缩放）
        v = (450 + 300 * np.exp(-((hour - 10) ** 2) / 4.0)) * (1.0 + 0.04 * day)
    else:
        v = 300 + 100 * np.sin(i / 4.0)
    return round(float(v), 4)


def long_df(days=2, n_pts=96, kind="industrial", start_day=1, extra=None):
    rows = []
    for d in range(days):
        for i in range(n_pts):
            h, m = divmod(int(i * 24 * 60 / n_pts), 60)
            row = {"日期": f"2026-10-{start_day + d:02d}", "时间": f"{h:02d}:{m:02d}",
                   "正向有功功率": shape_value(kind, i, d, n_pts)}
            if extra:
                row.update(extra)
            rows.append(row)
    return pd.DataFrame(rows)


def expected_from_long(df, load="正向有功功率"):
    d = df.copy()
    d["__d__"] = d["日期"].astype(str)
    d["__t__"] = d["时间"].astype(str)
    piv = d.pivot_table(index="__d__", columns="__t__", values=load, aggfunc="first")
    order = sorted(piv.columns, key=lambda x: int(x.split(":")[0]) * 60 + int(x.split(":")[1]))
    return piv[order]


# ────────────────────────────────────────────────────────────
# 走完整链路
# ────────────────────────────────────────────────────────────
def run_pipeline(mod, fp, sheet, mapping_override=None):
    """文件 → 探测 → 分类 → 扫描 → 取宽表。返回 (summaries, data_dict, provider, err)。"""
    try:
        fmt = mod._detect_file_format_type(fp, sheet)
        df = mod._read_file_full(fp, sheet if sheet != "CSV文件" else None)
        if df is None or len(df) == 0:
            return None, None, None, "read_failed"
        mapping = mod._smart_classify_columns(list(df.columns), df)
        mapping["format_type"] = fmt
        mapping.setdefault("header_row", 0)
        if mapping_override:
            mapping.update(mapping_override)
        args = (fp, sheet, mapping, None, threading.Event(), None, False)
        try:
            res = mod._scan_file_worker(args)
        except ValueError:
            # Fusion 的 worker 只解包 6 个参数（无 force_chunk）
            res = mod._scan_file_worker(args[:6])
        if isinstance(res, dict):
            return None, None, None, f"scan_error:{res.get('error', '')}"
        if not res:
            return [], None, None, "no_summary"
        gs = mod.GroupSummary(**res[0])
        provider = mod.DataProvider()
        data = provider.get_group_data(gs)
        if not data:
            return res, None, provider, f"load_error:{provider.get_load_error(gs)}"
        return res, data, provider, None
    except Exception as e:
        return None, None, None, f"{type(e).__name__}: {e}"


def assert_matrix(tag, want, data, tol=1e-9):
    """宽表数值逐点比对（want 是 DataFrame，data 是 get_group_data 的结果）。"""
    arr = data.get("load_array")
    if arr is None:
        check(tag, False, "load_array 缺失")
        return
    arr = np.asarray(arr, dtype=float)
    w = want.values.astype(float)
    if arr.shape != w.shape:
        check(tag, False, f"shape {arr.shape} ≠ 期望 {w.shape}")
        return
    diff = np.nanmax(np.abs(arr - w)) if arr.size else 0.0
    check(tag, diff <= tol, f"最大偏差 {diff:.6g}")


# ────────────────────────────────────────────────────────────
# A. 长表形态
# ────────────────────────────────────────────────────────────
def group_A():
    print("\n" + "=" * 74 + "\nA. 长表形态（xlsx，96 点/天 × 2 天）\n" + "=" * 74)
    for kind, label in (("industrial", "工业双峰"), ("commercial", "商业单峰"), ("pv", "光伏倒送"),
                        ("flat", "连续生产"), ("spiky", "尖峰冲击"), ("seasonal", "季节递增")):
        fp = os.path.join(TMP, f"A_{kind}.xlsx")
        src = long_df(kind=kind)
        src.to_excel(fp, index=False)
        want = expected_from_long(src)
        for tag, mod in (("Ultra", U), ("Fusion", F)):
            res, data, provider, err = run_pipeline(mod, fp, "Sheet1")
            if err:
                check(f"A {label} [{tag}]", False, err)
                continue
            check(f"A {label} 摘要 [{tag}]", len(res) == 1 and res[0]["date_count"] == 2,
                  f"summaries={len(res)} date_count={res[0]['date_count'] if res else '-'}")
            assert_matrix(f"A {label} 数值 [{tag}]", want, data)


# ────────────────────────────────────────────────────────────
# B. 宽表
# ────────────────────────────────────────────────────────────
def group_B():
    print("\n" + "=" * 74 + "\nB. 宽表（日期行 + 时刻列）\n" + "=" * 74)
    # B1 96 列
    cols = {}
    labels = [f"{(i * 15) // 60}:{(i * 15) % 60:02d}" for i in range(96)]
    for i, lab in enumerate(labels):
        cols[lab] = [shape_value("industrial", i, d) for d in range(2)]
    wide = pd.DataFrame({"日期": ["2026-10-01", "2026-10-02"], **cols})
    fp = os.path.join(TMP, "B_wide96.xlsx")
    wide.to_excel(fp, index=False)
    for tag, mod in (("Ultra", U), ("Fusion", F)):
        res, data, provider, err = run_pipeline(mod, fp, "Sheet1")
        if err:
            check(f"B1 宽表96列 [{tag}]", False, err)
            continue
        arr = np.asarray(data["load_array"], dtype=float)
        check(f"B1 宽表96列 shape [{tag}]", arr.shape == (2, 96), f"shape={arr.shape}")
        diff = np.nanmax(np.abs(arr - wide[labels].values.astype(float)))
        check(f"B1 宽表96列 数值 [{tag}]", diff <= 1e-9, f"最大偏差 {diff:.6g}")

    # B2 小时级 24 列
    labels24 = [f"{h}:00" for h in range(24)]
    cols24 = {lab: [shape_value("industrial", i * 4, 0) for _ in range(2)] for i, lab in enumerate(labels24)}
    wide24 = pd.DataFrame({"日期": ["2026-10-01", "2026-10-02"], **cols24})
    fp2 = os.path.join(TMP, "B_wide24.xlsx")
    wide24.to_excel(fp2, index=False)
    for tag, mod in (("Ultra", U), ("Fusion", F)):
        res, data, provider, err = run_pipeline(mod, fp2, "Sheet1")
        if err:
            check(f"B2 宽表24列 [{tag}]", False, err)
            continue
        arr = np.asarray(data["load_array"], dtype=float)
        check(f"B2 宽表24列 shape [{tag}]", arr.shape == (2, 24), f"shape={arr.shape}")


# ────────────────────────────────────────────────────────────
# C. 转置宽表
# ────────────────────────────────────────────────────────────
def group_C():
    print("\n" + "=" * 74 + "\nC. 转置宽表（时间行 + 日期列）\n" + "=" * 74)
    rows = []
    for i in range(96):
        h, m = divmod(i * 15, 60)
        rows.append({"时间": f"{h:02d}:{m:02d}",
                     "2026-10-01": shape_value("industrial", i, 0),
                     "2026-10-02": shape_value("industrial", i, 1)})
    trans = pd.DataFrame(rows)
    fp = os.path.join(TMP, "C_transposed.xlsx")
    trans.to_excel(fp, index=False)
    for tag, mod in (("Ultra", U), ("Fusion", F)):
        res, data, provider, err = run_pipeline(mod, fp, "Sheet1")
        if err:
            check(f"C1 转置宽表 [{tag}]", False, err)
            continue
        arr = np.asarray(data["load_array"], dtype=float)
        check(f"C1 转置宽表 shape [{tag}]", arr.shape == (2, 96), f"shape={arr.shape}")
        if arr.shape == (2, 96):
            want = trans[["2026-10-01", "2026-10-02"]].values.T.astype(float)
            diff = np.nanmax(np.abs(arr - want))
            check(f"C1 转置宽表 数值 [{tag}]", diff <= 1e-9, f"最大偏差 {diff:.6g}")


# ────────────────────────────────────────────────────────────
# D. 示数表
# ────────────────────────────────────────────────────────────
def group_D():
    print("\n" + "=" * 74 + "\nD. 示数表（日重置累积电量）\n" + "=" * 74)
    for base, label in ((0.0, "基准0"), (1000.0, "基准1000")):
        rows = []
        for d in range(2):
            acc = base
            for i in range(96):
                h, m = divmod(i * 15, 60)
                rows.append({"日期": f"2026-10-{d + 1:02d}", "时间": f"{h:02d}:{m:02d}", "总电量": acc})
                acc += 10.0                      # 40 kW 恒定
        src = pd.DataFrame(rows)
        fp = os.path.join(TMP, f"D_acc_{int(base)}.xlsx")
        src.to_excel(fp, index=False)
        for tag, mod in (("Ultra", U), ("Fusion", F)):
            res, data, provider, err = run_pipeline(mod, fp, "Sheet1")
            if err:
                check(f"D 示数表{label} [{tag}]", False, err)
                continue
            arr = np.asarray(data["load_array"], dtype=float)
            med = float(np.nanmedian(arr))
            # 40 kW 恒定；末点无后继差分为 NaN 允许
            check(f"D 示数表{label} 转 40kW [{tag}]", abs(med - 40.0) < 1.0,
                  f"中位功率 {med:.2f}（期望 40）")


# ────────────────────────────────────────────────────────────
# E. 多资产
# ────────────────────────────────────────────────────────────
def group_E():
    print("\n" + "=" * 74 + "\nE. 多资产长表\n" + "=" * 74)
    rows = []
    for aid, scale in (("M001", 1.0), ("M002", 1.6)):
        for d in range(2):
            for i in range(96):
                h, m = divmod(i * 15, 60)
                rows.append({"资产编号": aid, "日期": f"2026-10-{d + 1:02d}",
                             "时间": f"{h:02d}:{m:02d}",
                             "正向有功功率": round(shape_value("industrial", i, d) * scale, 4)})
    src = pd.DataFrame(rows)
    fp = os.path.join(TMP, "E_multiasset.xlsx")
    src.to_excel(fp, index=False)
    for tag, mod in (("Ultra", U), ("Fusion", F)):
        res, data, provider, err = run_pipeline(mod, fp, "Sheet1")
        if err:
            check(f"E1 多资产分组 [{tag}]", False, err)
            continue
        assets = sorted({s["asset_value"] for s in res})
        check(f"E1 多资产分组 [{tag}]", assets == ["M001", "M002"], f"资产={assets}")
        check(f"E1 多资产摘要数 [{tag}]", len(res) == 2, f"summaries={len(res)}")


# ────────────────────────────────────────────────────────────
# F. 多月
# ────────────────────────────────────────────────────────────
def group_F():
    print("\n" + "=" * 74 + "\nF. 跨月\n" + "=" * 74)
    rows = []
    for date in ("2026-10-31", "2026-11-01"):
        for i in range(96):
            h, m = divmod(i * 15, 60)
            rows.append({"日期": date, "时间": f"{h:02d}:{m:02d}",
                         "正向有功功率": shape_value("industrial", i, 0)})
    src = pd.DataFrame(rows)
    fp = os.path.join(TMP, "F_crossmonth.xlsx")
    src.to_excel(fp, index=False)
    for tag, mod in (("Ultra", U), ("Fusion", F)):
        res, data, provider, err = run_pipeline(mod, fp, "Sheet1")
        if err:
            check(f"F1 跨月分组 [{tag}]", False, err)
            continue
        months = sorted({s["month_str"] for s in res})
        check(f"F1 跨月分组 [{tag}]", months == ["2026-10", "2026-11"], f"月份={months}")


# ────────────────────────────────────────────────────────────
# G. 数值保真
# ────────────────────────────────────────────────────────────
def group_G():
    print("\n" + "=" * 74 + "\nG. 数值保真\n" + "=" * 74)
    # G1 小数量级不丢精度
    rows = []
    for d in range(2):
        for i in range(96):
            h, m = divmod(i * 15, 60)
            rows.append({"日期": f"2026-10-{d + 1:02d}", "时间": f"{h:02d}:{m:02d}",
                         "正向有功功率": round(0.5 + i * 0.001, 6)})
    src = pd.DataFrame(rows)
    fp = os.path.join(TMP, "G_small.xlsx")
    src.to_excel(fp, index=False)
    want = expected_from_long(src)
    for tag, mod in (("Ultra", U), ("Fusion", F)):
        res, data, provider, err = run_pipeline(mod, fp, "Sheet1")
        if err:
            check(f"G1 小数精度 [{tag}]", False, err)
            continue
        assert_matrix(f"G1 小数精度 [{tag}]", want, data, tol=1e-6)

    # G2 负值（光伏倒送）完整保留
    src2 = long_df(kind="pv")
    fp2 = os.path.join(TMP, "G_negative.xlsx")
    src2.to_excel(fp2, index=False)
    want2 = expected_from_long(src2)
    for tag, mod in (("Ultra", U), ("Fusion", F)):
        res, data, provider, err = run_pipeline(mod, fp2, "Sheet1")
        if err:
            check(f"G2 负值保留 [{tag}]", False, err)
            continue
        arr = np.asarray(data["load_array"], dtype=float)
        neg = int(np.sum(arr < 0))
        check(f"G2 负值保留 [{tag}]", neg > 0, f"负值点数={neg}")
        assert_matrix(f"G2 负值数值 [{tag}]", want2, data)

    # G3 空值行：应丢弃而非补 0
    src3 = long_df(days=2)
    src3.loc[5, "正向有功功率"] = np.nan
    fp3 = os.path.join(TMP, "G_nan.xlsx")
    src3.to_excel(fp3, index=False)
    for tag, mod in (("Ultra", U), ("Fusion", F)):
        res, data, provider, err = run_pipeline(mod, fp3, "Sheet1")
        if err:
            check(f"G3 空值处理 [{tag}]", False, err)
            continue
        arr = np.asarray(data["load_array"], dtype=float)
        # 空值点应为 NaN，不得被填成 0
        zero_count = int(np.sum(arr == 0))
        check(f"G3 空值不补 0 [{tag}]", zero_count == 0, f"0 值点数={zero_count}")


# ────────────────────────────────────────────────────────────
# H. 脏数据端到端
# ────────────────────────────────────────────────────────────
def group_H():
    print("\n" + "=" * 74 + "\nH. 脏数据端到端\n" + "=" * 74)
    # H1 时间戳重复
    src = long_df(days=1)
    dup = src.iloc[:8].copy()
    src2 = pd.concat([src, dup], ignore_index=True)
    fp = os.path.join(TMP, "H_dup.xlsx")
    src2.to_excel(fp, index=False)
    for tag, mod in (("Ultra", U), ("Fusion", F)):
        res, data, provider, err = run_pipeline(mod, fp, "Sheet1")
        if err:
            check(f"H1 重复时间戳 [{tag}]", False, err)
            continue
        arr = np.asarray(data["load_array"], dtype=float)
        check(f"H1 重复时间戳去重 [{tag}]", arr.shape == (1, 96), f"shape={arr.shape}")

    # H2 日期格式混杂（斜线 + 中文）
    src3 = long_df(days=2, start_day=1)
    src3.loc[src3.index % 3 == 0, "日期"] = "2026/10/01"
    src3.loc[src3.index % 3 == 1, "日期"] = "2026年10月01日"
    src3.loc[src3.index % 3 == 2, "日期"] = "20261001"
    fp2 = os.path.join(TMP, "H_mixeddate.xlsx")
    src3.to_excel(fp2, index=False)
    for tag, mod in (("Ultra", U), ("Fusion", F)):
        res, data, provider, err = run_pipeline(mod, fp2, "Sheet1")
        if err:
            check(f"H2 日期格式混杂 [{tag}]", False, err)
            continue
        arr = np.asarray(data["load_array"], dtype=float)
        # 三种写法都是 10-01，应合并成 1 天
        check(f"H2 日期格式混杂合并 [{tag}]", arr.shape[0] == 1, f"天数={arr.shape[0]}")

    # H3 部分行日期无法解析
    src4 = long_df(days=2, start_day=1)
    src4.loc[src4.index[:10], "日期"] = "坏日期"
    fp3 = os.path.join(TMP, "H_baddate.xlsx")
    src4.to_excel(fp3, index=False)
    for tag, mod in (("Ultra", U), ("Fusion", F)):
        res, data, provider, err = run_pipeline(mod, fp3, "Sheet1")
        if err:
            check(f"H3 坏日期丢弃 [{tag}]", False, err)
            continue
        arr = np.asarray(data["load_array"], dtype=float)
        check(f"H3 坏日期丢弃 [{tag}]", arr.shape[0] == 2, f"天数={arr.shape[0]}")


# ────────────────────────────────────────────────────────────
# I. 双端一致
# ────────────────────────────────────────────────────────────
def group_I():
    print("\n" + "=" * 74 + "\nI. 双端一致\n" + "=" * 74)
    for label, kind in (("工业双峰", "industrial"), ("光伏倒送", "pv"), ("商业单峰", "commercial")):
        fp = os.path.join(TMP, f"I_{kind}.xlsx")
        want = expected_from_long(long_df(kind=kind))
        long_df(kind=kind).to_excel(fp, index=False)
        mats = {}
        for tag, mod in (("Ultra", U), ("Fusion", F)):
            res, data, provider, err = run_pipeline(mod, fp, "Sheet1")
            mats[tag] = None if err else np.asarray(data["load_array"], dtype=float)
        if mats["Ultra"] is None or mats["Fusion"] is None:
            check(f"I {label} 双端一致", False, "一侧加载失败")
            continue
        if mats["Ultra"].shape != mats["Fusion"].shape:
            check(f"I {label} 双端一致", False, f"shape {mats['Ultra'].shape} vs {mats['Fusion'].shape}")
            continue
        diff = np.nanmax(np.abs(mats["Ultra"] - mats["Fusion"]))
        check(f"I {label} 双端一致", diff <= 1e-9, f"最大偏差 {diff:.6g}")


# ────────────────────────────────────────────────────────────
GROUPS = {"A": group_A, "B": group_B, "C": group_C, "D": group_D,
          "E": group_E, "F": group_F, "G": group_G, "H": group_H, "I": group_I}

ONLY = next((a for a in sys.argv[1:] if a in "ABCDEFGHI" and len(a) == 1), None)

try:
    for k, fn in GROUPS.items():
        if ONLY and k != ONLY:
            continue
        try:
            fn()
        except Exception:
            import traceback
            traceback.print_exc()
            check(f"组 {k} 执行异常", False, "见上方 traceback")
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print()
print("=" * 74)
print(f"结果：PASS {PASS}   FAIL {FAIL}")
print("=" * 74)
if FAILS:
    print("\n失败项：")
    for n, d in FAILS:
        print(f"  - {n}  {d}")
sys.exit(1 if FAIL else 0)
