# -*- coding: utf-8 -*-
"""Release 正文的两段固定指引。

## 为什么要这个文件

GitHub 每个 Release 页面最下方都会自动附 `Source code (zip)` / `(tar.gz)`。
那不是我们上传的，是仓库源码。用户误点后在 zip 里看到一堆 `.py`，
会以为包做错了——这件事真实发生过一次。

所以正文必须有两段说明，位置不同、解决的问题也不同：

    顶部  download_hint()   —— 下载哪个文件（exe 包 vs 自动生成的源码包）
    末尾  version_guide()   —— 该用哪个版本（现役 vs 历史）

## 用法

发版时拼装正文：

    from release_hints import compose
    body = compose(
        core_markdown,
        tag="ultra-v3.2.3",
        zip_name="LoomGrid-Ultra-v3.2.3-win64.zip",
        zip_mb=273,
        prod_fusion="fusion-v2.3.1",
        prod_ultra="ultra-v3.2.3",
    )
    # 再写进文件，gh release create --notes-file

给已有 Release 补：

    python tools/release_hints.py --tag ultra-v3.2.2 --fix-existing

幂等：正文里已有 `<!-- dl-hint -->` / `<!-- ver-guide -->` 标记就跳过。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

GH = r"C:\Users\LENOVO\.hanako\tools\gh\bin\gh.exe"
REPO = "172629438/loomgrid"

MARK_DL = "<!-- dl-hint -->"
MARK_VER = "<!-- ver-guide -->"

# 当前生产版本：改版时只改这里
PROD_FUSION = "fusion-v2.3.1"
PROD_ULTRA = "ultra-v3.2.2"


def download_hint(zip_name: str, zip_mb: float) -> str:
    """顶部：告诉用户点哪个文件。"""
    stem = zip_name[:-4] if zip_name.lower().endswith(".zip") else zip_name
    return (
        f"{MARK_DL}\n"
        f"> **下载这个文件：`{zip_name}`**（{zip_mb:.0f} MB）\n"
        f">\n"
        f"> 解压后进入 `{stem}\\` 目录，双击 `{stem}.exe` 即可运行，"
        f"不需要装 Python 或任何运行库。\n"
        f">\n"
        f"> 页面最下方 GitHub 自动附带的 **`Source code (zip)` / `Source code (tar.gz)`** "
        f"是**源代码**，不是程序——那是给开发者读代码用的，下载后双击不了任何东西。\n"
        f">\n"
        f"> 解压后的目录里会看到 `_internal\\`，那是运行时依赖，**不要移动或删除**；"
        f"其中若有个别 `.py` 文件（来自第三方库 `qt_material` 的资源配置），"
        f"属于正常，不影响使用。\n\n"
        f"---\n\n"
    )


def version_guide(tag: str, prod_fusion: str = PROD_FUSION,
                  prod_ultra: str = PROD_ULTRA) -> str:
    """末尾：告诉用户该用哪个版本。"""
    is_current = tag in (prod_fusion, prod_ultra)
    fv = prod_fusion.replace("fusion-", "")
    uv = prod_ultra.replace("ultra-", "")

    if is_current:
        line = "**你正在看的这一版就是当前生产版本，可以直接用。**\n\n"
    else:
        line = ("**这是历史版本，不建议用于正式方案。** "
                "本版留着是为了复现当时的行为、做版本对比——"
                "它和最新版之间已经积累了一批修复。\n\n")

    f_mark = "（本页）" if tag == prod_fusion else ""
    u_mark = "（本页）" if tag == prod_ultra else ""

    table = (
        "| 你要什么 | 用哪个 | 说明 |\n"
        "|---|---|---|\n"
        f"| 正式做方案，稳定优先 | **Fusion {fv}**{f_mark} | "
        "轻量、批处理快，10.6k 行，跑一整个文件夹不卡 |\n"
        f"| MILP 容量规划 / Pareto / 13 步诊断 | **Ultra {uv}**{u_mark} | "
        "功能最全，36.2k 行，LP + MILP + 仿真 + 贪心四引擎 |\n"
        "| 复现历史行为、版本对比 | 本页所属版本 | 历史快照，不建议用于正式方案 |\n"
    )

    tail = (
        "\n不确定就看 **[在线介绍](https://172629438.github.io/loomgrid/)**——"
        "里面那条负荷曲线可以用鼠标查任意时刻的数据，"
        "还有三个引擎的收益对照器可以自己调参数。\n\n"
        "全部版本：[Releases](https://github.com/172629438/loomgrid/releases)　"
        "·　源码：[main](https://github.com/172629438/loomgrid)\n"
    )

    return f"{MARK_VER}\n---\n\n## 该下载哪个版本\n\n{line}{table}{tail}"


def compose(core: str, tag: str, zip_name: str, zip_mb: float,
            prod_fusion: str = PROD_FUSION, prod_ultra: str = PROD_ULTRA) -> str:
    """拼出完整正文：下载指引 → 正文 → 版本指引。"""
    return (download_hint(zip_name, zip_mb).rstrip() + "\n\n"
            + core.strip() + "\n\n"
            + version_guide(tag, prod_fusion, prod_ultra))


# ── 给已有 Release 补（命令行） ──────────────────────────────────────

def _gh(*args):
    r = subprocess.run([GH] + list(args), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(r.stderr[:300])
    return r.stdout


def _patch(rel_id, body):
    r = subprocess.run(
        [GH, "api", "-X", "PATCH", f"repos/{REPO}/releases/{rel_id}", "--input", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        input=json.dumps({"body": body}))
    if r.returncode != 0:
        raise RuntimeError(r.stderr[:300])


def fix_existing(only_tag: str | None = None):
    rels = json.loads(_gh("api", f"repos/{REPO}/releases?per_page=50").lstrip("\ufeff"))
    for r in sorted(rels, key=lambda x: x["tag_name"]):
        tag = r["tag_name"]
        if only_tag and tag != only_tag:
            continue
        zips = [a for a in r.get("assets", []) if a["name"].lower().endswith(".zip")]
        if not zips:
            print(f"  {tag:<22} 无 zip，跳过")
            continue
        a = zips[0]
        body = r.get("body") or ""
        changed = False

        if MARK_DL not in body:
            body = download_hint(a["name"], a["size"] / 1024 / 1024) + body.lstrip()
            changed = True
        if MARK_VER not in body:
            body = body.rstrip() + "\n\n" + version_guide(tag)
            changed = True

        if not changed:
            print(f"  {tag:<22} 已有两段，跳过")
            continue
        _patch(r["id"], body)
        kind = "现役" if tag in (PROD_FUSION, PROD_ULTRA) else "历史"
        print(f"  {tag:<22} OK  [{kind}]  {a['name']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", help="只处理指定的 tag")
    ap.add_argument("--fix-existing", action="store_true",
                    help="给已有 Release 补齐两段指引")
    args = ap.parse_args()

    if args.fix_existing:
        fix_existing(args.tag)
    else:
        # 无参数时打印一段样例，方便复制
        print(compose("（正文内容写这里）", "ultra-v3.2.2",
                      "LoomGrid-Ultra-v3.2.2-win64.zip", 272.8))
