# -*- coding: utf-8 -*-
"""LoomGrid 发布打包脚本（PyInstaller）.

用法:
    python tools/pack_release.py ultra 3.2.8
    python tools/pack_release.py fusion 2.3.4

为什么不用 cx_Freeze（v3.2.8 起的决定，别改回去）:

    cx_Freeze 8.7.3 打这个 36k 行单文件 + PySide6 + matplotlib 的组合，
    产物在引导阶段就坏，而且 base="gui" 会把 ImportError 吞成 0xC0000005
    （访问冲突），没有窗口、没有日志、没有任何输出。

    实测：EXCLUDES 里排掉 unittest 后，matplotlib -> pyparsing ->
    pyparsing.testing 在模块级 import unittest 时失败，cx_Freeze 吞掉
    异常，exe 双击即退。移除该排除项后仍然崩。同一份源码换 PyInstaller
    6.20 打包，正常启动。

    这个坑藏了一整个开发周期：八轮测试 433 个断言，一次都没碰过打包产物。

本脚本末尾的冒烟闸门是硬性要求:

    打完包必须真启动一次、确认进程存活、读一眼日志。
    「zip 的 sha256 和 Release 记录一致」只证明上传没坏，
    不证明包能用——这两件事经常被混为一谈。

打包脚本必须入库:
    它留在临时目录里的时候，换台机器就没了；而这次的故障恰恰出在
    打包脚本的排除列表上。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TARGETS = {
    "ultra": {
        "src": "负荷曲线批量生成Ultra.py",
        "stem": "LoomGrid-Ultra",
        "entry": "loomgrid_ultra",
        "exclude_fonts": False,
    },
    "fusion": {
        "src": "负荷曲线批量生成工具 Pro 融合版 (v2.3).py",
        "stem": "LoomGrid-Fusion",
        "entry": "loomgrid_fusion",
        "exclude_fonts": False,
    },
}

# 排除的模块：体积大且运行时不用的。
# 注意：不要往里加 unittest —— matplotlib 经 pyparsing.testing 依赖它，
# 排掉会让 exe 在引导阶段静默崩溃（详见模块 docstring）。
EXCLUDES = [
    "tkinter", "IPython", "jedi", "nbformat", "notebook",
    "torch", "tensorflow", "sklearn", "numba", "pytest",
    "PySide6.QtQuick", "PySide6.QtQml", "PySide6.Qt3DCore",
    "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtBluetooth",
    "PySide6.QtNfc", "PySide6.QtPositioning", "PySide6.QtSerialPort",
    "PySide6.QtSql", "PySide6.QtTest",
    "PyQt5", "PyQt6", "PySide2",
]

HIDDEN = [
    "numpy", "pandas", "matplotlib", "scipy",
    "python_calamine", "openpyxl", "xlrd",
]


def build(target: str, version: str) -> int:
    cfg = TARGETS[target]
    tag = f"{cfg['stem']}-v{version}"
    build_root = os.path.join(REPO, "build_release", tag)
    dist_root = os.path.join(REPO, "dist")
    os.makedirs(build_root, exist_ok=True)
    os.makedirs(dist_root, exist_ok=True)

    src = os.path.join(REPO, cfg["src"])
    if not os.path.exists(src):
        print(f"源文件不存在: {src}")
        return 2
    entry = os.path.join(build_root, f"{cfg['entry']}.py")
    shutil.copyfile(src, entry)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onedir", "--windowed",
        "--name", tag,
        "--distpath", os.path.join(build_root, "dist"),
        "--workpath", os.path.join(build_root, "work"),
        "--specpath", build_root,
    ]
    icon = os.path.join(REPO, "assets", "app.ico")
    if os.path.exists(icon):
        cmd += ["--icon", icon]
    for mod in EXCLUDES:
        cmd += ["--exclude-module", mod]
    for mod in HIDDEN:
        cmd += ["--hidden-import", mod]
    cmd.append(entry)

    print(f"① PyInstaller 打包 {tag} …", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(f"   失败 exit={r.returncode}")
        print((r.stderr or "")[-2000:])
        return 1

    out_dir = os.path.join(build_root, "dist", tag)
    exe = os.path.join(out_dir, f"{tag}.exe")
    if not os.path.exists(exe):
        print(f"   exe 未生成: {out_dir}")
        return 1
    nfiles = sum(len(fs) for _, _, fs in os.walk(out_dir))
    size = sum(os.path.getsize(os.path.join(dp, f))
               for dp, _, fs in os.walk(out_dir) for f in fs)
    print(f"② 产物 {nfiles} 文件 / {size / 1048576:.1f} MB")

    # 冒烟闸门：必须真起来，否则整个打包算失败
    print("③ 启动冒烟（20s）…", flush=True)
    p = subprocess.Popen([exe], cwd=out_dir,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        time.sleep(0.5)
        if p.poll() is not None:
            break
    if p.poll() is not None:
        print(f"   ❌ 冒烟失败：进程退出，code={p.poll()}")
        print("      排查顺序：① 是否排掉了运行时依赖 ② 是否用了 --windowed 但")
        print("      代码里有 print(...file=sys.stderr) 或 log.add(sys.stderr)")
        print("      ③ 先打一个 console 版（--console）看 traceback")
        return 1
    print("   ✅ 存活")
    p.terminate()
    time.sleep(1)

    zp = os.path.join(dist_root, f"{tag}-win64.zip")
    if os.path.exists(zp):
        os.remove(zp)
    print(f"④ 压缩 …", flush=True)
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for dp, _, fs in os.walk(out_dir):
            for f in fs:
                fp = os.path.join(dp, f)
                z.write(fp, os.path.relpath(fp, os.path.join(build_root, "dist")))
    print(f"   {os.path.getsize(zp) / 1048576:.1f} MB → {zp}")
    return 0


def main() -> int:
    if len(sys.argv) < 3 or sys.argv[1] not in TARGETS:
        print(__doc__)
        print(f"可用目标: {', '.join(TARGETS)}")
        return 2
    return build(sys.argv[1], sys.argv[2])


if __name__ == "__main__":
    sys.exit(main())
