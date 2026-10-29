# -*- coding: utf-8 -*-
"""LoomGrid 界面截图流水线。

原则：图必须由真实窗口渲染，不 P 图、不摆拍空界面。
做法：造真实电表数据 → 注入 → 等渲染稳定 → 直接抓控件。

## 为什么需要它

改版之后 README 里的图就过期了，过期图比没有图更误导。这个脚本让
「刷新全部截图」变成一个命令的事，图跟着代码走。

## 用法

    python tools/shot_lab.py --list          # 看有哪些 target
    python tools/shot_lab.py --all           # 全部重截
    python tools/shot_lab.py ultra-load-sim  # 只截一张

产物写到 `assets/screenshots/`。

## 两个坑（踩过，记下来）

**一、字体必须手动装。** `offscreen` / `minimal` 平台插件没有字体后端，
`QFontDatabase.families()` 返回 0 个，所有文字渲染成豆腐块。必须在建窗口
之前 `addApplicationFont` 把 `C:\\Windows\\Fonts` 里的中文字体装进去。

**二、主题受配置影响。** 主窗口读 `column_mapping_config.json` 的
`app_settings.dark_theme`，对话框不读。不统一的话会出现「深色底 + 浅色控件」
的混搭图。用 `--theme light|dark` 显式指定，脚本会临时改配置并在结束时还原。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
import time
import traceback

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "assets", "screenshots")
TMP = os.path.join(os.environ.get("TEMP", "."), "loomgrid_shots")
CFG = os.path.join(REPO, "column_mapping_config.json")
CFG_BAK = CFG + ".shotbak"

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_OPENGL", "software")
os.environ.setdefault("QT_LOGGING_RULES", "*.debug=false")

sys.path.insert(0, REPO)

import numpy as np  # noqa: E402

ULTRA = os.path.join(REPO, "负荷曲线批量生成Ultra.py")
FUSION = os.path.join(REPO, "负荷曲线批量生成工具 Pro 融合版 (v2.3).py")

WIN_FONTS = r"C:\Windows\Fonts"
_FONT_FILES = ["msyh.ttc", "msyhbd.ttc", "simhei.ttf", "simsun.ttc",
               "simkai.ttf", "Deng.ttf", "msjhl.ttc", "seguiemj.ttf"]
_fonts_loaded = False


# ---------- 环境准备 ----------

def load_fonts(app=None) -> None:
    """把系统字体装进 Qt —— offscreen 平台自己没有字体后端。"""
    global _fonts_loaded
    if _fonts_loaded:
        return
    from PySide6.QtGui import QFontDatabase
    for fn in _FONT_FILES:
        p = os.path.join(WIN_FONTS, fn)
        if os.path.exists(p):
            fid = QFontDatabase.addApplicationFont(p)
            if fid >= 0:
                print(f"    font +{fn}")
    _fonts_loaded = True
    n = len(QFontDatabase.families())
    print(f"    字体族 {n} 个")
    if n == 0:
        print("    [WARN] 字体为空，中文会变豆腐块")


def setup_matplotlib_font() -> None:
    """matplotlib 自己维护 font_manager，Qt 的注入管不到它。"""
    try:
        import matplotlib
        matplotlib.rcParams["font.sans-serif"] = [
            "Microsoft YaHei", "SimHei", "DengXian", "SimSun", "Arial"]
        matplotlib.rcParams["axes.unicode_minus"] = False
    except Exception as e:
        print(f"    matplotlib 字体失败: {type(e).__name__}: {e}")


def preferred_family() -> str:
    from PySide6.QtGui import QFontDatabase
    fams = QFontDatabase.families()
    for want in ("Microsoft YaHei UI", "Microsoft YaHei", "SimHei",
                 "SimSun", "DengXian"):
        if want in fams:
            return want
    return ""


def force_font(app) -> None:
    """主程序的 apply_global_font 在 offscreen 下取不到嵌入字体，这里补设一次。"""
    from PySide6.QtGui import QFont
    fam = preferred_family()
    if not fam:
        return
    f = QFont()
    f.setFamilies([fam, "Microsoft YaHei", "SimHei", "sans-serif"])
    f.setPointSize(9)
    f.setStyleStrategy(QFont.PreferAntialias)
    app.setFont(f)


def set_theme(dark: bool) -> None:
    """临时改配置里的主题；结束后用 restore_theme() 还原。"""
    if not os.path.exists(CFG):
        return
    if not os.path.exists(CFG_BAK):
        shutil.copy2(CFG, CFG_BAK)
    d = json.load(open(CFG, encoding="utf-8"))
    d.setdefault("app_settings", {})["dark_theme"] = dark
    tmp = CFG + ".tmp"
    json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    os.replace(tmp, CFG)


def restore_theme() -> None:
    if os.path.exists(CFG_BAK):
        shutil.copy2(CFG_BAK, CFG)
        os.remove(CFG_BAK)


def qt_app():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv[:1])
    load_fonts(app)
    setup_matplotlib_font()
    return app


def load_mod(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def init_theme(mod, app) -> None:
    """按 main() 的顺序初始化主题与字体。"""
    for fn in ("force_light_theme", "apply_global_font", "apply_global_stylesheet"):
        f = getattr(mod, fn, None)
        if callable(f):
            try:
                f(app)
            except Exception as e:
                print(f"    {fn} 失败: {type(e).__name__}: {e}")
    force_font(app)


# ---------- 造数据 ----------

def make_load_df(days: int = 45, seed: int = 7):
    """商业综合体年度电表数据：工作日双峰、周末降 28%、带噪声。"""
    import pandas as pd
    rng = np.random.default_rng(seed)
    rows = []
    start = pd.Timestamp("2025-01-01")
    for d in range(days):
        date = start + pd.Timedelta(days=d)
        base = 380.0 if date.weekday() >= 5 else 520.0
        for i in range(96):
            h = i / 4.0
            if 9 <= h < 12:
                shape = 1.0
            elif 13 <= h < 18:
                shape = 0.95
            elif 19 <= h < 22:
                shape = 0.62
            elif 0 <= h < 7:
                shape = 0.22
            else:
                shape = 0.55
            kwh = base * shape * 0.25 * (1 + rng.normal(0, 0.06))
            rows.append({
                "日期": date.strftime("%Y-%m-%d"),
                "时间": f"{i // 4:02d}:{(i % 4) * 15:02d}",
                "正向有功功率": round(max(kwh * 4, 5.0), 2),
            })
    return pd.DataFrame(rows)


def write_xlsx(df, path: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_excel(path, index=False, sheet_name="电表数据")
    return path


# ---------- 抓图 ----------

def stabilize(app, widget, w: int, h: int, settle: int = 6) -> None:
    from PySide6.QtWidgets import QDialog
    widget.resize(w, h)
    if isinstance(widget, QDialog):
        widget.setModal(False)
    widget.show()
    for _ in range(settle):
        app.processEvents()


def settle_loop(app, seconds: float = 2.0) -> None:
    """跑事件循环若干秒，等异步加载/渲染落定。"""
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.05)


def grab(widget, name: str) -> str:
    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, name)
    pix = widget.grab()
    pix.save(p, "PNG")
    print(f"  saved {name}  {pix.width()}x{pix.height()}  "
          f"{os.path.getsize(p)/1024:.0f} KB")
    return p


def check_not_blank(path: str):
    """像素方差 + 灰度层级，挡掉空白图。"""
    from PySide6.QtGui import QImage
    img = QImage(path)
    if img.isNull():
        return False, "无法读取"
    w, h = img.width(), img.height()
    if w < 400 or h < 300:
        return False, f"尺寸过小 {w}x{h}"
    step = max(1, w // 120)
    vals = []
    for y in range(0, h, step * 2):
        for x in range(0, w, step):
            c = img.pixelColor(x, y)
            vals.append((c.red() + c.green() + c.blue()) / 3)
    arr = np.array(vals, dtype=float)
    var, uniq = arr.var(), len(set(np.round(arr).astype(int)))
    if var < 20:
        return False, f"方差过低 {var:.1f}（疑似空白）"
    if uniq < 6:
        return False, f"灰度层级过少 {uniq}"
    return True, f"方差 {var:.1f}，灰度层级 {uniq}"


# ---------- targets ----------

def t_main_imported(app):
    m = load_mod("u_main", ULTRA)
    init_theme(m, app)
    mw = m.MainWindow()
    stabilize(app, mw, 1440, 900, settle=6)
    xlsx = write_xlsx(make_load_df(days=45, seed=7),
                      os.path.join(TMP, "main_load.xlsx"))
    print(f"    注入 {os.path.basename(xlsx)}")
    try:
        mw._on_files_dropped([xlsx])
    except Exception as e:
        print(f"    _on_files_dropped: {type(e).__name__}: {e}")
    settle_loop(app, 2.0)
    stabilize(app, mw, 1440, 900, settle=8)
    return grab(mw, "ultra-main-imported.png")


def t_load_sim(app):
    m = load_mod("u_ls", ULTRA)
    init_theme(m, app)
    dlg = m.LoadSimDialog()
    stabilize(app, dlg, 1240, 820, settle=8)
    for fn in ("_update_preview", "_schedule_preview_update"):
        f = getattr(dlg, fn, None)
        if callable(f):
            try:
                f()
            except Exception as e:
                print(f"    {fn}: {type(e).__name__}: {e}")
    settle_loop(app, 0.8)
    return grab(dlg, "ultra-load-sim.png")


def t_park_sim(app):
    m = load_mod("u_pk", ULTRA)
    init_theme(m, app)
    dlg = m.ParkSimDialog()
    for name, ind, kw in [
        ("1#厂房生产线", "电子制造", 800),
        ("2#装配车间", "电子制造", 450),
        ("中央空调机房", "通用", 320),
        ("冷库", "食品加工", 260),
        ("办公楼照明", "商业办公", 150),
    ]:
        dlg.assets.append({"name": name, "industry": ind, "power": kw})
        dlg.list_widget.addItem(f"{name} | {ind} | {kw}kW")
    dlg._update_summary()
    stabilize(app, dlg, 1180, 520, settle=10)
    return grab(dlg, "ultra-park-sim.png")


def t_sensitivity(app):
    m = load_mod("u_sn", ULTRA)
    init_theme(m, app)
    w = m.SensitivitySliderPanel()
    for k, irr, npv in [
        ("peak_price", 28.6, 3120000),
        ("valley_price", 24.2, 2280000),
        ("system_cost", 29.4, 3380000),
        ("efficiency", 27.8, 2960000),
        ("degradation", 26.1, 2720000),
    ]:
        try:
            w.update_results(k, irr, npv)
        except Exception:
            pass
    stabilize(app, w, 560, 700, settle=10)
    return grab(w, "ultra-sensitivity.png")


def t_data_preview(app):
    m = load_mod("u_dp", ULTRA)
    init_theme(m, app)
    df = make_load_df(days=3, seed=11)
    df.insert(0, "表号", "MTR-001")
    df["反向有功功率"] = 0.0
    df["功率因数"] = 0.92
    fp = write_xlsx(df, os.path.join(TMP, "preview_load.xlsx"))
    dlg = m.DataPreviewDialog(None, df, fp, "电表数据")
    stabilize(app, dlg, 880, 780, settle=10)
    return grab(dlg, "ultra-data-preview.png")


def t_pv_match(app):
    m = load_mod("u_pv", ULTRA)
    init_theme(m, app)
    dlg = m.PVMonthMatchDialog(
        None, ["2025-01", "2025-02", "2025-03", "2025-04"],
        ["2025-03", "2025-04", "2025-05", "2025-06"])
    stabilize(app, dlg, 900, 600, settle=10)
    return grab(dlg, "ultra-pv-match.png")


def t_fusion_main(app):
    m = load_mod("f_main", FUSION)
    init_theme(m, app)
    mw = m.MainWindow()
    stabilize(app, mw, 1280, 760, settle=6)
    xlsx = write_xlsx(make_load_df(days=45, seed=7),
                      os.path.join(TMP, "fusion_load.xlsx"))
    print(f"    注入 {os.path.basename(xlsx)}")
    try:
        mw._on_files_dropped([xlsx])
    except Exception as e:
        print(f"    _on_files_dropped: {type(e).__name__}: {e}")
    settle_loop(app, 2.0)
    stabilize(app, mw, 1280, 760, settle=8)
    return grab(mw, "fusion-main-imported.png")


def t_fusion_mapping(app):
    """列映射 — 自动识别数据格式与四列对应。"""
    m = load_mod("f_map", FUSION)
    init_theme(m, app)
    df = make_load_df(days=3, seed=11)
    df.insert(0, "表号", "MTR-001")
    fp = write_xlsx(df, os.path.join(TMP, "fusion_map.xlsx"))
    cfg = m.Config()
    dlg = m.MappingDialog(None, fp, "电表数据", cfg)
    stabilize(app, dlg, 1180, 780, settle=8)
    settle_loop(app, 1.5)
    stabilize(app, dlg, 1180, 780, settle=8)
    return grab(dlg, "fusion-mapping.png")


def t_fusion_pro_mode(app):
    """专业分析模式选择。"""
    m = load_mod("f_pro", FUSION)
    init_theme(m, app)
    cfg = m.Config()
    sm = getattr(m, "SettingsModel", None)
    settings = sm() if callable(sm) else None
    dlg = m.ProfessionalModeDialog(None, cfg, settings, [], "河南", 0)
    stabilize(app, dlg, 560, 460, settle=10)
    return grab(dlg, "fusion-pro-mode.png")


# 河南 / 山东分时电价：截图用的真实策略数据
_FUSION_PLANS = [
    ("河南", "夏季（6-8月）", [6, 7, 8], [
        {"start": 10, "end": 12, "type": "discharge"},
        {"start": 14, "end": 16, "type": "discharge"},
        {"start": 19, "end": 22, "type": "discharge"},
        {"start": 23, "end": 7, "type": "charge"},
    ]),
    ("河南", "冬季（12,1,2月）", [12, 1, 2], [
        {"start": 8, "end": 11, "type": "discharge"},
        {"start": 18, "end": 21, "type": "discharge"},
        {"start": 23, "end": 7, "type": "charge"},
    ]),
    ("河南", "春秋季（3-5,9-11月）", [3, 4, 5, 9, 10, 11], [
        {"start": 10, "end": 12, "type": "discharge"},
        {"start": 15, "end": 18, "type": "discharge"},
        {"start": 22, "end": 6, "type": "charge"},
    ]),
    ("山东", "全年统一", list(range(1, 13)), [
        {"start": 8, "end": 11, "type": "discharge"},
        {"start": 17, "end": 20, "type": "discharge"},
        {"start": 22, "end": 6, "type": "charge"},
    ]),
]


def t_fusion_period_mgr(app):
    """充放时段策略管理 —— 配置里没策略就是空表，所以先写入真实季节策略。"""
    m = load_mod("f_pm", FUSION)
    init_theme(m, app)
    cfg = m.Config()
    pt = getattr(m, "PeriodType", None)
    conv = {}
    if pt is not None:
        conv = {"charge": getattr(pt, "charge", "charge"),
                "discharge": getattr(pt, "discharge", "discharge")}
    for prov, gname, months, periods in _FUSION_PLANS:
        if prov not in cfg.provinces():
            cfg.add_province(prov)
        cfg.add_group(prov, gname, months,
                      [{**p, "type": conv.get(p["type"], p["type"])}
                       for p in periods])
    cfg.set_active_province("河南")

    dlg = m.ChargeDischargeManagerDialog(None, cfg)
    dlg.refresh_tree()
    # 默认列宽会把「夏季（6-8月）」截成「夏季（6...」
    dlg.tree.setColumnWidth(0, 240)
    dlg.tree.setColumnWidth(1, 220)
    stabilize(app, dlg, 760, 420, settle=10)
    return grab(dlg, "fusion-period-manager.png")


TARGETS = {
    "ultra-main-imported": t_main_imported,
    "ultra-load-sim": t_load_sim,
    "ultra-park-sim": t_park_sim,
    "ultra-sensitivity": t_sensitivity,
    "ultra-data-preview": t_data_preview,
    "ultra-pv-match": t_pv_match,
    "fusion-main-imported": t_fusion_main,
    "fusion-mapping": t_fusion_mapping,
    "fusion-pro-mode": t_fusion_pro_mode,
    "fusion-period-manager": t_fusion_period_mgr,
}


def main() -> int:
    ap = argparse.ArgumentParser(description="LoomGrid 界面截图流水线")
    ap.add_argument("targets", nargs="*", help="要截的 target；--all 表示全部")
    ap.add_argument("--list", action="store_true", help="列出全部 target")
    ap.add_argument("--all", action="store_true", help="截全部")
    ap.add_argument("--theme", choices=["light", "dark"], default="light",
                    help="截图用哪套主题（默认 light）")
    a = ap.parse_args()

    if a.list:
        for k in TARGETS:
            print(" ", k)
        return 0

    names = list(TARGETS) if (a.all or not a.targets) else a.targets
    bad = [n for n in names if n not in TARGETS]
    if bad:
        print(f"未知 target: {bad}")
        print("可用：", ", ".join(TARGETS))
        return 2

    os.makedirs(TMP, exist_ok=True)
    set_theme(a.theme == "dark")
    print(f"主题 = {a.theme}\n")

    app = qt_app()
    results = []
    try:
        for name in names:
            print(f"=== {name} ===")
            try:
                p = TARGETS[name](app)
                ok, why = check_not_blank(p)
                results.append((name, ok, why))
                print(f"  {'通过' if ok else '不通过'} - {why}\n")
            except Exception as e:
                traceback.print_exc(limit=8)
                results.append((name, False, f"{type(e).__name__}: {e}"))
                print()
    finally:
        restore_theme()

    print("=" * 70)
    for n, ok, why in results:
        print(f"{n:<26}{'OK' if ok else 'FAIL':<8}{why}")
    n_ok = sum(1 for _, o, _ in results if o)
    print(f"\n通过 {n_ok}/{len(results)}")
    return 0 if n_ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
