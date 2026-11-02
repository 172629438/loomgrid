# -*- coding: utf-8 -*-
"""打一个 console 版 Fusion，用来抓崩溃时的真实报错。"""
import os
import shutil
import sys

from cx_Freeze import setup, Executable

ROOT = r"C:\Users\Administrator\.hanako\repos\172629438__loomgrid"
os.chdir(ROOT)

SRC = "loomgrid_fusion_dbg.py"
OUT = "dist/fusion-debug"
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
    name="fusion-debug",
    version="2.3.2",
    options={"build_exe": {
        "excludes": EXCLUDES,
        "packages": ["numpy", "pandas", "matplotlib", "scipy"],
        "include_msvcr": True,
        "optimize": 0,
        "build_exe": OUT,
    }},
    executables=[Executable(
        SRC,
        base=None,          # 关键：console 版，stderr 可见
        target_name="fusion-debug.exe",
        icon="assets/app.ico",
    )],
)
