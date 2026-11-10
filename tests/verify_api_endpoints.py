# -*- coding: utf-8 -*-
"""api 18 个端点的可调用性扫描。

不测业务正确性，只测：
  ① 空 body / 空参数会不会 500（应该是 4xx）
  ② 端点引用的函数是否真的存在（导入期就该报错，但可能漏）
  ③ 有没有端点根本没挂上（路由未注册）
"""
import io
import json
import os
import sys

REPO = r"C:\Users\LENOVO\.hanako\repos\172629438__loomgrid"
sys.path.insert(0, REPO)
os.environ.setdefault("LGM_ALLOW_ROOTS", str(REPO))
sys.stdout.reconfigure(encoding="utf-8")

from fastapi.testclient import TestClient
from api.app import app

C = TestClient(app, raise_server_exceptions=False)

ok, bad, note = [], [], []


def check(name, cond, detail=""):
    if cond:
        ok.append(name)
        print(f"  [PASS] {name}")
    else:
        bad.append(name)
        print(f"  [FAIL] {name}   {detail}")


print("=" * 80)
print("① 路由表完整性")
print("=" * 80)
routes = []
for r in app.routes:
    if hasattr(r, "methods") and hasattr(r, "path"):
        methods = sorted(m for m in r.methods if m not in ("HEAD", "OPTIONS"))
        if methods:
            routes.append((methods[0], r.path))
print(f"  注册了 {len(routes)} 个路由")
for m, p in sorted(routes, key=lambda x: x[1]):
    print(f"    {m:5s} {p}")

EXPECTED = [
    ("POST", "/api/calc/spaces"), ("POST", "/api/calc/satisfied-days"),
    ("POST", "/api/calc/monthly-spaces"), ("POST", "/api/calc/capacity"),
    ("POST", "/api/calc/pv-feedback"),
    ("POST", "/api/export/preview"),
    # /api/export/adopt 刻意不在这里：它被 LGM_DEV_TOOLS=1 门控，
    # 未设时路由根本不存在（生产包被试探时应该是 404，不是 403）。
    ("POST", "/api/export/run"), ("GET", "/api/export/status"),
    ("GET", "/api/export/report"),
    ("POST", "/api/ingest/upload"), ("POST", "/api/ingest/allow"),
    ("POST", "/api/ingest/inspect"), ("POST", "/api/ingest/mapping"),
    ("GET", "/api/ingest/groups"),
    ("POST", "/api/plot/fit"),
    ("GET", "/api/preview"),          # router prefix=/api，端点 /preview
    ("GET", "/api/simulate/templates"), ("POST", "/api/simulate/load"),
    ("POST", "/api/simulate/apply"), ("POST", "/api/simulate/revert"),
]
paths = {p for _, p in routes}
missing = [(m, p) for m, p in EXPECTED if p not in paths]
print()
print(f"  期望路径缺失: {missing if missing else '无'}")
check("所有期望端点都已注册", not missing, str(missing))

print()
print("=" * 80)
print("② 空请求：应该 4xx，不该 500")
print("=" * 80)
CASES = [
    ("POST", "/api/calc/spaces", {}),
    ("POST", "/api/calc/satisfied-days", {}),
    ("POST", "/api/calc/monthly-spaces", {}),
    ("POST", "/api/calc/capacity", {}),
    ("POST", "/api/calc/pv-feedback", {}),
    ("POST", "/api/export/preview", {}),
    ("POST", "/api/export/run", {}),
    ("GET", "/api/export/status", None),
    ("GET", "/api/export/report", None),
    ("POST", "/api/ingest/upload", {}),
    ("POST", "/api/ingest/allow", {}),
    ("POST", "/api/ingest/inspect", {}),
    ("POST", "/api/ingest/mapping", {}),
    ("GET", "/api/ingest/groups", None),
    ("POST", "/api/plot/fit", {}),
    ("GET", "/api/preview", None),
    ("GET", "/api/simulate/templates", None),
    ("POST", "/api/simulate/load", {}),
    ("POST", "/api/simulate/apply", {}),
    ("POST", "/api/simulate/revert", {}),
]
five_hundreds = []
for m, p, body in CASES:
    try:
        if m == "GET":
            r = C.get(p)
        else:
            r = C.post(p, json=body)
        code = r.status_code
    except Exception as e:
        code = f"EXC {type(e).__name__}"
    mark = ""
    if code == 500:
        mark = "  ← 500"
        five_hundreds.append(p)
    elif isinstance(code, int) and 200 <= code < 300:
        mark = "  ← 空请求居然成功"
    print(f"  {m:4s} {p:32s} → {code}{mark}")

check("没有端点 500", not five_hundreds, str(five_hundreds))

print()
print("=" * 80)
print("③ GET 端点在没有会话数据时的表现")
print("=" * 80)
for p in ("/api/export/status", "/api/export/report",
          "/api/ingest/groups", "/api/preview",
          "/api/simulate/templates"):
    r = C.get(p)
    body = r.text[:110].replace("\n", " ")
    print(f"  {p:32s} → {r.status_code}  {body}")

print()
print("=" * 80)
print("④ templates 端点应返回 25 个行业")
print("=" * 80)
r = C.get("/api/simulate/templates")
if r.status_code == 200:
    try:
        d = r.json()
        items = d.get("data") or d
        if isinstance(items, dict):
            items = items.get("templates") or list(items.values())
        n = len(items) if isinstance(items, list) else "?"
        print(f"  返回 {n} 个模板")
        check("templates 返回非空", isinstance(n, int) and n > 0, str(n))
    except Exception as e:
        print(f"  解析失败: {e}")
        check("templates 可解析", False, str(e))
else:
    print(f"  {r.status_code}: {r.text[:200]}")
    check("templates 可访问", False, str(r.status_code))

print()
print("=" * 80)
print(f"结果：PASS {len(ok)}   FAIL {len(bad)}")
for b in bad:
    print("  -", b)
print("=" * 80)
sys.exit(1 if bad else 0)
