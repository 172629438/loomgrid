# -*- coding: utf-8 -*-
"""阶段 0 · 基线场景生成器 —— 18 个输入样本。

用途
----
为「行为基线」造确定性输入。基线要能复现，所以样本本身必须落盘随仓库提交，
而不是每次靠随机数现造。

设计原则
--------
1. **每个场景对应一个真实出过问题的形态**，不是为凑数。C1/C2/C4/C8 都有对应场景。
2. **确定性**：固定 seed、固定日期，两次生成字节级一致（xlsx 内部时间戳除外，
   故用内容哈希而非文件哈希做校验）。
3. **只造输入**，不 import 主程序——采集在 capture_baseline.py 里做。

场景清单（id / 形态 / 覆盖的历史问题）
-------------------------------------
W1  宽表 96 点 · 5 天                 日常主路径
W2  宽表 24 点 · 5 天                 分辨率差异
W3  宽表多资产（两个 sheet）          资产识别与分组
L1  长表 96 点 · 5 天                 长表主路径
L2  长表多资产 · 2 资产 × 3 天        长表资产列
L3  长表含无效时刻                     C1 时间列被抢占 / 无效时刻静默丢弃
T1  转置宽表（时间行 × 日期列）       转置形态判定
T2  转置宽表 + 累计示数               转置 × 累计叠加
A1  长表示数（日重置累积电量）        示数差分成功率
A2  宽表行累计示数                    宽表行差分
S1  单日 96 点                        单日形态
S2  单日 24 点                        单日 × 低分辨率
Y1  文件名带年份 · 数据无年份         C8 默认今年（跨年串月份风险）
Y2  文件名带年份 · 数据带年份         年份一致对照
N1  全负（光伏倒送）                  Mode A/B 充电空间口径
E1  UTF-8-BOM CSV                     编码探测
E2  GBK CSV                           编码探测（中文 Excel 另存默认）
E3  资产名含 & < >                    C2 报告 HTML 转义

用法
----
    python tools/baseline_scenarios.py                 # 生成到 baseline/_inputs/
    python tools/baseline_scenarios.py --out <目录>
    python tools/baseline_scenarios.py --list          # 只列清单不生成
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os

import numpy as np
import pandas as pd

SEED = 20261103
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_OUT = os.path.join(ROOT, "baseline", "_inputs")


# ────────────────────────────────────────────────────────────
# 负荷形态（确定性，无随机噪声以保证逐点可复现）
# ────────────────────────────────────────────────────────────
def load_profile(kind: str, i: int, n_pts: int, day: int) -> float:
    """给定形态与时刻序号，返回功率值（kW）。"""
    h = i * 24.0 / n_pts
    if kind == "industrial":       # 双峰：09 点、19 点
        v = 420.0 + 520.0 * np.exp(-((h - 9.5) ** 2) / 2.0) + 460.0 * np.exp(-((h - 19.5) ** 2) / 2.0)
    elif kind == "commercial":     # 单峰午后
        v = 120.0 + 780.0 * np.exp(-((h - 14.0) ** 2) / 9.0)
    elif kind == "pv_backfeed":    # 光伏倒送：中午负
        v = 260.0 - 620.0 * np.exp(-((h - 12.5) ** 2) / 6.0)
    elif kind == "all_negative":   # 全天倒送
        v = -120.0 - 180.0 * np.exp(-((h - 12.5) ** 2) / 8.0)
    elif kind == "spiky":          # 尖峰冲击
        v = 300.0 + (900.0 if i in (40, 41) else 0.0)
    elif kind == "flat":           # 连续生产
        v = 600.0 + 8.0 * np.sin(i / 6.0)
    else:
        v = 350.0 + 120.0 * np.sin(i / 4.0)
    # 日间轻微差异，让逐日测算结果不完全相同（否则「逐日逐段」比对看不出串天）
    v = v * (1.0 + 0.03 * day)
    return round(float(v), 4)


def time_labels(n_pts: int) -> list[str]:
    step = 1440 // n_pts
    out = []
    for i in range(n_pts):
        h, m = divmod(i * step, 60)
        out.append(f"{h:02d}:{m:02d}")
    return out


def date_seq(start: str, days: int) -> list[str]:
    return [d.strftime("%Y-%m-%d") for d in pd.date_range(start, periods=days, freq="D")]


def _write_xlsx(df: pd.DataFrame, path: str, sheet: str = "Sheet1") -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        df.to_excel(w, sheet_name=sheet, index=False)


def _write_csv(df: pd.DataFrame, path: str, encoding: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_csv(path, index=False, encoding=encoding)


def _entry(path: str, sheet: str = "Sheet1", **override) -> dict:
    return {"path": path, "sheet": sheet, "mapping_override": override or None}


# ────────────────────────────────────────────────────────────
# 各场景构造
# ────────────────────────────────────────────────────────────
def build_wide(out: str, n_pts: int, days: int, start: str, kind: str, fname: str) -> list[dict]:
    labels = time_labels(n_pts)
    rows = {}
    for d, day in enumerate(date_seq(start, days)):
        rows[day] = [load_profile(kind, i, n_pts, d) for i in range(n_pts)]
    df = pd.DataFrame({"日期": list(rows.keys()), **{lab: [rows[k][i] for k in rows] for i, lab in enumerate(labels)}})
    p = os.path.join(out, fname)
    _write_xlsx(df, p)
    return [_entry(p)]


def build_long(out: str, n_pts: int, days: int, start: str, kind: str, fname: str,
               assets: dict[str, float] | None = None) -> list[dict]:
    labels = time_labels(n_pts)
    rows = []
    for aid, scale in (assets or {"": 1.0}).items():
        for d, day in enumerate(date_seq(start, days)):
            for i, lab in enumerate(labels):
                rec = {}
                if aid:
                    rec["资产编号"] = aid
                rec["日期"] = day
                rec["时间"] = lab
                rec["正向有功功率"] = round(load_profile(kind, i, n_pts, d) * scale, 4)
                rows.append(rec)
    p = os.path.join(out, fname)
    _write_xlsx(pd.DataFrame(rows), p)
    return [_entry(p)]


def build_w1(out: str) -> list[dict]:
    return build_wide(out, 96, 5, "2026-01-15", "industrial", "W1_宽表96点.xlsx")


def build_w2(out: str) -> list[dict]:
    return build_wide(out, 24, 5, "2026-01-15", "commercial", "W2_宽表24点.xlsx")


def build_w3(out: str) -> list[dict]:
    """宽表多资产：一个工作簿两个 sheet。"""
    labels = time_labels(96)
    p = os.path.join(out, "W3_宽表多资产.xlsx")
    os.makedirs(out, exist_ok=True)
    with pd.ExcelWriter(p, engine="openpyxl") as w:
        for sheet, kind, scale in (("A栋", "industrial", 1.0), ("B栋", "commercial", 0.72)):
            days = date_seq("2026-01-15", 3)
            cols = {}
            for i, lab in enumerate(labels):
                cols[lab] = [round(load_profile(kind, i, 96, d) * scale, 4) for d in range(len(days))]
            pd.DataFrame({"日期": days, **cols}).to_excel(w, sheet_name=sheet, index=False)
    return [_entry(p, "A栋"), _entry(p, "B栋")]


def build_l1(out: str) -> list[dict]:
    return build_long(out, 96, 5, "2026-01-15", "industrial", "L1_长表96点.xlsx")


def build_l2(out: str) -> list[dict]:
    return build_long(out, 96, 3, "2026-01-15", "industrial", "L2_长表多资产.xlsx",
                      assets={"M001": 1.0, "M002": 1.6})


def build_l3(out: str) -> list[dict]:
    """C1 场景：时间列里混入非法值，验证是否静默丢弃。

    历史上 C1 是「时间列被别的列抢占」——词表漏字导致时间列识别到了错误列。
    这里造两种破坏：① 个别行时间为 "25:00"；② 混入一行空时间。
    期望：程序给出 warning 而不是静默消失（基线记录的就是「当前实际行为」）。
    """
    n_pts, days = 96, 3
    labels = time_labels(n_pts)
    rows = []
    for d, day in enumerate(date_seq("2026-01-15", days)):
        for i, lab in enumerate(labels):
            t = lab
            if d == 1 and i == 20:
                t = "25:00"          # 非法时刻
            elif d == 1 and i == 21:
                t = ""               # 空时刻
            rows.append({"日期": day, "时间": t,
                         "正向有功功率": round(load_profile("industrial", i, n_pts, d), 4)})
    p = os.path.join(out, "L3_长表含无效时刻.xlsx")
    _write_xlsx(pd.DataFrame(rows), p)
    return [_entry(p)]


def build_t1(out: str) -> list[dict]:
    labels = time_labels(96)
    days = date_seq("2026-01-15", 3)
    rows = []
    for i, lab in enumerate(labels):
        rec = {"时间": lab}
        for d, day in enumerate(days):
            rec[day] = round(load_profile("industrial", i, 96, d), 4)
        rows.append(rec)
    p = os.path.join(out, "T1_转置宽表.xlsx")
    _write_xlsx(pd.DataFrame(rows), p)
    return [_entry(p)]


def build_t2(out: str) -> list[dict]:
    """转置宽表 + 每列是累计示数。"""
    labels = time_labels(96)
    days = date_seq("2026-01-15", 2)
    rows = []
    acc = {d: 0.0 for d in days}
    for i, lab in enumerate(labels):
        rec = {"时间": lab}
        for d, day in enumerate(days):
            acc[day] += 40.0          # 15 分钟 10 kWh → 40 kW
            rec[day] = round(acc[day], 4)
        rows.append(rec)
    p = os.path.join(out, "T2_转置累计.xlsx")
    _write_xlsx(pd.DataFrame(rows), p)
    return [_entry(p)]


def build_a1(out: str) -> list[dict]:
    """长表示数：每日从 0 重置、单调递增的电量读数。"""
    n_pts, days = 96, 3
    labels = time_labels(n_pts)
    rows = []
    for d, day in enumerate(date_seq("2026-01-15", days)):
        acc = 0.0
        for i, lab in enumerate(labels):
            rows.append({"日期": day, "时间": lab, "总电量": round(acc, 4)})
            acc += load_profile("flat", i, n_pts, d) * 0.25
    p = os.path.join(out, "A1_长表示数.xlsx")
    _write_xlsx(pd.DataFrame(rows), p)
    return [_entry(p)]


def build_a2(out: str) -> list[dict]:
    """宽表行累计：每行 96 个值是当天的累计示数。"""
    n_pts, days = 96, 3
    labels = time_labels(n_pts)
    records = {}
    for d, day in enumerate(date_seq("2026-01-15", days)):
        acc = 0.0
        vals = []
        for i in range(n_pts):
            vals.append(round(acc, 4))
            acc += load_profile("flat", i, n_pts, d) * 0.25
        records[day] = vals
    df = pd.DataFrame({"日期": list(records.keys()),
                       **{lab: [records[k][i] for k in records] for i, lab in enumerate(labels)}})
    p = os.path.join(out, "A2_宽表行累计.xlsx")
    _write_xlsx(df, p)
    return [_entry(p)]


def build_s1(out: str) -> list[dict]:
    return build_wide(out, 96, 1, "2026-01-20", "industrial", "S1_单日96点.xlsx")


def build_s2(out: str) -> list[dict]:
    return build_wide(out, 24, 1, "2026-01-20", "commercial", "S2_单日24点.xlsx")


def build_y1(out: str) -> list[dict]:
    """C8 场景：文件名写 2025 年，数据只有「月-日」无年份。"""
    n_pts = 96
    labels = time_labels(n_pts)
    rows = {}
    for d, md_str in enumerate(["01-15", "01-16", "01-17"]):
        rows[md_str] = [load_profile("industrial", i, n_pts, d) for i in range(n_pts)]
    df = pd.DataFrame({"日期": list(rows.keys()),
                       **{lab: [rows[k][i] for k in rows] for i, lab in enumerate(labels)}})
    p = os.path.join(out, "A栋2025年负荷.xlsx")
    _write_xlsx(df, p)
    return [_entry(p)]


def build_y2(out: str) -> list[dict]:
    """对照：文件名与数据都带 2025 年。"""
    labels = time_labels(96)
    days = date_seq("2025-01-15", 3)
    rows = {day: [load_profile("industrial", i, 96, d) for i in range(96)] for d, day in enumerate(days)}
    df = pd.DataFrame({"日期": list(rows.keys()),
                       **{lab: [rows[k][i] for k in rows] for i, lab in enumerate(labels)}})
    p = os.path.join(out, "B栋2025年负荷-带年份.xlsx")
    _write_xlsx(df, p)
    return [_entry(p)]


def build_n1(out: str) -> list[dict]:
    return build_wide(out, 96, 3, "2026-01-15", "all_negative", "N1_全负倒送.xlsx")


def build_e1(out: str) -> list[dict]:
    df_rows = []
    labels = time_labels(96)
    for d, day in enumerate(date_seq("2026-01-15", 3)):
        for i, lab in enumerate(labels):
            df_rows.append({"日期": day, "时间": lab,
                            "正向有功功率": round(load_profile("industrial", i, 96, d), 4)})
    p = os.path.join(out, "E1_utf8bom.csv")
    _write_csv(pd.DataFrame(df_rows), p, "utf-8-sig")
    return [_entry(p, "CSV文件")]


def build_e2(out: str) -> list[dict]:
    df_rows = []
    labels = time_labels(96)
    for d, day in enumerate(date_seq("2026-01-15", 3)):
        for i, lab in enumerate(labels):
            df_rows.append({"日期": day, "时间": lab,
                            "正向有功功率": round(load_profile("commercial", i, 96, d), 4)})
    p = os.path.join(out, "E2_gbk.csv")
    _write_csv(pd.DataFrame(df_rows), p, "gbk")
    return [_entry(p, "CSV文件")]


def build_e3(out: str) -> list[dict]:
    """C2 场景：资产名含 & < >，文件名含 &——报告 HTML 必须转义。

    注意：Windows 文件名不允许 < >，所以这两类字符只能走「资产名」通道；
    文件名只保留合法的 &。报告的「表号」列与「源文件」列由此各自携带脏字符。
    """
    n_pts, days = 96, 3
    labels = time_labels(n_pts)
    rows = []
    for d, day in enumerate(date_seq("2026-01-15", days)):
        for i, lab in enumerate(labels):
            rows.append({"资产编号": "A&B楼<1>", "日期": day, "时间": lab,
                         "正向有功功率": round(load_profile("industrial", i, n_pts, d), 4)})
    p = os.path.join(out, "A&B楼负荷.xlsx")
    _write_xlsx(pd.DataFrame(rows), p)
    return [_entry(p)]


# ────────────────────────────────────────────────────────────
# 清单
# ────────────────────────────────────────────────────────────
SCENARIOS = [
    {"id": "W1", "name": "宽表96点", "group": "宽表", "build": build_w1,
     "notes": "日常主路径，96 点 5 天", "covers": []},
    {"id": "W2", "name": "宽表24点", "group": "宽表", "build": build_w2,
     "notes": "小时级分辨率", "covers": []},
    {"id": "W3", "name": "宽表多资产", "group": "宽表", "build": build_w3,
     "notes": "两个 sheet 视为两个资产", "covers": []},
    {"id": "L1", "name": "长表96点", "group": "长表", "build": build_l1,
     "notes": "长表主路径", "covers": []},
    {"id": "L2", "name": "长表多资产", "group": "长表", "build": build_l2,
     "notes": "资产编号列分组", "covers": []},
    {"id": "L3", "name": "长表含无效时刻", "group": "长表", "build": build_l3,
     "notes": "25:00 与空时刻，验证不静默丢弃", "covers": ["C1"]},
    {"id": "T1", "name": "转置宽表", "group": "转置", "build": build_t1,
     "notes": "时间行 × 日期列", "covers": []},
    {"id": "T2", "name": "转置累计", "group": "转置", "build": build_t2,
     "notes": "转置 × 示数叠加", "covers": []},
    {"id": "A1", "name": "长表示数", "group": "累计", "build": build_a1,
     "notes": "日重置单调递增", "covers": []},
    {"id": "A2", "name": "宽表行累计", "group": "累计", "build": build_a2,
     "notes": "行内累计差分成功率", "covers": [],
     "known_defects": [
         "_detect_transposed_wide 误判：00:00 列值全为 0，_parse_min 成功 → 当成时间列，"
         "其余 95 列被当日期列，整表判成 wide_transposed，产出 [95,1] 垃圾且不报错。"
         "正确判定应为 _detect_wide_matrix 的 [3,96]。"
     ]},
    {"id": "S1", "name": "单日96点", "group": "单日", "build": build_s1,
     "notes": "单日形态", "covers": []},
    {"id": "S2", "name": "单日24点", "group": "单日", "build": build_s2,
     "notes": "单日 × 低分辨率", "covers": []},
    {"id": "Y1", "name": "文件名带年份·数据无年份", "group": "含年份文件名", "build": build_y1,
     "notes": "2025 文件名 + 无年份数据，C8 默认今年", "covers": ["C8"]},
    {"id": "Y2", "name": "文件名带年份·数据带年份", "group": "含年份文件名", "build": build_y2,
     "notes": "两侧年份一致对照", "covers": ["C8"]},
    {"id": "N1", "name": "全负倒送", "group": "全负", "build": build_n1,
     "notes": "全天负负荷", "covers": []},
    {"id": "E1", "name": "UTF-8-BOM CSV", "group": "编码边界", "build": build_e1,
     "notes": "Excel 另存 UTF-8 带 BOM", "covers": []},
    {"id": "E2", "name": "GBK CSV", "group": "编码边界", "build": build_e2,
     "notes": "中文 Excel 默认另存编码", "covers": []},
    {"id": "E3", "name": "资产名含特殊字符", "group": "编码边界", "build": build_e3,
     "notes": "资产名含 & < >（文件名只含 &，< > 是 Windows 非法字符），报告需 HTML 转义", "covers": ["C2"]},
]


def content_fingerprint(path: str) -> str:
    """文件内容指纹：CSV 直接哈希；xlsx 按解压后的工作表 XML 哈希（跳过时间戳）。"""
    import zipfile
    h = hashlib.sha256()
    if path.lower().endswith((".xlsx", ".xlsm")):
        with zipfile.ZipFile(path) as z:
            for name in sorted(n for n in z.namelist() if n.endswith(".xml")):
                h.update(name.encode("utf-8"))
                h.update(z.read(name))
    else:
        with open(path, "rb") as f:
            h.update(f.read())
    return h.hexdigest()


def generate(out_dir: str = DEFAULT_OUT, only: list[str] | None = None) -> dict:
    """生成全部场景，返回清单。"""
    manifest = {"seed": SEED, "scenarios": []}
    for sc in SCENARIOS:
        if only and sc["id"] not in only:
            continue
        sdir = os.path.join(out_dir, sc["id"])
        if os.path.isdir(sdir):
            for f in os.listdir(sdir):
                os.remove(os.path.join(sdir, f))
        os.makedirs(sdir, exist_ok=True)
        entries = sc["build"](sdir)
        for e in entries:
            e["sha256"] = content_fingerprint(e["path"])
            e["bytes"] = os.path.getsize(e["path"])
            # 清单里存相对路径 —— 基线要随仓库公开提交，不能带本机绝对路径
            e["path"] = os.path.relpath(e["path"], out_dir).replace("\\", "/")
        manifest["scenarios"].append({
            "id": sc["id"], "name": sc["name"], "group": sc["group"],
            "notes": sc["notes"], "covers": sc["covers"], "inputs": entries,
        })
        print(f"  {sc['id']:3s} {sc['name']:24s} -> {len(entries)} 个输入")
    with open(os.path.join(out_dir, "scenarios.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return manifest


def main():
    ap = argparse.ArgumentParser(description="生成阶段 0 基线场景输入")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--only", nargs="*", help="只生成指定 id")
    ap.add_argument("--list", action="store_true", help="只列清单")
    a = ap.parse_args()
    if a.list:
        for sc in SCENARIOS:
            cover = f"  [{','.join(sc['covers'])}]" if sc["covers"] else ""
            print(f"{sc['id']:3s} {sc['group']:8s} {sc['name']:24s} {sc['notes']}{cover}")
        return
    print(f"生成到 {a.out}")
    m = generate(a.out, a.only)
    total = sum(len(s["inputs"]) for s in m["scenarios"])
    print(f"\n完成：{len(m['scenarios'])} 个场景 / {total} 个输入文件")


if __name__ == "__main__":
    main()
