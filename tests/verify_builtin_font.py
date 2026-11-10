# -*- coding: utf-8 -*-
"""v2.3.22 自带字体（Noto Sans SC）的回归测试。

为什么单独一份：字体是「换台机器才知道」的那类依赖，
本机有系统字体时永远测不出问题。这里用两种方式逼近真实：
  · 清空 fontManager.ttflist 模拟「系统没装中文字体」
  · 用 fontTools 查字形覆盖，不靠「图看着像不像」
"""
import io as _io
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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


FONT_DIR = os.path.join(REPO, "assets", "fonts")
FONT = os.path.join(FONT_DIR, "NotoSansSC-Regular.otf")
LIC = os.path.join(FONT_DIR, "LICENSE-OFL.txt")

print("=" * 76)
print("① 字体文件与许可证")
print("=" * 76)
check("自带字体文件存在", os.path.exists(FONT), FONT)
if os.path.exists(FONT):
    sz = os.path.getsize(FONT)
    print(f"  {os.path.basename(FONT)}  {sz/1024/1024:.2f} MB")
    check("体积合理（字形完整，>5MB）", sz > 5_000_000, f"{sz/1024/1024:.1f} MB")
check("OFL 许可证已附带（分发合规要求）", os.path.exists(LIC),
      "OFL 1.1 要求随字体分发许可证文本")
if os.path.exists(LIC):
    txt = open(LIC, encoding="utf-8", errors="replace").read()
    check("许可证文本是 OFL", "SIL OPEN FONT LICENSE" in txt.upper()
          or "OFL" in txt.upper(), txt[:60])

print()
print("=" * 76)
print("② 字体选择优先级：自带优先于系统")
print("=" * 76)
from plotting import fonts as F

check("_font_dirs 覆盖 assets/fonts",
      any("assets" in d and "fonts" in d for d in F._font_dirs()),
      str(F._font_dirs()))

F._FONT_NAME = None
F.CJK_FONT_MISSING = False
nm = F._find_cjk_font()
print(f"  _find_cjk_font() = {nm!r}")
check("选中的是自带 Noto Sans SC（不是系统 SimHei）",
      nm == "Noto Sans SC", repr(nm))
check("CJK_FONT_MISSING 为 False", F.CJK_FONT_MISSING is False)

print()
print("=" * 76)
print("③ 模拟「系统一个中文字体都没有」")
print("=" * 76)
from matplotlib.font_manager import fontManager

orig = fontManager.ttflist
try:
    fontManager.ttflist = []          # 清空，只剩 addfont 能往里加
    F._FONT_NAME = None
    F.CJK_FONT_MISSING = False
    nm2 = F._find_cjk_font()
    print(f"  空 ttflist 下 = {nm2!r}")
    check("只靠自带字体也能出中文", nm2 == "Noto Sans SC", repr(nm2))
    check("这种情况下不置 CJK_FONT_MISSING", F.CJK_FONT_MISSING is False)
finally:
    fontManager.ttflist = orig
    F._FONT_NAME = None
    F.CJK_FONT_MISSING = False

print()
print("=" * 76)
print("④ 字形覆盖（不靠「图看着像不像」）")
print("=" * 76)
if os.path.exists(FONT):
    try:
        from fontTools.ttLib import TTFont
        tt = TTFont(FONT)
        cmap = tt.getBestCmap()
        # 本程序实际会用到的字
        chars = ("负荷曲线充电空间放电需求功率峰谷时刻资产月度"
                 "容量满足天数保守宽松示数正向有功电度尖峰深谷平段"
                 "额定效率配置预览导出扫描映射列名表头行倍率容量线"
                 "异常值标注图例标题文件名省份策略组全选不选反选季度")
        miss = [c for c in chars if ord(c) not in cmap]
        print(f"  测试 {len(chars)} 字，缺 {len(miss)} 个")
        if miss:
            print(f"    缺: {''.join(miss)}")
        check("常用汉字与业务用字全覆盖", not miss, f"{len(miss)} 字缺字形")

        # 数字与符号也要有（功率、负数）
        sym = "0123456789.-%()（）：kWkWh"
        miss2 = [c for c in sym if ord(c) not in cmap and c.strip()]
        check("数字与常用符号覆盖", not miss2, "".join(miss2))
    except ImportError:
        print("  [SKIP] 无 fontTools，跳过字形检查")
    except Exception as e:
        check("字形检查未抛异常", False, f"{type(e).__name__}: {e}")

print()
print("=" * 76)
print("⑤ 真画一张中文图")
print("=" * 76)
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    F._FONT_NAME = None
    used = F._find_cjk_font()
    plt.rcParams["font.sans-serif"] = [used]
    plt.rcParams["axes.unicode_minus"] = False

    fig, ax = plt.subplots(figsize=(5, 2.5))
    ax.plot([0, 1, 2, 3], [100, 300, 200, 400])
    ax.set_title("负荷曲线 · 充电空间 / 放电空间")
    ax.set_xlabel("时刻")
    ax.set_ylabel("功率 (kW)")
    ax.legend(["赵钢1#线"], loc="upper left")
    buf = _io.BytesIO()
    fig.savefig(buf, format="png", dpi=80)
    plt.close(fig)
    check("中文图渲染无异常", buf.tell() > 5000, f"{buf.tell()} B")
    print(f"  产物 {buf.tell()} B")
except Exception as e:
    import traceback
    traceback.print_exc(file=sys.stdout)
    check("中文图渲染", False, f"{type(e).__name__}: {e}")

print()
print("=" * 76)
print("⑥ 打包流程会把字体带进产物")
print("=" * 76)
pack = os.path.join(REPO, "tools", "pack_release.py")
psrc = open(pack, encoding="utf-8").read()
check("pack_release 有字体复制步骤", "自带字体" in psrc
      or "assets\", \"fonts" in psrc or "assets/fonts" in psrc.replace("\\\\", "/"))
check("字体复制到产物根的 fonts/（不在 _internal 下）",
      'os.path.join(out_dir, "fonts")' in psrc,
      "放错位置 _font_dirs 会找不到")

print()
print("=" * 76)
print(f"结果：PASS {len(passed)}   FAIL {len(failed)}")
for f in failed:
    print("  -", f)
print("=" * 76)
sys.exit(1 if failed else 0)
