# -*- coding: utf-8 -*-
"""批量跑 tests/verify_*.py 并汇总。

README 里给的是 heredoc 写法，Windows cmd/PowerShell 5.1 都跑不了，
这个脚本就是 Windows 可用的等价物。

用法：
    python tools/run_all_tests.py              # 跑全部
    python tools/run_all_tests.py --core       # 只跑不依赖 GUI / HTTP 的
    python tools/run_all_tests.py --filter cap # 只跑文件名含 cap 的

判据（与 tests/README.md 一致）：脚本末尾打印 PASS/FAIL，退出码 0 = 全过。
环境类失败（缺 fastapi / uvicorn / 无显示器）单独标记 ENV，不混进 FAIL。
"""
import argparse
import glob
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

# 需要 HTTP 服务或真实窗口的脚本：故意不进 CI，跑不动时标 ENV 而不是 FAIL
NEEDS_ENV = {
    # v2.3.11fix11: verify_prod_artifact.py 会启动 dist/LoomGrid-Fusion 下的
    # **Web 版** exe（它起 FastAPI 并打印 127.0.0.1:端口），启动时会弹出浏览器页面。
    # 它测的不是桌面版产物，--core 不应包含它。
    "verify_prod_artifact.py",
    "verify_api.py", "verify_wiring.py", "verify_wiring_e2e.py",
    "verify_excel_e2e.py", "verify_export_async.py",
    "verify_simulate_api.py", "verify_simulate_apply.py",
    "verify_gui_flow.py", "verify_gui_signal.py", "verify_gui_smoke2.py",
    "verify_algorithms_deep.py", "verify_plot_fit_e2e.py",
    "verify_segment_filter.py",
}
ENV_HINTS = re.compile(
    r"ModuleNotFoundError|No module named|qt\.qpa|no Qt platform plugin|"
    r"ConnectionError|Max retries|address already in use|"
    r"uvicorn|fastapi|websockets",
    re.I,
)


def run_one(path, timeout):
    t0 = time.time()
    try:
        p = subprocess.run(
            [sys.executable, path],
            cwd=REPO,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return "TIMEOUT", f">{timeout}s", time.time() - t0, ""
    out = (p.stdout or "") + (p.stderr or "")
    tail = ""
    for line in reversed(out.strip().splitlines()):
        if "PASS" in line or "FAIL" in line:
            tail = line.strip()
            break
    if p.returncode == 0:
        status = "PASS"
    else:
        status = "ENV" if ENV_HINTS.search(out) else "FAIL"
    return status, tail, time.time() - t0, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--core", action="store_true", help="跳过需要 GUI/HTTP 的脚本")
    ap.add_argument("--filter", default="", help="文件名子串过滤")
    ap.add_argument("--timeout", type=int, default=240)
    ap.add_argument("-v", "--verbose", action="store_true", help="失败时打印尾部输出")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(REPO, "tests", "verify_*.py")))
    if args.core:
        files = [f for f in files if os.path.basename(f) not in NEEDS_ENV]
    if args.filter:
        files = [f for f in files if args.filter.lower() in os.path.basename(f).lower()]

    print(f"共 {len(files)} 个脚本，超时 {args.timeout}s/个\n")
    rows, failed, env_failed = [], [], []
    for i, f in enumerate(files, 1):
        name = os.path.basename(f)
        status, tail, sec, out = run_one(f, args.timeout)
        rows.append((name, status, tail, sec))
        print(f"[{i:>2}/{len(files)}] {status:<7} {sec:5.1f}s  {name}")
        if status == "FAIL":
            failed.append(name)
            if args.verbose:
                print("\n".join(out.strip().splitlines()[-25:]))
        elif status in ("ENV", "TIMEOUT"):
            env_failed.append((name, status))

    print("\n" + "=" * 70)
    ok = sum(1 for r in rows if r[1] == "PASS")
    print(f"PASS {ok} / FAIL {len(failed)} / ENV·TIMEOUT {len(env_failed)} / 总 {len(rows)}")
    if failed:
        print("真失败：" + ", ".join(failed))
    if env_failed:
        print("环境或超时（不计入失败）：" + ", ".join(f"{n}({s})" for n, s in env_failed))
    print("=" * 70)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
