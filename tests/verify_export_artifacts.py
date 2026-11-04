# -*- coding: utf-8 -*-
"""验证导出结果里的 artifacts（显式产物类型）。

为什么需要这个字段
------------------
`result.files` 是一个**混合列表**，顺序是「PNG, xlsx, PNG, xlsx…」——
但只在每组都导宽表时成立。某组只导 PNG，顺序就变了。
按扩展名分流能工作，但那是依赖一个契约没保证的约定；加第三种产物还要再改。

所以 result 新增 `artifacts: [{name, kind}]`，kind ∈ {chart, midwide}。
`files` 保持原样，现有调用零影响。

判据：
  ① 不导宽表 → artifacts 全是 chart，数量 == files 数量，name 逐一对应
  ② 导宽表   → artifacts 含 midwide，总数 = 组数 × 2
  ③ 兼容性   → files 字段仍在，且内容与 artifacts 的 name 集合一致
  ④ 多分组   → artifacts 的数量随组数线性增长

用法：
    python tests/verify_export_artifacts.py
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


def wait_done(task_id: str, timeout: float = 120.0) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = CLIENT.get("/api/export/status", params={"task_id": task_id})
        body = r.json()
        if not body.get("ok"):
            return {"status": "error", "error": str(body.get("error"))}
        d = body["data"]
        if d.get("status") in ("done", "error"):
            return d
        time.sleep(0.3)
    return {"status": "timeout"}


def export(session_id: str, out_dir: Path, **extra) -> dict:
    payload = {
        "session_id": session_id,
        "output_dir": str(out_dir),
        "title_template": "{table_no}_{year_month}",
        "filename_template": "{table_no}_{year_month}",
    }
    payload.update(extra)
    r = CLIENT.post("/api/export/run", json=payload)
    body = r.json()
    if not body.get("ok"):
        return {"status": "error", "error": str(body.get("error"))}
    return wait_done(body["data"]["task_id"])


def result_of(status: dict) -> dict:
    return status.get("result") or {}


def main() -> int:
    xlsx = next(iter(sorted((ROOT / "baseline" / "_inputs" / "L2").glob("*.xlsx"))), None)
    if xlsx is None:
        print("找不到 L2 输入")
        return 1

    r = CLIENT.post("/api/ingest/inspect", json={"path": str(xlsx)})
    sheet = r.json()["data"]["sheets"][0]
    r = CLIENT.post("/api/ingest/mapping", json={
        "path": str(xlsx), "sheet": sheet["name"], "mapping": sheet["suggest"]})
    body = r.json()
    session_id = body["data"]["session_id"]
    n_groups = len(body["data"]["groups"])
    print(f"session={session_id[:8]}…  分组数={n_groups}")

    base = Path(tempfile.mkdtemp(prefix="lg-art-"))
    d1 = base / "no_midwide"
    d2 = base / "with_midwide"
    for d in (d1, d2):
        d.mkdir(parents=True, exist_ok=True)

    print("\n[① 不导宽表]")
    st = export(session_id, d1, export_midwide=False)
    check(st.get("status") == "done", "导出完成", str(st))
    res = result_of(st)
    arts = res.get("artifacts") or []
    files = res.get("files") or []
    check(len(arts) == n_groups, f"artifacts 数量 == 组数（{len(arts)} / {n_groups}）", str(arts))
    check(all(a.get("kind") == "chart" for a in arts), "全部是 chart", str(arts))
    check(len(files) == len(arts), f"files 与 artifacts 等长（{len(files)}）", str(files))
    check([a["name"] for a in arts] == list(files), "name 与 files 逐一对应", f"{arts} vs {files}")

    print("\n[② 导宽表]")
    st2 = export(session_id, d2, export_midwide=True)
    check(st2.get("status") == "done", "导出完成", str(st2))
    res2 = result_of(st2)
    arts2 = res2.get("artifacts") or []
    check(len(arts2) == n_groups * 2, f"artifacts 数量 == 组数×2（{len(arts2)} / {n_groups*2}）", str(arts2))
    kinds = [a.get("kind") for a in arts2]
    check(kinds.count("chart") == n_groups, f"chart 数量 == 组数（{kinds.count('chart')}）", str(kinds))
    check(kinds.count("midwide") == n_groups, f"midwide 数量 == 组数（{kinds.count('midwide')}）", str(kinds))

    print("\n[③ files 字段向后兼容]")
    check("files" in res2 and len(res2["files"]) == len(arts2),
          "files 仍在且等长", f"{len(res2.get('files') or [])} vs {len(arts2)}")
    check(set(res2["files"]) == set(a["name"] for a in arts2),
          "files 的集合 == artifacts 的 name 集合")

    print("\n[④ 磁盘产物与 artifacts 对得上]")
    on_disk = {p.name for p in d2.iterdir() if p.is_file()}
    declared = {a["name"] for a in arts2}
    check(declared <= on_disk, "每个 artifact 都真的落盘了",
          f"缺：{declared - on_disk}")
    pngs = [n for n in on_disk if n.endswith(".png")]
    xlsxs = [n for n in on_disk if n.endswith(".xlsx")]
    check(len(pngs) == n_groups and len(xlsxs) == n_groups,
          f"磁盘上 PNG {len(pngs)} / xlsx {len(xlsxs)}", f"{sorted(on_disk)}")

    print("\n[⑤ 单组导出（顺序变化场景）]")
    d3 = base / "single"
    d3.mkdir(parents=True, exist_ok=True)
    st3 = export(session_id, d3, groups=[1], export_midwide=True)
    res3 = result_of(st3)
    arts3 = res3.get("artifacts") or []
    check(len(arts3) == 2, f"单组导宽表 → 2 个 artifact（{len(arts3)}）", str(arts3))
    check([a["kind"] for a in arts3] == ["chart", "midwide"],
          "顺序是 chart 后 midwide", str([a["kind"] for a in arts3]))

    print(f"\n合计 PASS {passed} / FAIL {failed}")
    print(f"输出目录：{base}")
    if failed:
        return 1
    shutil.rmtree(base, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
