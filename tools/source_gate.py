# -*- coding: utf-8 -*-
"""发版前的源码归档闸门。

背景（2026-10-28 事故）：
    v3.2.2~v3.2.7 六个版本，每次改完本地文件直接打包发 Release，
    源码从未单独提交。结果本地文件被下一版覆盖后，这些版本的源码永久丢失，
    只能靠打包副产物（loomgrid_ultra_main.py）侥幸找回 v3.2.7。

这个脚本的作用：把「提交源码」从「可选动作」变成「发版前置条件」。
    · 检查工作树里主程序是否有未提交的改动
    · 有则打印 diff 统计并要求先提交
    · 可用 --fix 自动完成提交（走 Git Data API，与项目既有做法一致）

用法：
    python tools/source_gate.py --check          # 发版前检查（退出码非 0 = 未提交）
    python tools/source_gate.py --fix -m "说明"   # 自动提交主程序源码
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GH = r"C:\Users\LENOVO\.hanako\tools\gh\bin\gh.exe"
REMOTE = "172629438/loomgrid"

# 需要纳入版本管理的源码（发版前必须有提交）
TRACKED = [
    "负荷曲线批量生成Ultra.py",
    "负荷曲线批量生成工具 Pro 融合版 (v2.3).py",
    "参考程序.py",
    "备份程序.py",
]


def git(*a, cwd=REPO):
    return subprocess.run(["git"] + list(a), cwd=cwd, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def status_of(fname: str) -> str:
    """返回该文件在 git 里的状态：clean / modified / untracked / absent"""
    p = os.path.join(REPO, fname)
    if not os.path.exists(p):
        return "absent"
    r = git("status", "--porcelain", "--", fname)
    out = (r.stdout or "").strip()
    if not out:
        return "clean"
    code = out[:2].strip()
    if code == "??":
        return "untracked"
    return "modified"


def check() -> int:
    print("发版源码闸门 —— 检查工作树\n")
    bad = []
    for f in TRACKED:
        st = status_of(f)
        mark = {"clean": "已提交", "modified": "有未提交改动",
                "untracked": "从未入库", "absent": "文件不存在"}[st]
        flag = "OK " if st == "clean" else "NG "
        print(f"  [{flag}] {f:<44}{mark}")
        if st != "clean":
            bad.append(f)

    if bad:
        print(f"\n❌ {len(bad)} 个源码文件未提交。发版前必须先提交，")
        print("   否则下一个版本覆盖本地文件后，这一版的源码将永久丢失。")
        print("   修复：python tools/source_gate.py --fix -m \"commit 说明\"")
        return 1
    print("\n✅ 所有源码均已提交，可以发版。")
    return 0


def gh_api(method, path, body=None):
    args = [GH, "api", "--method", method, path]
    inp = json.dumps(body, ensure_ascii=False) if body is not None else None
    if inp:
        args += ["--input", "-"]
    r = subprocess.run(args, capture_output=True, text=True,
                       encoding="utf-8", input=inp)
    if r.returncode != 0:
        raise RuntimeError(f"gh api 失败: {r.stderr[:300]}")
    out = (r.stdout or "").strip()
    return json.loads(out.lstrip("\ufeff")) if out else {}


def fix(message: str) -> int:
    todo = [f for f in TRACKED if status_of(f) != "clean" and status_of(f) != "absent"]
    if not todo:
        print("无待提交文件")
        return 0
    print(f"提交 {len(todo)} 个源码文件…")
    head = gh_api("GET", f"repos/{REMOTE}/git/ref/heads/main")["object"]["sha"]
    base_tree = gh_api("GET", f"repos/{REMOTE}/git/commits/{head}")["tree"]["sha"]

    entries = []
    for rel in todo:
        raw = open(os.path.join(REPO, rel), "rb").read()
        blob = gh_api("POST", f"repos/{REMOTE}/git/blobs",
                      {"content": base64.b64encode(raw).decode("ascii"),
                       "encoding": "base64"})
        entries.append({"path": rel, "mode": "100644", "type": "blob",
                        "sha": blob["sha"]})
        print(f"  {rel}  {len(raw)} B")
    tree = gh_api("POST", f"repos/{REMOTE}/git/trees",
                  {"base_tree": base_tree, "tree": entries})
    commit = gh_api("POST", f"repos/{REMOTE}/git/commits",
                    {"message": message, "tree": tree["sha"], "parents": [head]})
    gh_api("PATCH", f"repos/{REMOTE}/git/refs/heads/main", {"sha": commit["sha"]})
    print(f"OK main -> {commit['sha'][:8]}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--fix", action="store_true")
    ap.add_argument("-m", "--message", default="")
    a = ap.parse_args()
    if a.fix:
        return fix(a.message or "chore: 提交源码快照")
    return check()


if __name__ == "__main__":
    sys.exit(main())
