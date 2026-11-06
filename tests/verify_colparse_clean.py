# -*- coding: utf-8 -*-
"""列名识别 + 数据清洗：把真实出现过的写法全部锁住。

为什么单独一个文件
------------------
这两块是「入口」，错了之后后面再准也没用。而它们的失败方式全都是静默的：
写不认识 → 返回 NaT / NaN → 整行 dropna → 用户只看到「少了几个点」。

本轮修掉四条（都是实测发现的）：
  ① 「8点30分」—— 正则只写了「时」，同一种写法漏一半
  ② 「0800」「2400」—— 四位纯数字 HHMM
  ③ 「2026年7月1日」「7月1日」—— 中文日期完全不被识别
  ④ 「260701」—— 被 pandas 解成 2001-07-26（不报错但错 25 年）
  ⑤ 「1,234.5」—— 千分位被 to_numeric 变成 NaN，整行丢掉
  ⑥ 「回路名称」—— 资产列词表缺失，多回路数据会混成一条

跑法：python tests/verify_colparse_clean.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd  # noqa: E402

from core.ingest import (  # noqa: E402
    _normalize_time_column_name, _parse_min, _parse_long_to_standard,
    _smart_classify_columns, _to_dt, INVALID_MINUTES,
)

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}  {detail}")


# ══════════════════════════════════════════════════════════
print("=" * 74)
print("① 时间列名 —— 29 种真实写法")
print("=" * 74)
TIMES = {
    "00:00": 0, "0:00": 0, "24:00": 1440, "8:00": 480, "08:00": 480,
    "8:00:00": 480, "08:00:00": 480,
    "8：00": 480, "8：00：00": 480,          # 全角冒号
    "8时": 480, "8点": 480, "8点钟": 480,
    "8时30分": 510, "8点30分": 510,           # ← 本轮修
    "0时": 0, "0点": 0, "零点": 0,             # ← 本轮修
    "23:45": 1425, "23:59": 1439,
    "12:00 AM": 720, "8:00 AM": 480,
    "800": 480, "0800": 480, "2400": 1440,   # ← 本轮修
    "8": 480, "8.5": 510,
    " 08:00 ": 480, "０８：００": 480,         # 空白 / 全角数字
    "8点30": 510, "8时0分": 480,
}
for raw, expect in TIMES.items():
    got = _parse_min(_normalize_time_column_name(raw))
    check(f"{raw!r} → {expect} 分", got == expect, f"实得 {got}")

# ══════════════════════════════════════════════════════════
print()
print("=" * 74)
print("② 日期 —— 24 种写法")
print("=" * 74)
DATES = {
    "2026-07-01": "2026-07-01", "2026-7-1": "2026-07-01",
    "2026/07/01": "2026-07-01", "2026/7/1": "2026-07-01",
    "2026.07.01": "2026-07-01", "2026.7.1": "2026-07-01",
    "20260701": "2026-07-01",
    "260701": "2026-07-01",                   # ← 本轮修（原为 2001-07-26）
    "2026年7月1日": "2026-07-01",              # ← 本轮修
    "2026年07月01日": "2026-07-01",            # ← 本轮修
    "2026年7月": "2026-07-01",                 # ← 本轮修
    "2026-07": "2026-07-01",
    "2026-07-01 00:00:00": "2026-07-01",
    "2026/7/1 0:00": "2026-07-01",
    "2026-07-01T00:00:00": "2026-07-01",
    "2026-Jul-01": "2026-07-01", "01-Jul-2026": "2026-07-01",
    "Jul 1, 2026": "2026-07-01",
}
for raw, expect in DATES.items():
    v = _to_dt(pd.Series([raw])).iloc[0]
    got = "NaT" if pd.isna(v) else v.strftime("%Y-%m-%d")
    check(f"{raw!r} → {expect}", got == expect, f"实得 {got}")

# 无年份：补今年，不断言具体年份，只断言能解析出来
for raw in ("07-01", "7/1", "7-1", "7月1日"):
    v = _to_dt(pd.Series([raw])).iloc[0]
    ok = (not pd.isna(v)) and v.month == 7 and v.day == 1
    check(f"{raw!r} 能解析成 7 月 1 日（补今年）", ok, f"实得 {v}")

# ══════════════════════════════════════════════════════════
print()
print("=" * 74)
print("③ 资产列识别 —— 该识别的识别，不该识别的别乱认")
print("=" * 74)
SHOULD_ASSET = [
    (["时间", "回路名称", "总有功功率"], "回路名称"),
    (["时间", "支路", "功率"], "支路"),
    (["时间", "线路名称", "有功"], "线路名称"),
    (["时间", "出线", "有功功率"], "出线"),
    (["时间", "设备名称", "总有功"], "设备名称"),
    (["时间", "变压器名称", "负荷"], "变压器名称"),
    (["时间", "资产编号", "有功"], "资产编号"),
]
for cols, expect in SHOULD_ASSET:
    r = _smart_classify_columns(cols)
    check(f"{cols} → asset_col={expect}", r.get("asset_col") == expect, str(r))

SHOULD_NOT_ASSET = [
    ["采集时间", "赵钢1#线十总降213#总有功"],   # 用户真实列名
    ["采集时间", "1#线总有功"],
    ["时间", "2#进线有功功率"],                  # 含「进线」但其实是负荷列
    ["时间", "总降213#总有功"],
    ["时刻", "峰段功率"],
    ["时间", "有功功率"],
]
for cols in SHOULD_NOT_ASSET:
    r = _smart_classify_columns(cols)
    check(f"{cols} → asset_col 为空", not r.get("asset_col"), str(r))
    if len(cols) == 2:
        check("  同一组仍选出 load_col", bool(r.get("load_col")), str(r))

# ══════════════════════════════════════════════════════════
print()
print("=" * 74)
print("④ 数据清洗")
print("=" * 74)


def parse(rows, date_col="采集时间", time_col="采集时间", load_col="负荷"):
    df = pd.DataFrame(rows)
    r = _parse_long_to_standard(df, date_col, time_col, load_col)
    out = r[0] if isinstance(r, tuple) else r
    return out


BASE = [{"采集时间": f"2026-07-01 {t}", "负荷": v}
        for t, v in (("00:00", 100), ("00:15", 110), ("00:30", 120))]

# 千分位必须保住
out = parse(list(BASE) + [{"采集时间": "2026-07-01 00:45", "负荷": "1,234.5"}])
check("千分位 '1,234.5' 不被丢弃", len(out) == 4, f"行数 {len(out)}")
if len(out) == 4:
    check("千分位解析成 1234.5",
          abs(float(out["__load__"].iloc[-1]) - 1234.5) < 1e-9,
          str(out["__load__"].tolist()))

# 欧洲小数逗号不能被当成千分位
out = parse(list(BASE) + [{"采集时间": "2026-07-01 00:45", "负荷": "1,5"}])
if len(out) == 4:
    v = float(out["__load__"].iloc[-1])
    check("'1,5' 不被当成 15（保持 NaN 或原样）", not (abs(v - 15.0) < 1e-9), str(v))
else:
    check("'1,5' 不放大成 15（被丢弃也可接受）", True)

# 文本非数值：该丢
out = parse(list(BASE) + [
    {"采集时间": "2026-07-01 00:45", "负荷": "--"},
    {"采集时间": "2026-07-01 01:00", "负荷": "N/A"},
])
check("文本非数值被丢弃（仍 3 行）", len(out) == 3, f"行数 {len(out)}")

# 负数：倒送必须保留
out = parse(list(BASE) + [{"采集时间": "2026-07-01 00:45", "负荷": -50}])
check("负数保留（倒送）", len(out) == 4 and float(out["__load__"].iloc[-1]) == -50.0)

# 空值：该丢
out = parse(list(BASE) + [{"采集时间": "2026-07-01 00:45", "负荷": None}])
check("空值被丢弃", len(out) == 3, f"行数 {len(out)}")

# 日期时间合并列 / 分开两列，两种都要能解析
out = parse([{"日期": "2026-07-01", "时间": t, "负荷": v}
             for t, v in (("00:00", 100), ("00:15", 110))],
            "日期", "时间")
check("日期与时间分两列能解析", len(out) == 2, f"行数 {len(out)}")

# 中文日期走完整清洗链路
out = parse([{"采集时间": f"2026年7月1日 {t}", "负荷": v}
             for t, v in (("00:00", 100), ("00:15", 110))])
check("中文日期走完解析链路（不被整表丢弃）", len(out) == 2, f"行数 {len(out)}")

print()
print("=" * 74)
print(f"结果：PASS {PASS}   FAIL {FAIL}")
print("=" * 74)
sys.exit(1 if FAIL else 0)
