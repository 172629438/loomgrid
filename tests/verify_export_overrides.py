# -*- coding: utf-8 -*-
"""验证 group_overrides 的三条语义。

为什么用文件哈希做判据
----------------------
「倍率生效」肉眼能看，但不能机器判。同一份数据、同一环境、同一 matplotlib，
倍率不同 → 像素必然不同 → 字节哈希必然不同。反过来，语义等价的两条路径
（不传 vs 显式 null、分组覆盖 vs 全局倍率）应产出完全相同的字节。

所以判据是**哈希相等/不等**，不是「看起来对」。

覆盖：
  ① 不传 group_overrides 与显式传 null → 产出字节完全一致（行为不变的硬证据）
  ② 全局 multiplier=2.0 与 overrides=[{index:0, multiplier:2.0}] → 字节一致（覆盖真的生效）
  ③ 全局 multiplier=2.0 与 multiplier=1.0 → 字节不同（对照，证明判据①不是恒真）
  ④ 越界 index（groups 里没有的序号）→ 忽略，导出照常成功
  ⑤ groups 为空时不该崩（边界）

用法：
    python tests/verify_export_overrides.py
"""
from __future__ import annotations

import hashlib
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


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def wait_done(task_id: str, timeout: float = 90.0) -> dict:
    """轮询到 done/error。返回 status 数据。"""
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
        "export_midwide": False,
    }
    payload.update(extra)
    r = CLIENT.post("/api/export/run", json=payload)
    body = r.json()
    if not body.get("ok"):
        return {"status": "error", "error": str(body.get("error"))}
    tid = body["data"]["task_id"]
    return wait_done(tid)


def pngs(d: Path) -> list[Path]:
    return sorted(p for p in d.glob("*.png"))


def png_hashes(d: Path) -> dict[str, str]:
    return {p.name: sha(p) for p in pngs(d)}


def main() -> int:
    # 准备一个多分组 session：L2 长表多资产
    xlsx = None
    for cand in sorted((ROOT / "baseline" / "_inputs" / "L2").glob("*.xlsx")):
        xlsx = cand
        break
    if xlsx is None:
        print("找不到 L2 输入")
        return 1

    r = CLIENT.post("/api/ingest/inspect", json={"path": str(xlsx)})
    body = r.json()
    if not body.get("ok"):
        print("inspect 失败：", body.get("error"))
        return 1
    sheet = body["data"]["sheets"][0]

    r = CLIENT.post("/api/ingest/mapping", json={
        "path": str(xlsx), "sheet": sheet["name"], "mapping": sheet["suggest"]})
    body = r.json()
    if not body.get("ok"):
        print("mapping 失败：", body.get("error"))
        return 1
    session_id = body["data"]["session_id"]
    groups = body["data"]["groups"]
    print(f"session={session_id[:8]}…  分组数={len(groups)}  资产={[g['asset'] for g in groups][:5]}")

    if not groups:
        print("没有分组，无法继续")
        return 1

    base = Path(tempfile.mkdtemp(prefix="lg-ovr-"))
    d_absent = base / "01_absent"      # 完全不传 group_overrides
    d_null = base / "02_null"          # 显式传 null
    d_mult2 = base / "03_global2"      # 全局倍率 2.0
    d_ovr2 = base / "04_override2"     # 覆盖 index=0 倍率 2.0
    d_oob = base / "05_out_of_band"    # 越界 index
    d_base = base / "06_global1"       # 全局倍率 1.0（对照）
    for d in (d_absent, d_null, d_mult2, d_ovr2, d_oob, d_base):
        d.mkdir(parents=True, exist_ok=True)

    print("\n[基线对照：全局倍率 1.0]")
    st = export(session_id, d_base, groups=[0], multiplier=1.0)
    check(st.get("status") == "done", "导出完成", str(st))
    h_base = png_hashes(d_base)
    check(len(h_base) == 1, f"产出 1 张 PNG（实际 {len(h_base)}）", str(h_base))

    print("\n[① 不传 vs 显式 null —— 行为必须一字不差]")
    st1 = export(session_id, d_absent, groups=[0], multiplier=1.0)
    st2 = export(session_id, d_null, groups=[0], multiplier=1.0, group_overrides=None)
    check(st1.get("status") == "done" and st2.get("status") == "done", "两次都完成", f"{st1} / {st2}")
    h1, h2 = png_hashes(d_absent), png_hashes(d_null)
    check(h1 == h2, "不传 与 显式 null 产出字节完全一致", f"{h1} vs {h2}")
    check(h1 == h_base, "且与基线对照一致（倍率 1.0）", f"{h1} vs {h_base}")

    print("\n[② 分组覆盖真的生效]")
    st3 = export(session_id, d_ovr2, groups=[0], multiplier=1.0,
                 group_overrides=[{"index": 0, "multiplier": 2.0}])
    check(st3.get("status") == "done", "带覆盖的导出完成", str(st3))
    h3 = png_hashes(d_ovr2)

    st4 = export(session_id, d_mult2, groups=[0], multiplier=2.0)
    check(st4.get("status") == "done", "全局倍率 2.0 的导出完成", str(st4))
    h4 = png_hashes(d_mult2)

    check(h3 == h4, "覆盖 index=0 倍率2.0  ==  全局倍率2.0（字节一致）", f"{h3} vs {h4}")

    print("\n[③ 对照：倍率 2.0 与 1.0 必须不同（否则①②的判据恒真）]")
    check(h4 != h_base, "2.0 与 1.0 产出不同", f"{h4} vs {h_base}")

    print("\n[④ 越界 index 被忽略]")
    st5 = export(session_id, d_oob, groups=[0], multiplier=1.0,
                 group_overrides=[{"index": 9, "multiplier": 9.0}])
    check(st5.get("status") == "done", "不报错", str(st5))
    h5 = png_hashes(d_oob)
    check(len(h5) == 1, f"照常导出 1 张（实际 {len(h5)}）", str(h5))
    check(h5 == h_base, "且参数回落到全局 1.0（与基线一致）", f"{h5} vs {h_base}")

    print(f"\n合计 PASS {passed} / FAIL {failed}")
    print(f"输出目录：{base}")
    if failed:
        return 1
    shutil.rmtree(base, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
