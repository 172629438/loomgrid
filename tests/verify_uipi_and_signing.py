# -*- coding: utf-8 -*-
"""v2.3.20 两项的验证。

① 管理员拖放：验证降权重启函数存在且逻辑正确（无法真测管理员场景）
② 代码签名：真跑签名脚本，验签名状态与打包集成
"""
import io
import os
import subprocess
import sys

REPO = r"C:\Users\LENOVO\.hanako\repos\172629438__loomgrid"
MAIN = os.path.join(REPO, "apps", "负荷曲线批量生成工具 Pro 融合版 (v2.3).py")
sys.path.insert(0, REPO)
sys.stdout.reconfigure(encoding="utf-8")

passed, failed = [], []


def check(name, cond, detail=""):
    if cond:
        passed.append(name)
        print(f"  [PASS] {name}")
    else:
        failed.append(name)
        print(f"  [FAIL] {name}  {detail}")


src = io.open(MAIN, encoding="utf-8").read()

print("=" * 76)
print("① 管理员拖放：降权重启")
print("=" * 76)
check("有 _relaunch_as_normal", "def _relaunch_as_normal(" in src)
check("有 _uipi_blocks_drop", "def _uipi_blocks_drop(" in src)
check("启动时检测 UIPI 拦截", "_uipi_blocks_drop()" in src)
check("提供「以普通权限重启」的选项",
      "是否现在以普通权限重启" in src,
      "只提示不给路 = 没解决")
check("用 explorer.exe 转发降权", 'subprocess.Popen(["explorer.exe"' in src)
check("源码运行时说明无法自动重启",
      "当前是源码运行，无法自动重启" in src)

# 真跑：验证 _uipi_blocks_drop 在非管理员下返回 False
import importlib.util
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
spec = importlib.util.spec_from_file_location("fm_2320", MAIN)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

check("_uipi_blocks_drop 可调用", hasattr(mod, "_uipi_blocks_drop"))
if hasattr(mod, "_uipi_blocks_drop"):
    r = mod._uipi_blocks_drop()
    print(f"  当前环境 _uipi_blocks_drop() = {r}（非管理员应为 False）")
    check("非管理员环境下返回 False", r is False, repr(r))

check("_relaunch_as_normal 可调用", hasattr(mod, "_relaunch_as_normal"))
if hasattr(mod, "_relaunch_as_normal"):
    # 源码运行时应该返回 False（explorer 带不了参数）
    r2 = mod._relaunch_as_normal()
    print(f"  源码运行时 _relaunch_as_normal() = {r2}（应为 False）")
    check("源码运行时降权返回 False（explorer 不能带参）", r2 is False, repr(r2))

print()
print("=" * 76)
print("② 代码签名")
print("=" * 76)
sign_ps1 = os.path.join(REPO, "tools", "sign_fusion.ps1")
check("有 tools/sign_fusion.ps1", os.path.exists(sign_ps1))

if os.path.exists(sign_ps1):
    raw = open(sign_ps1, "rb").read()
    check("脚本带 UTF-8 BOM（PowerShell 5.1 必需）",
          raw.startswith(b"\xef\xbb\xbf"),
          "无 BOM 会让中文注释变乱码、连带花括号解析错")
    # 语法检查
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"$null = [System.Management.Automation.PSParser]::Tokenize("
         f"(Get-Content -Raw -LiteralPath '{sign_ps1}'), [ref]$null); 'OK'"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("PowerShell 语法可解析", "OK" in (r.stdout or ""),
          (r.stderr or r.stdout)[:200])

pack = os.path.join(REPO, "tools", "pack_release.py")
psrc = io.open(pack, encoding="utf-8").read()
check("打包脚本集成了签名步骤", "sign_fusion.ps1" in psrc)
# v2.3.22：插入字体复制后步骤编号变成：④字体 / ⑤签名 / ⑥压缩
check("签名在压缩之前（否则 zip 里是未签名 exe）",
      psrc.find("sign_fusion.ps1") < psrc.find("⑥ 压缩"),
      "顺序错了")

# 真查现有 exe 的签名状态
exe = None
for root, dirs, files in os.walk(os.path.join(REPO, "build_release")):
    for f in files:
        if f.startswith("LoomGrid-Fusion-v2.3.19") and f.endswith(".exe"):
            exe = os.path.join(root, f)
            break
    if exe:
        break

if exe:
    print(f"  检查已签的 exe: {os.path.basename(exe)}")
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"(Get-AuthenticodeSignature -LiteralPath '{exe}').Status"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    st = (r.stdout or "").strip()
    print(f"  签名状态: {st}")
    check("v2.3.19 的 exe 已签名且有效", st == "Valid", st)
else:
    print("  [SKIP] 没找到 v2.3.19 的 exe")

print()
print("=" * 76)
print(f"结果：PASS {len(passed)}   FAIL {len(failed)}")
for f in failed:
    print("  -", f)
print("=" * 76)
sys.exit(1 if failed else 0)
