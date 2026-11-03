"""LoomGrid Fusion 桌面启动器。

职责只有一件：起后端、打开界面、退出时收干净。

为什么不用 pywebview
--------------------
pywebview 是更好的形态（无地址栏、像原生应用），但它多一个依赖、多一层
WebView2 运行时依赖。阶段 5 的目标是「双击能跑」，先用系统默认浏览器
把链路打通；壳的形态是后续可替换的一层。

端口策略
--------
不写死 8000 —— 用户机器上 8000 可能被占。从 8000 起找一个空闲端口，
把实际端口传给前端（前端用相对路径，同源，不需要知道端口）。
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser


def _base_dir() -> str:
    """打包后资源在 sys._MEIPASS，源码运行时在脚本所在目录。"""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


BASE = _base_dir()
WEB_DIR = os.path.join(BASE, "web")


def _free_port(start: int = 8000, tries: int = 40) -> int:
    for p in range(start, start + tries):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", p))
            return p
        except OSError:
            continue
        finally:
            s.close()
    # 全被占时退回临时端口
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _wait_ready(port: int, timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = socket.socket()
        try:
            s.settimeout(0.5)
            s.connect(("127.0.0.1", port))
            return True
        except OSError:
            time.sleep(0.15)
        finally:
            s.close()
    return False


def main() -> int:
    if not os.path.isdir(WEB_DIR):
        print("找不到 web 目录：%s" % WEB_DIR, file=sys.stderr)
        return 2

    port = _free_port()
    os.environ["LGM_WEB_DIR"] = WEB_DIR
    os.environ["LGM_PORT"] = str(port)

    # 后端在后台线程里跑
    import uvicorn

    from api.app import app as fastapi_app

    config = uvicorn.Config(fastapi_app, host="127.0.0.1", port=port,
                            log_level="warning", access_log=False)
    server = uvicorn.Server(config)

    th = threading.Thread(target=server.run, daemon=True)
    th.start()

    if not _wait_ready(port):
        print("后端启动超时（端口 %d）" % port, file=sys.stderr)
        return 3

    url = "http://127.0.0.1:%d/views/v1-workbench.html" % port
    # flush=True 必须有：打印到管道时 stdout 默认块缓冲，
    # 外部进程（含打包后的启动验证脚本）会卡在 readline 上等不到这一行。
    print("LoomGrid Fusion 已启动：%s" % url, flush=True)
    try:
        webbrowser.open(url)
    except Exception:
        pass

    # 主线程保持存活，直到用户关掉控制台窗口
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.should_exit = True
        th.join(timeout=5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
