# -*- coding: utf-8 -*-
"""API 层验证：起真实 uvicorn，用真实 HTTP 请求跑一遍。

为什么不用 TestClient
--------------------
阶段 2 的硬闸门是「不用前端、纯 curl 跑通」。TestClient 走的是进程内 ASGI，
跳过了端口、序列化、中间件这些真实链路。这里起真服务、发真请求。

覆盖
----
0 服务启动
1 /api/health          存活探针 + 核心模块可导入 + 两种口径可见
2 /api/ingest/inspect  日期列识别、表型判定、置信度与理由
3 /api/ingest/mapping  分组产出、session_id、月份与日期数
4 错误路径             文件不存在、缺 date_col

端口用随机高位端口，避免与开发中的服务冲突。
"""
from __future__ import annotations

import json
import os
import random
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
os.chdir(ROOT)

VERBOSE = "-v" in sys.argv
PASS = FAIL = 0
FAILS = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        if VERBOSE:
            print(f"[PASS] {name}")
    else:
        FAIL += 1
        FAILS.append((name, detail))
        print(f"[FAIL] {name}  {detail}")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PORT = free_port()
BASE = f"http://127.0.0.1:{PORT}"


def req(method, path, body=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


proc = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "api.app:app", "--port", str(PORT),
     "--log-level", "warning"],
    cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

try:
    ready = False
    for _ in range(80):
        time.sleep(0.5)
        try:
            code, _ = req("GET", "/api/health")
            if code == 200:
                ready = True
                break
        except Exception:
            continue
    check("0) 服务启动", ready, f"端口 {PORT}")
    if not ready:
        out = proc.stdout.read() if proc.stdout else ""
        print("启动输出：", out[:800])
        raise SystemExit(1)

    code, body = req("GET", "/api/health")
    check("1) health 返回 200", code == 200)
    check("2) health 列出核心模块",
          body.get("ok") and len(body["data"]["modules"]) == 4,
          str(body["data"].get("modules")))
    check("3) health 暴露两种口径",
          body["data"]["demand_basis"] == ["min_feasible", "full_load"],
          str(body["data"].get("demand_basis")))

    sample = os.path.join(ROOT, "baseline", "_inputs", "W1", "W1_宽表96点.xlsx")
    if not os.path.exists(sample):
        print(f"跳过 4-7：样例缺失 {sample}")
    else:
        code, body = req("POST", "/api/ingest/inspect", {"path": sample})
        check("4) inspect 返回 200", code == 200)
        check("5) inspect ok=true", body.get("ok") is True, str(body.get("error")))
        sh = body["data"]["sheets"][0]
        check("6) inspect 识别日期列与表型",
              sh["suggest"]["date_col"] == "日期" and sh["suggest"]["format_type"] == "wide",
              f"{sh['suggest']['date_col']} / {sh['suggest']['format_type']}")
        check("7) inspect 给出置信度与理由",
              0 <= sh["confidence"]["score"] <= 1 and isinstance(sh["confidence"]["reasons"], list),
              f"score={sh['confidence']['score']}")

        mapping = dict(sh["suggest"])
        code, body = req("POST", "/api/ingest/mapping",
                         {"path": sample, "sheet": sh["name"], "mapping": mapping})
        check("8) mapping ok=true", body.get("ok") is True, str(body.get("error")))
        groups = body["data"]["groups"]
        check("9) mapping 产出分组且给 session_id",
              len(groups) >= 1 and bool(body["data"]["session_id"]), f"{len(groups)} 组")
        g = groups[0]
        check("10) 分组信息完整且月份日期正确",
              all(k in g for k in ("asset", "month", "date_count", "time_points"))
              and g["month"] == "2026-01" and g["date_count"] == 5,
              f"{g}")

    code, body = req("POST", "/api/ingest/inspect", {"path": "C:/__nope__.xlsx"})
    check("11) 不存在的文件返回 ok=false + INGEST_READ_FAILED",
          body.get("ok") is False and body["error"]["code"] == "INGEST_READ_FAILED",
          str(body.get("error", {}).get("code")))

    code, body = req("POST", "/api/ingest/mapping",
                     {"path": sample if os.path.exists(sample) else "x", "sheet": "Sheet1",
                      "mapping": {}})
    check("12) 缺 date_col 返回 INGEST_NO_DATE_COL",
          body.get("ok") is False and body["error"]["code"] == "INGEST_NO_DATE_COL",
          str(body.get("error", {}).get("code")))

    # 13) 回归：无示数特征 + 有负荷列 → 曾因误加 @lru_cache 而 500
    # 短路求值：accumulated_hint 为真时不会调到 _detect_accumulated_pattern；
    # 基线的示数场景恰好都短路了，所以这个缺陷只在真正调到函数时暴露。
    try:
        import tempfile

        import pandas as pd
        tmp = tempfile.mkdtemp(prefix="lg_nohint_")
        csv_path = os.path.join(tmp, "nohint.csv")
        labels = [f"{(i * 15) // 60:02d}:{(i * 15) % 60:02d}" for i in range(96)]
        recs = [{"日期": f"2026-01-{15 + d:02d}", "时间": lab, "正向有功功率": 400 + i}
                for d in range(3) for i, lab in enumerate(labels)]
        pd.DataFrame(recs).to_csv(csv_path, index=False, encoding="utf-8-sig")

        code, body = req("POST", "/api/ingest/inspect", {"path": csv_path})
        check("13) 无示数特征 + 有负荷列 → 200（不崩）",
              code == 200 and body.get("ok") is True,
              f"HTTP {code} {str(body.get('error'))[:80]}")
        if body.get("ok"):
            sh2 = body["data"]["sheets"][0]
            check("14) 该场景应判为长表",
                  sh2["suggest"]["format_type"] == "long",
                  str(sh2["suggest"]["format_type"]))
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    except Exception as e:
        check("13) 无示数特征 + 有负荷列 → 200（不崩）", False, repr(e))

finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()

print()
if FAILS:
    print("失败项：")
    for n, d in FAILS:
        print(f"  - {n}  {d}")
print(f"结果：PASS {PASS}   FAIL {FAIL}")
sys.exit(1 if FAIL else 0)
