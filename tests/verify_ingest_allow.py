# -*- coding: utf-8 -*-
"""验证 /api/ingest/allow 的授权边界。

为什么要单独测这个端点
----------------------
它是本项目里唯一「能扩大文件读取范围」的入口。判据不能是「看起来挺严」，
而要逐条给出「这个路径被拒了 / 那个被放了」。

覆盖：
  ① 扩展名白名单     .xlsx / .csv / .tsv 通过；.exe / .ps1 / .txt / .dat / .zip 拒绝
  ② 系统目录         C:\\Windows\\... 拒绝
  ③ 程序目录         C:\\Program Files\\... 拒绝
  ④ 盘符根           拒绝（不是文件）
  ⑤ 目录             拒绝
  ⑥ 不存在的路径     拒绝
  ⑦ 路径穿越         规范化后判断，越界拒绝
  ⑧ 空字符串         忽略
  ⑨ 拒绝原因         每条都能给出中文原因
  ⑩ 授权前后对比     未授权 inspect → INGEST_PATH_FORBIDDEN；授权后 → 通过

用法：
    python tests/verify_ingest_allow.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 不设 LGM_ALLOW_ROOTS —— 要测的就是「没有额外白名单时」的行为
os.environ.pop("LGM_ALLOW_ROOTS", None)

from fastapi.testclient import TestClient  # noqa: E402

from api.app import app  # noqa: E402

CLIENT = TestClient(app)

passed = 0
failed = 0


def check(ok: bool, label: str, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}  {detail}")


def allow(paths: list[str]) -> dict:
    r = CLIENT.post("/api/ingest/allow", json={"paths": paths})
    body = r.json()
    assert body.get("ok"), body
    return body["data"]


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="lg-allow-test-"))

    # 造样本：合规数据文件 + 各类不该被接受的文件
    xlsx = tmp / "ok.xlsx"
    xlsx.write_bytes(b"PK\x03\x04" + b"\x00" * 100)   # 只要扩展名与存在性
    csv = tmp / "ok.csv"
    csv.write_text("date,load\n2026-01-01,100\n", encoding="utf-8")
    tsv = tmp / "ok.tsv"
    tsv.write_text("date\tload\n", encoding="utf-8")

    bad = {}
    for ext in (".exe", ".ps1", ".txt", ".dat", ".zip"):
        p = tmp / f"bad{ext}"
        p.write_text("x", encoding="utf-8")
        bad[ext] = str(p)

    print("\n[① 扩展名白名单]")
    data = allow([str(xlsx), str(csv), str(tsv)])
    check(len(data["added"]) == 3, "xlsx / csv / tsv 允许", str(data))

    for ext, p in bad.items():
        data = allow([p])
        rej = data["rejected"]
        ok = len(rej) == 1 and rej[0] == p
        check(ok, f"{ext} 拒绝", str(data))
        if ok:
            reason = data.get("reasons", {}).get(p, "")
            check("不支持的文件类型" in reason, f"{ext} 拒绝原因可读", reason)

    print("\n[② 系统目录]")
    win_ini = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "win.ini")
    data = allow([win_ini])
    check(len(data["rejected"]) == 1, f"系统文件拒绝（{win_ini}）", str(data))
    check("系统目录" in data.get("reasons", {}).get(win_ini, "")
          or "不支持的文件类型" in data.get("reasons", {}).get(win_ini, ""),
          "拒绝原因可读", str(data.get("reasons")))

    print("\n[③ 程序目录]")
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    probe = os.path.join(pf, "desktop.ini")
    data = allow([probe])
    check(len(data["rejected"]) == 1, f"程序目录拒绝（{probe}）", str(data))

    print("\n[④ 盘符根 / 目录 / 不存在 / 空字符串]")
    data = allow(["C:\\"])
    check(len(data["rejected"]) == 1, "盘符根拒绝", str(data))

    data = allow([str(tmp)])
    check(len(data["rejected"]) == 1, "目录拒绝", str(data))

    missing = str(tmp / "nope.xlsx")
    data = allow([missing])
    check(len(data["rejected"]) == 1, "不存在的文件拒绝", str(data))

    data = allow(["   ", ""])
    check(len(data["added"]) == 0 and len(data["rejected"]) == 0,
          "空白字符串被忽略", str(data))

    print("\n[⑤ 路径穿越]")
    traversal = str(tmp / ".." / ".." / "Windows" / "win.ini")
    data = allow([traversal])
    check(len(data["added"]) == 0, "路径穿越不获得授权", str(data))

    print("\n[⑥ 授权前后 inspect 对比]")
    fresh = tmp / "fresh.xlsx"
    fresh.write_bytes(b"PK\x03\x04" + b"\x00" * 100)

    r = CLIENT.post("/api/ingest/inspect", json={"path": str(fresh)})
    body = r.json()
    code = (body.get("error") or {}).get("code")
    check(body.get("ok") is False and code == "INGEST_PATH_FORBIDDEN",
          "未授权时 inspect 被拒（INGEST_PATH_FORBIDDEN）", str(body))

    data = allow([str(fresh)])
    check(str(fresh) in data["added"], "授权后 added", str(data))

    r = CLIENT.post("/api/ingest/inspect", json={"path": str(fresh)})
    body = r.json()
    # 授权通过了，但内容不是真 xlsx —— 会失败在读取阶段，错误码不应再是 FORBIDDEN
    code = (body.get("error") or {}).get("code")
    check(code != "INGEST_PATH_FORBIDDEN",
          "授权后不再被路径白名单拦（改成读取失败属预期）", str(body))

    print(f"\n合计 PASS {passed} / FAIL {failed}")
    print(f"样本目录：{tmp}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
