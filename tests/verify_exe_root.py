"""在打包产物上验根路径 + 端口占用，不走完整重打包。"""

import os
import socket
import subprocess
import time
import urllib.request

EXE = r"C:\Users\LENOVO\.hanako\repos\172629438__loomgrid\dist\LoomGrid-Fusion\LoomGrid-Fusion.exe"
ok = True

# ── 测试 1：根路径 ──
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
        print("[FAIL] 拿不到端口")
        ok = False
    else:
        print("[PASS] 端口 %d" % port)
        base = "http://127.0.0.1:%d" % port
        with urllib.request.urlopen(base + "/", timeout=15) as r:
            body = r.read()
            final = r.geturl()
        good = r.status == 200 and "workbench" in final
        print("[%s] GET / → %s（%d 字节）" % ("PASS" if good else "FAIL",
                                              final.replace(base, ""), len(body)))
        ok = ok and good

        with urllib.request.urlopen(base + "/favicon.ico", timeout=10) as r:
            print("[%s] /favicon.ico → %d" % ("PASS" if r.status in (204, 200) else "FAIL", r.status))
            ok = ok and r.status in (204, 200)

        # 页面里引用的静态资源是否都能取到（tokens / 组件 / 视图）
        for p in ("/theme/tokens.css", "/components/app-shell.css",
                  "/components/curve-viewer.css", "/views/v3-preview.html"):
            try:
                with urllib.request.urlopen(base + p, timeout=10) as r:
                    print("[%s] %s → %d (%d B)" % ("PASS" if r.status == 200 else "FAIL",
                                                   p, r.status, len(r.read())))
                    ok = ok and r.status == 200
            except Exception as e:
                print("[FAIL] %s：%s" % (p, e))
                ok = False
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()

# ── 测试 2：端口被占 ──
print("\n--- 端口占用 ---")
holder = socket.socket()
holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
holder.bind(("127.0.0.1", 8000))
holder.listen(1)
print("已占住 8000")
proc2 = subprocess.Popen([EXE], cwd=os.path.dirname(EXE),
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace")
try:
    p2 = None
    deadline = time.time() + 60
    while time.time() < deadline:
        line = proc2.stdout.readline() if proc2.stdout else ""
        if "127.0.0.1:" in line:
            p2 = int(line.split("127.0.0.1:")[1].split("/")[0])
            break
        if proc2.poll() is not None:
            break
        time.sleep(0.15)
    if p2 is None:
        print("[FAIL] 8000 被占时没启动")
        ok = False
    else:
        print("[%s] 8000 被占 → 自动改用 %d" % ("PASS" if p2 != 8000 else "FAIL", p2))
        ok = ok and p2 != 8000
        with urllib.request.urlopen("http://127.0.0.1:%d/api/health" % p2, timeout=15) as r:
            print("[%s] 新端口 health → %d" % ("PASS" if r.status == 200 else "FAIL", r.status))
            ok = ok and r.status == 200
finally:
    proc2.terminate()
    try:
        proc2.wait(timeout=10)
    except Exception:
        proc2.kill()
    holder.close()

print("\n" + "=" * 50)
print("结果：" + ("PASS" if ok else "FAIL"))
