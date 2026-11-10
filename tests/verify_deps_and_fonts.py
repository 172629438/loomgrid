# -*- coding: utf-8 -*-
"""依赖完整性与字体回退的回归测试。

验的是「换台机器还能不能用」，不是「本机能不能跑」。
分三块：
  ① 产物依赖：源码里的三方依赖，PYZ 里是否都有
  ② Excel 引擎：openpyxl/xlrd/xlsxwriter 这类间接依赖是否在
  ③ 字体回退：程序目录放字体会不会被认出；完全没有时会打标记+弹提示
"""
import ast
import io
import os
import shutil
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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


# ══════════════════════════════════════════════════════════════════
print("=" * 76)
print("① 字体回退链")
print("=" * 76)

from plotting import fonts as F

check("有 _font_dirs（程序目录 + fonts/）", hasattr(F, "_font_dirs"))
check("有 _register_local_fonts", hasattr(F, "_register_local_fonts"))
check("有 _pick_from_installed", hasattr(F, "_pick_from_installed"))
check("有 CJK_FONT_MISSING 标记", hasattr(F, "CJK_FONT_MISSING"))

dirs = F._font_dirs()
check("_font_dirs 返回三处（程序根 / fonts / assets-fonts）",
      len(dirs) == 3, str(dirs))
check("第二处是 fonts 子目录", os.path.basename(dirs[1]) == "fonts", dirs[1])
check("第三处是 assets/fonts（自带字体）",
      "assets" in dirs[2] and "fonts" in dirs[2], dirs[2])

# 真测：往 fonts/ 放一个字体，看能不能注册上
test_dir = os.path.join(REPO, "fonts")
created = False
if not os.path.isdir(test_dir):
    os.makedirs(test_dir, exist_ok=True)
    created = True

src_font = r"C:\Windows\Fonts\simhei.ttf"
dst_font = os.path.join(test_dir, "test_cjk_probe.ttf")
copied = False
try:
    if os.path.exists(src_font) and not os.path.exists(dst_font):
        shutil.copyfile(src_font, dst_font)
        copied = True
    if copied:
        added = F._register_local_fonts()
        check("程序目录里的字体被 addfont 注册", bool(added), str(added))
        F._FONT_NAME = None
        F.CJK_FONT_MISSING = False
        nm = F._find_cjk_font()
        check("能找到中文字体（不是 DejaVu）", nm and nm != "DejaVu Sans", repr(nm))
    else:
        print("  [SKIP] 本机无 simhei.ttf，跳过放入测试")
finally:
    if copied and os.path.exists(dst_font):
        os.remove(dst_font)
    if created and os.path.isdir(test_dir) and not os.listdir(test_dir):
        os.rmdir(test_dir)

# 降级路径
orig_c, orig_p, orig_r = (F._CJK_FONT_CANDIDATES, F._pick_from_installed,
                          F._register_local_fonts)
try:
    F._CJK_FONT_CANDIDATES = []
    F._pick_from_installed = lambda: None
    F._register_local_fonts = lambda: []
    F.CJK_FONT_MISSING = False
    r = F._find_cjk_font()
    check("无字体时降级成 DejaVu Sans", r == "DejaVu Sans", repr(r))
    check("降级时置 CJK_FONT_MISSING", F.CJK_FONT_MISSING is True)
finally:
    F._CJK_FONT_CANDIDATES, F._pick_from_installed, F._register_local_fonts = (
        orig_c, orig_p, orig_r)
    F.CJK_FONT_MISSING = False
    F._FONT_NAME = None

# 主程序侧
msrc = io.open(MAIN, encoding="utf-8").read()
check("主程序引了 fonts 模块", "from plotting import fonts as _fonts_mod" in msrc)
check("启动时读 CJK_FONT_MISSING", 'getattr(_fonts_mod, "CJK_FONT_MISSING"' in msrc)
check("有「缺少中文字体」弹窗", "缺少中文字体" in msrc)

# ══════════════════════════════════════════════════════════════════
print()
print("=" * 76)
print("② Fusion 执行路径的三方依赖清单")
print("=" * 76)

FUSION_ROOTS = ["core", "services", "plotting", "ui"]
files = [MAIN]
for r in FUSION_ROOTS:
    d = os.path.join(REPO, r)
    for dp, dns, fns in os.walk(d):
        if "__pycache__" in dp:
            continue
        files += [os.path.join(dp, f) for f in fns if f.endswith(".py")]

STDLIB = set(getattr(sys, "stdlib_module_names", ()))
LOCAL = {"core", "services", "plotting", "ui", "apps"}
tops = set()
for p in files:
    try:
        tree = ast.parse(io.open(p, encoding="utf-8").read())
    except Exception:
        continue
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                tops.add(a.name.split(".")[0])
        elif isinstance(n, ast.ImportFrom):
            if n.level or not n.module:
                continue
            tops.add(n.module.split(".")[0])

third = sorted(t for t in tops
               if t not in STDLIB and t not in LOCAL and not t.startswith("_"))
print(f"  三方依赖 {len(third)} 个：{third}")

# 期望清单：少一个都说明代码变了却没更新测试
EXPECTED = {"PySide6", "matplotlib", "numpy", "pandas", "scipy",
            "chardet", "chinese_calendar", "psutil"}
check("依赖清单与预期一致", set(third) == EXPECTED,
      f"多: {set(third) - EXPECTED}  少: {EXPECTED - set(third)}")

# 每个依赖都要能被 import（本机实测，不是查文件）
for pkg in sorted(EXPECTED):
    try:
        __import__(pkg)
        ok = True
    except ImportError:
        ok = False
    check(f"{pkg} 可 import", ok, "本机都导不进来，产物更不可能有")

# ══════════════════════════════════════════════════════════════════
print()
print("=" * 76)
print("③ 产物里的依赖（有 exe 才查）")
print("=" * 76)

EXE = None
br = os.path.join(REPO, "build_release")
if os.path.isdir(br):
    for d in sorted(os.listdir(br), reverse=True):
        cand = os.path.join(br, d, "dist", d, f"{d}.exe")
        if os.path.exists(cand):
            EXE = cand
            break

if EXE:
    try:
        from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader
        car = CArchiveReader(EXE)
        pz = [n for n in car.toc if n.endswith(".pyz")][0]
        tz = os.path.join(tempfile.gettempdir(), "dep_regr.pyz")
        open(tz, "wb").write(car.extract(pz))
        pyz = set(ZlibArchiveReader(tz).toc)

        print(f"  产物: {os.path.basename(EXE)}  PYZ {len(pyz)} 模块")
        for pkg in sorted(EXPECTED | {"openpyxl", "xlrd", "xlsxwriter", "lxml"}):
            n = sum(1 for m in pyz if m == pkg or m.startswith(pkg + "."))
            check(f"{pkg} 在产物 PYZ 里", n > 0, f"{n} 个子模块")
    except Exception as e:
        print(f"  [SKIP] 读 PYZ 失败: {type(e).__name__}: {e}")
else:
    print("  [SKIP] 没找到打包产物")

print()
print("=" * 76)
print(f"结果：PASS {len(passed)}   FAIL {len(failed)}")
for f in failed:
    print("  -", f)
print("=" * 76)
sys.exit(1 if failed else 0)
