# -*- coding: utf-8 -*-
"""批量打包所有版本为单文件 exe（PyInstaller）

用法：
    python build_all_exe.py            # 打全部
    python build_all_exe.py ultra      # 只打 Ultra
    python build_all_exe.py fusion     # 只打融合版
    python build_all_exe.py legacy     # 只打历史版本

产物落在 dist/，日志落在 build_logs/。
"""
import os
import subprocess
import sys
import time
import glob

ROOT = os.path.dirname(os.path.abspath(__file__))
ICON = os.path.join(ROOT, "assets", "app.ico")
DIST = os.path.join(ROOT, "dist")
BUILD = os.path.join(ROOT, "build")
LOGS = os.path.join(ROOT, "build_logs")

EXCLUDES = [
    "torch", "torchvision", "torchaudio", "tensorflow",
    "IPython", "jedi", "notebook", "ipykernel", "jupyter_client",
    "pytest", "tkinter", "PyQt5", "PyQt6", "PySide2",
    "numba", "llvmlite", "sqlalchemy", "psycopg2",
]

GROUPS = {
    "ultra": [
        ("负荷曲线批量生成Ultra.py", "LoomGrid-Ultra-v3.2"),
    ],
    "fusion": [
        ("负荷曲线批量生成工具 Pro 融合版 (v2.3).py", "LoomGrid-Fusion-v2.3"),
    ],
    "legacy": [
        ("legacy/LoomGrid-Fusion-v1.1.py", "LoomGrid-Fusion-v1.1"),
        ("legacy/LoomGrid-Fusion-v1.2.py", "LoomGrid-Fusion-v1.2"),
        ("legacy/LoomGrid-Fusion-v1.3.py", "LoomGrid-Fusion-v1.3"),
        ("legacy/LoomGrid-Fusion-v1.4.py", "LoomGrid-Fusion-v1.4"),
        ("legacy/LoomGrid-Fusion-v2.0.py", "LoomGrid-Fusion-v2.0"),
        ("legacy/LoomGrid-Fusion-v2.1.py", "LoomGrid-Fusion-v2.1"),
        ("legacy/LoomGrid-Fusion-v2.2.py", "LoomGrid-Fusion-v2.2"),
    ],
    "refs": [
        ("备份程序.py", "LoomGrid-Backup-v2.29"),
        ("参考程序.py", "LoomGrid-Reference-v2.29"),
    ],
}


def build(src, name):
    src_path = os.path.join(ROOT, src)
    if not os.path.exists(src_path):
        print(f"[skip] 源文件不存在: {src}")
        return None
    os.makedirs(LOGS, exist_ok=True)
    log = os.path.join(LOGS, f"{name}.log")
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--onefile", "--windowed",
        f"--icon={ICON}",
        f"--name={name}",
        f"--distpath={DIST}",
        f"--workpath={BUILD}",
        f"--specpath={BUILD}",
    ]
    for m in EXCLUDES:
        cmd += ["--exclude-module", m]
    cmd.append(src_path)

    t0 = time.time()
    with open(log, "w", encoding="utf-8", errors="replace") as f:
        p = subprocess.run(cmd, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT)
    dt = time.time() - t0
    exe = os.path.join(DIST, f"{name}.exe")
    if p.returncode == 0 and os.path.exists(exe):
        mb = os.path.getsize(exe) / 1024 / 1024
        print(f"[OK]   {name}.exe  {mb:.1f} MB  {dt:.0f}s")
        return exe
    print(f"[FAIL] {name}  rc={p.returncode}  {dt:.0f}s  日志: {log}")
    return None


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    targets = []
    if which == "all":
        for g in GROUPS.values():
            targets += g
    elif which in GROUPS:
        targets = GROUPS[which]
    else:
        print(f"未知分组: {which}，可选 {list(GROUPS)} 或 all")
        return

    print(f"待打包 {len(targets)} 个\n" + "=" * 60)
    ok = []
    for src, name in targets:
        e = build(src, name)
        if e:
            ok.append(e)

    print("\n" + "=" * 60)
    print(f"完成 {len(ok)}/{len(targets)}")
    for e in ok:
        print(f"  {os.path.basename(e)}  {os.path.getsize(e)/1024/1024:.1f} MB")


if __name__ == "__main__":
    main()
