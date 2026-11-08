# -*- coding: utf-8 -*-
"""组合规模压测：7 文件 × 5 资产 × 7 个月 = 35 组。

为什么单独立一个
----------------
之前的测试都是单维度：
  · verify_e2e_batch   7 文件，每个只有 1 个资产
  · verify_e2e_forms   单文件，多资产

真实场景是两者相乘：一次拖入 7 个月的文件，每个文件里 5 个回路。

这条路径上最关键的判据是「分组总数是否等于 文件数 × 回路数」：
少一个说明丢组（静默），多一个说明串组（重复算）。两种都不会报错。

断言（7 条）：
  ① 总组数 = 35
  ② 35 个 (月份, 资产) 组合互不重复
  ③ 每月恰好 5 组，每资产恰好 7 组
  ④ 每组 min/max 落在自己资产的值域内（不串组）
  ⑤ 每组 date_count = 该月天数
  ⑥ 单文件损坏不影响其余 6 个

跑法：python tests/verify_combo_scale.py
"""
import os
import shutil
import sys
import tempfile
import threading
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd  # noqa: E402

from core.ingest import _scan_file_worker  # noqa: E402

PASS = FAIL = 0
TIMES = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 15, 30, 45)]
ASSETS = ["赵钢1#线", "赵钢2#线", "精轧1#线", "精轧2#线", "公辅变压器"]
MONTHS = [(2026, 7, 31), (2026, 8, 31), (2026, 9, 30), (2026, 10, 31),
          (2026, 11, 30), (2026, 12, 31), (2027, 1, 31)]

MAPPING = {
    "date_col": "采集时间", "time_col": "采集时间", "load_col": "总有功功率",
    "asset_col": "回路名称", "format_type": "long", "header_row": 0,
    "delimiter": ",", "convert_accumulated": False, "transpose": False,
}


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}  {detail}")


def write_file(tmp, year, month, days):
    rows = []
    for ai, asset in enumerate(ASSETS):
        for d in range(1, days + 1):
            date = f"{year}-{month:02d}-{d:02d}"
            for p, t in enumerate(TIMES):
                # 每个资产值域不同（1000/2000/3000/4000/5000 起），便于验不串组
                rows.append({"采集时间": f"{date} {t}", "回路名称": asset,
                             "总有功功率": (ai + 1) * 1000 + d * 10 + p * 0.1})
    name = f"曲线导出-1-1-{year}-{month:02d}-01,{year}-{month:02d}-{days:02d}.xlsx"
    p = os.path.join(tmp, name)
    pd.DataFrame(rows).to_excel(p, index=False)
    return p


def scan(fp):
    return _scan_file_worker((fp, "Sheet1", MAPPING, None, threading.Event(), None))


tmp = tempfile.mkdtemp(prefix="combo_")
try:
    files = [(write_file(tmp, y, m, d), y, m, d) for y, m, d in MONTHS]
    check(f"{len(files)} 个文件已生成", len(files) == 7)

    all_groups = []
    per_file_ok = True
    for p, y, m, _ in files:
        res = scan(p)
        if not isinstance(res, list) or len(res) != 5:
            per_file_ok = False
            print(f"  {y}-{m:02d} 组数异常: {res if not isinstance(res, list) else len(res)}")
            continue
        all_groups.extend(res)
    check("每个文件都是 5 组", per_file_ok)

    # ① 总组数
    check(f"总组数 35（实 {len(all_groups)}）", len(all_groups) == 35, str(len(all_groups)))

    # ② 组合唯一
    keys = [(g["month_str"], g["asset_value"]) for g in all_groups]
    check("35 个 (月份, 资产) 组合互不重复", len(set(keys)) == 35, f"唯一 {len(set(keys))}")

    # ③ 分布
    per_month = Counter(g["month_str"] for g in all_groups)
    per_asset = Counter(g["asset_value"] for g in all_groups)
    check("每月 5 组", all(v == 5 for v in per_month.values()), str(dict(per_month)))
    check("每资产 7 组", all(v == 7 for v in per_asset.values()), str(dict(per_asset)))

    # ④ 值域不串组
    ok_range = True
    for g in all_groups:
        ai = ASSETS.index(g["asset_value"])
        lo, hi = (ai + 1) * 1000, (ai + 1) * 1000 + 400
        if not (lo <= g["min_val"] <= hi and lo <= g["max_val"] <= hi):
            ok_range = False
            print(f"  {g['month_str']} {g['asset_value']}: "
                  f"min={g['min_val']} max={g['max_val']} 应在 [{lo},{hi}]")
    check("35 组 min/max 全部落在各自资产值域", ok_range)

    # ⑤ 天数
    ok_days = True
    for g in all_groups:
        y, m = g["month_str"].split("-")
        expect = next(d for (yy, mm, d) in MONTHS if yy == int(y) and mm == int(m))
        if g["date_count"] != expect:
            ok_days = False
            print(f"  {g['month_str']} {g['asset_value']}: {g['date_count']} != {expect}")
    check("35 组的天数全部正确", ok_days)

    # ⑥ 单文件损坏不影响其余
    bad = files[3][0]
    with open(bad, "w", encoding="utf-8") as f:
        f.write("坏文件")
    results = [scan(p) for p, _, _, _ in files]
    ok_cnt = sum(1 for r in results if isinstance(r, list) and len(r) == 5)
    err_cnt = sum(1 for r in results if isinstance(r, dict) and "error" in r)
    check(f"6 个正常 + 1 个报错（ok={ok_cnt} err={err_cnt}）",
          ok_cnt == 6 and err_cnt == 1, f"ok={ok_cnt} err={err_cnt}")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
print("=" * 74)
print(f"结果：PASS {PASS}   FAIL {FAIL}")
print("=" * 74)
sys.exit(1 if FAIL else 0)
