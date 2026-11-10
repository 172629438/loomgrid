# -*- coding: utf-8 -*-
"""doc_numbers · 对外文案里的数字以代码为唯一来源

README 和站点上那些「9,533 行」「7 个算法模块」「116 个验证脚本」「40 项断言」
过去靠手写，改一次代码就失准一次——2026-11-10 查出来五处：Fusion 行数停在
7,995（实际 9,533）、模块数写 8（实际 7）、算法总行数 3,384（实际 3,196）、
验证断言 433（无出处，CI 实为 40）、总源码 110,767（四文件相加 109,786）。

用法：
    python tools/doc_numbers.py            # 打印实算值
    python tools/doc_numbers.py --check    # 与 README / 站点比对，不一致退出 1

接进 CI 只需一行：
    - run: python tools/doc_numbers.py --check
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MAINS = {
    "Fusion": os.path.join("apps", "负荷曲线批量生成工具 Pro 融合版 (v2.3).py"),
    "Ultra": os.path.join("apps", "负荷曲线批量生成Ultra.py"),
    "Backup": os.path.join("legacy", "备份程序.py"),
    "Reference": os.path.join("legacy", "参考程序.py"),
}

CI_SCRIPTS = ["verify_invariants.py", "verify_algorithms_math.py", "verify_alg_parity.py"]


def count_lines(p: str) -> tuple[int, int]:
    with open(p, encoding="utf-8", errors="replace") as f:
        txt = f.read()
    n = txt.count("\n") + (0 if txt.endswith("\n") else 1)
    classes = len(re.findall(r"^\s*class\s+\w+", txt, re.M))
    return n, classes


def actual() -> dict:
    d = {}

    for label, rel in MAINS.items():
        n, c = count_lines(os.path.join(ROOT, rel.replace("/", os.sep)))
        d[f"{label}_lines"] = n
        d[f"{label}_classes"] = c

    alg = os.path.join(ROOT, "algorithms")
    py = sorted(f for f in os.listdir(alg) if f.endswith(".py"))
    mods = [f for f in py if f != "__init__.py"]
    d["algo_modules"] = len(mods)
    d["algo_files"] = len(py)
    d["algo_lines"] = sum(count_lines(os.path.join(alg, f))[0] for f in py)

    tests = [f for f in os.listdir(os.path.join(ROOT, "tests"))
             if f.startswith("verify_") and f.endswith(".py")]
    d["test_scripts"] = len(tests)

    # CI 断言数：跑那三个脚本，取脚本自己报的 PASS 数
    ci_pass = 0
    for s in CI_SCRIPTS:
        r = subprocess.run([sys.executable, os.path.join("tests", s)], cwd=ROOT,
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        m = re.search(r"结果：PASS\s+(\d+)", r.stdout or "")
        ci_pass += int(m.group(1)) if m else len(re.findall(r"\[PASS\]", r.stdout or ""))
    d["ci_asserts"] = ci_pass

    d["source_total"] = sum(d[f"{k}_lines"] for k in MAINS)

    # tariffs 若在相邻目录，顺带核电价规模
    tdir = os.path.join(os.path.dirname(ROOT), "172629438__loomgrid-tariffs")
    if os.path.isdir(tdir):
        try:
            import json
            meta = json.load(open(os.path.join(tdir, "data", "meta.json"), encoding="utf-8"))
            d["tariff_zones"] = meta["province_count"]
            d["tariff_regions"] = len({({"冀北": "河北", "河北南网": "河北",
                                          "蒙东": "内蒙古", "蒙西": "内蒙古"}).get(p, p)
                                       for p in meta["provinces"]})
            d["period_rows"] = meta["period_rows"]
            rows = list(csv.DictReader(open(os.path.join(tdir, "data", "province_tariff_periods.csv"),
                                            encoding="utf-8-sig")))
            d["period_sets"] = len({(r["province"], r["season"]) for r in rows})
        except Exception as e:  # noqa: BLE001
            d["_tariffs_error"] = repr(e)[:80]
    return d


# 文档里可核的句式：正则捕获组 1 应该是实算值
CLAIMS = [
    ("README.md", r"\| 代码量 \| ([\d,]+) 行 \| [\d,]+ 行 \|", "Fusion_lines"),
    ("README.md", r"\| 代码量 \| [\d,]+ 行 \| ([\d,]+) 行 \|", "Ultra_lines"),
    ("README.md", r"\| ([\d,]+) 个可独立运行的算法模块", "algo_modules"),
    ("README.md", r"\| ([\d,]+) 行<br>均不依赖 GUI", "algo_lines"),
    ("README.md", r"\| \*\*([\d,]+)\*\* 行 \|", "source_total"),
    ("README.md", r"CI 跑 3 个脚本 ([\d,]+) 项断言", "ci_asserts"),
    ("README.md", r"`tests/` 下现有 ([\d,]+) 个验证脚本", "test_scripts"),
    ("README.md", r"\| v2\.3\.8 \| ([\d,]+) \|", "Fusion_lines"),
    ("docs/index.html", r'<div class="n">([\d,]+)</div><div class="l">个验证脚本</div>', "test_scripts"),
    ("docs/index.html", r'<div class="n">([\d,]+)</div><div class="l">项 CI 断言</div>', "ci_asserts"),
]


def check(d: dict) -> int:
    bad = 0
    for rel, pat, key in CLAIMS:
        p = os.path.join(ROOT, rel.replace("/", os.sep))
        if not os.path.exists(p):
            continue
        txt = open(p, encoding="utf-8", errors="replace").read()
        m = re.search(pat, txt)
        if not m:
            print(f"  MISS  {rel:16} 找不到「{key}」的声明句")
            bad += 1
            continue
        claimed = int(m.group(1).replace(",", ""))
        real = d.get(key)
        if real is None:
            continue
        if claimed != real:
            print(f"  DIFF  {rel:16} {key:14} 文档 {claimed:,} ≠ 实算 {real:,}")
            bad += 1
    print(f"\n  不一致 {bad} 处" if bad else "\n  全部一致")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="与 README / 站点比对")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    d = actual()
    if a.check:
        print("doc_numbers --check")
        sys.exit(check(d))
    for k, v in d.items():
        print(f"  {k:18} {v:,}" if isinstance(v, int) else f"  {k:18} {v}")
