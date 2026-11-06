# -*- coding: utf-8 -*-
"""多文件批量：一次 7 个月，映射一次其余自动套用。

为什么这条最重要
----------------
用户的真实用法不是「一个文件跑一遍」而是「一次拖入 7 个月的文件，
映射第一个，其余自动套用」。这条链此前完全没有端到端测试。

链上有三个环节可能坏：
  ① 映射记忆的匹配 —— 为什么会认为「这个文件该用上次那套」
  ② 文件之间是否串位 —— A 的映射套到 B
  ③ 部分文件失败时其余是否还能跑

本脚本覆盖 4 组：
  ① 7 个月顺序批量：每个文件的月份、天数、数值都对
  ② 映射自动套用：第 2 个之后由 recommend 提供映射
  ③ 串位防护：同批次里混入不同产线，各自套各自的映射
  ④ 单文件失败不影响其余（坏文件跳过，好的照跑）

跑法：python tests/verify_e2e_batch.py
"""
import io
import os
import shutil
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from core.ingest import _scan_file_worker  # noqa: E402
from services.config import Config  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}  {detail}")


TIMES_96 = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 15, 30, 45)]


def write_month(tmpdir, year, month, days, asset="A线总有功", base=1000.0):
    """写一个月的长表文件，返回 (路径, 真值统计)。"""
    rows = []
    for d in range(1, days + 1):
        date = f"{year}-{month:02d}-{d:02d}"
        for p, t in enumerate(TIMES_96):
            # 每天的负荷 = base + 日*10 + 点位，保证每天每天点都不同
            rows.append({"采集时间": f"{date} {t}", asset: base + d * 10 + p * 0.1})
    df = pd.DataFrame(rows)
    name = f"曲线导出-1-1-{year}-{month:02d}-01,{year}-{month:02d}-{days:02d}.xlsx"
    p = os.path.join(tmpdir, name)
    df.to_excel(p, index=False)
    vals = df[asset].to_numpy(dtype=float)
    return p, {"days": days, "points": 96, "min": float(vals.min()),
               "max": float(vals.max()), "rows": len(df)}


def mapping_for(asset, date_col="采集时间"):
    return {
        "date_col": date_col, "time_col": date_col,
        "load_col": asset, "asset_col": None,
        "format_type": "long", "header_row": 0, "delimiter": ",",
        "convert_accumulated": False, "transpose": False,
    }


def scan_one(fp, sheet, mapping):
    return _scan_file_worker((fp, sheet, mapping, None, threading.Event(), None))


# ══════════════════════════════════════════════════════════
print("=" * 78)
print("① 7 个月顺序批量：月份、天数、数值逐个核对")
print("=" * 78)
tmp = tempfile.mkdtemp(prefix="batch1_")
MONTHS = [(2026, 7, 31), (2026, 8, 31), (2026, 9, 30), (2026, 10, 31),
          (2026, 11, 30), (2026, 12, 31), (2027, 1, 31)]
try:
    files = []
    for y, m, d in MONTHS:
        files.append(write_month(tmp, y, m, d))
    check(f"已生成 {len(files)} 个文件", len(files) == 7)

    mp = mapping_for("A线总有功")
    all_ok = True
    for (fp, truth), (y, m, d) in zip(files, MONTHS):
        res = scan_one(fp, "Sheet1", mp)
        if not isinstance(res, list) or len(res) != 1:
            check(f"{y}-{m:02d} 扫描返回单组", False, str(res)[:120])
            all_ok = False
            continue
        g = res[0]
        ym_ok = g["month_str"] == f"{y}-{m:02d}"
        dc_ok = g["date_count"] == truth["days"]
        tp_ok = g["time_points"] == 96
        mx_ok = abs(g["max_val"] - truth["max"]) < 1e-6
        mn_ok = abs(g["min_val"] - truth["min"]) < 1e-6
        if not (ym_ok and dc_ok and tp_ok and mx_ok and mn_ok):
            check(f"{y}-{m:02d} 分组正确", False,
                  f"ym={g['month_str']} dc={g['date_count']}/{truth['days']} "
                  f"tp={g['time_points']} max={g['max_val']}/{truth['max']}")
            all_ok = False
    check("7 个月全部：月份/天数/点数/min/max 均正确", all_ok)

    # 跨年边界（2026-12 → 2027-01）不能混成一组
    res12 = scan_one(files[5][0], "Sheet1", mp)
    res01 = scan_one(files[6][0], "Sheet1", mp)
    check("跨年不混淆：12 月归 2026-12",
          res12[0]["month_str"] == "2026-12", res12[0]["month_str"])
    check("跨年不混淆：1 月归 2027-01",
          res01[0]["month_str"] == "2027-01", res01[0]["month_str"])
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ══════════════════════════════════════════════════════════
print()
print("=" * 78)
print("② 映射自动套用：第 2 个之后由 recommend 提供")
print("=" * 78)
tmp = tempfile.mkdtemp(prefix="batch2_")
try:
    Config.FILE = os.path.join(tmp, "cfg.json")
    Config.BACKUP_DIR = os.path.join(tmp, "bk")
    os.makedirs(Config.BACKUP_DIR, exist_ok=True)
    cfg = Config()

    files = [write_month(tmp, 2026, m, 30) for m in (7, 8, 9)]
    # 第一个文件：用户手工映射
    first_mapping = mapping_for("A线总有功")
    cfg.add_map("曲线导出*.xlsx", "Sheet1", first_mapping)

    hits = 0
    for fp, _ in files:
        cols = ["采集时间", "A线总有功"]
        rec = cfg.recommend(fp, "Sheet1", cols)
        if rec and rec.get("load_col") == "A线总有功":
            hits += 1
        res = scan_one(fp, "Sheet1", rec or first_mapping)
        if not isinstance(res, list) or len(res) != 1:
            check(f"{os.path.basename(fp)} 可用推荐映射扫描", False, str(res)[:100])
    check(f"3 个文件全部由 recommend 命中同一套映射（{hits}/3）", hits == 3)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ══════════════════════════════════════════════════════════
print()
print("=" * 78)
print("③ 串位防护：同批次混入不同产线")
print("=" * 78)
tmp = tempfile.mkdtemp(prefix="batch3_")
try:
    Config.FILE = os.path.join(tmp, "cfg.json")
    Config.BACKUP_DIR = os.path.join(tmp, "bk")
    os.makedirs(Config.BACKUP_DIR, exist_ok=True)
    cfg = Config()

    fa, ta = write_month(tmp, 2026, 7, 30, asset="A线总有功", base=1000.0)
    fb, tb = write_month(tmp, 2026, 7, 30, asset="B线总有功", base=5000.0)
    cfg.add_map("曲线导出*.xlsx", "Sheet1", mapping_for("A线总有功"))

    rec_a = cfg.recommend(fa, "Sheet1", ["采集时间", "A线总有功"])
    rec_b = cfg.recommend(fb, "Sheet1", ["采集时间", "B线总有功"])
    check("A 文件推荐到 A 线映射",
          rec_a is not None and rec_a.get("load_col") == "A线总有功", str(rec_a))
    check("B 文件不会被套上 A 线映射", rec_b is None, str(rec_b))

    # B 文件必须用自己的映射扫，且数值来自 B 的真实列
    res_b = scan_one(fb, "Sheet1", mapping_for("B线总有功"))
    ok_b = (isinstance(res_b, list) and len(res_b) == 1
            and abs(res_b[0]["max_val"] - tb["max"]) < 1e-6)
    check("B 文件用自己的映射能扫出正确数值", ok_b, str(res_b)[:140])
    check("A/B 两文件的数值不相等（确实是两份数据）",
          abs(ta["max"] - tb["max"]) > 1000)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ══════════════════════════════════════════════════════════
print()
print("=" * 78)
print("④ 单文件失败不影响其余")
print("=" * 78)
tmp = tempfile.mkdtemp(prefix="batch4_")
try:
    good1, t1 = write_month(tmp, 2026, 7, 30)
    bad = os.path.join(tmp, "曲线导出-坏文件.xlsx")
    io.open(bad, "w", encoding="utf-8").write("这不是 Excel")
    good2, t2 = write_month(tmp, 2026, 8, 31)

    mp = mapping_for("A线总有功")
    results = [scan_one(p, "Sheet1", mp) for p in (good1, bad, good2)]
    check("坏文件返回 error 而不是抛异常",
          isinstance(results[1], dict) and "error" in results[1], str(results[1])[:120])
    check("前一个文件正常扫出",
          isinstance(results[0], list) and len(results[0]) == 1)
    check("坏文件之后仍能继续扫",
          isinstance(results[2], list) and len(results[2]) == 1)
    check("后一个文件数值正确",
          abs(results[2][0]["max_val"] - t2["max"]) < 1e-6)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
print("=" * 78)
print(f"结果：PASS {PASS}   FAIL {FAIL}")
print("=" * 78)
sys.exit(1 if FAIL else 0)
