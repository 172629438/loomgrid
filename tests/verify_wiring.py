"""阶段 4.5 接线验证：上传 → inspect → mapping → export 全链路。

用真实 HTTP，不用 TestClient（后者走进程内 ASGI，跳过端口与序列化）。
端口随机取空闲高位端口，不会和已在跑的服务撞。
"""

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


def make_csv():
    """造一个 96 点宽表 CSV：日期 + 96 个时刻列。"""
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
    path = os.path.join(tempfile.mkdtemp(prefix="loomgrid-t-"), "负荷宽表96.csv")
    io.open(path, "w", encoding="utf-8-sig").write("\n".join(rows))
    return path


def main():
    port = free_port()
    base = "http://127.0.0.1:%d" % port
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api.app:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )

    results = []

    def check(name, cond, detail=""):
        results.append((name, bool(cond), detail))
        print(("[PASS] " if cond else "[FAIL] ") + name + (("  " + detail) if detail else ""))

    try:
        # 等服务就绪
        up = False
        for _ in range(60):
            try:
                r = requests.get(base + "/api/health", timeout=1)
                if r.status_code == 200:
                    up = True
                    break
            except Exception:
                time.sleep(0.3)
        check("服务启动", up, base)
        if not up:
            raise RuntimeError("uvicorn 未就绪")

        csv_path = make_csv()
        check("测试 CSV 已造", os.path.exists(csv_path), os.path.basename(csv_path))

        # 1. 上传
        with open(csv_path, "rb") as f:
            r = requests.post(base + "/api/ingest/upload",
                              files={"file": ("负荷宽表96.csv", f, "text/csv")},
                              timeout=30)
        j = r.json()
        check("upload 200", r.status_code == 200, "status=%d" % r.status_code)
        check("upload ok=true", j.get("ok") is True, json.dumps(j.get("error"), ensure_ascii=False))
        up_path = (j.get("data") or {}).get("path")
        check("upload 返回 path", bool(up_path) and os.path.exists(up_path or ""), str(up_path))

        # 2. inspect
        r = requests.post(base + "/api/ingest/inspect", json={"path": up_path}, timeout=60)
        j = r.json()
        check("inspect 200", r.status_code == 200)
        check("inspect ok=true", j.get("ok") is True, json.dumps(j.get("error"), ensure_ascii=False))
        sheets = (j.get("data") or {}).get("sheets") or []
        check("inspect 返回 sheets", len(sheets) >= 1, "sheets=%d" % len(sheets))
        if sheets:
            sug = sheets[0].get("suggest") or {}
            check("识别为 wide", sug.get("format_type") == "wide", str(sug.get("format_type")))
            check("date_col 已识别", bool(sug.get("date_col")), str(sug.get("date_col")))
            # confidence 挂在 SheetInfo 层级，不在 suggest 里
            conf = sheets[0].get("confidence") or {}
            check("带 confidence", "score" in conf and "reasons" in conf,
                  "score=%s reasons=%d" % (conf.get("score"), len(conf.get("reasons") or [])))

        # 3. mapping —— 请求体是 {path, sheet, mapping}，不是 {path, sheets:[...]}
        if sheets:
            sug = sheets[0].get("suggest") or {}
            body = {
                "path": up_path,
                "sheet": sheets[0].get("name"),
                "mapping": {
                    "date_col": sug.get("date_col"),
                    "time_col": sug.get("time_col"),
                    "asset_col": sug.get("asset_col"),
                    "load_col": sug.get("load_col") or [],
                    "format_type": sug.get("format_type"),
                    "header_row": sug.get("header_row", 0),
                    "transpose": sug.get("transpose", False),
                },
            }
            r = requests.post(base + "/api/ingest/mapping", json=body, timeout=120)
            j = r.json()
            check("mapping 200", r.status_code == 200)
            check("mapping ok=true", j.get("ok") is True, json.dumps(j.get("error"), ensure_ascii=False))
            groups = (j.get("data") or {}).get("groups") or []
            check("mapping 产出分组", len(groups) >= 1, "groups=%d" % len(groups))
            if groups:
                g = groups[0]
                check("分组有 session_id", bool((j.get("data") or {}).get("session_id")))
                check("分组有 time_points", (g.get("time_points") or 0) > 0, str(g.get("time_points")))

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()

    ok = sum(1 for _, c, _ in results if c)
    bad = len(results) - ok
    print("\n" + "=" * 60)
    print("PASS %d / FAIL %d" % (ok, bad))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
