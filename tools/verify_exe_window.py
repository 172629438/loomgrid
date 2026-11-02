# -*- coding: utf-8 -*-
"""启动 exe 并按 AGENTS.md 的判据验证：读窗口标题，不看进程存活。

为什么不能看进程存活
--------------------
卡在错误对话框里的进程同样"存活"。cx_Freeze / PyInstaller 的引导错误
常常表现为一个弹窗（「Fatal error」「Python error in main script」），
此时主进程活着，看起来一切正常。

判据
----
    通过 = 至少 1 个可见窗口 + 任一窗口标题包含预期关键词 + 无错误对话框
    失败 = 无窗口 / 标题不含关键词 / 出现 Fatal error、Python error 这类对话框

窗口数不作为判据：Ultra 有全屏启动动画 + 模态欢迎框，
同一进程出现 3 个窗口是正常启动序列。真正要防的是「卡在错误对话框里
进程仍然存活」那个情形，靠标题关键词而非窗口数识别。

用法
----
    python tools/verify_exe_window.py <exe路径> [--expect 标题关键词] [--seconds 25]

退出码：0 正常，1 异常。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)
EnumWindows = user32.EnumWindows
EnumWindowsProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
GetWindowTextW = user32.GetWindowTextW
GetWindowTextLengthW = user32.GetWindowTextLengthW
IsWindowVisible = user32.IsWindowVisible
GetWindowThreadProcessId = user32.GetWindowThreadProcessId


def windows_of_pid(pid: int):
    """枚举指定进程的可见顶层窗口标题。"""
    found = []

    def _cb(hwnd, _lparam):
        if not IsWindowVisible(hwnd):
            return True
        wpid = wt.DWORD()
        GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
        if wpid.value != pid:
            return True
        n = GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        GetWindowTextW(hwnd, buf, n + 1)
        found.append(buf.value)
        return True

    EnumWindows(EnumWindowsProc(_cb), 0)
    return found


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    exe = os.path.abspath(sys.argv[1])
    if not os.path.exists(exe):
        print(f"✗ exe 不存在: {exe}")
        return 1

    expect = None
    seconds = 25
    args = sys.argv[2:]
    for i, a in enumerate(args):
        if a == "--expect" and i + 1 < len(args):
            expect = args[i + 1]
        if a == "--seconds" and i + 1 < len(args):
            seconds = int(args[i + 1])

    print(f"启动: {os.path.basename(exe)}  ({os.path.getsize(exe)/1024/1024:.1f} MB)")
    proc = subprocess.Popen([exe], cwd=os.path.dirname(exe))

    seen = []
    t0 = time.time()
    try:
        while time.time() - t0 < seconds:
            time.sleep(2.5)
            if proc.poll() is not None:
                print(f"✗ 进程在 {time.time()-t0:.1f}s 时退出，code={proc.returncode}")
                return 1
            titles = windows_of_pid(proc.pid)
            for t in titles:
                if t not in seen:
                    seen.append(t)
                    print(f"  +{time.time()-t0:5.1f}s  窗口: {t!r}")

        final = windows_of_pid(proc.pid)
        print(f"\n存活 {seconds}s，最终窗口数 {len(final)}")

        # 判定
        bad_markers = ("error", "错误", "fatal", "traceback", "无法", "failed", "exception")
        bad = [t for t in final if any(m in t.lower() for m in bad_markers)]

        ok = True
        if bad:
            print(f"✗ 检测到错误对话框: {bad}")
            ok = False
        if len(final) == 0:
            print("✗ 没有可见窗口（进程活着但界面没起来）")
            ok = False
        if expect and final and not any(expect in t for t in final):
            print(f"✗ 没有任何窗口标题包含 {expect!r}")
            ok = False
        # 窗口数不做硬判据：Ultra 有全屏启动动画 + 模态欢迎框，
        # 同一进程三个窗口是正常启动序列，不是故障。
        # 真正要防的是「错误对话框」，已由 bad_markers 覆盖。
        if len(final) > 1 and not bad:
            print(f"  窗口数 {len(final)} > 1，但无错误标识（启动动画/模态框属正常）")

        print("\n" + ("✓ 通过" if ok else "✗ 不通过"))
        return 0 if ok else 1
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
