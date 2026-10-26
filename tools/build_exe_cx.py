# -*- coding: utf-8 -*-
"""LoomGrid 全版本 Windows 打包（cx_Freeze）

用法：
    python tools/build_exe_cx.py

产物：dist/LoomGrid-Suite/ —— 12 个 exe 共用一套运行时，约 452 MB

为什么不用 PyInstaller：本机上 --onefile 会在 isolated child 阶段抛
OSError [Errno 22]；--onedir 会卡在 hook-PySide6.QtCore.py，25 分钟不出结果。
cx_Freeze 同样 12 个目标只用 87 秒，且天然支持多 exe 共享同一套 lib。
"""
import sys, os, shutil, traceback
from cx_Freeze import setup, Executable

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 中文名 → 英文副本（cx_Freeze 对中文源文件名不稳）
COPIES = {
    "负荷曲线批量生成Ultra.py":              "loomgrid_ultra_main.py",
    "负荷曲线批量生成工具 Pro 融合版 (v2.3).py": "loomgrid_fusion_main.py",
    "备份程序.py":                            "loomgrid_backup_main.py",
    "参考程序.py":                            "loomgrid_reference_main.py",
}
for src, dst in COPIES.items():
    if os.path.exists(src):
        shutil.copyfile(src, dst)

TARGETS = [
    ("loomgrid_ultra_main.py",      "LoomGrid-Ultra-v3.2.exe"),
    ("loomgrid_fusion_main.py",     "LoomGrid-Fusion-v2.3.exe"),
    ("loomgrid_backup_main.py",     "LoomGrid-Backup-v2.29.exe"),
    ("loomgrid_reference_main.py",  "LoomGrid-Reference-v2.29.exe"),
    ("legacy/LoomGrid-Fusion-v1.1.py", "LoomGrid-Fusion-v1.1.exe"),
    ("legacy/LoomGrid-Fusion-v1.2.py", "LoomGrid-Fusion-v1.2.exe"),
    ("legacy/LoomGrid-Fusion-v1.3.py", "LoomGrid-Fusion-v1.3.exe"),
    ("legacy/LoomGrid-Fusion-v1.4.py", "LoomGrid-Fusion-v1.4.exe"),
    ("legacy/LoomGrid-Fusion-v2.0.py", "LoomGrid-Fusion-v2.0.exe"),
    ("legacy/LoomGrid-Fusion-v2.1.py", "LoomGrid-Fusion-v2.1.exe"),
    ("legacy/LoomGrid-Fusion-v2.2.py", "LoomGrid-Fusion-v2.2.exe"),
    ("legacy/LoomGrid-Fusion-v2.3.py", "LoomGrid-Fusion-v2.3-legacy.exe"),
]

EXCLUDES = [
    "tkinter", "unittest", "pydoc", "doctest",
    "IPython", "jedi", "nbformat", "notebook",
    "torch", "tensorflow", "sklearn", "numba",
    "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineCore",
    "PySide6.QtQuick", "PySide6.QtQml", "PySide6.Qt3DCore",
    "PySide6.QtMultimedia", "PySide6.QtCharts",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning",
    "PySide6.QtSerialPort", "PySide6.QtSql", "PySide6.QtTest",
]

base = "gui" if sys.platform == "win32" else None

executables, skipped = [], []
for src, exe in TARGETS:
    if not os.path.exists(src):
        skipped.append((src, "源文件不存在"))
        continue
    executables.append(Executable(
        src, base=base, target_name=exe, icon="assets/app.ico",
        shortcut_name=exe.replace(".exe", ""), shortcut_dir=None,
    ))

print(f"[build] 目标 {len(executables)} 个，跳过 {len(skipped)} 个", flush=True)
for s, r in skipped:
    print(f"  [skip] {s} — {r}", flush=True)

sys.argv = [sys.argv[0], "build"]

setup(
    name="LoomGrid-Suite",
    version="1.0.0",
    description="LoomGrid - C&I Energy Storage Sizing Studio (all versions)",
    options={"build_exe": {
        "excludes": EXCLUDES,
        "packages": ["numpy", "pandas", "matplotlib", "scipy"],
        "include_msvcr": True,
        "optimize": 0,
        "build_exe": "dist/LoomGrid-Suite",
    }},
    executables=executables,
)
