"""在打包产物上验 48 列提示 + 假 xlsx 报错。

为什么不用 TestClient
--------------------
TestClient 跑的是源码。源码改了、exe 没重打，测试照样全绿——
而用户拿到的是旧 exe。这轮就是这么漏的：代码是新的、产物是旧的、
两边都「对」，只是没接上。

判据是「测产物，不测源码」。
"""

import io
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = r"C:\Users\LENOVO\.hanako\repos\172629438__loomgrid"
EXE = os.path.join(ROOT, "dist", "LoomGrid-Fusion", "LoomGrid-Fusion.exe")

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("[PASS] %s %s" % (name, detail))
    else:
        FAIL += 1
        print("[FAIL] %s %s" % (name, detail))


def multipart_upload(base, path, fname):
    boundary = "----lgm" + str(int(time.time() * 1000))
    data = io.open(path, "rb").read()
    body = (
        ("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"%s\"\r\n"
         "Content-Type: application/octet-stream\r\n\r\n" % (boundary, fname)).encode("utf-8")
        + data
        + ("\r\n--%s--\r\n" % boundary).encode("utf-8")
    )
    req = urllib.request.Request(base + "/api/ingest/upload", data=body, method="POST")
    req.add_header("Content-Type", "multipart/form-data; boundary=" + boundary)
    import json
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def post_json(base, path, payload):
    import json
    req = urllib.request.Request(base + path,
                                 data=json.dumps(payload).encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


# v2.3.8fix: Web 版已于 2026-11-04 从仓库下架（dist/LoomGrid-Fusion 已删除），
# 本脚本测的就是那个产物。产物不在时直接跳过，不报红——否则全套测试会
# 一直挂一个“找不到文件”的假失败。
if not os.path.exists(EXE):
    print(f"[SKIP] 未找到 Web 版产物：{EXE}")
    print("       Web 版已下架，本脚本仅在产物存在时运行。")
    print("       （桌面版产物请用 tools/verify_exe_window.py 验窗口标题）")
    print("\n结果：PASS 0   FAIL 0   SKIP 1")
    sys.exit(0)

proc = subprocess.Popen([EXE], cwd=os.path.dirname(EXE),
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, encoding="utf-8", errors="replace")
port = None
try:
    deadline = time.time() + 90
    while time.time() < deadline:
        line = proc.stdout.readline() if proc.stdout else ""
        if "127.0.0.1:" in line:
            port = int(line.split("127.0.0.1:")[1].split("/")[0])
            break
        if proc.poll() is not None:
            break
        time.sleep(0.15)

    if port is None:
        print("[FAIL] exe 没起来")
        sys.exit(1)
    base = "http://127.0.0.1:%d" % port
    print("exe 端口 %d\n" % port)

    d = tempfile.mkdtemp(prefix="lgm-prod-")

    # ── ① 48 列 ──
    rows = ["日期,户号," + ",".join("%02d:%02d" % (h, m) for h in range(12) for m in (0, 15, 30, 45))]
    for day in range(3):
        rows.append("2026-01-0%d,1002," % (day + 1) + ",".join("300.0" for _ in range(48)))
    p48 = os.path.join(d, "half48.csv")
    io.open(p48, "w", encoding="utf-8", newline="").write("\n".join(rows) + "\n")
    up = multipart_upload(base, p48, "half48.csv")
    inspect = post_json(base, "/api/ingest/inspect", {"path": up["data"]["path"]})
    sh = inspect["data"]["sheets"][0]
    reasons = sh["confidence"]["reasons"]
    hit = [r for r in reasons if "48" in r and "粒度" in r]
    check("① 48 列 → reasons 有粒度提示", bool(hit), (hit[0][:80] if hit else str(reasons[:2])))
    check("① 提示里写明 96 是标准", bool(hit) and "96" in hit[0], "")
    check("① load_col 仍是 48", len(sh["suggest"].get("load_col") or []) == 48,
          "%d 列" % len(sh["suggest"].get("load_col") or []))

    # ── ② 假 xlsx ──
    fake = os.path.join(d, "假文件.xlsx")
    io.open(fake, "w", encoding="utf-8", newline="").write("这不是 excel，只是一段文字\n")
    up2 = multipart_upload(base, fake, "假文件.xlsx")
    ins2 = post_json(base, "/api/ingest/inspect", {"path": up2["data"]["path"]})
    err = ins2.get("error") or {}
    msg = err.get("message", "")
    check("② 假 xlsx → 报错", ins2.get("ok") is False, "[%s]" % err.get("code"))
    check("② 是中文人话", "格式无法识别" in msg, repr(msg[:70]))
    check("② 带文件名", "假文件.xlsx" in msg, "")
    check("② 不再说「CSV文件」", "CSV文件" not in msg, "")
    check("② 原始异常在 detail.raw", bool((err.get("detail") or {}).get("raw")),
          str((err.get("detail") or {}).get("raw"))[:40])
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()

# ── ③ STALE_TEST 清理（重新造一次，启动 exe 看是否被删）──
print()
stale = os.path.join(tempfile.gettempdir(), "loomgrid-upload-STALE_TEST")
os.makedirs(stale, exist_ok=True)
io.open(os.path.join(stale, "marker.txt"), "w").write("stale")
t = time.time() - 25 * 3600
os.utime(stale, (t, t))
print("已造 STALE_TEST（mtime 25 小时前）")
proc2 = subprocess.Popen([EXE], cwd=os.path.dirname(EXE),
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace")
try:
    time.sleep(8)
    gone = not os.path.isdir(stale)
    check("③ 启动时清理 25 小时前的旧目录", gone, "已删除" if gone else "仍存在")
finally:
    proc2.terminate()
    try:
        proc2.wait(timeout=10)
    except Exception:
        proc2.kill()

print("\n" + "=" * 52)
print("PASS %d  FAIL %d" % (PASS, FAIL))
sys.exit(0 if FAIL == 0 else 1)
