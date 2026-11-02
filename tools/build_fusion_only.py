# -*- coding: utf-8 -*-
"""只打 Fusion 单目标，产出可直接 zip 的独立目录。

与 build_exe_cx.py 的区别：那个一次打 12 个目标、共享一套 lib，体积大；
这里只收集 Fusion 实际需要的依赖，产物对齐历史 release（约 300 MB 解压）。
"""
import os
import shutil
import sys

from cx_Freeze import setup, Executable

ROOT = r"C:\Users\Administrator\.hanako\repos\172629438__loomgrid"
os.chdir(ROOT)

VER = "2.3.2"
SRC = "loomgrid_fusion_main.py"
TARGET_EXE = f"LoomGrid-Fusion-v{VER}"
OUT = f"dist/{TARGET_EXE}"

# cx_Freeze 对中文源文件名不稳，先复制一份英文名
shutil.copyfile("负荷曲线批量生成工具 Pro 融合版 (v2.3).py", SRC)

if os.path.exists(OUT):
    shutil.rmtree(OUT)

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

sys.argv = [sys.argv[0], "build"]

setup(
    name=TARGET_EXE,
    version=VER,
    description="LoomGrid Fusion - C&I Energy Storage Sizing Studio",
    options={"build_exe": {
        "excludes": EXCLUDES,
        "packages": ["numpy", "pandas", "matplotlib", "scipy"],
        "include_msvcr": True,
        "optimize": 0,
        "build_exe": OUT,
    }},
    executables=[Executable(
        SRC,
        base="gui" if sys.platform == "win32" else None,
        target_name=f"{TARGET_EXE}.exe",
        icon="assets/app.ico",
        shortcut_name=TARGET_EXE,
        shortcut_dir=None,
    )],
)
