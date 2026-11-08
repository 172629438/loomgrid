# -*- coding: utf-8 -*-
"""v2.3.18 无障碍与主题修复的回归测试。

覆盖四件事：
  ① 月份全选 / 全不选 / 反选
  ② 全局应用图标（窗口 + 任务栏），资源路径能在源码树与打包产物两种布局下找到
  ③ UI 主题：默认浅色、不跟随系统、与「图表暗色背景」彻底分离、深色可读性
  ④ 拖拽：主窗口真的接收拖放、浮层不再打断拖放会话、目录递归限深

做法说明：GUI 控件的行为在无显示环境里跑不了，这里用两类判据：
  · 纯逻辑可测的（资源路径、月份选择算法、调色板构造）——真跑
  · 只能靠源码契约的（控件是否 setAcceptDrops、浮层是否还带 WindowStaysOnTopHint）
    ——AST / 源码切片断言，并在注释里写明"这条验不了真窗口行为"
"""
import ast
import io
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(REPO, "apps", "负荷曲线批量生成工具 Pro 融合版 (v2.3).py")

sys.stdout.reconfigure(encoding="utf-8")

passed = 0
failed = []


def check(name, cond, detail=""):
    global passed
    if cond:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed.append(name)
        print(f"  [FAIL] {name}  {detail}")


src = io.open(MAIN, encoding="utf-8").read()
tree = ast.parse(src)
lines = src.splitlines()


def cls_node(name):
    for n in ast.walk(tree):
        if isinstance(n, ast.ClassDef) and n.name == name:
            return n
    return None


def func_in(clazz, fname):
    for n in clazz.body:
        if isinstance(n, ast.FunctionDef) and n.name == fname:
            return n
    return None


def body_src(node, fname):
    """取方法源码切片（用行号，避开 ast.unparse 的版本差异）。"""
    return "\n".join(lines[node.lineno - 1:node.end_lineno])


def body_code(node, fname):
    """只取方法里的**代码行**，剔掉注释与 docstring。

    必要性：好几条断言要查“某个坏写法是否还在”，而修复注释本身会引用
    坏写法（例：“原来这里直接 os.walk 全递归”）。拿整段源码去匹配
    会把注释判成代码，反复误红。
    """
    out = []
    for ln in lines[node.lineno - 1:node.end_lineno]:
        stripped = ln.strip()
        if stripped.startswith("#"):
            continue
        # 去掉行尾注释（简单处理：不在引号内的 #）
        code = re.sub(r"(?<!['\"])#(?![^{]*\}).*$", "", ln)
        out.append(code)
    return "\n".join(out)


def code_lines():
    """全文件的**真正代码行**：剔除注释与字符串字面量（含 docstring）。

    为什么连字符串也要剔：修复注释/docstring 里会引用旧写法（例：
    “原来切回浅色用的是 QApplication.style().standardPalette()”），
    拿全文去匹配会把说明文字当成代码，反复误红。
    用 tokenize 精确判定哪些字符属于 STRING token。
    """
    import tokenize
    skip = set()
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type in (tokenize.STRING, tokenize.COMMENT):
            for ln in range(tok.start[0], tok.end[0] + 1):
                skip.add(ln)
    return [l for i, l in enumerate(lines, 1) if i not in skip]


print("=" * 74)
print("① 月份全选 / 全不选 / 反选")
print("=" * 74)

gped = cls_node("GroupPeriodEditDialog")
check("GroupPeriodEditDialog 存在", gped is not None)

if gped:
    for fn in ("_set_all_months", "_invert_months"):
        check(f"{fn} 已定义", func_in(gped, fn) is not None)

    init_src = body_src(func_in(gped, "__init__"), "__init__") if func_in(gped, "__init__") else ""
    for label in ("全选", "全不选", "反选"):
        check(f"按钮「{label}」存在", f'"{label}"' in init_src, "按钮文案缺失")

    # 三个按钮必须都接上槽，否则点了没反应
    check("三个按钮都接了 clicked", init_src.count("clicked.connect") >= 3,
          f"clicked.connect 出现 {init_src.count('clicked.connect')} 次")

    sam = func_in(gped, "_set_all_months")
    if sam:
        s = body_src(sam, "_set_all_months")
        check("_set_all_months 遍历所有 month_checks",
              "month_checks.values()" in s and "setChecked(checked)" in s, s[:120])

    inv = func_in(gped, "_invert_months")
    if inv:
        s = body_src(inv, "_invert_months")
        check("_invert_months 取反而不是设固定值",
              "not cb.isChecked()" in s, s[:120])

    # 季度快选
    check("_pick_quarter 已定义", func_in(gped, "_pick_quarter") is not None)
    pq = func_in(gped, "_pick_quarter")
    if pq:
        s = body_src(pq, "_pick_quarter")
        check("_pick_quarter 是「只选该季度」而非「叠加」",
              "setChecked(int(k) in months)" in s, s[:150])
    check("四个季度按钮已创建（动态生成）",
          '_quarter_btns' in init_src and 'f"Q{q}"' in init_src
          and '(1, (1, 2, 3))' in init_src,
          "季度快选缺失")
    check("季度点击会只选该季度",
          "_pick_quarter" in init_src)

print()
print("=" * 74)
print("② 全局应用图标")
print("=" * 74)

check("_asset_path 已定义", "def _asset_path(" in src)
check("_app_icon 已定义", "def _app_icon(" in src)
check("main() 里设置了 app.setWindowIcon",
      re.search(r"app\.setWindowIcon\(", src) is not None,
      "没设应用级图标 → 任务栏还是 Qt 默认图标")
check("MainWindow 里设置了 setWindowIcon",
      "self.setWindowIcon(_ic)" in src,
      "没设窗口级图标 → 标题栏还是默认")
check("ProfessionalWindow 也设了图标",
      src.count("setWindowIcon(_ic)") >= 2,
      "专业窗口单独开窗时任务栏图标会掉回默认")

# _asset_path 必须在源码树布局（apps/../assets）下能找到真实文件
icon_src = os.path.join(REPO, "assets", "app.ico")
check("assets/app.ico 存在于仓库", os.path.exists(icon_src), icon_src)

# 打包布局：PyInstaller onedir 会把 --add-data 的内容放 _internal/assets
check("打包脚本带上了 assets 目录",
      "--add-data" in io.open(os.path.join(REPO, "tools", "pack_release.py"),
                              encoding="utf-8").read(),
      "只给 exe 加了 --icon，运行时找不到图标文件")

print()
print("=" * 74)
print("③ UI 主题")
print("=" * 74)

check("build_light_palette 已定义", "def build_light_palette(" in src)
check("build_dark_palette 已定义", "def build_dark_palette(" in src)
check("浅色不再用 standardPalette()（那是跟随系统的）",
      not any(
          re.search(r"QApplication\.style\(\)\.standardPalette\(\)|setPalette\([^)]*standardPalette", l)
          for l in code_lines()
      ))

# 默认值必须是浅色
m = re.search(r"app_set\('ui_dark_theme',\s*(True|False)\)", src)
check("UI 主题默认值是 False（浅色）", m is not None and m.group(1) == "False",
      m.group(0) if m else "没找到默认值")

# 两套「暗色」必须分开
check("UI 主题用独立配置键 ui_dark_theme",
      "ui_dark_theme" in src and src.count("ui_dark_theme") >= 2)
check("图表背景仍然用 dark_theme（没被误合并）",
      re.search(r"dark_theme=self\._dark_theme", src) is not None
      or "dark_theme=dark_theme" in src)

# 污染源必须消失：图表复选框不能写回 UI 主题
check("图表背景不再写回 self._dark_theme（污染源已移除）",
      not any(re.search(r"self\._dark_theme\s*=\s*dark_theme", l) for l in code_lines()))

# 浅色也必须在启动时显式应用
mw = cls_node("MainWindow")
if mw:
    init = func_in(mw, "__init__")
    init_src = body_src(init, "__init__")
    check("启动时有 else: _apply_light_theme（系统深色时不跟着暗）",
          "_apply_light_theme()" in init_src, "只有 if 分支 → 浅色时调色板停在系统默认")
    check("_repolish_all 存在（切主题后强制重解析样式）",
          func_in(mw, "_repolish_all") is not None,
          "只换 QPalette 不 repolish，palette() 样式不刷新")

# 深色可读性：禁用态必须显式设
darks = src[src.index("def build_dark_palette"):src.index("def apply_global_stylesheet")]
for role in ("Disabled, QPalette.WindowText", "Disabled, QPalette.ButtonText"):
    check(f"深色调色板显式设了 {role.split(', ')[-1]}",
          role in darks, "禁用文字在深灰底上看不见")

# 浅色底 + 深色前景：凡是设了浅色背景的地方不能留白字
risky = []
for j, l in enumerate(lines):
    if "setStyleSheet" not in l:
        continue
    if not re.search(r"background-color:\s*(lightgray|#FFF9C4|#f0f0f0|#90EE90|#FFB6C1)", l, re.I):
        continue
    # 同一行或紧邻的续行里必须有 color: 显式前景
    ctx = "\n".join(lines[j:j + 3])
    if not re.search(r"color:\s*#", ctx):
        risky.append(j + 1)
check("浅色背景处都显式给了深色前景", not risky,
      f"这些行深色主题下会白字配浅底：{risky}")

# 次要文字不能再是发闷的 gray
check("次要文字色不再是 gray",
      'color: gray;' not in src,
      "gray 在深色底上发闷，用户反馈“显示不明显”")

# ── 调色板实际取值与对比度（真跑，不是源码切片）─────────────────
# 这是本文件里唯一能证明「浅色真的浅、深色字真的看得见」的机器可核方式。
try:
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QPalette

    _app = QApplication.instance() or QApplication([])

    def _rel_lum(c):
        def f(v):
            v = v / 255.0
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        return (0.2126 * f(c.red()) + 0.7152 * f(c.green())
                + 0.0722 * f(c.blue()))

    def _contrast(a, b):
        la, lb = _rel_lum(a), _rel_lum(b)
        hi, lo = max(la, lb), min(la, lb)
        return (hi + 0.05) / (lo + 0.05)

    def run_palette_builders():
        import importlib.util
        s = importlib.util.spec_from_file_location("fusion_main_for_test", MAIN)
        m = importlib.util.module_from_spec(s)
        s.loader.exec_module(m)
        return m.build_light_palette(), m.build_dark_palette()

    try:
        LP, DP = run_palette_builders()
    except Exception as e:
        LP = DP = None
        check("能加载主程序并构造两套调色板", False, f"{type(e).__name__}: {e}")

    if LP is not None:
        lw = LP.color(QPalette.Window)
        lt = LP.color(QPalette.WindowText)
        check("浅色 Window 亮度 > 0.7（确认是浅底）", _rel_lum(lw) > 0.7,
              f"亮度 {_rel_lum(lw):.3f}")
        check("浅色 WindowText 对比度 >= 7:1",
              _contrast(lt, lw) >= 7.0, f"{_contrast(lt, lw):.2f}:1")

        dw = DP.color(QPalette.Window)
        dt = DP.color(QPalette.WindowText)
        check("深色 Window 亮度 < 0.1（确认是深底）", _rel_lum(dw) < 0.1,
              f"亮度 {_rel_lum(dw):.3f}")
        check("深色 WindowText 对比度 >= 7:1",
              _contrast(dt, dw) >= 7.0, f"{_contrast(dt, dw):.2f}:1")

        ddis = DP.color(QPalette.Disabled, QPalette.WindowText)
        check("深色禁用文字对比度 >= 3:1（原来没设）",
              _contrast(ddis, dw) >= 3.0, f"{_contrast(ddis, dw):.2f}:1")

        dmid = DP.color(QPalette.Mid)
        check("深色 Mid / Window >= 1.5:1（边框看得见）",
              _contrast(dmid, dw) >= 1.5, f"{_contrast(dmid, dw):.2f}:1")

        check("两套调色板真的不同（防止深色被写成浅色）",
              lw != dw and lt != dt, f"{lw.name()}/{dw.name()}")
except ImportError:
    print("  [SKIP] 无 PySide6，跳过调色板实测")

print()
print("=" * 74)
print("④ 拖拽")
print("=" * 74)

if mw:
    init = func_in(mw, "__init__")
    init_src = body_src(init, "__init__")
    check("MainWindow.setAcceptDrops(True)",
          "self.setAcceptDrops(True)" in init_src,
          "QMainWindow 默认拒收 → 拖到列表以外全无反应")
    for ev in ("dragEnterEvent", "dragLeaveEvent", "dropEvent"):
        check(f"MainWindow.{ev} 已实现", func_in(mw, ev) is not None)
    drop = func_in(mw, "dropEvent")
    if drop:
        ds = body_code(drop, "dropEvent")
        check("主窗口 drop 目录时用限深 _walk_dir",
              "_walk_dir" in ds and "os.walk" not in ds,
              "直接 os.walk 全递归，拖盘符根会假死")

# DropOverlay 不能再是顶层窗
ov = cls_node("DropOverlay")
if ov:
    oinit = func_in(ov, "__init__")
    osrc = body_src(oinit, "__init__")
    check("DropOverlay 不再用 WindowStaysOnTopHint",
          "WindowStaysOnTopHint" not in osrc,
          "顶层窗在拖拽途中弹出会打断 Windows 拖放会话")
    check("DropOverlay 不再自己收拖放",
          "self.setAcceptDrops(True)" not in osrc,
          "它会把本该给主窗口的 drop 抢走")
    check("DropOverlay 鼠标事件穿透",
          "WA_TransparentForMouseEvents" in osrc)
    check("DropOverlay 不再定义自己的 dropEvent 处理逻辑",
          func_in(ov, "dropEvent") is None
          or "pass" in body_src(func_in(ov, "dropEvent"), "dropEvent"),
          "重写 dropEvent 会截胡")

# 所有目录递归入口都必须走限深版
on_drop = None
if mw:
    on_drop = func_in(mw, "_on_files_dropped")
if on_drop:
    s = body_code(on_drop, "_on_files_dropped")
    check("_on_files_dropped 目录展开用 _walk_dir",
          "_walk_dir" in s and "os.walk" not in s,
          "这是拖拽的最终入口，它不限深等于前面白改")

# FileListWidget 的空态 label 仍然穿透
flw = cls_node("FileListWidget")
if flw:
    finit = body_src(func_in(flw, "__init__"), "__init__")
    check("空态 label 保持鼠标事件穿透",
          "WA_TransparentForMouseEvents" in finit,
          "标签铺满列表会吞掉拖拽事件")

# 子控件截胡：这一层是“鼠标落在哪就决定能不能拖”的直接原因
check("_disable_child_drops 已定义",
      any(f.name == "_disable_child_drops" for f in mw.body) if mw else False)
if mw:
    dcd_init = body_src(func_in(mw, "__init__"), "__init__")
    check("__init__ 里调了 _disable_child_drops",
          "_disable_child_drops()" in dcd_init)
    dcd = func_in(mw, "_disable_child_drops")
    if dcd:
        s = body_src(dcd, "_disable_child_drops")
        check("_disable_child_drops 遍历 findChildren 并关掉 acceptDrops",
              "findChildren(QWidget)" in s and "setAcceptDrops(False)" in s, s[:150])
        check("_disable_child_drops 放过 FileListWidget",
              "isinstance(w, FileListWidget)" in s,
              "连列表自己的 drop 也关了 → 拖进去彻底没反应")

# 真起一个窗口数控件（offscreen，不弹窗）
try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication as _QA, QWidget as _QW
    _qa = _QA.instance() or _QA([])
    _qa.setStyle("Fusion")
    import importlib.util as _ilu
    _s = _ilu.spec_from_file_location("fm_drop_probe", MAIN)
    _m = _ilu.module_from_spec(_s)
    _s.loader.exec_module(_m)
    _win = _m.MainWindow()

    check("实测：MainWindow.acceptDrops() 为 True", _win.acceptDrops() is True)
    _fl = _win.findChild(_m.FileListWidget)
    check("实测：FileListWidget 仍接收拖放",
          _fl is not None and _fl.acceptDrops() is True)

    _bad = [w for w in _win.findChildren(_QW)
            if not isinstance(w, _m.FileListWidget) and w.acceptDrops()]
    check(f"实测：无子控件再截获拖放（发现 {len(_bad)} 个）", not _bad,
          f"{[type(w).__name__ for w in _bad][:8]}")

    _log = getattr(_win, "log_area", None)
    if _log is not None:
        check("实测：日志区不再吃拖放（它占主窗口下半部分）",
              _log.acceptDrops() is False)
    _win.close()

    # 系统级：管理员运行时 UIPI 会阻止拖放，程序改不了但得提示
    check("有 _is_elevated 检测", hasattr(_m, "_is_elevated"))
    if hasattr(_m, "_is_elevated"):
        check("_is_elevated 返回布尔值",
              isinstance(_m._is_elevated(), bool),
              repr(_m._is_elevated()))
    check("启动时会提示管理员权限对拖放的影响",
          "UIPI" in src and "_is_elevated" in src)
except ImportError:
    print("  [SKIP] 无 PySide6，跳过真窗口控件实测")
except Exception as _e:
    check("真窗口控件实测未抛异常", False, f"{type(_e).__name__}: {_e}")

print()
print("=" * 74)
print("⑤ 快捷键（无障碍）")
print("=" * 74)

shortcuts = dict(re.findall(r'setShortcut\("([^"]+)"\)', src) and [])
found = re.findall(r'\.setShortcut\("([^"]+)"\)', src)
print(f"  已设快捷键: {found}")
for key, meaning in (("Ctrl+O", "添加文件"), ("Ctrl+Q", "退出"),
                     ("F5", "开始处理"), ("Ctrl+P", "专业分析"),
                     ("Ctrl+T", "主题切换")):
    check(f"{meaning} 有快捷键 {key}", key in found,
          "高频操作只能点鼠标，不符合无障碍要求")
print()
print("=" * 74)
print(f"结果：PASS {passed}   FAIL {len(failed)}")
if failed:
    print("失败项：")
    for f in failed:
        print("  -", f)
print("=" * 74)
sys.exit(1 if failed else 0)
