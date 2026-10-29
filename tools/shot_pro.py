# -*- coding: utf-8 -*-
"""截 ProfessionalWindow（Ultra 专业分析）——此前后多次未攻下的那块界面。

思路：不走文件导入（会被字段映射对话框拦住），改用 DataProvider.register_simulated
直接注入曲线，拿到 GroupSummary 后按 MainWindow 的调用方式构造窗口。
"""
from __future__ import annotations

import importlib.util
import os
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_OPENGL"] = "software"

SRC = r"C:\Users\LENOVO\.hanako\repos\172629438__loomgrid\负荷曲线批量生成Ultra.py"
OUT_DIR = r"C:\Users\LENOVO\.hanako\repos\172629438__loomgrid\assets\screenshots"


def load_mod():
    spec = importlib.util.spec_from_file_location("lg_ultra", SRC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["lg_ultra"] = mod
    spec.loader.exec_module(mod)
    return mod


def main():
    mod = load_mod()
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFontDatabase, QFont
    from PySide6.QtCore import QTimer

    app = QApplication.instance() or QApplication(sys.argv)

    # offscreen 平台没有字体后端，必须手动装字体（否则全是豆腐块）
    for f in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf",
              r"C:\Windows\Fonts\simsun.ttc", r"C:\Windows\Fonts\seguiemj.ttf"):
        if os.path.exists(f):
            QFontDatabase.addApplicationFont(f)
    font = QFont("Microsoft YaHei", 10)
    app.setFont(font)

    # 强制浅色主题，否则会出现深色背景配浅色控件的混搭
    try:
        mod.set_global_theme("light")
        mod.invalidate_qss_cache()
        mod.apply_global_stylesheet(app)
        print(f"主题: {mod.get_global_theme()}")
    except Exception as e:
        print(f"主题强制失败: {e}")

    import numpy as np

    # 造 30 天 × 96 点商业综合体曲线：工作日夜峰、周末降 28%
    rng = np.random.default_rng(42)
    days, pts = 30, 96
    base = np.zeros(pts)
    for i in range(pts):
        h = i / 4.0
        if 0 <= h < 6:
            base[i] = 180
        elif 6 <= h < 8:
            base[i] = 180 + (h - 6) * 320
        elif 8 <= h < 11:
            base[i] = 820 + (h - 8) * 180
        elif 11 <= h < 13:
            base[i] = 1360 - (h - 11) * 380
        elif 13 <= h < 17:
            base[i] = 600 + (h - 13) * 230
        elif 17 <= h < 19:
            base[i] = 1520
        elif 19 <= h < 22:
            base[i] = 1520 - (h - 19) * 380
        else:
            base[i] = 380 - (h - 22) * 60
    arr = np.zeros((days, pts))
    for d in range(days):
        weekend = (d % 7) in (5, 6)
        scale = 0.72 if weekend else 1.0
        noise = rng.normal(1.0, 0.025, pts)
        arr[d] = base * scale * noise
    arr = np.clip(arr, 12, None)

    times = [f"{i // 4:02d}:{(i % 4) * 15:02d}" for i in range(pts)]
    dates = [f"2025-06-{d + 1:02d}" for d in range(days)]

    provider = mod.DataProvider()
    summary = provider.register_simulated(
        "商业综合体A座",
        {"load_array": arr, "time_labels": times,
         "date_labels": dates, "asset": "商业综合体A座"},
        month_str="2025-06",
    )
    summaries = [summary]
    print(f"summary: {summary.asset_value} / {summary.month_str} / "
          f"{summary.date_count}天 / {summary.time_points}点")

    settings = mod._PerGroupSettings(summaries, 1.0)
    cfg = mod.Config()

    os.makedirs(OUT_DIR, exist_ok=True)
    made = []
    for mode, tag in (("A", "peak-valley"), ("B", "pv")):
        try:
            win = mod.ProfessionalWindow(
                mode=mode, summaries=summaries, provider=provider,
                config_manager=cfg, settings_model=settings,
                province_name="河南", plot_options={}, global_multiplier=1.0,
            )
            win.resize(1400, 900)
            win.show()

            # 等 _delayed_load（QTimer 10ms + 计算）
            for _ in range(60):
                app.processEvents()
                QTimer.singleShot(0, lambda: None)
                import time as _t
                _t.sleep(0.05)

            # 填入一个合理的配置容量，让窗口算出满足天数与经济指标
            try:
                if hasattr(win, "config_cap_spin"):
                    win.config_cap_spin.setValue(800.0)
                if hasattr(win, "rated_power_spin"):
                    win.rated_power_spin.setValue(500.0)
                app.processEvents()
                for _ in range(20):
                    app.processEvents()
                    import time as _t
                    _t.sleep(0.05)
            except Exception as e:
                print(f"  容量注入: {type(e).__name__}: {e}")

            for _ in range(40):
                app.processEvents()
                import time as _t
                _t.sleep(0.05)

            path = os.path.join(OUT_DIR, f"ultra-pro-{tag}.png")
            pm = win.grab()
            pm.save(path)
            made.append(path)
            print(f"  [{mode}] {path}  {pm.width()}x{pm.height()}  "
                  f"{os.path.getsize(path)/1024:.0f} KB")
            win.close()
        except Exception as e:
            import traceback
            print(f"  [{mode}] 失败: {type(e).__name__}: {e}")
            traceback.print_exc(limit=6)

    print(f"\n产出 {len(made)} 张")


if __name__ == "__main__":
    main()
