# -*- coding: utf-8 -*-
"""一次性探针：/api/preview 的真实返回长什么样、值是什么。

起因：V3 用 ScottPlot 画图，页面元信息全对（5×96、图例五条日期），
但图表区空白、Y 轴是 -10~10（ScottPlot 无数据时的默认范围）。
要判的是「数据没到手」还是「数据到手但画不出去」，所以先看真实值。

用法：
    python tests/probe_preview_payload.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("LGM_ALLOW_ROOTS", str(ROOT / "baseline" / "_inputs"))

from fastapi.testclient import TestClient  # noqa: E402

from api.app import app  # noqa: E402

C = TestClient(app)
W1 = ROOT / "baseline" / "_inputs" / "W1" / "W1_宽表96点.xlsx"


def main() -> int:
    r = C.post("/api/ingest/inspect", json={"path": str(W1)})
    sheet = r.json()["data"]["sheets"][0]
    r = C.post("/api/ingest/mapping", json={
        "path": str(W1), "sheet": sheet["name"], "mapping": sheet["suggest"]})
    sid = r.json()["data"]["session_id"]
    print(f"session = {sid}")

    r = C.get(f"/api/preview?session_id={sid}&group=0&format=json")
    body = r.json()
    print(f"\nHTTP {r.status_code} · 顶层键 = {list(body.keys())}")
    data = body.get("data") or {}
    print(f"data 键 = {list(data.keys())}")

    for k in ("shape", "date_labels", "time_labels"):
        v = data.get(k)
        print(f"  {k}: type={type(v).__name__} "
              f"value={v if k == 'shape' else (v[:3] if isinstance(v, list) else v)}")

    arr = data.get("load_array")
    print(f"  load_array: type={type(arr).__name__} 外层长度={len(arr) if arr else 0}")
    if arr:
        print(f"    每行长度 = {[len(x) for x in arr]}")
        first = arr[0]
        print(f"    第 0 行前 6 个值 = {first[:6]}")
        flat = [v for row in arr for v in row]
        nones = sum(1 for v in flat if v is None)
        print(f"    总格数 = {len(flat)} · None 个数 = {nones}")
        nums = [v for v in flat if isinstance(v, (int, float))]
        if nums:
            print(f"    数值范围 = {min(nums)} … {max(nums)}")
        else:
            print("    ⚠️ 没有任何数值 —— 这就是曲线空白的原因")
    else:
        print("    ⚠️ load_array 是空的")

    # 顺便看 bin 分支的响应头（两个 shape 不是同一个东西）
    rb = C.get(f"/api/preview?session_id={sid}&group=0&format=bin")
    print(f"\nbin: HTTP {rb.status_code} · 字节 {len(rb.content)}")
    for h in ("X-Shape", "X-Dtype", "X-Byte-Order"):
        print(f"  {h} = {rb.headers.get(h)}")
    print(f"  X-Date-Labels 前 60 字 = {(rb.headers.get('X-Date-Labels') or '')[:60]}")

    print("\n完整顶层 JSON（截 800 字）：")
    print(json.dumps(body, ensure_ascii=False)[:800])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
