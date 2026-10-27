# -*- coding: utf-8 -*-
"""数据接入层测试基线：表型识别、列分类、日期时间解析、脏数据处理。

为什么单独建这个
----------------
第三轮审查发现，三轮里最重的五条问题（N6 多值列长表误判、N9 词表错乱等）
全部出在数据接入层，而那里此前没有任何断言。这个文件把「什么表应该被识别成什么」
固定下来——读表错了不会报错，只会安静地画一条错误的曲线，然后基于它算出错误容量。

覆盖范围
--------
A 表型识别   B 列名变体   C 日期解析   D 时间解析   E 示数转换   F 脏数据   G 双端一致

用法
----
    python tests/verify_ingest.py            # 全部
    python tests/verify_ingest.py -v         # 打印每项明细
    python tests/verify_ingest.py A          # 只跑某一组
"""
from __future__ import annotations

import importlib.util
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

ULTRA = os.path.join(ROOT, "负荷曲线批量生成Ultra.py")
FUSION = os.path.join(ROOT, "负荷曲线批量生成工具 Pro 融合版 (v2.3).py")

VERBOSE = "-v" in sys.argv
ONLY = next((a for a in sys.argv[1:] if a in "ABCDEFGHI" and len(a) == 1), None)

PASS = FAIL = 0
FAILS = []


def ok(name, detail=""):
    global PASS
    PASS += 1
    if VERBOSE:
        print(f"[PASS] {name}  {detail}")


def bad(name, detail=""):
    global FAIL
    FAIL += 1
    FAILS.append((name, detail))
    print(f"[FAIL] {name}  {detail}")


def check(name, cond, detail=""):
    (ok if cond else bad)(name, detail)


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


print("加载主程序…", flush=True)
U = load_module(ULTRA, "lg_ingest")
print("加载 Fusion…", flush=True)
F = load_module(FUSION, "lgf_ingest")

# ────────────────────────────────────────────────────────────
# 场景构造工具
# ────────────────────────────────────────────────────────────
LABELS96 = [f"{(i * 15) // 60}:{(i * 15) % 60:02d}" for i in range(96)]


def long_df(cols_load=("正向有功功率",), n_days=2, n_pts=4, extra_cols=None):
    """构造长表：日期 + 时间 + 若干负荷列。"""
    rows = []
    for d in range(n_days):
        for i in range(n_pts):
            h, m = divmod(i * (24 * 60 // n_pts), 60)
            row = {"日期": f"2026-10-{d + 1:02d}", "时间": f"{h:02d}:{m:02d}"}
            for c in cols_load:
                row[c] = 500.0 + i + d
            if extra_cols:
                row.update(extra_cols)
            rows.append(row)
    return pd.DataFrame(rows)


def wide_df(n_pts=4, n_days=2):
    data = {"日期": [f"2026-10-{d + 1:02d}" for d in range(n_days)]}
    for i in range(n_pts):
        h, m = divmod(i * (24 * 60 // n_pts), 60)
        data[f"{h}:{m:02d}"] = [500.0 + i + d for d in range(n_days)]
    return pd.DataFrame(data)


def transposed_df(n_days=2, n_pts=2):
    data = {"时间": []}
    for i in range(n_pts):
        h, m = divmod(i * (24 * 60 // n_pts), 60)
        data["时间"].append(f"{h:02d}:{m:02d}")
    for d in range(n_days):
        data[f"2026-10-{d + 1:02d}"] = [500.0 + i for i in range(n_pts)]
    return pd.DataFrame(data)


def classify(mod, df):
    return mod._smart_classify_columns(list(df.columns), df)


# ────────────────────────────────────────────────────────────
# A. 表型识别
# ────────────────────────────────────────────────────────────
def group_A():
    print("\n" + "=" * 74)
    print("A. 表型识别")
    print("=" * 74)
    cases = [
        ("A1 标准长表", long_df(), "long", "正向有功功率"),
        ("A2 多值列长表(+无功+电流)", long_df(("正向有功功率", "四象限无功功率", "A相电流")), "long", "正向有功功率"),
        ("A3 多值列长表(+功率因数+频率+温度)", long_df(("正向有功功率", "功率因数", "频率", "温度")), "long", "正向有功功率"),
        ("A4 宽表(日期+时刻列)", wide_df(), "wide", None),
        ("A5 示数表(时间+日期列)", transposed_df(), "wide_transposed", None),
        ("A6 长表带资产列", long_df(extra_cols={"资产编号": "M001"}), "long", "正向有功功率"),
    ]
    for name, df, want_fmt, want_load in cases:
        for tag, mod in (("Ultra", U), ("Fusion", F)):
            try:
                r = classify(mod, df)
                fmt = r.get("format_type")
                load = r.get("load_col")
                good = (fmt == want_fmt)
                if want_load:
                    good = good and (load == want_load if isinstance(load, str) else want_load in (load or []))
                check(f"{name} [{tag}]", good,
                      f"format={fmt}(期望 {want_fmt}) load={load}" + (f"(期望 {want_load})" if want_load else ""))
            except Exception as e:
                bad(f"{name} [{tag}]", f"{type(e).__name__}: {e}")


# ────────────────────────────────────────────────────────────
# B. 列名变体
# ────────────────────────────────────────────────────────────
def group_B():
    print("\n" + "=" * 74)
    print("B. 列名变体")
    print("=" * 74)
    cases = [
        ("B1 英文列名", ["date", "time", "load_kw"], "load_kw"),
        ("B2 中英混合", ["日期", "time", "有功功率(kW)"], "有功功率(kW)"),
        ("B3 带全角括号", ["日期", "时间", "正向有功功率（kW）"], "正向有功功率（kW）"),
        ("B4 干扰列在左(无功)", ["日期", "时间", "四象限无功功率", "正向有功功率"], "正向有功功率"),
        ("B5 干扰列在左(电流)", ["日期", "时间", "A相电流", "正向有功功率"], "正向有功功率"),
        ("B6 纯无功表(无有功)", ["日期", "时间", "四象限无功功率"], "四象限无功功率"),
    ]
    for name, cols, want_load in cases:
        df = pd.DataFrame({c: ([f"2026-10-0{i+1}" for i in range(2)] if "日期" in c or c == "date"
                               else (["00:00", "00:15"] if "时间" in c or c == "time"
                                     else [500.0, 505.0])) for c in cols})
        for tag, mod in (("Ultra", U), ("Fusion", F)):
            try:
                r = classify(mod, df)
                load = r.get("load_col")
                got = load if isinstance(load, str) else (load[0] if load else None)
                check(f"{name} [{tag}]", got == want_load, f"load_col={got}（期望 {want_load}）")
            except Exception as e:
                bad(f"{name} [{tag}]", f"{type(e).__name__}: {e}")


# ────────────────────────────────────────────────────────────
# C. 日期解析
# ────────────────────────────────────────────────────────────
def group_C():
    print("\n" + "=" * 74)
    print("C. 日期解析")
    print("=" * 74)
    cases = [
        ("C1 ISO", "2026-10-01", "2026-10-01"),
        ("C2 斜线", "2026/10/1", "2026-10-01"),
        ("C3 点号", "2026.10.01", "2026-10-01"),
        ("C4 中文", "2026年10月1日", "2026-10-01"),
        ("C5 8 位数字", "20261001", "2026-10-01"),
        ("C6 带时间", "2026-10-01 00:00:00", "2026-10-01"),
    ]
    for name, raw, want in cases:
        for tag, mod in (("Ultra", U), ("Fusion", F)):
            try:
                got = mod._parse_date_flexible(raw)
                gs = str(got) if got is not None else "None"
                check(f"{name} [{tag}]", gs == want, f"{raw!r} -> {gs}（期望 {want}）")
            except Exception as e:
                bad(f"{name} [{tag}]", f"{type(e).__name__}: {e}")


# ────────────────────────────────────────────────────────────
# D. 时间解析
# ────────────────────────────────────────────────────────────
def group_D():
    print("\n" + "=" * 74)
    print("D. 时间解析")
    print("=" * 74)
    cases = [
        ("D1 HH:MM", "00:15", 15),
        ("D2 HH:MM:SS", "00:15:00", 15),
        ("D3 中文时分", "12时30分", 750),
        ("D4 整点", "08:00", 480),
        ("D5 中文时", "8时", 480),
    ]
    for name, raw, want in cases:
        for tag, mod in (("Ultra", U), ("Fusion", F)):
            try:
                got = mod._parse_min(raw)
                check(f"{name} [{tag}]", got == want, f"{raw!r} -> {got}（期望 {want} 分钟）")
            except Exception as e:
                bad(f"{name} [{tag}]", f"{type(e).__name__}: {e}")
    # 浮点小时
    for tag, mod in (("Ultra", U), ("Fusion", F)):
        try:
            got = mod._parse_min(0.25)
            check(f"D6 浮点小时 [{tag}]", got == 15, f"0.25 -> {got}（期望 15 分钟）")
        except Exception as e:
            bad(f"D6 浮点小时 [{tag}]", f"{type(e).__name__}: {e}")


# ────────────────────────────────────────────────────────────
# E. 示数转换
# ────────────────────────────────────────────────────────────
def group_E():
    print("\n" + "=" * 74)
    print("E. 累积示数转换")
    print("=" * 74)

    def convert(mod, df):
        """兼容两种签名：Ultra 返回 (df, failed) 元组，Fusion 返回单值 df。"""
        res = mod._convert_accumulated_to_power(df.copy(), "日期", "时间", "总电量")
        if isinstance(res, tuple):
            return res[0], bool(res[1])
        return res, False

    # E1 日重置（基准非 0）：全天 96 点，每 15 分钟 +10 kWh → 平均功率 ≈40 kW
    rows = []
    for d in (1, 2):
        for i in range(96):
            h, m = divmod(i * 15, 60)
            rows.append({"日期": f"2026-10-{d:02d}", "时间": f"{h:02d}:{m:02d}",
                         "总电量": 1000.0 + i * 10.0})
    df = pd.DataFrame(rows)
    for tag, mod in (("Ultra", U), ("Fusion", F)):
        try:
            out, failed = convert(mod, df)
            vals = pd.to_numeric(out["总电量"], errors="coerce").dropna().values
            med = float(np.median(vals)) if len(vals) else float("nan")
            check(f"E1 日重置表(基准非0)转 40kW [{tag}]",
                  (not failed) and len(vals) > 0 and abs(med - 40.0) < 1.0,
                  f"failed={failed} 中位功率={med:.2f}（期杲 40.00）点数={len(vals)}")
        except Exception as e:
            bad(f"E1 日重置表(基准非0)转 40kW [{tag}]", f"{type(e).__name__}: {e}")

    # E2 月度累计（跨天不回落）：全天 96 点连续递增，应被拒绝而非静默差分
    rows = []
    v = 5000.0
    for d in (1, 2):
        for i in range(96):
            h, m = divmod(i * 15, 60)
            rows.append({"日期": f"2026-10-{d:02d}", "时间": f"{h:02d}:{m:02d}", "总电量": v})
            v += 10.0
    df2 = pd.DataFrame(rows)
    for tag, mod in (("Ultra", U), ("Fusion", F)):
        try:
            out, failed = convert(mod, df2)
            vals = pd.to_numeric(out["总电量"], errors="coerce").dropna().values
            # 拒绝时值应保持原量级（数千）；错误差分时中位数会落到 40 左右
            kept = len(vals) > 0 and float(np.median(vals)) > 1000.0
            check(f"E2 月度累计表被拒绝 [{tag}]", bool(failed) or kept,
                  f"failed={failed} 中位数={float(np.median(vals)) if len(vals) else 'NA'}")
        except Exception as e:
            bad(f"E2 月度累计表被拒绝 [{tag}]", f"{type(e).__name__}: {e}")

    # E3 跨零点：全天 96 点，每天凌晨从 200 起升到高位，跨天回落
    rows = []
    for d in (1, 2):
        for i in range(96):
            h, m = divmod(i * 15, 60)
            rows.append({"日期": f"2026-10-{d:02d}", "时间": f"{h:02d}:{m:02d}",
                         "总电量": 200.0 + i * 25.0})
    df3 = pd.DataFrame(rows)
    for tag, mod in (("Ultra", U), ("Fusion", F)):
        try:
            out, failed = convert(mod, df3)
            vals = pd.to_numeric(out["总电量"], errors="coerce").dropna().values
            med = float(np.median(vals)) if len(vals) else float("nan")
            # 25 小时 kWh 每 15 分钟 → 100 kW
            check(f"E3 跨零点日重置转 100kW [{tag}]",
                  (not failed) and abs(med - 100.0) < 5.0, f"failed={failed} 中位数={med:.2f}（期杲 100）")
        except Exception as e:
            bad(f"E3 跨零点日重置转 100kW [{tag}]", f"{type(e).__name__}: {e}")


# ────────────────────────────────────────────────────────────
# F. 脏数据
# ────────────────────────────────────────────────────────────
def group_F():
    print("\n" + "=" * 74)
    print("F. 脏数据")
    print("=" * 74)
    # F1 负值（光伏倒送）不应导致识别失败
    df = long_df()
    df.loc[df.index[:2], "正向有功功率"] = -120.0
    for tag, mod in (("Ultra", U), ("Fusion", F)):
        try:
            r = classify(mod, df)
            check(f"F1 负值负荷不崩 [{tag}]", r.get("load_col") is not None, f"load={r.get('load_col')}")
        except Exception as e:
            bad(f"F1 负值负荷不崩 [{tag}]", f"{type(e).__name__}: {e}")

    # F2 空值
    df2 = long_df()
    df2.loc[0, "正向有功功率"] = np.nan
    for tag, mod in (("Ultra", U), ("Fusion", F)):
        try:
            r = classify(mod, df2)
            check(f"F2 含空值不崩 [{tag}]", r.get("format_type") == "long", f"fmt={r.get('format_type')}")
        except Exception as e:
            bad(f"F2 含空值不崩 [{tag}]", f"{type(e).__name__}: {e}")

    # F3 单列全零
    df3 = long_df(("正向有功功率", "备用列"))
    df3["备用列"] = 0.0
    for tag, mod in (("Ultra", U), ("Fusion", F)):
        try:
            r = classify(mod, df3)
            load = r.get("load_col")
            got = load if isinstance(load, str) else (load[0] if load else None)
            check(f"F3 全零列不被选为负荷 [{tag}]", got == "正向有功功率", f"load={got}")
        except Exception as e:
            bad(f"F3 全零列不被选为负荷 [{tag}]", f"{type(e).__name__}: {e}")

    # F4 单行表
    df4 = pd.DataFrame({"日期": ["2026-10-01"], "时间": ["00:00"], "正向有功功率": [500.0]})
    for tag, mod in (("Ultra", U), ("Fusion", F)):
        try:
            r = classify(mod, df4)
            check(f"F4 单行表不崩 [{tag}]", r.get("format_type") is not None, f"fmt={r.get('format_type')}")
        except Exception as e:
            bad(f"F4 单行表不崩 [{tag}]", f"{type(e).__name__}: {e}")


# ────────────────────────────────────────────────────────────
# G. 双端一致
# ────────────────────────────────────────────────────────────
def group_G():
    print("\n" + "=" * 74)
    print("G. 双端一致（Ultra vs Fusion 对同一输入给出相同判定）")
    print("=" * 74)
    cases = [("标准长表", long_df()), ("多值列长表", long_df(("正向有功功率", "四象限无功功率", "A相电流"))),
             ("宽表", wide_df()), ("示数表", transposed_df())]
    for name, df in cases:
        ru, rf = classify(U, df), classify(F, df)
        key = lambda r: (r.get("format_type"), r.get("date_col"), r.get("time_col"),
                         str(r.get("load_col")))
        check(f"G {name}", key(ru) == key(rf), f"Ultra={key(ru)} Fusion={key(rf)}")


# ────────────────────────────────────────────────────────────
def group_H():
    print("\n" + "=" * 74)
    print("H. 真文件编码与格式")
    print("=" * 74)
    import shutil
    import tempfile

    tmp = tempfile.mkdtemp(prefix="lg_ing_")
    base = long_df(n_days=2, n_pts=4)
    try:
        cases = [
            ("H1 CSV utf-8", "t_utf8.csv", "utf-8", ","),
            ("H2 CSV utf-8-sig", "t_sig.csv", "utf-8-sig", ","),
            ("H3 CSV gbk", "t_gbk.csv", "gbk", ","),
            ("H4 CSV gb18030", "t_g18.csv", "gb18030", ","),
            ("H5 CSV 分号分隔", "t_semi.csv", "utf-8", ";"),
            ("H6 CSV tab 分隔", "t_tab.csv", "utf-8", "\t"),
        ]
        for name, fn, enc, sep in cases:
            p = os.path.join(tmp, fn)
            base.to_csv(p, index=False, encoding=enc, sep=sep)
            for tag, mod in (("Ultra", U), ("Fusion", F)):
                try:
                    det = mod._detect_csv_delimiter(p) if hasattr(mod, "_detect_csv_delimiter") else sep
                    out = mod._read_file_full(p, delimiter=det if det else sep)
                    good = (out is not None and len(out) == len(base)
                            and "正向有功功率" in list(out.columns))
                    check(f"{name} [{tag}]", good,
                          f"行={0 if out is None else len(out)}/期杲{len(base)} 分隔符={det!r} 列={list(out.columns)[:3] if out is not None else None}")
                except Exception as e:
                    bad(f"{name} [{tag}]", f"{type(e).__name__}: {e}")

        p7 = os.path.join(tmp, "t.xlsx")
        try:
            base.to_excel(p7, index=False)
        except Exception as e:
            print(f"  (xlsx 写入不可用：{e})")
        if os.path.exists(p7):
            for tag, mod in (("Ultra", U), ("Fusion", F)):
                try:
                    out = mod._read_file_full(p7)
                    good = (out is not None and len(out) == len(base)
                            and "正向有功功率" in list(out.columns))
                    check(f"H7 XLSX [{tag}]", good,
                          f"行={0 if out is None else len(out)} 列={list(out.columns)[:3] if out is not None else None}")
                except Exception as e:
                    bad(f"H7 XLSX [{tag}]", f"{type(e).__name__}: {e}")

        p8 = os.path.join(tmp, "t_e2e.csv")
        long_df(("正向有功功率", "四象限无功功率", "A相电流")).to_csv(p8, index=False, encoding="gbk")
        for tag, mod in (("Ultra", U), ("Fusion", F)):
            try:
                out = mod._read_file_full(p8)
                r = mod._smart_classify_columns(list(out.columns), out)
                good = r.get("format_type") == "long" and r.get("load_col") == "正向有功功率"
                check(f"H8 文件→分类 端到端[{tag}]", good,
                      f"fmt={r.get('format_type')} load={r.get('load_col')}")
            except Exception as e:
                bad(f"H8 文件→分类 端到端[{tag}]", f"{type(e).__name__}: {e}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def group_I():
    print("\n" + "=" * 74)
    print("I. 引擎模式与扩展场景")
    print("=" * 74)
    # I1-I5：professional 路径（_smart_classify_columns 与 basic 是两套独立实现，
    # 修一处不代表修两处——这组就是防这个）
    if hasattr(U, "set_engine_mode"):
        try:
            U.set_engine_mode("professional")
            cases = [
                ("I1 长表", long_df(), "long", "正向有功功率"),
                ("I2 多值列长表", long_df(("正向有功功率", "四象限无功功率", "A相电流")), "long", "正向有功功率"),
                ("I3 宽表", wide_df(), "wide", None),
                ("I4 示数表", transposed_df(), "wide_transposed", None),
            ]
            # 干扰列在左（B4 同型）
            d = pd.DataFrame({"日期": ["2026-10-01"] * 2, "时间": ["00:00", "00:15"],
                              "四象限无功功率": [10.0, 11.0], "正向有功功率": [500.0, 505.0]})
            cases.append(("I5 干扰列在左", d, "long", "正向有功功率"))
            for name, df, want_fmt, want_load in cases:
                try:
                    r = U._smart_classify_columns(list(df.columns), df)
                    good = r.get("format_type") == want_fmt
                    if want_load:
                        lc = r.get("load_col")
                        good = good and (lc == want_load if isinstance(lc, str) else want_load in (lc or []))
                    check(f"{name} [professional]", good,
                          f"fmt={r.get('format_type')} load={r.get('load_col')}")
                except Exception as e:
                    bad(f"{name} [professional]", f"{type(e).__name__}: {e}")
        finally:
            U.set_engine_mode("basic")
    else:
        print("  (Ultra 无 set_engine_mode，跳过)")

    # I6 96 列宽表（15 分钟分辨率宽表，真实报表常见形态）
    df96 = pd.DataFrame({"日期": ["2026-10-01", "2026-10-02"]})
    for lab in LABELS96:
        df96[lab] = [500.0, 510.0]
    for tag, mod in (("Ultra", U), ("Fusion", F)):
        try:
            r = classify(mod, df96)
            check(f"I6 96 列宽表 [{tag}]", r.get("format_type") == "wide", f"fmt={r.get('format_type')}")
        except Exception as e:
            bad(f"I6 96 列宽表 [{tag}]", f"{type(e).__name__}: {e}")

    # I7 1 小时分辨率长表
    dfh = long_df(n_days=2, n_pts=24)
    for tag, mod in (("Ultra", U), ("Fusion", F)):
        try:
            r = classify(mod, dfh)
            check(f"I7 小时级长表 [{tag}]", r.get("format_type") == "long", f"fmt={r.get('format_type')}")
        except Exception as e:
            bad(f"I7 小时级长表 [{tag}]", f"{type(e).__name__}: {e}")

    # I8 多资产长表（园区多表）
    rows = []
    for aid in ("M001", "M002"):
        for i in range(4):
            h, m = divmod(i * 15, 60)
            rows.append({"资产编号": aid, "日期": "2026-10-01", "时间": f"{h:02d}:{m:02d}",
                         "正向有功功率": 300.0 + i})
    dfa = pd.DataFrame(rows)
    for tag, mod in (("Ultra", U), ("Fusion", F)):
        try:
            r = classify(mod, dfa)
            check(f"I8 多资产长表 [{tag}]",
                  r.get("format_type") == "long" and r.get("asset_col") == "资产编号",
                  f"fmt={r.get('format_type')} asset={r.get('asset_col')}")
        except Exception as e:
            bad(f"I8 多资产长表 [{tag}]", f"{type(e).__name__}: {e}")

    # I9 示数表带资产列（每表各自日重置，不能混算差分）
    rows = []
    for aid in ("M001", "M002"):
        for i in range(4):
            h, m = divmod(i * 15, 60)
            rows.append({"资产编号": aid, "日期": "2026-10-01", "时间": f"{h:02d}:{m:02d}",
                         "总电量": 1000.0 + i * 10.0})
    dfma = pd.DataFrame(rows)
    for tag, mod in (("Ultra", U), ("Fusion", F)):
        try:
            res = mod._convert_accumulated_to_power(dfma.copy(), "日期", "时间", "总电量", "资产编号")
            out, failed = (res if isinstance(res, tuple) else (res, False))
            vals = pd.to_numeric(out["总电量"], errors="coerce").dropna().values
            med = float(np.median(vals)) if len(vals) else float("nan")
            # 两表各 3 个有效差分，都是 10kWh/0.25h=40kW；混算会出现 0 或负值
            check(f"I9 多资产示数分表差分 [{tag}]",
                  (not failed) and abs(med - 40.0) < 1.0, f"failed={failed} 中位数={med:.2f}")
        except Exception as e:
            bad(f"I9 多资产示数分表差分 [{tag}]", f"{type(e).__name__}: {e}")

    # I10 时间格式：纯数字槽位与中文时（兼容性探查，不强制成功，但不得报错）
    for tag, mod in (("Ultra", U), ("Fusion", F)):
        try:
            got = mod._parse_min("8时30分")
            check(f"I10 中文时分(带分) [{tag}]", got == 510, f"-> {got}（期杲 510）")
        except Exception as e:
            bad(f"I10 中文时分(带分) [{tag}]", f"{type(e).__name__}: {e}")


GROUPS = {"A": group_A, "B": group_B, "C": group_C, "D": group_D,
          "E": group_E, "F": group_F, "G": group_G, "H": group_H, "I": group_I}

for k, fn in GROUPS.items():
    if ONLY and k != ONLY:
        continue
    try:
        fn()
    except Exception as e:
        import traceback
        traceback.print_exc()
        bad(f"组 {k} 执行异常", str(e))

print()
print("=" * 74)
print(f"结果：PASS {PASS}   FAIL {FAIL}")
print("=" * 74)
if FAILS:
    print("\n失败项：")
    for n, d in FAILS:
        print(f"  - {n}  {d}")
sys.exit(1 if FAIL else 0)
