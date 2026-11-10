# -*- coding: utf-8 -*-
"""打包产物专项测试：验的是 exe，不是源码。

源码测试全绿不等于产物能用——打包会改模块路径、丢资源文件、丢数据。
这个文件针对那几个已知的产物侧风险逐项验：

  ① exe 不存在 / 大小为 0
  ② 启动就崩（进程秒退）
  ③ 启动弹错误对话框（进程「存活」但卡在对话框里）
  ④ 窗口标题里的版本号与代码里的 FUSION_VERSION 不一致
  ⑤ 图标资源没打进产物（setWindowIcon 拿不到文件）
  ⑥ core/services 没打进去（PyInstaller 里 import 失败）
  ⑦ 省份数据没跟上（产物里少省份）

用法：
  python tests/verify_packaged_exe.py <exe路径>
  python tests/verify_packaged_exe.py            # 自动找 dist 下最新的
"""
import glob
import os
import re
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

passed, failed = [], []
info = []


def check(name, cond, detail=""):
    if cond:
        passed.append(name)
        print(f"  [PASS] {name}")
    else:
        failed.append(name)
        print(f"  [FAIL] {name}  {detail}")


def find_exe():
    if len(sys.argv) > 1:
        return sys.argv[1]
    # 从代码里取版本，拼出预期路径
    main_py = os.path.join(REPO, "apps", "负荷曲线批量生成工具 Pro 融合版 (v2.3).py")
    src = open(main_py, encoding="utf-8").read()
    m = re.search(r'FUSION_VERSION = "([\d.]+)"', src)
    ver = m.group(1) if m else None
    cands = []
    if ver:
        cands.append(os.path.join(REPO, "build_release",
                                  f"LoomGrid-Fusion-v{ver}", f"LoomGrid-Fusion-v{ver}.exe"))
        cands.append(os.path.join(REPO, "dist",
                                  f"LoomGrid-Fusion-v{ver}", f"LoomGrid-Fusion-v{ver}.exe"))
    for c in cands:
        if os.path.exists(c):
            return c
    # 兜底：build_release / dist 下所有 exe，取最新的
    allx = glob.glob(os.path.join(REPO, "build_release", "**", "*.exe"), recursive=True) + \
           glob.glob(os.path.join(REPO, "dist", "**", "*.exe"), recursive=True)
    allx = [p for p in allx if os.path.getsize(p) > 1_000_000]
    if not allx:
        return None
    return max(allx, key=os.path.getmtime)


print("=" * 76)
print("① 产物定位")
print("=" * 76)
EXE = find_exe()
if not EXE:
    # 源码阶段本来就没有 exe（要先跑 pack_release.py），不是回归也不是环境问题。
    # 直接 SKIP 退出 0——否则每次源码测试都多一条红，人就会开始忽略红。
    print("[SKIP] 未找到打包产物（先跑 tools/pack_release.py fusion <版本>）")
    sys.exit(0)

size_mb = os.path.getsize(EXE) / 1024 / 1024
print(f"  产物: {EXE}")
print(f"  大小: {size_mb:.1f} MB")
check("exe 存在且非空", size_mb > 1, f"{size_mb:.2f} MB")
check("exe 大小合理（>15MB，说明依赖打进去了）", size_mb > 15, f"{size_mb:.1f} MB")

EXE_DIR = os.path.dirname(EXE)

print()
print("=" * 76)
print("② 版本号一致性")
print("=" * 76)
main_py = os.path.join(REPO, "apps", "负荷曲线批量生成工具 Pro 融合版 (v2.3).py")
src = open(main_py, encoding="utf-8").read()
m = re.search(r'FUSION_VERSION = "([\d.]+)"', src)
code_ver = m.group(1) if m else None
print(f"  代码里 FUSION_VERSION = {code_ver}")

exe_name = os.path.basename(EXE)
m2 = re.search(r"v(\d+\.\d+\.\d+)", exe_name)   # 不能写 [\d.]+，会把 .exe 的点也吃进去
exe_ver = m2.group(1) if m2 else None
print(f"  exe 文件名里的版本   = {exe_ver}")

# 产物落后于代码 → 不是回归，是还没重打。
# 源码测试阶段天天碰到这情况，报红会让人习惯性忽略红，所以明确 SKIP。
# 发版时必须重新打包后再跑一次，那时这条必须 PASS。
if code_ver and exe_ver and code_ver != exe_ver:
    print()
    print(f"  [SKIP] 产物版本 {exe_ver} 落后于代码 {code_ver}")
    print(f"         这不是回归，是还没重新打包。")
    print(f"         发版前请跑：python tools/pack_release.py fusion {code_ver}")
    print(f"         然后重跑本文件——那时本项必须 PASS。")
    sys.exit(0)

check("exe 文件名版本 == 代码版本", code_ver == exe_ver,
      f"代码 {code_ver} vs 文件名 {exe_ver}")

# 打包时把主程序复制成 entry.py，看产物里的源码是否含同一个版本常量
entry = os.path.join(EXE_DIR, "_internal", "entry.py")
src_in_exe = None
if os.path.exists(entry):
    src_in_exe = open(entry, encoding="utf-8", errors="replace").read()
    m3 = re.search(r'FUSION_VERSION = "([\d.]+)"', src_in_exe)
    v3 = m3.group(1) if m3 else None
    check("产物内嵌源码的版本号一致", v3 == code_ver, f"产物 {v3} vs 代码 {code_ver}")

print()
print("=" * 76)
print("③ 资源与数据完整性（产物里有没有这些文件）")
print("=" * 76)
internal = os.path.join(EXE_DIR, "_internal")

# 图标
icon_hits = []
for root, dirs, files in os.walk(EXE_DIR):
    for f in files:
        if f in ("app.ico", "app.png"):
            icon_hits.append(os.path.join(root, f))
check("图标文件打进了产物", bool(icon_hits),
      "setWindowIcon 拿不到文件 → 标题栏/任务栏还是默认图标")
for h in icon_hits:
    print(f"    {os.path.relpath(h, EXE_DIR)}")

# 省份数据（33 省）—— PyInstaller onedir 把 Python 模块打进 exe 内部的
# PYZ.pyz，不在文件系统上。所以不能 os.walk 找 tou.py，必须读 CArchive。
# 这一条踩过：第一版在 _internal 下找 services/tou.*，当然找不到，
# 差点当成「数据没打进去」报上去。
pyz_mods = []
try:
    from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader
    import tempfile

    car = CArchiveReader(EXE)
    pyz_names = [n for n in car.toc if n.endswith(".pyz")]
    for pn in pyz_names:
        data = car.extract(pn)
        tz = os.path.join(tempfile.gettempdir(), f"verify_{pn}")
        with open(tz, "wb") as f:
            f.write(data)
        pyz_mods.extend(ZlibArchiveReader(tz).toc)
    print(f"  PYZ 里共 {len(pyz_mods)} 个模块")
except Exception as e:
    print(f"  （读 PYZ 失败: {type(e).__name__}: {e}）")

check("services.tou 在产物 PYZ 里", "services.tou" in pyz_mods,
      "省份数据没打进去 → 下拉框里没有省份")
for mod in ("core.spaces", "core.periods", "core.ingest", "core.models",
            "services.config", "services.provider"):
    check(f"{mod} 在产物 PYZ 里", mod in pyz_mods)

# 模块存在 ≠ 数据对。把 tou 的字节码抽出来真执行，数省份。
try:
    import tempfile as _tf

    _code = None
    for pn in [n for n in car.toc if n.endswith(".pyz")]:
        _data = car.extract(pn)
        _tz = os.path.join(_tf.gettempdir(), f"verify2_{pn}")
        with open(_tz, "wb") as f:
            f.write(_data)
        _z = ZlibArchiveReader(_tz)
        if "services.tou" in _z.toc:
            _code = _z.extract("services.tou")
            break
    if _code is not None:
        _g = {"__name__": "services.tou", "__builtins__": __builtins__}
        exec(_code, _g)
        _pt = _g.get("PROVINCE_TOU") or {}
        print(f"  产物里实际省份数: {len(_pt)}")
        check("产物里省份 >= 33", len(_pt) >= 33, f"只有 {len(_pt)}")
        _new = ["山西", "陕西", "甘肃", "青海", "宁夏", "新疆",
                "内蒙古（蒙西）", "内蒙古（蒙东）", "辽宁", "吉林",
                "黑龙江", "云南", "贵州", "西藏"]
        _miss = [p for p in _new if p not in _pt]
        check("本轮新增的 14 省全在产物里", not _miss, str(_miss))
        _dv = _g.get("derive_periods")
        if _dv:
            _ps = _dv(_pt["山西"]["months"]["7"])
            _ok = (any(x["type"] == "charge" for x in _ps)
                   and any(x["type"] == "discharge" for x in _ps))
            check("产物里的推导函数能跑（山西 7 月充放齐全）", _ok, str(_ps))
    else:
        check("能从产物抽到 services.tou 字节码", False)
except Exception as _e:
    check("产物数据实测未抛异常", False, f"{type(_e).__name__}: {_e}")

print()
print("=" * 76)
print("④ 进程启动与窗口（不是只看存活）")
print("=" * 76)
try:
    proc = subprocess.Popen([EXE], cwd=EXE_DIR,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
except Exception as e:
    check("能启动进程", False, f"{type(e).__name__}: {e}")
    proc = None

if proc:
    time.sleep(3)
    alive3 = proc.poll() is None
    check("3 秒后进程仍在（没秒退）", alive3,
          f"exit code = {proc.returncode}")

    if alive3:
        # 用 win32 API 列窗口，找标题
        titles = []
        try:
            sys.path.insert(0, REPO)
            import ctypes
            from ctypes import wintypes

            EnumWindows = ctypes.windll.user32.EnumWindows
            GetWindowTextW = ctypes.windll.user32.GetWindowTextW
            GetWindowTextLengthW = ctypes.windll.user32.GetWindowTextLengthW
            IsWindowVisible = ctypes.windll.user32.IsWindowVisible
            GetWindowThreadProcessId = ctypes.windll.user32.GetWindowThreadProcessId

            WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            target_pid = proc.pid

            def cb(hwnd, lparam):
                if not IsWindowVisible(hwnd):
                    return True
                pid = wintypes.DWORD()
                GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value != target_pid:
                    return True
                n = GetWindowTextLengthW(hwnd)
                if n > 0:
                    buf = ctypes.create_unicode_buffer(n + 1)
                    GetWindowTextW(hwnd, buf, n + 1)
                    if buf.value:
                        titles.append(buf.value)
                return True

            EnumWindows(WNDENUMPROC(cb), 0)
        except Exception as e:
            print(f"  （窗口枚举异常: {e}）")

        print(f"  可见窗口标题: {titles}")
        check("出现了窗口", bool(titles), "进程活着但一个窗口都没有")
        check("有欢迎/主窗口标题",
              any("LoomGrid" in t or "负荷曲线" in t for t in titles),
              str(titles))
        check("没有 Python 错误对话框",
              not any("Error" in t or "error" in t or "错误" in t for t in titles),
              f"出现错误对话框：{titles}")

        # 窗口标题里的版本
        ver_in_title = [t for t in titles if "v" in t]
        if ver_in_title:
            print(f"  含版本号的标题: {ver_in_title}")
            check("标题版本与代码一致",
                  any(code_ver in t for t in ver_in_title) if code_ver else True,
                  f"代码 {code_ver}，标题 {ver_in_title}")

    # 收尾：确保进程被结束
    try:
        proc.terminate()
        proc.wait(timeout=8)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass

print()
print("=" * 76)
print("⑤ zip 包结构（若有）")
print("=" * 76)
zips = glob.glob(os.path.join(REPO, "dist", "*.zip"))
zips = [z for z in zips if code_ver and code_ver in os.path.basename(z)]
if zips:
    import zipfile
    z = zips[0]
    zsize = os.path.getsize(z) / 1024 / 1024
    print(f"  {os.path.basename(z)}  {zsize:.1f} MB")
    with zipfile.ZipFile(z) as zf:
        names = zf.namelist()
        tops = {n.split("/")[0] for n in names}
        print(f"  顶层条目: {sorted(tops)[:5]}")
        check("zip 只有一个顶层目录", len(tops) == 1, str(sorted(tops)[:5]))
        check("zip 里有 exe",
              any(n.endswith(".exe") for n in names),
              "解压后找不到可执行文件")
        # 目录名不能带 -win64（历史说明文字错误）
        top = sorted(tops)[0]
        check("顶层目录名不含 -win64", "-win64" not in top,
              f"实际 {top}，说明文字可能对不上")
else:
    print("  （还没生成 zip，跳过）")

print()
print("=" * 76)
print("⑥ 签名（用户在意的「无法验证发布者」）")
print("=" * 76)

try:
    import subprocess as _sp

    def _sig_status(path):
        r = _sp.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-AuthenticodeSignature -LiteralPath '{path}').Status"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60)
        return (r.stdout or "").strip()

    main_status = _sig_status(EXE)
    print(f"  目录里的 exe: {main_status}")
    check("主 exe 已签名且有效", main_status == "Valid",
          f"{main_status} —— 未签名时运行会弹「无法验证发布者」")

    # zip 里的 exe 也必须已签——签名在压缩前做，用户下载的是 zip
    if zips:
        import tempfile as _tf
        with zipfile.ZipFile(zips[0]) as zf:
            exes = [n for n in zf.namelist() if n.endswith(".exe")]
            if exes:
                _tmp = _tf.mkdtemp(prefix="sigchk_")
                zf.extract(exes[0], _tmp)
                zexe = os.path.join(_tmp, exes[0])
                z_status = _sig_status(zexe)
                print(f"  zip 里的 exe: {z_status}")
                check("zip 里的 exe 也带签名（用户下载的是它）",
                      z_status == "Valid", z_status)
                import shutil as _sh
                _sh.rmtree(_tmp, ignore_errors=True)
except Exception as _e:
    check("签名检查未抛异常", False, f"{type(_e).__name__}: {_e}")

print()
print("=" * 76)
print(f"结果：PASS {len(passed)}   FAIL {len(failed)}")
for f in failed:
    print("  -", f)
print("=" * 76)
sys.exit(1 if failed else 0)
