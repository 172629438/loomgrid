#-*- coding: utf-8 -*-
"""发版脚本：参数化，取代之前每次重写的 release_23xx.py。

之前 16 个版本每次都复制一份 release_23xx.py 改版本号，
16 份几乎一样的文件散在 _tmp 里，删了就没了。
这个脚本把公共逻辑固化下来，以后发版只需：
    python tools/publish_release.py --version 2.3.40 --title "..." --notes-file body.md

它做的事：
  ① 校验 zip 存在、算 SHA-256、打印体积
  ② 校验本地 HEAD == origin/main（不许用未推送的代码发版）
  ③ 用 release_hints.compose() 拼正文（下载提示 + 版本指引）
  ④ 建 draft → 传 zip → 转正 → 标 latest
  ⑤ 回读核验：isDraft / 资产 uploaded / digest 与本地一致 / 正文含 SHA

正文里的 `{sha256}` `{zip_size}` `{version}` `{tag}` 会被替换，
写 notes-file 时直接用占位符即可。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GH = os.path.join(os.path.dirname(REPO), "..", "tools", "gh", "bin", "gh.exe")
GH = os.path.normpath(GH)


def run(args, cwd=None):
    return subprocess.run(args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=cwd or REPO)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def gh_bin() -> str:
    if os.path.exists(GH):
        return GH
    # 退化：PATH 里找
    return "gh"


def main() -> int:
    ap = argparse.ArgumentParser(description="发 Fusion Release")
    ap.add_argument("--version", required=True, help="如 2.3.40")
    ap.add_argument("--title", default=None, help="Release 标题（默认自动）")
    ap.add_argument("--notes-file", default=None,
                    help="正文模板文件；支持 {sha256} {zip_size} {version} {tag} 占位符")
    ap.add_argument("--zip", default=None, help="zip 路径（默认按版本号推）")
    ap.add_argument("--body-only", action="store_true",
                    help="只生成正文并打印路径，不建 Release")
    args = ap.parse_args()

    tag = f"fusion-v{args.version}"
    zip_path = args.zip or os.path.join(
        REPO, "dist", f"LoomGrid-Fusion-v{args.version}-win64.zip")

    if not os.path.exists(zip_path):
        print(f"找不到 zip: {zip_path}", file=sys.stderr)
        print("先跑： python tools/pack_release.py fusion %s" % args.version,
              file=sys.stderr)
        return 2

    size_mb = os.path.getsize(zip_path) / 1024 / 1024
    digest = sha256_file(zip_path)
    print(f"zip      {os.path.basename(zip_path)}  {size_mb:.1f} MB")
    print(f"SHA-256  {digest}")

    local = run(["git", "rev-parse", "HEAD"]).stdout.strip()
    remote = run(["git", "rev-parse", "origin/main"]).stdout.strip()
    print(f"HEAD     local={local[:8]} remote={remote[:8]}")
    if local != remote:
        print("本地与远端不一致 —— 先 push，否则发的是旧代码", file=sys.stderr)
        return 3

    # 正文
    sys.path.insert(0, os.path.join(REPO, "tools"))
    import importlib
    import release_hints
    importlib.reload(release_hints)
    hint = release_hints.download_hint(os.path.basename(zip_path), size_mb)
    guide = release_hints.version_guide(tag)

    if args.notes_file and os.path.exists(args.notes_file):
        tpl = open(args.notes_file, encoding="utf-8").read()
        tpl = (tpl.replace("{sha256}", digest)
                  .replace("{zip_size}", f"{size_mb:.1f}")
                  .replace("{version}", args.version)
                  .replace("{tag}", tag))
        body = tpl
        if "<!-- dl-hint" not in body:
            body = hint + "\n\n---\n\n" + body
        if "<!-- ver-guide" not in body:
            body = body + "\n\n---\n\n" + guide
    else:
        body = hint + "\n\n---\n\n## v" + args.version + "\n\n" + guide

    body_path = os.path.join(os.environ.get("TEMP", "."), f"rel_body_{args.version}.md")
    open(body_path, "w", encoding="utf-8").write(body)
    print(f"正文     {len(body)} 字 → {body_path}")

    if args.body_only:
        return 0

    title = args.title or f"Fusion v{args.version}"
    gh = gh_bin()

    r = run([gh, "release", "create", tag, zip_path, "--title", title,
             "--notes-file", body_path, "--draft"])
    if r.returncode != 0:
        print("create 失败:", (r.stderr or r.stdout)[-500:], file=sys.stderr)
        return 4

    r = run([gh, "release", "edit", tag, "--draft=false", "--latest"])
    if r.returncode != 0:
        print("转正失败:", (r.stderr or r.stdout)[-500:], file=sys.stderr)
        return 5

    r = run([gh, "release", "view", tag,
             "--json", "isDraft,isPrerelease,assets,body"])
    if r.returncode == 0:
        d = json.loads(r.stdout)
        print(f"draft={d.get('isDraft')}  pre={d.get('isPrerelease')}")
        ok_digest = False
        for a in d.get("assets", []):
            rd = (a.get("digest") or "").replace("sha256:", "")
            ok_digest = rd == digest
            print(f"  {a.get('name')}  {a.get('size')/1048576:.1f} MB  "
                  f"state={a.get('state')}  digest一致={ok_digest}")
        has_sha = digest in (d.get("body") or "")
        print(f"  正文含 SHA: {has_sha}")
        if d.get("isDraft") or not ok_digest or not has_sha:
            print("核验未通过", file=sys.stderr)
            return 6

    print(f"\n完成: https://github.com/172629438/loomgrid/releases/tag/{tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
