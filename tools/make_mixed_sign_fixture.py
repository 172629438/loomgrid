# -*- coding: utf-8 -*-
"""造一份「白天正、夜间负」的混合数据，用来验双点对比的异号分支。

为什么需要：现网 16 个测试文件里 15 个全正、1 个全负，**没有混合的**，
所以「A、B 异号不计百分比」那个分支至今「写了没跑」。
真实光伏场景里「白天用电为正、夜间倒送为负」是常态，不是边界臆想。

做法：复制 W1 宽表，把 22:00–06:00 时段的值取负（模拟夜间倒送）。
输出：baseline/_inputs/M1/M1_混合正负.xlsx（放进白名单目录，程序可直接读）

用法：
    python tools/make_mixed_sign_fixture.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import openpyxl  # noqa: E402

SRC = ROOT / "baseline" / "_inputs" / "W1" / "W1_宽表96点.xlsx"
DST_DIR = ROOT / "baseline" / "_inputs" / "M1"
DST = DST_DIR / "M1_混合正负.xlsx"

# 夜间时段（含左不含右，和 core 的取点口径无所谓——这里只改值）
NIGHT = {"22:00", "22:15", "22:30", "22:45", "23:00", "23:15", "23:30", "23:45",
         "00:00", "00:15", "00:30", "00:45", "01:00", "01:15", "01:30", "01:45",
         "02:00", "02:15", "02:30", "02:45", "03:00", "03:15", "03:30", "03:45",
         "04:00", "04:15", "04:30", "04:45", "05:00", "05:15", "05:30", "05:45"}


def norm(v) -> str:
    """把单元格值规范成 HH:MM 字符串（兼容 time / str / datetime）。"""
    if v is None:
        return ""
    if hasattr(v, "strftime"):
        return v.strftime("%H:%M")
    s = str(v).strip()
    return s[:5] if len(s) >= 5 else s


def main() -> int:
    if not SRC.exists():
        print(f"源文件不存在：{SRC}")
        return 1
    DST_DIR.mkdir(parents=True, exist_ok=True)

    wb = openpyxl.load_workbook(SRC)
    ws = wb.active

    # 第 1 行是表头：A1 日期列，B1 起是时刻
    time_cols: list[tuple[int, str]] = []
    for c in range(2, ws.max_column + 1):
        t = norm(ws.cell(row=1, column=c).value)
        if t:
            time_cols.append((c, t))

    night_cols = [(c, t) for c, t in time_cols if t in NIGHT]
    print(f"时刻列 {len(time_cols)} 个，其中夜间 {len(night_cols)} 个将被取负")

    changed = 0
    for r in range(2, ws.max_row + 1):
        for c, t in night_cols:
            cell = ws.cell(row=r, column=c)
            if isinstance(cell.value, (int, float)) and cell.value != 0:
                cell.value = -abs(cell.value)
                changed += 1

    wb.save(DST)
    print(f"改写 {changed} 个单元格 → {DST}")

    # 自检：读回确认同时存在正负
    wb2 = openpyxl.load_workbook(DST)
    ws2 = wb2.active
    vals = []
    for r in range(2, ws2.max_row + 1):
        for c in range(2, ws2.max_column + 1):
            v = ws2.cell(row=r, column=c).value
            if isinstance(v, (int, float)):
                vals.append(v)
    arr = np.array(vals, dtype=float)
    print(f"读回：{arr.size} 个数值，范围 {arr.min():.1f} … {arr.max():.1f}，"
          f"负值 {int((arr < 0).sum())} 个，正值 {int((arr > 0).sum())} 个")
    ok = arr.min() < 0 and arr.max() > 0
    print("结果：" + ("PASS（混合正负成立）" if ok else "FAIL（没造出混合）"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
