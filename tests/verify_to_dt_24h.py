# -*- coding: utf-8 -*-
"""`_to_dt` 的 24:00 与无年份日期组合。

缺陷（v2.3.11 之前）
--------------------
日期列是**无年份**的 `01-01` / `1/1`，时间为 `24:00` 时：

    needs_year 命中 → 拼成 '2026/01-01 24:00'   （用 '/' 连接，月份仍用 '-'）
    mask_24 命中  → 用 r'(\\d{4}-\\d{2}-\\d{2})' 提日期
    提取不到（是 '2026/01-01'，混合分隔符）→ NaT
    → 该行被 dropna(subset=['__dt__']) 丢掉

后果：每天最后一个采样点消失，96 点变 95 点，日均值与时段边界都偏一格。
而且只在「有 24:00 且日期无年份」这一种组合下发生——同批文件有的正常有的丢点。

跑法：python tests/verify_to_dt_24h.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd  # noqa: E402

from core.ingest import _to_dt  # noqa: E402

YEAR = pd.Timestamp.now().year
PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}  {detail}")


def nat_count(vals):
    return int(pd.Series(_to_dt(pd.Series(vals))).isna().sum())


# ① 无年份 + 24:00 —— 曾经全身 NaT
check("无年份 '01-01 24:00' 不产生 NaT", nat_count(["01-01 24:00", "01-02 00:00"]) == 0,
      f"NaT={nat_count(['01-01 24:00', '01-02 00:00'])}")
check("无年份 '1/1 24:00' 不产生 NaT", nat_count(["1/1 24:00"]) == 0)
check("无年份 '01.01 24:00' 不产生 NaT", nat_count(["01.01 24:00"]) == 0)

# ② 24:00 归到次日
r = _to_dt(pd.Series(["01-31 24:00"]))
check("'01-31 24:00' → 次月 1 日 00:00",
      r.iloc[0] == pd.Timestamp(year=YEAR, month=2, day=1),
      str(r.iloc[0]))

# ③ 有年份时一直正常，不能被改坏
check("有年份 '2026-01-01 24:00' 不产生 NaT", nat_count(["2026-01-01 24:00"]) == 0)
r2 = _to_dt(pd.Series(["2026-01-01 24:00"]))
check("有年份 24:00 也归次日",
      r2.iloc[0] == pd.Timestamp("2026-01-02 00:00:00"), str(r2.iloc[0]))

# ④ 普通日期与无年份非 24:00 仍按今年补
r3 = _to_dt(pd.Series(["7-1 08:00"]))
check("'7-1 08:00' → 今年 7 月 1 日 08:00",
      r3.iloc[0] == pd.Timestamp(year=YEAR, month=7, day=1, hour=8), str(r3.iloc[0]))

# ⑤ 混合批次：不应出现「部分正常部分丢点」
mixed = ["01-01 23:45", "01-01 24:00", "01-02 00:00", "01-02 00:15"]
check("混合批次零 NaT", nat_count(mixed) == 0, f"NaT={nat_count(mixed)}")

# ⑥ 非法值仍然被标为 NaT（不能为了消除 NaT 把错误藏起来）
check("真非法值仍为 NaT", nat_count(["不是日期"]) == 1)

print()
print(f"结果：PASS {PASS}   FAIL {FAIL}")
sys.exit(1 if FAIL else 0)
