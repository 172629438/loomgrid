# -*- coding: utf-8 -*-
"""验证 /api/export/adopt 的三道门。

adopt 会扫描目录——和 /api/ingest/allow 同级别的读取面。所以判据不是
「能读出来」，而是「不该读的地方读不到、不该存在的路由不存在」。

判据：
  ① 未设 LGM_DEV_TOOLS 时，路由**不注册**（独立子进程确认 /adopt 不在 route 列表里）
  ② 设了之后：扫描目录，png → chart，xlsx → midwide，html 记为 report 不进 files
  ③ 系统目录 / 程序目录 → EXPORT_ADOPT_FORBIDDEN
  ④ 不存在的目录 / 文件路径 → EXPORT_ADOPT_INVALID
  ⑤ 返回带 adopted: true（提醒调用方这不是真导出）
  ⑥ 造出来的 task 能被 /status 读到，且 status=done

用法：
    python tests/verify_export_adopt.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 必须在 import api.app 之前设，因为路由在模块加载时决定是否注册
os.environ["LGM_DEV_TOOLS"] = "1"

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


def main() -> int:
    print("[① 未设 LGM_DEV_TOOLS 时路由不注册]")
    probe = (
        "import os; os.environ.pop('LGM_DEV_TOOLS', None);"
        "from api.app import app;"
        "print([r.path for r in app.routes if 'adopt' in getattr(r,'path','')])"
    )
    out = subprocess.run([sys.executable, "-c", probe], cwd=str(ROOT),
                         capture_output=True, text=True)
    check(out.stdout.strip() == "[]",
          "子进程未设变量 → routes 里没有 /adopt",
          f"stdout={out.stdout.strip()} stderr={out.stderr.strip()[:200]}")

    print("\n[⑤⑥ 正常目录]")
    d = Path(tempfile.mkdtemp(prefix="lg-adopt-"))
    (d / "M001_2026-01_负荷曲线.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    (d / "M002_2026-01_负荷曲线.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    (d / "M001_2026-01_负荷曲线.xlsx").write_bytes(b"PK\x03\x04" + b"\x00" * 64)
    (d / "生成报告.html").write_text("<html></html>", encoding="utf-8")
    (d / "忽略我.txt").write_text("x", encoding="utf-8")

    r = CLIENT.post("/api/export/adopt", json={"output_dir": str(d)})
    body = r.json()
    check(body.get("ok") is True, "adopt 成功", str(body))
    data = body.get("data") or {}
    check(data.get("adopted") is True, "带 adopted: true 标记", str(data.get("adopted")))

    arts = data.get("artifacts") or []
    kinds = {a["name"]: a["kind"] for a in arts}
    check(kinds.get("M001_2026-01_负荷曲线.png") == "chart", "png → chart", str(kinds))
    check(kinds.get("M001_2026-01_负荷曲线.xlsx") == "midwide", "xlsx → midwide", str(kinds))
    check("生成报告.html" not in kinds, "html 不进程 files（归 report）", str(kinds))
    check("忽略我.txt" not in kinds, "无关文件被跳过", str(kinds))
    check(data.get("report") == "生成报告.html", "report 字段指到 html", str(data.get("report")))

    tid = data.get("task_id")
    check(bool(tid), "返回了 task_id", str(tid))
    if tid:
        s = CLIENT.get("/api/export/status", params={"task_id": tid}).json()
        check(s.get("ok") and (s.get("data") or {}).get("status") == "done",
              "status 可读且为 done", str(s))

    print("\n[③ 系统目录被拒]")
    win = os.environ.get("SystemRoot", r"C:\Windows")
    r = CLIENT.post("/api/export/adopt", json={"output_dir": win})
    body = r.json()
    code = (body.get("error") or {}).get("code")
    check(body.get("ok") is False and code == "EXPORT_ADOPT_FORBIDDEN",
          f"拒绝扫描 {win}", str(body))

    print("\n[④ 非法路径被拒]")
    r = CLIENT.post("/api/export/adopt", json={"output_dir": str(d / "不存在")})
    body = r.json()
    check((body.get("error") or {}).get("code") == "EXPORT_ADOPT_INVALID",
          "不存在的目录 → EXPORT_ADOPT_INVALID", str(body))

    r = CLIENT.post("/api/export/adopt", json={"output_dir": str(d / "M001_2026-01_负荷曲线.png")})
    body = r.json()
    check((body.get("error") or {}).get("code") == "EXPORT_ADOPT_INVALID",
          "传文件而非目录 → EXPORT_ADOPT_INVALID", str(body))

    print(f"\n合计 PASS {passed} / FAIL {failed}")
    shutil.rmtree(d, ignore_errors=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
