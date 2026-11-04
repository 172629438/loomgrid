# -*- coding: utf-8 -*-
"""验证导出失败路径：删掉源文件后导出，failed 里要有可读的结构。

为什么需要这个
--------------
V6 的失败归因只有在「真的有失败」时才能验。正常路径不产生失败，
所以这里造一个：把源文件复制到临时目录 → 建 session → 删文件 → 导出。

要验证的是**失败数据的形状**（V6 拿它做骨架聚合），不是「失败了」这件事：
  · failed 是非空数组，每条有 group + error
  · groups_failed > 0 且 groups_succeeded == 0
  · error 文本含文件名或「未找到/不存在」类线索（能定位是哪个文件）

用法：
    python tests/verify_export_failure.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("LGM_ALLOW_ROOTS", str(ROOT / "baseline" / "_inputs"))

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


def wait_done(task_id: str, timeout: float = 90.0) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        body = CLIENT.get("/api/export/status", params={"task_id": task_id}).json()
        if not body.get("ok"):
            return {"status": "error", "error": str(body.get("error"))}
        d = body["data"]
        if d.get("status") in ("done", "error"):
            return d
        time.sleep(0.3)
    return {"status": "timeout"}


def main() -> int:
    src = ROOT / "baseline" / "_inputs" / "L2" / "L2_长表多资产.xlsx"
    tmp = Path(tempfile.mkdtemp(prefix="lg-fail-"))
    work = tmp / "src"
    work.mkdir(parents=True)
    copy = work / "L2_长表多资产.xlsx"
    shutil.copy2(src, copy)

    # 临时目录不在 LGM_ALLOW_ROOTS 里，先走 allow 端点授权（与桌面壳同一路径）
    allow = CLIENT.post("/api/ingest/allow", json={"paths": [str(copy)]}).json()
    if not allow.get("ok") or str(copy) not in (allow.get("data") or {}).get("added", []):
        print("allow 失败：", allow)
        return 1

    r = CLIENT.post("/api/ingest/inspect", json={"path": str(copy)})
    body = r.json()
    if not body.get("ok"):
        print("inspect 失败：", body.get("error"))
        return 1
    sheet = body["data"]["sheets"][0]

    r = CLIENT.post("/api/ingest/mapping", json={
        "path": str(copy), "sheet": sheet["name"], "mapping": sheet["suggest"]})
    body = r.json()
    if not body.get("ok"):
        print("mapping 失败：", body.get("error"))
        return 1
    session_id = body["data"]["session_id"]
    n_groups = len(body["data"]["groups"])
    print(f"session={session_id[:8]}…  分组数={n_groups}")

    # 删掉源文件 —— 导出时 get_group_data 应当拿不到数据
    copy.unlink()
    print(f"已删除源文件：{copy.name}")

    out = tmp / "out"
    out.mkdir()
    r = CLIENT.post("/api/export/run", json={
        "session_id": session_id,
        "output_dir": str(out),
        "title_template": "{table_no}_{year_month}",
        "filename_template": "{table_no}_{year_month}",
    })
    body = r.json()
    check(body.get("ok") is True, "导出任务受理（失败发生在执行期，不是受理期）", str(body))
    if not body.get("ok"):
        return 1

    st = wait_done(body["data"]["task_id"])
    check(st.get("status") == "done", "任务跑完（不是 500）", str(st.get("status")))

    res = st.get("result") or {}
    fails = res.get("failed") or []
    check(len(fails) > 0, f"failed 非空（{len(fails)} 条）", str(fails))
    check(res.get("groups_failed", 0) > 0, f"groups_failed={res.get('groups_failed')}", str(res))
    check(res.get("groups_succeeded", -1) == 0, "groups_succeeded=0", str(res.get("groups_succeeded")))

    if fails:
        first = fails[0]
        check("group" in first, "每条有 group 字段", str(first))
        check("error" in first and first["error"], "每条有非空 error", str(first))
        text = " ".join(str(f.get("error", "")) for f in fails)
        check(any(k in text for k in ("未找到", "不存在", "无数据", "读", "File", "Not")),
              "error 里有可定位的线索", text[:200])

        # 骨架聚合的可行性：把所有 error 喂给同一套规则后应能收敛成少量组
        import re
        def skeleton(raw: str) -> str:
            s = re.sub(r"[A-Za-z]:\\[^\s，。；,;]*", "<路径>", raw)
            s = re.sub(r"[「『\"'][^」』\"']*[」』\"']", "<名>", s)
            s = re.sub(r"\d+(?:\.\d+)?", "<n>", s)
            return re.sub(r"\s+", " ", s).strip()

        sk = {skeleton(f.get("error", "")) for f in fails}
        check(len(sk) <= len(fails), f"骨架聚合不增加组数（{len(sk)} ≤ {len(fails)}）", str(sk))
        print(f"    骨架样例：{list(sk)[0][:120] if sk else '—'}")

    print(f"\n合计 PASS {passed} / FAIL {failed}")
    shutil.rmtree(tmp, ignore_errors=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
