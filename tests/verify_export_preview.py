# -*- coding: utf-8 -*-
"""验证 /api/export/preview：文件名预览必须与落盘用同一份命名实现。

为什么要单独测
--------------
「资产名含斜杠」是 Fusion 里真实踩过的坑：`A/B栋` 不做清洗会往路径里插斜杠，
写盘直接失败。清洗逻辑在 `plotting/naming._sanitize_name`，**只能有一份**。

判据：
  ① 清洗规则本身（斜杠、Windows 保留名、超长、控制字符）
  ② 预览端点与直接调 generate_filename 完全一致（同源）
  ③ 未知占位符原样保留（用 .format 会 KeyError）
  ④ session 不存在 → 结构化错误

用法：
    python tests/verify_export_preview.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("LGM_ALLOW_ROOTS", str(ROOT / "baseline" / "_inputs"))

from fastapi.testclient import TestClient  # noqa: E402

from api.app import app  # noqa: E402
from plotting.naming import generate_filename, parse_title_template  # noqa: E402

CLIENT = TestClient(app)
TEMPLATE = "{table_no}_{year_month}_负荷曲线"

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
    print("[① 清洗规则]")
    ctx = {"table_no": "A/B栋", "asset": "A/B栋", "year_month": "2026-01", "sheet": "Sheet1"}
    fn = generate_filename(TEMPLATE, ctx)
    check("/" not in fn and "\\" not in fn, "斜杠被替换", fn)
    check(fn == "A_B栋_2026-01_负荷曲线.png", "替换成下划线且结构完整", fn)

    ctx2 = {"table_no": "CON", "year_month": "2026-01"}
    fn2 = generate_filename(TEMPLATE, ctx2)
    check(fn2.startswith("CON_"), "Windows 保留名加后缀", fn2)

    ctx3 = {"table_no": "X" * 80, "year_month": "2026-01"}
    fn3 = generate_filename(TEMPLATE, ctx3)
    # _sanitize_name 的长度上限约束的是「不含扩展名」的主体（max_len=50，截断后以 ... 结尾），
    # 加 .png 之后总长会到 54。断言按实际契约写，不拍脑袋定总长。
    base3 = fn3[:-len(".png")]
    check(len(base3) <= 50 and base3.endswith("..."),
          "超长截断：主体 ≤ 50 且以 ... 结尾", f"base={len(base3)} total={len(fn3)}")

    ctx4 = {"table_no": "A\x00B\x1fC", "year_month": "2026-01"}
    fn4 = generate_filename(TEMPLATE, ctx4)
    check("\x00" not in fn4 and "\x1f" not in fn4, "控制字符被清掉", repr(fn4))

    ctx5 = {"table_no": "..\\..\\etc", "year_month": "2026-01"}
    fn5 = generate_filename(TEMPLATE, ctx5)
    check(".." not in fn5, "路径穿越片段被压掉", fn5)

    print("\n[② 未知占位符原样保留]")
    t = parse_title_template("{table_no}_{unknown}_{year_month}", {"table_no": "T1", "year_month": "2026-01"})
    check("{unknown}" in t, "未知占位符留在标题里（不抛 KeyError）", t)

    print("\n[③ 端点与直接调用同源]")
    xlsx = ROOT / "baseline" / "_inputs" / "W1" / "W1_宽表96点.xlsx"
    r = CLIENT.post("/api/ingest/inspect", json={"path": str(xlsx)})
    body = r.json()
    if not body.get("ok"):
        check(False, "inspect", str(body.get("error")))
        return 1
    sheet = body["data"]["sheets"][0]

    r = CLIENT.post("/api/ingest/mapping", json={
        "path": str(xlsx), "sheet": sheet["name"], "mapping": sheet["suggest"]})
    body = r.json()
    if not body.get("ok"):
        check(False, "mapping", str(body.get("error")))
        return 1
    session_id = body["data"]["session_id"]

    r = CLIENT.post("/api/export/preview", json={
        "session_id": session_id, "groups": None,
        "title_template": "{table_no}-{year_month}",
        "filename_template": TEMPLATE,
    })
    body = r.json()
    if not body.get("ok"):
        check(False, "preview 端点", str(body.get("error")))
        return 1

    items = body["data"]["items"]
    check(len(items) > 0, f"返回 {len(items)} 条预览", str(body["data"].get("total")))

    # 逐条与直接调用比对
    from api.routes_ingest import get_session
    from core.models import GroupSummary

    sess = get_session(session_id)
    mismatch = 0
    for it in items:
        gs = GroupSummary.from_dict(sess["groups"][it["group_index"]])
        ctx = {
            "year_month": gs.month_str or "",
            "table_no": gs.asset_value or "未命名",
            "asset": gs.asset_value or "未命名",
            "sheet": gs.sheet_name or "",
        }
        expect_title = parse_title_template("{table_no}-{year_month}", ctx)
        expect_file = generate_filename(TEMPLATE, ctx)
        if it["title"] != expect_title or it["filename"] != expect_file:
            mismatch += 1
            print(f"    差异: {it} vs {expect_file}")
    check(mismatch == 0, "每条 title/filename 与直接调用完全一致", f"{mismatch} 条不一致")

    print(f"    示例：{items[0]['filename']}")

    print("\n[④ 错误路径]")
    r = CLIENT.post("/api/export/preview", json={"session_id": "no-such", "groups": None})
    body = r.json()
    check(r.status_code == 200 and body.get("ok") is False
          and (body.get("error") or {}).get("code") == "EXPORT_SESSION_NOT_FOUND",
          "session 不存在 → 200 + EXPORT_SESSION_NOT_FOUND", str(body))

    print(f"\n合计 PASS {passed} / FAIL {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
