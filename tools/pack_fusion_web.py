# -*- coding: utf-8 -*-
"""LoomGrid Fusion Web 版打包脚本（PyInstaller）。

用法:
    python tools/pack_fusion_web.py

与 pack_release.py 的区别
--------------------------
那个打的是 Qt 单 py 文件（负荷曲线批量生成工具 Pro 融合版.py），产物是一个
自包含 exe。本脚本打的是新架构：FastAPI 后端 + web 前端 + 浏览器壳。

    入口        launcher.py（起 uvicorn → 探端口 → 打开浏览器）
    模块        api/ core/ services/ plotting/
    静态资源    web/（tokens.css、12 个组件、6 个视图）

为什么 --onedir 不用 --onefile
------------------------------
onefile 每次启动都要把全部内容解压到临时目录，PySide6 + matplotlib 这几个
包解压就要十几秒，用户会以为程序卡死。onedir 启动快、体积可查、出错时能看到
缺了哪个文件。代价是一个文件夹而不是单个 exe —— 用 zip 交付即可。

冒烟闸门（硬性）
----------------
打完包必须真启动一次：进程存活 + 端口可连 + /api/health 200 + 前端页面能取到。
「zip 里文件都在」不证明「能跑」。
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
import zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(REPO, "dist")
BUILD = os.path.join(REPO, "build")
STEM = "LoomGrid-Fusion"

# 不打包的：测试、工具脚本、基线数据、开发期产物，
# 以及本机装了但 LoomGrid 完全用不到的重量级包。
# PyInstaller 会把环境里的包按 import 链全收进来 —— 第一次打包 _internal 到 949 MB，
# 里面躺着 torch(359MB)、cv2(138MB)、llvmlite(101MB)、scipy(72MB)、transformers(35MB)，
# 这些都是别的项目装的，跟一个算负荷曲线的工具没有任何关系。
EXCLUDES = [
    "tests", "tools", "baseline", "dist", "build", ".git",
    "PySide6", "PyQt5", "PyQt6", "tkinter",
    # 机器学习棧
    "torch", "torchvision", "torchaudio", "transformers", "accelerate",
    "onnxruntime", "safetensors", "huggingface_hub", "hf_xet",
    "tiktoken", "tokenizers", "sklearn", "scikit_learn", "numba", "llvmlite",
    # 图像/视频
    "cv2", "opencv_python", "PIL.ImageQt",
    # 科学计算里 LoomGrid 不用的那部分（scipy 只被 sklearn 拉到，两者都排）
    "scipy", "sympy",
    # 开发工具与编辑器集成
    "jedi", "parso", "IPython", "mypy", "lief", "ast_serialize",
    "grpc", "opentelemetry", "google", "openai",
    # web 棧里只要 fastapi/starlette/uvicorn，其余服务端框架全排
    "jinja2", "werkzeug", "sqlalchemy", "aiohttp", "tornado",
    "bs4", "lxml", "curl_cffi", "Crypto", "cryptography", "bcrypt",
    "psutil", "typer", "rich", "prompt_toolkit", "watchfiles",
]

# uvicorn 的动态导入 PyInstaller 扫不到，必须显式声明
HIDDEN = [
    "uvicorn.logging",
    "uvicorn.loops", "uvicorn.loops.auto",
    "uvicorn.protocols", "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto", "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets", "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan", "uvicorn.lifespan.on",
    "api.app", "api.routes_ingest", "api.routes_preview",
    "api.routes_calc", "api.routes_export", "api.ws_progress",
    "core.ingest", "core.models", "core.timegrid", "core.spaces",
    "core.periods", "services.config", "services.provider",
    "services.capacity", "services.export", "services.tasks",
    "plotting.curves", "plotting.naming", "plotting.fonts",
    "wsproto", "h11",
]


def build() -> str:
    for d in (DIST, BUILD):
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)

    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onedir",
        "--name", STEM,
        "--add-data", os.path.join(REPO, "web") + os.pathsep + "web",
    ]
    for h in HIDDEN:
        args += ["--hidden-import", h]
    for e in EXCLUDES:
        args += ["--exclude-module", e]
    # 数据文件：openpyxl 有自己的 xml 模板，不 collect 会读不了 xlsx
    args += ["--collect-data", "openpyxl"]
    args += ["--collect-data", "chinesecalendar"]
    args += [os.path.join(REPO, "launcher.py")]

    print("[1/3] PyInstaller 打包中……")
    r = subprocess.run(args, cwd=REPO, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(r.stdout[-3000:])
        print(r.stderr[-3000:])
        raise SystemExit("PyInstaller 失败")

    out = os.path.join(DIST, STEM, STEM + ".exe")
    if not os.path.isfile(out):
        raise SystemExit("产物不存在：" + out)
    return out


def smoke(exe: str) -> bool:
    """真启动：进程存活 + 端口可连 + health 200 + 前端页面可取。"""
    print("[2/3] 冒烟闸门……")
    proc = subprocess.Popen([exe], cwd=os.path.dirname(exe),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")

    ok = True
    port = None
    try:
        deadline = time.time() + 90
        while time.time() < deadline:
            if proc.poll() is not None:
                print("  [FAIL] 进程提前退出，码=%s" % proc.returncode)
                ok = False
                break
            line = proc.stdout.readline() if proc.stdout else ""
            if line:
                m = line.strip()
                if "127.0.0.1:" in m:
                    try:
                        port = int(m.split("127.0.0.1:")[1].split("/")[0])
                    except Exception:
                        pass
                    break
            time.sleep(0.2)

        if port is None:
            print("  [FAIL] 没拿到启动端口")
            ok = False
        else:
            print("  [PASS] 报告端口 %d" % port)
            alive = proc.poll() is None
            print("  [%s] 进程存活（%.1fs）" % ("PASS" if alive else "FAIL",
                                              time.time() - (deadline - 90)))
            ok = ok and alive

            import urllib.request
            base = "http://127.0.0.1:%d" % port
            # 根路径必须能落到工作台 —— 用户双击 exe 看到的第一屏就是它。
            # 这一项曾经漏测：所有测试 curl 的都是 /api/health 和 /views/xxx，
            # 没人 curl 过 /，于是用户双击看到一行 {"detail":"Not Found"}。
            try:
                req = urllib.request.Request(base + "/", method="GET")
                with urllib.request.urlopen(req, timeout=15) as resp:
                    body = resp.read()
                    final = resp.geturl()
                good = resp.status == 200 and "workbench" in final
                print("  [%s] 根路径 → 工作台（%s，%d 字节）"
                      % ("PASS" if good else "FAIL", final.replace(base, ""), len(body)))
                ok = ok and good
            except Exception as exc:
                print("  [FAIL] 根路径：%s" % exc)
                ok = False

            for path, name in (("/api/health", "后端 health"),
                               ("/views/v1-workbench.html", "前端工作台"),
                               ("/theme/tokens.css", "设计令牌")):
                try:
                    with urllib.request.urlopen(base + path, timeout=15) as resp:
                        body = resp.read()
                    good = resp.status == 200 and len(body) > 100
                    print("  [%s] %s (%d 字节)" % ("PASS" if good else "FAIL", name, len(body)))
                    ok = ok and good
                except Exception as exc:
                    print("  [FAIL] %s：%s" % (name, exc))
                    ok = False
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
    return ok


def pack(exe: str) -> str:
    print("[3/3] 打包 zip……")
    src_dir = os.path.dirname(exe)
    zip_path = os.path.join(DIST, STEM + ".zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for root, _dirs, files in os.walk(src_dir):
            for f in files:
                full = os.path.join(root, f)
                z.write(full, os.path.relpath(full, os.path.dirname(src_dir)))
    return zip_path


def main() -> int:
    exe = build()
    ok = smoke(exe)
    zpath = pack(exe)
    size = os.path.getsize(zpath) / 1024 / 1024
    print("\n" + "=" * 56)
    print("产物：%s" % exe)
    print("zip ：%s  %.1f MB" % (zpath, size))
    print("冒烟：" + ("PASS" if ok else "FAIL"))
    print("=" * 56)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
