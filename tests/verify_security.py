r"""④ 的验收：CORS 收紧 + path 白名单是否真的生效。

判据（协作方要求写成可执行断言）：
  · 从外来源 fetch 应当被 CORS 拒
  · 传 C:\Windows\win.ini 作为 path，应返回结构化错误而不是列名
  · 正常链路（upload → inspect → mapping → export）不受影响
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


def main():
    port = free_port()
    base = "http://127.0.0.1:%d" % port
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api.app:app", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "warning"],
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

        # ── CORS ──
        r = requests.options(base + "/api/ingest/inspect", headers={
            "Origin": "http://127.0.0.1:8899",
            "Access-Control-Request-Method": "POST",
        }, timeout=10)
        allow = r.headers.get("access-control-allow-origin", "")
        check("CORS 允许本机前端", allow in ("http://127.0.0.1:8899", "*"), "allow=%r" % allow)

        r = requests.options(base + "/api/ingest/inspect", headers={
            "Origin": "http://evil.example.com",
            "Access-Control-Request-Method": "POST",
        }, timeout=10)
        allow = r.headers.get("access-control-allow-origin", "")
        check("CORS 拒绝外来源", allow == "" or "evil" not in allow, "allow=%r" % allow)

        r = requests.options(base + "/api/ingest/inspect", headers={
            "Origin": "null",
            "Access-Control-Request-Method": "POST",
        }, timeout=10)
        allow = r.headers.get("access-control-allow-origin", "")
        check("CORS 允许桌面壳（null）", allow == "null", "allow=%r" % allow)

        # ── path 白名单 ──
        r = requests.post(base + "/api/ingest/inspect",
                          json={"path": r"C:\Windows\win.ini"}, timeout=20)
        j = r.json()
        code = (j.get("error") or {}).get("code")
        check("inspect 拒绝系统路径", code == "INGEST_PATH_FORBIDDEN", "code=%r" % code)

        r = requests.post(base + "/api/ingest/inspect",
                          json={"path": os.path.join(os.path.expanduser("~"), "x.csv")}, timeout=20)
        j = r.json()
        code = (j.get("error") or {}).get("code")
        check("inspect 拒绝 upload 目录之外的路径", code == "INGEST_PATH_FORBIDDEN", "code=%r" % code)

        # ── 正常链路不受影响 ──
        times = []
        for i in range(96):
            m = i * 15
            times.append("%02d:%02d" % (m // 60, m % 60))
        rows = ["日期," + ",".join(times)]
        for d in range(1, 4):
            rows.append("2026-01-%02d," % d + ",".join("%.1f" % (300 + i + d) for i in range(96)))
        csv = os.path.join(tempfile.mkdtemp(prefix="lg-sec-"), "sec.csv")
        io.open(csv, "w", encoding="utf-8-sig").write("\n".join(rows))

        with open(csv, "rb") as f:
            up_j = requests.post(base + "/api/ingest/upload",
                                 files={"file": ("sec.csv", f, "text/csv")}, timeout=30).json()
        check("upload 正常", up_j.get("ok") is True)

        path = (up_j.get("data") or {}).get("path")
        ins = requests.post(base + "/api/ingest/inspect", json={"path": path}, timeout=60).json()
        check("upload 之后的 inspect 放行", ins.get("ok") is True,
              "format=" + str(((ins.get("data") or {}).get("sheets") or [{}])[0].get("suggest", {}).get("format_type")))

        # ── export 目录黑名单 ──
        sh = (ins.get("data") or {}).get("sheets") or [{}]
        sug = sh[0].get("suggest") or {}
        mp = requests.post(base + "/api/ingest/mapping", json={
            "path": path, "sheet": sh[0].get("name"),
            "mapping": {k: sug.get(k) for k in
                        ("date_col", "time_col", "asset_col", "load_col",
                         "format_type", "header_row", "transpose")},
        }, timeout=120).json()
        sid = (mp.get("data") or {}).get("session_id")

        r = requests.post(base + "/api/export/run", json={
            "session_id": sid,
            "output_dir": r"C:\Windows\Temp\lg-blocked",
        }, timeout=30)
        j = r.json()
        code = (j.get("error") or {}).get("code")
        check("export 拒绝系统目录", code == "EXPORT_OUTPUT_FORBIDDEN", "code=%r" % code)

        out = tempfile.mkdtemp(prefix="lg-sec-out-")
        r = requests.post(base + "/api/export/run", json={
            "session_id": sid, "output_dir": out,
            "filename_template": "{table_no}_{year_month}_负荷曲线",
        }, timeout=30)
        j = r.json()
        check("export 正常目录放行", j.get("ok") is True, "task=" + str((j.get("data") or {}).get("task_id"))[:12])

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()

    ok = sum(1 for _, c in results if c)
    bad = len(results) - ok
    print("\n" + "=" * 56)
    print("PASS %d / FAIL %d" % (ok, bad))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
