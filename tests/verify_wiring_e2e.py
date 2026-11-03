"""阶段 4.5 端到端：上传 → inspect → mapping → export/run → WebSocket → PNG 落盘。

判据（协作方定）：从选一个真实文件开始，到产出一张真实 PNG 结束。
本脚本走的是真实 HTTP + 真实 WebSocket，不用 TestClient。
"""

import asyncio
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

import requests

ROOT = r"C:\Users\LENOVO\.hanako\repos\172629438__loomgrid"


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def make_wide_csv():
    times = []
    for i in range(96):
        m = i * 15
        times.append("%02d:%02d" % (m // 60, m % 60))
    rows = ["日期," + ",".join(times)]
    for d in range(1, 6):
        vals = []
        for i in range(96):
            h = i * 0.25
            base = 330 + 120 * (1 if 8 <= h <= 10 else 0) + 80 * (1 if 18 <= h <= 20 else 0)
            vals.append("%.1f" % (base + (d * 3.7) % 40))
        rows.append("2026-01-%02d," % d + ",".join(vals))
    d = tempfile.mkdtemp(prefix="loomgrid-e2e-")
    p = os.path.join(d, "端到端96点.csv")
    io.open(p, "w", encoding="utf-8-sig").write("\n".join(rows))
    return p


def main():
    port = free_port()
    base = "http://127.0.0.1:%d" % port
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api.app:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )

    results = []

    def check(name, cond, detail=""):
        results.append((name, bool(cond)))
        print(("[PASS] " if cond else "[FAIL] ") + name + (("  " + str(detail)) if detail else ""))

    try:
        up = False
        for _ in range(60):
            try:
                if requests.get(base + "/api/health", timeout=1).status_code == 200:
                    up = True
                    break
            except Exception:
                time.sleep(0.3)
        check("服务启动", up, base)
        if not up:
            raise RuntimeError("uvicorn 未就绪")

        csv = make_wide_csv()
        with open(csv, "rb") as f:
            r = requests.post(base + "/api/ingest/upload",
                              files={"file": ("端到端96点.csv", f, "text/csv")}, timeout=30)
        j = r.json()
        check("① upload", j.get("ok") is True)
        path = (j.get("data") or {}).get("path")

        r = requests.post(base + "/api/ingest/inspect", json={"path": path}, timeout=60)
        j = r.json()
        sh = ((j.get("data") or {}).get("sheets") or [None])[0]
        check("② inspect", j.get("ok") is True and sh is not None,
              (sh or {}).get("suggest", {}).get("format_type"))

        sug = (sh or {}).get("suggest") or {}
        r = requests.post(base + "/api/ingest/mapping", json={
            "path": path, "sheet": sh.get("name"),
            "mapping": {
                "date_col": sug.get("date_col"), "time_col": sug.get("time_col"),
                "asset_col": sug.get("asset_col"), "load_col": sug.get("load_col") or [],
                "format_type": sug.get("format_type"),
                "header_row": sug.get("header_row", 0),
                "transpose": sug.get("transpose", False),
            },
        }, timeout=120)
        j = r.json()
        sid = (j.get("data") or {}).get("session_id")
        groups = (j.get("data") or {}).get("groups") or []
        check("③ mapping → session", j.get("ok") is True and bool(sid),
              "session=%s groups=%d" % ((sid or "")[:8], len(groups)))

        out_dir = tempfile.mkdtemp(prefix="loomgrid-out-")
        r = requests.post(base + "/api/export/run", json={
            "session_id": sid,
            "output_dir": out_dir,
            "filename_template": "{table_no}_{year_month}_负荷曲线",
        }, timeout=60)
        j = r.json()
        tid = (j.get("data") or {}).get("task_id")
        check("④ export/run 提交", j.get("ok") is True and bool(tid), "task=" + str(tid))

        # ⑤ WebSocket 收事件直到 done
        ws_url = base.replace("http", "ws") + "/ws/progress?task_id=" + str(tid)
        events = []

        async def collect():
            import websockets
            async with websockets.connect(ws_url) as ws:
                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=90)
                    except asyncio.TimeoutError:
                        break
                    try:
                        m = json.loads(raw)
                    except Exception:
                        continue
                    events.append(m)
                    t = m.get("event") or m.get("type") or ""
                    if t == "done":
                        return m

        final = asyncio.run(collect())
        kinds = [e.get("event") or e.get("type") for e in events]
        check("⑤ WebSocket 收到 done", final is not None, "events=%d" % len(events))
        check("⑤ 四阶段事件齐", all(k in kinds for k in
              ("stage.parse", "stage.scan", "stage.preload", "stage.generate")),
              ",".join([k for k in kinds if k and k.startswith("stage.")][:6]))

        # ⑥ 磁盘上有真实 PNG
        pngs = []
        for root2, _, files in os.walk(out_dir):
            pngs += [os.path.join(root2, f) for f in files if f.lower().endswith(".png")]
        check("⑥ PNG 已生成", len(pngs) > 0, "png=%d" % len(pngs))
        if pngs:
            with open(pngs[0], "rb") as f:
                magic = f.read(8)
            size = os.path.getsize(pngs[0])
            check("⑥ PNG 魔数正确", magic == b"\x89PNG\r\n\x1a\n", repr(magic))
            check("⑥ PNG 非空", size > 1000, "%d bytes" % size)
            check("⑥ 文件名含模板展开", "负荷曲线" in os.path.basename(pngs[0]),
                  os.path.basename(pngs[0]))

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()

    ok = sum(1 for _, c in results if c)
    bad = len(results) - ok
    print("\n" + "=" * 60)
    print("PASS %d / FAIL %d" % (ok, bad))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
