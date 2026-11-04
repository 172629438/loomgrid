# -*- coding: utf-8 -*-
"""只打 Fusion 单目标，产出可直接 zip 的独立目录。

与 build_exe_cx.py 的区别：那个一次打 12 个目标、共享一套 lib，体积大；
这里只收集 Fusion 实际需要的依赖，产物对齐历史 release（约 300 MB 解压）。
"""
import os
import re
import shutil
import sys

from cx_Freeze import setup, Executable

# 仓库根目录：本文件在 tools/ 下，上跳一层。
# 原来写死 C:\Users\Administrator\...，换一台机器直接跑不了。
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

SRC_REAL = "apps/负荷曲线批量生成工具 Pro 融合版 (v2.3).py"
# 版本号从程序真正使用的那个常量读，不从 docstring 读。
# docstring 是给人看的说明，FUSION_VERSION 是程序标题/弹窗实际引用的值；
# 两者分开写就会不同步——2026-11-03 就出过一次：docstring 写了 v2.3.7，
# 常量还停在 2.3.6，打出来的 exe 窗口标题写着旧版本。
_src_text = open(SRC_REAL, encoding="utf-8").read()
_m = re.search(r'^FUSION_VERSION\s*=\s*["\']([\d.]+)["\']', _src_text, re.M)
if not _m:
    raise SystemExit(f"没能从 {SRC_REAL} 读到 FUSION_VERSION 常量")
VER = _m.group(1)

# 顺带校验 docstring 是否跟上了，不一致只警告不阻断
_doc = re.search(r"LoomGrid Fusion v([\d.]+)", _src_text[:4000])
if _doc and _doc.group(1) != VER:
    print(f"[build] 警告: docstring 写的是 v{_doc.group(1)}，"
          f"但 FUSION_VERSION = {VER}，建议改成一致", flush=True)
print(f"[build] 从 FUSION_VERSION 读到版本号: v{VER}", flush=True)

SRC = "loomgrid_fusion_main.py"
TARGET_EXE = f"LoomGrid-Fusion-v{VER}"
OUT = f"dist/{TARGET_EXE}"

# cx_Freeze 对中文源文件名不稳，先复制一份英文名
shutil.copyfile(SRC_REAL, SRC)

if os.path.exists(OUT):
    shutil.rmtree(OUT)

EXCLUDES = [
    # ⚠ unittest 这一项换了打包器就要重新验。
    # PyInstaller 路径下排掉它会崩：matplotlib -> pyparsing ->
    # pyparsing.testing 在模块级 import unittest，取不到就双击即退。
    # cx_Freeze 的依赖分析方式不同，本机实测可用，故保持现状不改。
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
