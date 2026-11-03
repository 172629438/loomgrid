# -*- coding: utf-8 -*-
"""导出与进度验证：异步任务、状态轮询、WebSocket 事件流。

单独拆一个文件，因为这一块需要：起服务 + WebSocket 客户端 + 轮询，
比纯 HTTP 端点复杂，混在 verify_api.py 里会让那个文件太长。
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
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
        with urllib.request.urlopen(r, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def wait_task(tid: str, timeout: float = 120.0) -> dict:
    """轮询直到任务完成，返回 status 的 data 段。"""
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        code, body = req("GET", f"/api/export/status?task_id={tid}")
        if code == 200 and body.get("ok"):
            last = body["data"]
            if last.get("status") in ("done", "failed"):
                return last
        time.sleep(0.4)
    return last


def collect_ws(tid: str, timeout: float = 60.0) -> list[dict]:
    """连 WebSocket 收集到 done 为止的所有事件。"""
    import websockets

    async def _run():
        out = []
        async with websockets.connect(f"ws://127.0.0.1:{PORT}/ws/progress?task_id={tid}") as ws:
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                msg = json.loads(raw)
                out.append(msg)
                if msg.get("type") in ("done", "stage.error"):
                    break
        return out

    return asyncio.run(_run())


# path 白名单收紧后，直接引 baseline 里的文件会被拒（行为正确）。
# 用 LGM_ALLOW_ROOTS 声明可信目录 —— 与本文件同样处理的其他测试一致。
_env = dict(os.environ)
_env["PYTHONPATH"] = ROOT
_env["LGM_ALLOW_ROOTS"] = os.path.join(ROOT, "baseline", "_inputs")

proc = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "api.app:app", "--port", str(PORT),
     "--log-level", "warning"],
    cwd=ROOT, env=_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

outdir = tempfile.mkdtemp(prefix="lg_ws_")
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
        print("启动输出：", (proc.stdout.read() if proc.stdout else "")[:800])
        raise SystemExit(1)

    sample = os.path.join(ROOT, "baseline", "_inputs", "W1", "W1_宽表96点.xlsx")
    code, insp = req("POST", "/api/ingest/inspect", {"path": sample})
    full_map = insp["data"]["sheets"][0]["suggest"]
    code, mp = req("POST", "/api/ingest/mapping",
                   {"path": sample, "sheet": "Sheet1", "mapping": full_map})
    sid = mp["data"]["session_id"]
    check("1) 拿到 session", bool(sid))

    # 2) 异步提交：立即返回 task_id，不等结果
    t0 = time.time()
    code, body = req("POST", "/api/export/run",
                     {"session_id": sid, "output_dir": outdir, "export_midwide": True})
    elapsed = time.time() - t0
    ok_submit = code == 200 and body.get("ok") is True and body["data"].get("task_id")
    check("2) export/run 立即返回 task_id", bool(ok_submit), str(body.get("error"))[:80])
    tid = body["data"]["task_id"] if ok_submit else None
    check("3) 提交耗时 < 2 秒（异步，不等出图）", elapsed < 2.0, f"{elapsed:.2f}s")
    check("4) 响应里带 ws 与 status_url",
          ok_submit and "ws" in body["data"] and "status_url" in body["data"],
          str(list(body.get("data", {}).keys()))[:80])

    if tid:
        # 5) WebSocket 事件流
        try:
            events = collect_ws(tid)
            types = [e["type"] for e in events]
            check("5) WebSocket 收到事件", len(events) > 0, f"{len(events)} 个")
            check("6) 四个阶段事件齐全",
                  all(t in types for t in ("stage.parse", "stage.scan",
                                           "stage.preload", "stage.generate")),
                  str(sorted(set(types))))
            check("7) 有 progress 与 done",
                  "progress" in types and "done" in types, str(sorted(set(types))))
            done_ev = next((e for e in events if e["type"] == "done"), None)
            if done_ev:
                p = done_ev["payload"]
                check("8) done 事件带 succeeded 与 failed",
                      "succeeded" in p and "failed" in p,
                      f"succeeded={p.get('succeeded')} failed={len(p.get('failed', []))}")
        except Exception as e:
            check("5) WebSocket 收到事件", False, repr(e))

        # 9) 状态轮询兜底
        st = wait_task(tid)
        check("9) status 显示 done", st.get("status") == "done", str(st.get("status")))
        res = st.get("result") or {}
        check("10) 导出成功数 > 0", (res.get("succeeded") or 0) > 0,
              f"succeeded={res.get('succeeded')}")

        # 11) 磁盘产物
        pngs = [f for f in os.listdir(outdir) if f.lower().endswith(".png")]
        check("11) 真实 PNG 已生成", len(pngs) > 0, str(pngs[:2]))
        if pngs:
            p0 = os.path.join(outdir, pngs[0])
            with open(p0, "rb") as fh:
                magic = fh.read(8)
            check("12) PNG 魔数正确且非空",
                  magic == b"\x89PNG\r\n\x1a\n" and os.path.getsize(p0) > 1000,
                  f"{os.path.getsize(p0)} 字节")
        check("13) 默认模板文件名含年份-月份与「负荷曲线」",
              any("2026-01" in f and "负荷曲线" in f for f in pngs), str(pngs[:2]))

        # 14) 报告
        try:
            with urllib.request.urlopen(f"{BASE}/api/export/report?task_id={tid}", timeout=30) as r:
                html = r.read().decode("utf-8")
            check("14) report 端点返回 HTML",
                  "负荷曲线批量生成报告" in html and "成功列表" in html, f"{len(html)} 字符")
        except Exception as e:
            check("14) report 端点返回 HTML", False, repr(e))

    # 15) 过期任务的错误码
    code, body = req("GET", "/api/export/status?task_id=__nope__")
    check("15) 不存在的 task_id 返回 EXPORT_TASK_NOT_FOUND",
          body.get("ok") is False and body["error"]["code"] == "EXPORT_TASK_NOT_FOUND",
          str(body.get("error", {}).get("code")))

finally:
    shutil.rmtree(outdir, ignore_errors=True)
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
