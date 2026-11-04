# -*- coding: utf-8 -*-
"""点序号列名（1..96）必须被识别成采样点，而不是剔除（P2-15 返工）。

背景
----
第一版修法是「整组纯数字且 max>24 → 全部剔除」，结果是：

    _valid_time_cols(['1'..'96']) -> 0 列
    扫描 1..96 列名的宽表        -> {'skipped': 'no_valid_rows'}

两个问题：① 以前能出图（虽然只画前 24 列），现在完全不能用；
② `no_valid_rows` 的文案是「所有行日期都无法识别」，把用户引去查日期，
而真正的问题在列名。

改为「识别点序号」：
    1..96   → 96 点，15 分钟粒度 → 00:00 … 23:45
    1..48   → 48 点，30 分钟粒度
    1..288  → 288 点，5 分钟粒度
    1..100  → 100 点无法均分 24 小时 → 明确报错（文案点名列名与点数）

跑法：python tests/verify_point_sequence.py
"""
import logging
import os
import shutil
import sys
import tempfile

logging.disable(logging.CRITICAL)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd  # noqa: E402

from core.ingest import (  # noqa: E402
    _scan_wide_chunk, _time_labels_for_cols, _valid_time_cols,
    _wide_row_accumulated_to_power,
)
from core.models import GroupSummary  # noqa: E402
from services.provider import DataProvider  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}  {detail}")


# ── 1. _valid_time_cols 五种输入 ────────────────────────────
seq96 = [str(i) for i in range(1, 97)]
r96 = _valid_time_cols(seq96)
check("1..96 → 96 列", len(r96) == 96, len(r96))
check("1..96 按数值排序（首 '1' 末 '96'）", r96[:1] == ["1"] and r96[-1:] == ["96"], (r96[:3], r96[-3:]))

r48 = _valid_time_cols([str(i) for i in range(1, 49)])
check("1..48 → 48 列", len(r48) == 48, len(r48))

r288 = _valid_time_cols([str(i) for i in range(1, 289)])
check("1..288 → 288 列", len(r288) == 288, len(r288))

r24 = _valid_time_cols([str(i) for i in range(1, 25)])
check("1..24 → 24 列（保持原行为）", len(r24) == 24, len(r24))

clock = [f"{i // 4:02d}:{(i % 4) * 15:02d}" for i in range(96)]
check("HH:MM 96 列 → 96 列", len(_valid_time_cols(clock)) == 96)

# ── 2. 标签推算 ─────────────────────────────────────────────
lab96, err96 = _time_labels_for_cols(r96)
check("1..96 标签无错误", err96 is None, err96)
check("1..96 前 3 个 00:00/00:15/00:30", lab96[:3] == ["00:00", "00:15", "00:30"], lab96[:3])
check("1..96 后 3 个 23:15/23:30/23:45", lab96[-3:] == ["23:15", "23:30", "23:45"], lab96[-3:])

lab48, err48 = _time_labels_for_cols(r48)
check("1..48 首 00:00 / 末 23:30", err48 is None and lab48[0] == "00:00" and lab48[-1] == "23:30",
      (lab48[:2], lab48[-2:]) if lab48 else err48)

lab288, err288 = _time_labels_for_cols(r288)
check("1..288 第 2 个 00:05 / 末 23:55", err288 is None and lab288[1] == "00:05" and lab288[-1] == "23:55",
      (lab288[:2], lab288[-2:]) if lab288 else err288)

lab100, err100 = _time_labels_for_cols([str(i) for i in range(1, 101)])
check("1..100 不能均分 24 小时 → 报错", lab100 is None and err100 is not None, err100)
if err100:
    check("报错文案点名列名与点数",
          "1..100" in err100 and "100 点" in err100 and "HH:MM" in err100, err100)

# ── 3. 1..96 列名的宽表：完整扫描 + 加载标签 ─────────────────
tmp = tempfile.mkdtemp(prefix="loomgrid_pointseq_")
try:
    fp = os.path.join(tmp, "点序列表.xlsx")
    times = [f"{i // 4:02d}:{(i % 4) * 15:02d}" for i in range(96)]
    row = {str(i + 1): 100.0 + i for i in range(96)}
    row["日期"] = "2026-01-01"
    pd.DataFrame([row]).to_excel(fp, index=False)

    mapping = {"date_col": "日期", "load_col": [str(i) for i in range(1, 97)],
               "format_type": "wide", "header_row": 0, "time_col": None,
               "delimiter": ","}
    s = GroupSummary(fp, "Sheet1", "未命名", "2026-01", 1, 96, 0.0, 0.0, mapping)

    # 先走扫描阶段：产出 1 天 / 96 点
    stats = {}
    _scan_wide_chunk(fp, "Sheet1", mapping, pd.read_excel(fp), "", stats, None)
    st = stats.get(("2026-01", "未命名"))
    check("扫描阶段：1 天 / 96 点",
          st is not None and st["date_count"] == 1 and len(st["time_set"]) == 96,
          st if st is None else (st["date_count"], len(st["time_set"])))

    data = DataProvider().get_group_data(s)
    check("1..96 宽表能取到数据", data is not None, data)
    if data:
        tl = data["time_labels"]
        check("加载后 96 个时间标签", len(tl) == 96, len(tl))
        check("标签前 3 个 00:00/00:15/00:30", tl[:3] == ["00:00", "00:15", "00:30"], tl[:3])
        check("标签后 3 个 23:15/23:30/23:45", tl[-3:] == ["23:15", "23:30", "23:45"], tl[-3:])
        check("负荷列数 96", data["load_array"].shape == (1, 96), data["load_array"].shape)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ── 4. 累积示数 + 点序号列名 ────────────────────────────────
acc = pd.DataFrame([[float(i) for i in range(1, 97)]], columns=[str(i) for i in range(1, 97)])
out = _wide_row_accumulated_to_power(acc, [str(i) for i in range(1, 97)])
check("累积示数点序号列差出 95 列", out.shape == (1, 95), out.shape)
check("差分后首列是 '2'", list(out.columns)[:1] == ["2"], list(out.columns)[:3])

# ── 5. 回归：HH:MM 列名标签不变 ─────────────────────────────
lab_clock, err_clock = _time_labels_for_cols(clock)
check("HH:MM 列名标签无错误且首末正确",
      err_clock is None and lab_clock[0] == "00:00" and lab_clock[-1] == "23:45",
      (lab_clock[:2], lab_clock[-2:]) if lab_clock else err_clock)

print()
print(f"结果：PASS {PASS}   FAIL {FAIL}")
sys.exit(1 if FAIL else 0)
