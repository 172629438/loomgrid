# -*- coding: utf-8 -*-
"""API 层：错误返回是否统一成 Envelope。

为什么这条重要
--------------
本 API 成功返回 `{ok, data, error}`，业务错误也返回 `{ok: false, error: {...}}`。
但 FastAPI 的参数校验失败（422）默认吐成 `{"detail": [...]}`——结构完全不同。

前端若统一按 `body.ok` 判断，遇到 422 拿到的是 undefined：
要么当成功处理（拿到空数据），要么报一个没说清哪个字段错了的错。

本轮修掉：加 RequestValidationError handler，422 也包成
`{ok: false, error: {code: "VALIDATION_ERROR", message: "session_id：Field required"}}`。
HTTP 状态码仍保持 422，不改成 200——前端既能只看 ok，也能靠状态码分支。

跑法：python tests/verify_api_envelope.py
"""
import logging
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
sys.stdout.reconfigure(encoding="utf-8")
logging.disable(logging.CRITICAL)

from fastapi.testclient import TestClient  # noqa: E402

from api.app import app  # noqa: E402

PASS = FAIL = 0
client = TestClient(app, raise_server_exceptions=False)


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}  {detail}")


def call(method, url, **kw):
    r = client.request(method, url, **kw)
    try:
        body = r.json()
    except Exception:
        body = None
    return r.status_code, body


def is_env(body):
    return isinstance(body, dict) and set(("ok", "data", "error")) <= set(body.keys())


# ══════════════════════════════════════════════════════════
print("=" * 74)
print("① 参数校验失败（422）必须包成 Envelope（本轮修复）")
print("=" * 74)
CASES = [
    ("POST", "/api/calc/spaces", {"json": {}}, "calc/spaces 空 body"),
    ("POST", "/api/calc/satisfied-days", {"json": {}}, "satisfied-days 空 body"),
    ("POST", "/api/calc/capacity", {"json": {}}, "capacity 空 body"),
    ("POST", "/api/export/preview", {"json": {}}, "export/preview 空 body"),
    ("POST", "/api/export/run", {"json": {}}, "export/run 空 body"),
    ("GET", "/api/ingest/groups", {}, "ingest/groups 无 query"),
    ("GET", "/api/preview", {}, "preview 无 query"),
    ("POST", "/api/simulate/load", {"json": {}}, "simulate/load 空 body"),
    ("POST", "/api/simulate/apply", {"json": {}}, "simulate/apply 空 body"),
]
for method, url, kw, label in CASES:
    code, body = call(method, url, **kw)
    ok = is_env(body) and body.get("ok") is False
    err = (body or {}).get("error") or {}
    check(f"{label}: HTTP {code} 且结构是 Envelope", code == 422 and ok,
          f"code={code} body={str(body)[:90]}")
    if code == 422 and ok:
        check(f"  错误码是 VALIDATION_ERROR", err.get("code") == "VALIDATION_ERROR",
              str(err.get("code")))
        check(f"  message 指出缺哪个字段", "session_id" in str(err.get("message", ""))
              or "industry" in str(err.get("message", ""))
              or "original_session_id" in str(err.get("message", "")),
              str(err.get("message"))[:80])

# ══════════════════════════════════════════════════════════
print()
print("=" * 74)
print("② 类型错误也是 422 + Envelope")
print("=" * 74)
code, body = call("POST", "/api/calc/spaces",
                  json={"session_id": 123, "group": 0})
err = (body or {}).get("error") or {}
check(f"session_id 传 int: HTTP {code} 且 Envelope", code == 422 and is_env(body),
      f"code={code}")
check("  detail 里有字段级原因",
      isinstance(err.get("detail"), dict) and err["detail"].get("fields"),
      str(err.get("detail"))[:90])

# ══════════════════════════════════════════════════════════
print()
print("=" * 74)
print("③ 业务错误（session 不存在）仍是 200 + Envelope")
print("=" * 74)
SEGS = [{"start": 0.0, "end": 8.0, "type": "charge"},
        {"start": 10.0, "end": 12.0, "type": "discharge"}]
code, body = call("POST", "/api/calc/spaces",
                  json={"session_id": "deadbeef", "group": 0, "segments": SEGS})
err = (body or {}).get("error") or {}
check(f"不存在的 session: HTTP {code} 且 Envelope", code == 200 and is_env(body),
      f"code={code}")
check("  错误码指向 session（不是时段）",
      err.get("code") == "PREVIEW_GROUP_NOT_FOUND", str(err.get("code")))
check("  message 说明 session 不存在",
      "session" in str(err.get("message", "")).lower(), str(err.get("message"))[:80])

code, body = call("GET", "/api/preview?session_id=deadbeef&group=0")
err = (body or {}).get("error") or {}
check(f"preview 同样: HTTP {code} 且 Envelope", code == 200 and is_env(body))
check("  同一错误码", err.get("code") == "PREVIEW_GROUP_NOT_FOUND", str(err.get("code")))

# ══════════════════════════════════════════════════════════
print()
print("=" * 74)
print("④ 缺时段参数时消息说清是「没传」而不是「无效」")
print("=" * 74)
code, body = call("POST", "/api/calc/spaces",
                  json={"session_id": "x", "group": 0})
err = (body or {}).get("error") or {}
msg = str(err.get("message", ""))
check(f"空 body 时段: HTTP {code} Envelope", code == 200 and is_env(body), str(code))
check("消息指出「未传」", "未传" in msg or "没有有效时段" in msg, msg[:80])

# ══════════════════════════════════════════════════════════
print()
print("=" * 74)
print("⑤ 健康检查：核心模块可导入（分层破了这里会红）")
print("=" * 74)
code, body = call("GET", "/api/health")
data = (body or {}).get("data") or {}
check(f"health: HTTP {code} ok=True", code == 200 and (body or {}).get("ok") is True,
      str(code))
check("modules 列表非空", bool(data.get("modules")), str(data.get("modules")))
check("包含 core.ingest", "core.ingest" in (data.get("modules") or []))

print()
print("=" * 74)
print(f"结果：PASS {PASS}   FAIL {FAIL}")
print("=" * 74)
sys.exit(1 if FAIL else 0)
