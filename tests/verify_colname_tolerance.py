# -*- coding: utf-8 -*-
"""列名容错：前后空格、全角括号、全角冒号、全角数字。

为什么这条重要
--------------
Excel 列名带前后空格、或者用户用中文输入法打出全角括号（「负荷（kW）」），
都是极常见的情况。而前置检查原本用精确匹配 `c not in df.columns`：

    实际列名: " 采集时间 "、"负荷（kW）"
    mapping:  "采集时间"、"负荷(kW)"
    结果:      未找到所需列，请检查列名是否有多余空格或全角符号

反过来教育用户"去检查列名"，而 _safe_get_col 本来就有归一化能力——
只是永远跑不到，因为前置检查在它之前就 return 了。

修法：加 _resolve_col()，只做「完全等价」归一化（strip + 全角转半角 + 括号折叠），
不做模糊匹配（模糊留给 _safe_get_col，前置检查不能因为相似就放行）。
并把三处精确匹配换成它：_parse_long_to_standard、_scan_long_chunk、_scan_file_worker。

跑法：python tests/verify_colname_tolerance.py
"""
import os
import shutil
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd  # noqa: E402

from core.ingest import (  # noqa: E402
    _parse_long_to_standard, _resolve_col, _scan_file_worker,
)

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


def scan(fp, sheet="Sheet1", **kw):
    m = {"date_col": "采集时间", "time_col": "采集时间", "load_col": "负荷",
         "asset_col": None, "format_type": "long", "header_row": 0,
         "delimiter": ",", "convert_accumulated": False, "transpose": False}
    m.update(kw)
    return _scan_file_worker((fp, sheet, m, None, threading.Event(), None))


def write(tmp, name, date_hdr, load_hdr):
    rows = [{date_hdr: f"2026-07-01 {t}", load_hdr: 100 + p}
            for p, t in enumerate(TIMES[:8])]
    p = os.path.join(tmp, name)
    pd.DataFrame(rows).to_excel(p, index=False)
    return p


# ══════════════════════════════════════════════════════════
print("=" * 74)
print("① _resolve_col 单元行为")
print("=" * 74)
df = pd.DataFrame(columns=[" 采集时间 ", "负荷（kW）", " 备注"])
check("精确名命中", _resolve_col(df, " 采集时间 ") == " 采集时间 ")
check("前后空格容错", _resolve_col(df, "采集时间") == " 采集时间 ", "未命中")
check("全角括号容错", _resolve_col(df, "负荷(kW)") == "负荷（kW）", "未命中")
check("不存在的列返回 None", _resolve_col(df, "完全不存在") is None)
check("不做模糊匹配（差一个字不放过）", _resolve_col(df, "采集时") is None)

# ══════════════════════════════════════════════════════════
print()
print("=" * 74)
print("② 端到端：各种列名变体都能扫出正确数据")
print("=" * 74)
tmp = tempfile.mkdtemp(prefix="colname_")
try:
    CASES = [
        ("精确名", "采集时间", "负荷", "采集时间", "负荷"),
        ("日期列带空格", " 采集时间 ", "负荷", "采集时间", "负荷"),
        ("负荷列带空格", "采集时间", "负荷 ", "采集时间", "负荷"),
        ("全角括号", "采集时间", "负荷（kW）", "采集时间", "负荷(kW)"),
        ("全角冒号", "采集时间：", "负荷", "采集时间:", "负荷"),
        ("全角数字", "采集时间", "负荷", "采集时间", "负荷"),
        ("两者都有", " 采集时间 ", "负荷（kW）", "采集时间", "负荷(kW)"),
    ]
    for tag, dh, lh, md, ml in CASES:
        fp = write(tmp, f"{tag}.xlsx", dh, lh)
        r = scan(fp, **{"date_col": md, "time_col": md, "load_col": ml})
        if isinstance(r, list) and r:
            ok = (r[0]["date_count"] == 1 and r[0]["time_points"] == 8
                  and abs(r[0]["min_val"] - 100) < 1e-6
                  and abs(r[0]["max_val"] - 107) < 1e-6)
            check(f"{tag}（{dh!r} / {lh!r}）", ok, str(r[0]))
        else:
            check(f"{tag}（{dh!r} / {lh!r}）", False, str(r)[:100])

    # ══════════════════════════════════════════════════════
    print()
    print("=" * 74)
    print("③ 真正缺列时必须报错，不能因为容错而静默通过")
    print("=" * 74)
    fp = write(tmp, "missing.xlsx", "采集时间", "负荷")
    r = scan(fp, **{"date_col": "采集时间", "time_col": "采集时间",
                    "load_col": "完全不存在"})
    check("缺负荷列 → 返回 error", isinstance(r, dict) and "error" in r, str(r)[:100])

    r = scan(fp, **{"date_col": "不存在的日期列", "time_col": "不存在的日期列",
                    "load_col": "负荷"})
    check("缺日期列 → 返回 error", isinstance(r, dict) and "error" in r, str(r)[:100])

    # ══════════════════════════════════════════════════════
    print()
    print("=" * 74)
    print("④ 全角数字/字母列名")
    print("=" * 74)
    rows = [{"采集时间": f"2026-07-01 {t}", "负荷１": 100 + p}
            for p, t in enumerate(TIMES[:8])]
    fp = os.path.join(tmp, "fullwidth_num.xlsx")
    pd.DataFrame(rows).to_excel(fp, index=False)
    r = scan(fp, **{"date_col": "采集时间", "time_col": "采集时间",
                    "load_col": "负荷1"})
    check("全角数字 负荷１ vs 负荷1",
          isinstance(r, list) and r and r[0]["date_count"] == 1, str(r)[:100])

    # 直接测解析函数（绕过扫描层）
    df2 = pd.DataFrame([{" 时间 ": "2026-07-01 00:00", "负荷（kW）": 100.0},
                        {" 时间 ": "2026-07-01 00:15", "负荷（kW）": 110.0}])
    out, ratio = _parse_long_to_standard(df2, "时间", "时间", "负荷(kW)")
    check("直接调 _parse_long_to_standard 也能容错", len(out) == 2, f"行数 {len(out)}")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
print("=" * 74)
print(f"结果：PASS {PASS}   FAIL {FAIL}")
print("=" * 74)
sys.exit(1 if FAIL else 0)
