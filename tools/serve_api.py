# -*- coding: utf-8 -*-
"""启动 LoomGrid Web API（FastAPI + uvicorn）。

为什么有这个文件
----------------
api/ 层 2609 行、28 个路由写齐了，依赖（fastapi / uvicorn / pydantic /
websockets）也都装好了，但**没有任何启动入口** —— 桌面版不 import api，
也没有 Dockerfile。想跑起来只能自己记得
`python -m uvicorn api.app:app --port 8000`。

这个脚本把那条命令包成一条：
    python tools/serve_api.py
    python tools/serve_api.py --port 8080 --reload
    python tools/serve_api.py --host 0.0.0.0        # 局域网可访问
    python tools/serve_api.py --dev                  # 同时打开 LGM_DEV_TOOLS

注意
----
- 默认只绑 127.0.0.1（本机）。绑 0.0.0.0 会暴露到局域网，
  `/api/ingest/allow` 与 `/api/ingest/upload` 能读写本机文件，
  自己确认网络环境再开。
- `--reload` 只适合开发：它起一个 watcher 进程，Windows 上偶尔会留残进程。
- 启动后打开 http://127.0.0.1:<port>/docs 能看到全部端点的交互文档。
"""
from __future__ import annotations

import argparse
import os
import sys


def main() -> int:
    ap = argparse.ArgumentParser(
        description="启动 LoomGrid Web API（FastAPI）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--host", default="127.0.0.1",
                    help="监听地址（默认 127.0.0.1，只本机）")
    ap.add_argument("--port", type=int, default=8000, help="端口（默认 8000）")
    ap.add_argument("--reload", action="store_true", help="代码改动自动重启（开发用）")
    ap.add_argument("--dev", action="store_true",
                    help="开启开发工具：注册 /api/export/adopt 等调试端点")
    ap.add_argument("--log-level", default="info",
                    choices=["critical", "error", "warning", "info", "debug", "trace"],
                    help="日志级别（默认 info）")
    args = ap.parse_args()

    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if repo not in sys.path:
        sys.path.insert(0, repo)
    os.chdir(repo)          # api/app.py 里可能有相对路径（LGM_ALLOW_ROOTS 等）

    if args.dev:
        os.environ["LGM_DEV_TOOLS"] = "1"
    os.environ.setdefault("LGM_ALLOW_ROOTS", repo)

    try:
        import uvicorn
    except ImportError:
        print("缺少 uvicorn，请先安装：", file=sys.stderr)
        print("  pip install fastapi uvicorn[standard] websockets", file=sys.stderr)
        return 2

    try:
        from api.app import app           # noqa: F401  早失败早报错
    except Exception as e:
        print(f"无法导入 api.app：{type(e).__name__}: {e}", file=sys.stderr)
        print("请确认在仓库根目录运行，且 fastapi / pydantic 已安装。", file=sys.stderr)
        return 3

    n_routes = len(getattr(app, "routes", []))
    print(f"LoomGrid Web API")
    print(f"  仓库     {repo}")
    print(f"  路由     {n_routes} 个")
    print(f"  监听     http://{args.host}:{args.port}")
    print(f"  文档     http://{args.host}:{args.port}/docs")
    print(f"  健康检查 http://{args.host}:{args.port}/api/health")
    if args.dev:
        print("  开发工具 已开启（LGM_DEV_TOOLS=1）")
    if args.host not in ("127.0.0.1", "localhost"):
        print()
        print(f"  ⚠ 绑定的是 {args.host}，局域网内其他机器可以访问。")
        print("    /api/ingest/allow 与 /api/ingest/upload 能读写本机文件。")
    print()

    uvicorn.run(
        "api.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
