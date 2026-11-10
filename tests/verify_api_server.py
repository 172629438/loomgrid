# -*- coding: utf-8 -*-
"""启动 tools/serve_api.py 并验证服务真的可用。

不是只看进程活着——要真打 /api/health 与 /api/ingest/groups。
"""
import io
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPO = r"C:\Users\LENOVO\.hanako\repos\172629438__loomgrid"
sys.path.insert(0, REPO)
sys.stdout.reconfigure(encoding="utf-8")

ok, bad = [], []


def check(name, cond, detail=""):
    if cond:
        ok.append(name)
        print(f"  [PASS] {name}")
    else:
        bad.append(name)
        print(f"  [FAIL] {name}   {detail}")


PORT = 8765
print("=" * 76)
print(f"启动 serve_api.py（端口 {PORT}）")
print("=" * 76)

env = dict(os.environ)
env["PYTHONIOENCODING"] = "utf-8"
proc = subprocess.Popen(
    [sys.executable, os.path.join(REPO, "tools", "serve_api.py"),
     "--port", str(PORT), "--log-level", "warning"],
    cwd=REPO, env=env,
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
)

BASE = f"http://127.0.0.1:{PORT}"


def get(path, timeout=3):
    try:
        with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


print("等待服务就绪…")
ready = False
for _ in range(40):
    code, _body = get("/api/health", timeout=1)
    if code == 200:
        ready = True
        break
    time.sleep(0.5)

print(f"  就绪: {ready}")
check("服务能启动并响应 /api/health", ready, "40 次探测都没通")

if ready:
    print()
    print("=" * 76)
    print("端点探测")
    print("=" * 76)
    for path, expect_ok in (("/api/health", True),
                            ("/docs", True),
                            ("/api/ingest/groups", None),
                            ("/api/simulate/templates", True)):
        code, body = get(path)
        tag = ""
        if expect_ok is True and code != 200:
            tag = "  ← 期望 200"
        print(f"  GET {path:28s} → {code}{tag}")
        if expect_ok is True:
            check(f"{path} 返回 200", code == 200, str(code))

    print()
    print("=" * 76)
    print("templates 内容核对")
    print("=" * 76)
    code, body = get("/api/simulate/templates")
    if code == 200:
        try:
            d = json.loads(body)
            data = d.get("data") or d
            if isinstance(data, dict):
                items = data.get("templates") or data.get("items") or []
            else:
                items = data
            n = len(items) if isinstance(items, list) else "?"
            print(f"  返回 {n} 个行业模板")
            check("模板数 > 0", isinstance(n, int) and n > 0, str(n))
        except Exception as e:
            print(f"  解析失败: {e}")
            check("templates 可解析", False, str(e))

print()
print("=" * 76)
print("收尾：确认进程能正常终止")
print("=" * 76)
proc.terminate()
try:
    proc.wait(timeout=8)
    print(f"  退出码 {proc.returncode}")
    check("进程能终止", True)
except subprocess.TimeoutExpired:
    proc.kill()
    proc.wait(timeout=5)
    print("  超时后强杀")
    check("进程能终止", False, "terminate 8 秒未退")

print()
print("=" * 76)
print(f"结果：PASS {len(ok)}   FAIL {len(bad)}")
for b in bad:
    print("  -", b)
print("=" * 76)
sys.exit(1 if bad else 0)
