# -*- coding: utf-8 -*-
"""生成发布包：README / CHANGELOG / BUILD-INFO + zip。

为什么用 Python 而不是 PowerShell：PS 5.1 把 UTF-8 无 BOM 的中文脚本当 ANSI 读，
中文字面量会直接 ParserError（publish_ui.ps1 第一次就这么挂的）。Python 没这个坑。

前置：先跑 tools/publish_ui.ps1 产出 dist\\LoomGrid.UI
用法：python tools/make_release.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist" / "LoomGrid.UI"
VERSION = "v1.0.0"

README = """【这是什么】
LoomGrid Pro {ver} —— LoomGrid Fusion 的 WinUI 3 重制版。
界面重写，算法层零改动（与 Fusion v2.3.9 完全一致）。

【怎么启动】
双击 LoomGrid.UI.exe。
首次启动约 2 秒（拉起 Python 后端）。启动完成前请勿重复双击。

【需要什么】
Windows 10 1809+ 或 Windows 11
VC++ 2015-2022 运行时（Windows 10/11 一般已装）。
  若提示缺少 vcruntime140.dll，装：
  https://aka.ms/vs/17/release/vc_redist.x64.exe
不需要装 Python，不需要装 Windows App SDK（运行时已随包）。

【不要做什么】
不要把 python-core\\ 目录移动或删除 —— 程序依赖它。
不要把 exe 单独拷走 —— 它需要同目录的 dll。
不要把程序放进 C:\\Windows 或 Program Files 下直接运行（会有写入权限问题）。

【本版本的限制】
功能等价约 82%，不是 Fusion 的完全替代。
未实现的功能在界面上标为灰色，鼠标悬停有说明；
完整清单见程序内 V1 右下角「关于」。

【怎么验（七步）】
  1. 双击 exe，六屏逐一点开（V1→V2→V3→V4→V5→V6）
  2. 拖一个真实 Excel 进 V1，走完 V2 映射
  3. V3 看曲线出来、点日期格切日
  4. V4 看月度表数字，与手算核对一格
  5. V5 设输出目录、点「开始导出」
  6. V6 看结果、打开输出目录、确认 PNG 存在且文件名符合模板
  7. 关程序，任务管理器确认没有残留的 python 进程

【输出在哪】
默认 C:\\Users\\<你>\\Documents\\LoomGrid导出
可在 V5 里改。

【出问题怎么看】
启动日志：%LOCALAPPDATA%\\LoomGrid\\ui-startup.log
把这份日志连同 BUILD-INFO.txt 的内容一起提供，能快速定位版本与失败点。
"""

CHANGELOG = """LoomGrid Pro 版本记录
=====================

{ver}　{date}
  首个 WinUI 3 正式版，六屏全部可用。

  界面
    · V1 工作台：拖放多文件、三态判定（就绪/需确认/失败）、移除与清空
    · V2 列映射：真实样本预览、日期/资产/负荷列指定、逐文件确认
    · V3 预览与策略：曲线渲染、分组切换、日期面板切日、键盘 ←→ 翻组
    · V4 容量测算：mode A/B/C、评估策略、双向计算、每日表、月度表
    · V5 导出：按 session 分组导出、标题与文件名模板预览、分组级倍率覆盖
    · V6 结果：缩略图、宽表清单、失败归因、输出目录直达

  算法层
    · core / services / plotting 与 Fusion v2.3.9 完全一致
    · 基线 18 场景 703 项零差异

  与 Fusion 的差异
    · 探针（鼠标悬停读曲线）为新增能力，原 Fusion 曲线上没有鼠标交互
    · 未实现的功能在界面上禁用并标注，详见程序内「关于」

  已知未验证
    · 提权的源进程往程序里拖文件（UIPI 拦截）会给出提示，但此路径未实测
    · 目标机若未装 VC++ 2015-2022 运行时，会提示缺少 vcruntime140.dll
"""


def git(*args: str) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()
    except Exception:
        return "(unknown)"


def main() -> int:
    if not DIST.exists():
        print(f"产物目录不存在：{DIST}\n先跑 tools\\publish_ui.ps1")
        return 1

    now = datetime.now(timezone(timedelta(hours=8)))
    stamp = now.strftime("%Y-%m-%d %H:%M:%S +08:00")
    date = now.strftime("%Y-%m-%d")
    commit = git("rev-parse", "--short", "HEAD")

    build_info = (
        f"Version      {VERSION}\n"
        f"Commit       {commit}\n"
        f"Built at     {stamp}\n"
        f"Base         Fusion v2.3.9（算法层零改动，基线 703 项零差异）\n"
        f"Artifact     {DIST.name}\n"
    )

    (DIST / "README.txt").write_text(README.format(ver=VERSION), encoding="utf-8")
    (DIST / "CHANGELOG.txt").write_text(CHANGELOG.format(ver=VERSION, date=date), encoding="utf-8")
    (DIST / "BUILD-INFO.txt").write_text(build_info, encoding="utf-8")

    zip_path = ROOT / "dist" / f"LoomGrid-Pro-{VERSION}-win64.zip"
    if zip_path.exists():
        zip_path.unlink()

    n = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for p in sorted(DIST.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(DIST.parent))
                n += 1

    size_mb = zip_path.stat().st_size / 1024 / 1024
    print(f"README / CHANGELOG / BUILD-INFO 已写入 {DIST.name}")
    print(f"BUILD-INFO: commit={commit} built={stamp}")
    print(f"zip: {zip_path}")
    print(f"条目 {n} 个，{size_mb:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
