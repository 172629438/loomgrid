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
    ("README.md", r"\| v2\.\d+(?:\.\d+)? \| ([\d,]+) \|", "Fusion_lines"),
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


def latest_release_info() -> dict:
    """拉最新 Release 及各版本包体。拉不到返回空 dict，不阻断。

    为什么要这一项：2026-11-10 发现 README 写着 Fusion v2.3.8，而那天已经发到
    v2.3.40——一整天里连发了十几个版本，文档一个字没跟。这种“数字都对、就是
    不是最新”的漂移，行数统计抓不到，只有真去问远端才知道。

    返回：{"fusion-v": {"tag": "fusion-v2.3.40", "mb": 142.5}, "ultra-v": {...}}
    额外带一个 "sizes": {tag: mb}，供 sync 按版本取包体。
    """
    import json as _json
    import re as _re
    import shutil
    import subprocess
    import urllib.request

    url = "https://api.github.com/repos/172629438/loomgrid/releases?per_page=100"
    data = None

    # 优先走 gh（带 token，5000 次/小时）；否则匿名 urllib（60 次/小时，很容易撞限流）
    gh = shutil.which("gh") or os.path.expandvars(
        r"%USERPROFILE%\.hanako\tools\gh\bin\gh.exe")
    if gh and os.path.exists(gh):
        r = subprocess.run([gh, "api", "repos/172629438/loomgrid/releases?per_page=100"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode == 0:
            try:
                data = _json.loads(r.stdout.lstrip("\ufeff"))
            except Exception:
                data = None
    if data is None:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "doc_numbers"})
            data = _json.loads(urllib.request.urlopen(req, timeout=20).read().decode())
        except Exception:
            return {}

    def key(t):
        m = _re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", t)
        return tuple(int(x) if x else 0 for x in m.groups()) if m else (0, 0, 0)

    sizes = {}
    for x in data:
        zips = [a for a in x.get("assets", []) if a["name"].lower().endswith(".zip")]
        if zips:
            sizes[x["tag_name"]] = zips[0]["size"] / 1024 / 1024

    out = {"sizes": sizes}
    for pref in ("fusion-v", "ultra-v"):
        group = [x["tag_name"] for x in data if x["tag_name"].startswith(pref)]
        if group:
            tag = max(group, key=key)
            out[pref] = {"tag": tag, "mb": round(sizes.get(tag, 0))}
    return out


def latest_release_versions() -> dict:
    """兼容旧接口：只取各线最新 tag。"""
    info = latest_release_info()
    return {k: v["tag"] for k, v in info.items() if isinstance(v, dict) and "tag" in v}


def sync(dry_run: bool = False) -> int:
    """把 README 里的现役版本号与包体写成远端最新值。

    只改“当前版本”——历史范围（如 v1.1 ~ v2.3.0）不碰。判据是把 README 里
    该线的最大版号当成现役值，只替换这个具体字符串。
    """
    info = latest_release_info()
    if "fusion-v" not in info:
        print("  拉不到 Release 列表，不同步")
        return 2

    p = os.path.join(ROOT, "README.md")
    txt = open(p, encoding="utf-8").read()
    orig = txt
    changes = []

    for pref, label in (("fusion-v", "Fusion"), ("ultra-v", "Ultra")):
        item = info.get(pref)
        if not item:
            continue
        new_tag = item["tag"]
        new_ver = new_tag.replace(pref, "")
        mb = item["mb"]

        # 1) 版本号：找 README 里该线的最大版号即为“现役声明值”
        vers = re.findall(rf"{label}\s+v(\d+\.\d+(?:\.\d+)?)", txt)
        if not vers:
            continue

        def vkey(v):
            return tuple(int(x) for x in v.split("."))

        cur_ver = max(vers, key=vkey)
        if cur_ver != new_ver:
            n1 = txt.count(f"{label} v{cur_ver}")
            txt = txt.replace(f"{label} v{cur_ver}", f"{label} v{new_ver}")
            n2 = txt.count(f"{pref}{cur_ver}")
            txt = txt.replace(f"{pref}{cur_ver}", f"{pref}{new_ver}")
            changes.append(f"{label} 版号 {cur_ver} → {new_ver}（正文 {n1} 处 + 链接 {n2} 处）")

        # 2) 包体：四条固定句式
        pats = [
            rf"(\|\s*(?:\[\*\*)?{label} v{new_ver}(?:\*\*)?\]\([^)]*\)\s*\|[^|]*\|\s*)(\d+)( MB \|)",
            rf"({label} 压缩包 )\d+( MB)",
        ]
        # 这两句讲的是 Fusion 的 exe，只在 Fusion 那轮跑，否则 Ultra 会把它改成自己的包体
        if pref == "fusion-v":
            pats += [
                rf"(\*\*为什么 exe 有 )\d+( MB？\*\*)",
                rf"(压成 zip 就是 )\d+( MB)",
            ]
        for pat in pats:
            def repl(m, _mb=mb):
                return f"{m.group(1)}{_mb}{m.group(3) if m.lastindex and m.lastindex >= 3 else m.group(2)}"
            text_new, k = re.subn(pat, repl, txt)
            if k and text_new != txt:
                txt = text_new
                changes.append(f"{label} 包体 → {mb} MB（{k} 处）")

    # Ultra 在句尾提到包体的两处：「，「后面」和「Ultra N MB」
    u = info.get("ultra-v")
    if u:
        for pat in (r"(，Ultra )\d+( MB)", r"(，)\d+( MB）。)"):
            t2, k = re.subn(pat, lambda m, _mb=u["mb"]: f"{m.group(1)}{_mb}{m.group(2)}", txt)
            if k and t2 != txt:
                txt = t2
                changes.append(f"Ultra 包体 → {u['mb']} MB（{k} 处）")

    if txt == orig:
        print("  已是最新，无需改动")
        return 0
    for c in changes:
        print(f"  · {c}")
    if dry_run:
        print("  （dry-run，未写入）")
        return 0
    open(p, "w", encoding="utf-8", newline="").write(txt)
    print(f"  已写入 {p}")
    return 0


def check_releases() -> int:
    """README 里写的 Fusion / Ultra 版本，是不是远端最新那几个。"""
    badges = latest_release_versions()
    if not badges:
        print("  拉不到 Release 列表（网络或速率限制），跳过版本检查")
        return 0
    rd = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
    bad = 0
    for pref, label in (("fusion-v", "Fusion"), ("ultra-v", "Ultra")):
        newest = badges.get(pref)
        if not newest:
            continue
        want = newest.replace(pref, "")
        claimed = sorted(set(re.findall(rf"{label} v(\d+\.\d+(?:\.\d+)?)", rd)))
        if want not in claimed:
            print(f"  DIFF  {label}: README 写 {claimed}，远端最新是 v{want}")
            bad += 1
        else:
            print(f"  OK    {label} v{want}（READM 已指向最新）")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="与 README / 站点比对")
    ap.add_argument("--check-releases", action="store_true",
                    help="与 GitHub 最新 Release 比对（需网络）")
    ap.add_argument("--sync", action="store_true",
                    help="把 README 的现役版本号与包体写成远端最新值")
    ap.add_argument("--dry-run", action="store_true", help="配合 --sync：只看差异不写")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if a.sync:
        print("doc_numbers --sync" + (" (dry-run)" if a.dry_run else ""))
        sys.exit(sync(a.dry_run))
    if a.check_releases:
        print("doc_numbers --check-releases")
        sys.exit(check_releases())
    d = actual()
    if a.check:
        print("doc_numbers --check")
        sys.exit(check(d))
    for k, v in d.items():
        print(f"  {k:18} {v:,}" if isinstance(v, int) else f"  {k:18} {v}")
