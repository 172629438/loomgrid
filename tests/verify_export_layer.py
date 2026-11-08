# -*- coding: utf-8 -*-
"""导出层：文件名、同名覆盖、报告转义、CSV 编码。

为什么单独一个文件
------------------
导出层是「用户唯一能拿走的产物」。前面所有环节算对了，导出错了等于白干。
而它的失败方式有两种：一是文件名撞了就静默覆盖（少一张图，没人知道），
二是报告里的反查错位（表格里的表号/月份/源文件三列串行）。

本轮修掉一条：
  plot_group 不经过 _reserve_output_path，同资产同月不同 sheet 会互相覆盖。
  桌面版出图路径有原子占位会自动加 _1，只有 services 这条没有——而它是 API 用的。

跑法：python tests/verify_export_layer.py
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np  # noqa: E402

from core.models import GroupSummary  # noqa: E402
from plotting.naming import _sanitize_name, generate_filename  # noqa: E402
from services.export import (  # noqa: E402
    DEFAULT_FILENAME_TEMPLATE, export_csv, export_midwide, generate_report, plot_group,
)

PASS = FAIL = 0
TIMES = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 15, 30, 45)]
DATES = ["2026-07-01", "2026-07-02"]
LOAD = np.arange(len(DATES) * 96, dtype=float).reshape(len(DATES), 96)
DATA = {"date_labels": DATES, "time_labels": TIMES, "load_array": LOAD.tolist()}


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}  {detail}")


def mk(asset="A线", month="2026-07", sheet="Sheet1", fp=r"C:\data\x.xlsx"):
    return GroupSummary(file_path=fp, sheet_name=sheet, asset_value=asset,
                        month_str=month, date_count=2, time_points=96,
                        min_val=0.0, max_val=191.0, mapping={})


# ══════════════════════════════════════════════════════════
print("=" * 74)
print("① 文件名：非法字符 / 保留名 / 超长 / 未知占位符")
print("=" * 74)
CASES = [
    ("正常", {"year_month": "2026-07", "table_no": "表1", "asset": "A线", "sheet": "S1"}),
    ("含斜杠", {"year_month": "2026-07", "table_no": "表1", "asset": "A/B栋", "sheet": "S1"}),
    ("含反斜杠", {"year_month": "2026-07", "table_no": "表1", "asset": "A\\B", "sheet": "S1"}),
    ("Windows 保留名", {"year_month": "2026-07", "table_no": "CON", "asset": "CON", "sheet": "S1"}),
    ("全空", {"year_month": "", "table_no": "", "asset": "", "sheet": ""}),
    ("超长 300 字", {"year_month": "2026-07", "table_no": "表1", "asset": "A" * 300, "sheet": "S"}),
]
for tag, ctx in CASES:
    try:
        fn = generate_filename(DEFAULT_FILENAME_TEMPLATE, ctx)
        bad = [ch for ch in '<>:"/\\|?*' if ch in fn]
        check(f"{tag}：无非法字符（{len(fn)} 字）", not bad, f"含 {bad}")
    except Exception as e:
        check(f"{tag}", False, f"{type(e).__name__}: {e}")

fn = generate_filename("{table_no}_{unknown}_x", {"table_no": "T", "asset": "A"})
check("未知占位符原样保留（不 KeyError）", "unknown" in fn, fn)

# ══════════════════════════════════════════════════════════
print()
print("=" * 74)
print("② plot_group 与桌面版出图的文件名算法一致")
print("=" * 74)
tmp = tempfile.mkdtemp(prefix="exp2_")
try:
    s = mk()
    p = plot_group(s, DATA, os.path.join(tmp, "svc"))
    # 桌面版 _plot_single_worker 的 ctx
    ctx_main = {"year_month": s.month_str, "table_no": s.asset_value,
                "asset": s.asset_value, "sheet": s.sheet_name}
    expect = generate_filename(DEFAULT_FILENAME_TEMPLATE, ctx_main)
    check(f"文件名一致（{os.path.basename(p)}）", os.path.basename(p) == expect,
          f"期望 {expect}")

    # ══════════════════════════════════════════════════════
    print()
    print("=" * 74)
    print("③ 同名不覆盖（本轮修复）")
    print("=" * 74)
    d = os.path.join(tmp, "dup")
    s1 = mk(sheet="Sheet1")
    s2 = mk(sheet="Sheet2")      # 同资产同月，只有 sheet 不同
    p1 = plot_group(s1, DATA, d)
    p2 = plot_group(s2, DATA, d)
    check("两次出图路径不同", p1 != p2, f"{p1} == {p2}")
    files = sorted(os.listdir(d))
    check(f"目录里有 2 个 png（实 {len(files)}）", len(files) == 2, str(files))
    check("第二个带 _1 后缀", any(f.endswith("_1.png") for f in files), str(files))
    sizes = [os.path.getsize(os.path.join(d, f)) for f in files]
    check("两个文件都非空", all(x > 0 for x in sizes), str(sizes))

    # 三次同名，应到 _2
    p3 = plot_group(mk(sheet="Sheet3"), DATA, d)
    files3 = sorted(os.listdir(d))
    check(f"第三次同名到 3 个文件（实 {len(files3)}）", len(files3) == 3, str(files3))

    # ══════════════════════════════════════════════════════
    print()
    print("=" * 74)
    print("④ 报告：HTML 转义 / 计数 / 反查不错位")
    print("=" * 74)
    xss = mk(asset="<script>alert(1)</script>", fp=r"C:\x.ss.xlsx")
    fnx = generate_filename(DEFAULT_FILENAME_TEMPLATE,
                            {"year_month": "2026-07", "table_no": xss.asset_value,
                             "asset": xss.asset_value, "sheet": xss.sheet_name})
    rep = generate_report([os.path.join(tmp, "x", fnx)], ["坏文件.xlsx"], [xss],
                          os.path.join(tmp, "rep"))
    html = open(rep, encoding="utf-8").read()
    check("资产名里的 <script> 被转义", "<script>alert" not in html and "&lt;script&gt;" in html)
    check("成功 1 / 失败 1 计数正确",
          '<span class="success">1</span>' in html and '<span class="failure">1</span>' in html)
    check("失败列表包含文件名", "坏文件.xlsx" in html)

    # 前缀误匹配回归：资产 "1" 不能吃掉 "10"
    s1a = mk(asset="1", fp=r"C:\a1.xlsx")
    s10 = mk(asset="10", fp=r"C:\a10.xlsx")
    f1 = generate_filename(DEFAULT_FILENAME_TEMPLATE,
                           {"year_month": "2026-07", "table_no": "1", "asset": "1", "sheet": "S"})
    f10 = generate_filename(DEFAULT_FILENAME_TEMPLATE,
                            {"year_month": "2026-07", "table_no": "10", "asset": "10", "sheet": "S"})
    rep2 = generate_report([os.path.join(tmp, "y", f10)], [], [s1a, s10],
                           os.path.join(tmp, "rep2"))
    h2 = open(rep2, encoding="utf-8").read()
    # 文件名 f10 那一行，表号列应该是 10 不是 1
    row_ok = f"<td>{f10}</td><td>10</td>" in h2.replace("</td><td>", "</td><td>")
    check("资产 1 不会匹配到 10 的文件名（全等比较生效）", row_ok,
          "报告里 10 那一行的表号列不对")

    # ══════════════════════════════════════════════════════
    print()
    print("=" * 74)
    print("⑤ CSV 编码与列序")
    print("=" * 74)
    rows = [{"资产": "A线", "月份": "2026-07", "容量": 100.5},
            {"资产": "B线", "月份": "2026-07", "容量": 200.25}]
    cp = export_csv(rows, os.path.join(tmp, "csv"), name="测试.csv")
    raw = open(cp, "rb").read()
    check("含 UTF-8 BOM（Excel 直接双击不乱码）", raw[:3] == b"\xef\xbb\xbf", repr(raw[:3]))
    text = raw.decode("utf-8-sig")
    check("表头顺序与 dict 一致", text.splitlines()[0] == "资产,月份,容量", text.splitlines()[0])
    check("两行数据都在", len(text.strip().splitlines()) == 3, str(len(text.strip().splitlines())))

    # 空列表
    cp2 = export_csv([], os.path.join(tmp, "csv"), name="空.csv")
    check("空列表导出不崩、生成 0 字节文件",
          os.path.exists(cp2) and os.path.getsize(cp2) == 0)

    # ══════════════════════════════════════════════════════
    print()
    print("=" * 74)
    print("⑥ 中间宽表：资产名含斜杠不破坏路径")
    print("=" * 74)
    d2 = os.path.join(tmp, "mid")
    p_mid = export_midwide(mk(asset="A/B栋"), DATA, d2)
    check("文件落在指定目录（没钻进子目录）",
          os.path.dirname(os.path.abspath(p_mid)) == os.path.abspath(d2),
          os.path.dirname(os.path.abspath(p_mid)))
    check("文件名里的斜杠被消毒", "/" not in os.path.basename(p_mid), os.path.basename(p_mid))
    check("文件确实生成", os.path.getsize(p_mid) > 0)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
print("=" * 74)
print(f"结果：PASS {PASS}   FAIL {FAIL}")
print("=" * 74)
sys.exit(1 if FAIL else 0)
