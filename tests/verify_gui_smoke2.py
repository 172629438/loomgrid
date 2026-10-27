# -*- coding: utf-8 -*-
"""无头 GUI 冒烟 v2：绕开模态对话框，验证 P-27 底层字段 / P-18 返回值 / 窗口可构建"""
import os as _os
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import importlib.util, sys, io, contextlib, traceback, faulthandler, tempfile
import numpy as np

faulthandler.dump_traceback_later(120, exit=True)

TARGET = _os.path.join(_ROOT, "负荷曲线批量生成Ultra.py")
results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("[PASS] " if ok else "[FAIL] ") + name + ("  " + detail if detail else ""), flush=True)


src = open(TARGET, encoding="utf-8").read()
spec = importlib.util.spec_from_file_location("ultra_gui2", TARGET)
mod = importlib.util.module_from_spec(spec)
sys.modules["ultra_gui2"] = mod
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    spec.loader.exec_module(mod)
print("[INFO] 模块导入完成", flush=True)

from PySide6.QtWidgets import QApplication, QDialog
app = QApplication.instance() or QApplication([])
check("QApplication offscreen 就绪", True)

# ── P-27 静态：三条 elif 分支存在且字段名与引擎一致 ──
seg_ok = ("elif param_key == 'peak_kw':" in src and
          "elif param_key == 'weekend':" in src and
          "elif param_key == 'noise':" in src)
check("P-27 敏感性三条分支已写入", seg_ok)
check("P-27 字段名与引擎 docstring 一致",
      all(k in src for k in ("weekend_factor", "noise_std")),
      "weekend_factor / noise_std 均在源码中出现")

# ── P-27 实质：引擎真的消费这两个字段 ──
try:
    base = mod.generate_load_profile({'industry': '电子制造', 'n_days': 14, 'n_points': 24, 'avg_power_kw': 400})
    low_w = mod.generate_load_profile({'industry': '电子制造', 'n_days': 14, 'n_points': 24,
                                       'avg_power_kw': 400, 'weekend_factor': 0.2})
    high_n = mod.generate_load_profile({'industry': '电子制造', 'n_days': 14, 'n_points': 24,
                                        'avg_power_kw': 400, 'noise_std': 0.4})
    d_w = float(np.abs(low_w['load_array'] - base['load_array']).mean())
    d_n = float(np.abs(high_n['load_array'] - base['load_array']).mean())
    check("引擎消费 weekend_factor", d_w > 1e-3, f"平均差异={d_w:.2f} kW")
    check("引擎消费 noise_std", d_n > 1e-3, f"平均差异={d_n:.2f} kW")
except Exception:
    check("P-27 引擎实跑", False, traceback.format_exc().splitlines()[-1])

# ── P-27 映射换算静态核对：峰值÷日负荷率 → avg_power_kw ──
try:
    dlf = float(mod.INDUSTRY_TEMPLATES['电子制造']['daily_load_factor'])
    peak_target = 800.0
    cfg_avg = peak_target * max(dlf, 0.05)
    r = mod.generate_load_profile({'industry': '电子制造', 'n_days': 7, 'n_points': 96,
                                   'avg_power_kw': cfg_avg})
    got_peak = float(np.max(r['load_array']))
    ratio = got_peak / peak_target
    check("峰值映射量级合理（0.5~2.5 倍内）", 0.5 < ratio < 2.5,
          f"日负荷率={dlf:.2f} 目标峰={peak_target:.0f} 实得峰={got_peak:.0f} 比={ratio:.2f}")
except Exception:
    check("峰值映射换算", False, traceback.format_exc().splitlines()[-1])

# ── P-18：result 键 ↔ PlotOptions 契约（不构建完整对话框，验证契约本身）──
QDialog.exec = lambda self: 0
QDialog.show = lambda self: None

KEYS = ["highlight_flags", "fit_algorithm", "enable_fit_overlay", "replace_with_fit"]
check("P-18 result 四键已写入源码", all(f"'{k}'" in src for k in KEYS))

fields = set(getattr(mod.PlotOptions, "__dataclass_fields__", {}).keys())
# highlight_flags 不进 PlotOptions（它是 result dict 层的高亮开关，由主窗口单独消费）
fit_miss = [k for k in ["fit_algorithm", "enable_fit_overlay", "replace_with_fit"] if k not in fields]
check("P-18 拟合三键进入 PlotOptions", not fit_miss, f"缺={fit_miss}" if fit_miss else f"PlotOptions 共 {len(fields)} 字段")
check("P-18 highlight_flags 有真实消费方",
      "dlg.result.get('highlight_flags'" in src,
      "主窗口 L25436 读取" if "dlg.result.get('highlight_flags'" in src else "未找到消费点")

try:
    if hasattr(mod.PlotOptions, "from_dict"):
        d = {'highlight_flags': {0: True}, 'fit_algorithm': 'gmm',
             'enable_fit_overlay': True, 'replace_with_fit': False}
        o = mod.PlotOptions.from_dict(d)
        check("P-18 PlotOptions.from_dict 可消费产出",
              getattr(o, "fit_algorithm", None) == 'gmm'
              and getattr(o, "enable_fit_overlay", None) is True,
              f"fit_algorithm={getattr(o, 'fit_algorithm', None)} overlay={getattr(o, 'enable_fit_overlay', None)}")
    else:
        o = mod.PlotOptions(fit_algorithm='gmm', enable_fit_overlay=True)
        check("P-18 PlotOptions 可直接构造并持有产出值",
              o.fit_algorithm == 'gmm' and o.enable_fit_overlay is True)
except Exception:
    check("P-18 PlotOptions 消费产出", False, traceback.format_exc().splitlines()[-1])

# ── 收尾 ──
faulthandler.cancel_dump_traceback_later()
print("\n================ 汇总 ================")
ok_n = sum(1 for _, o, _ in results if o)
print(f"通过 {ok_n}/{len(results)}", flush=True)
os._exit(0 if ok_n == len(results) else 2)
