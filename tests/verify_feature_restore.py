# -*- coding: utf-8 -*-
"""特性回归补齐验证：v3.0 三层叠加模拟引擎 + simulate_to_csv + P-29 归属"""
import os as _os
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
import importlib.util, sys, io, contextlib, os, tempfile
import numpy as np

TARGET = _os.path.join(_ROOT, "负荷曲线批量生成Ultra.py")
results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("[PASS] " if ok else "[FAIL] ") + name + ("  " + detail if detail else ""))


spec = importlib.util.spec_from_file_location("ultra_feat", TARGET)
mod = importlib.util.module_from_spec(spec)
sys.modules["ultra_feat"] = mod
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    spec.loader.exec_module(mod)
check("模块导入", True)

# 1. 常量表已回填
check("INDUSTRY_PARAMS 已回填", hasattr(mod, "INDUSTRY_PARAMS") and len(mod.INDUSTRY_PARAMS) == 10,
      f"{len(getattr(mod, 'INDUSTRY_PARAMS', {}))} 个行业")

# 2. 函数存在
check("_generate_load_profile 存在", hasattr(mod, "_generate_load_profile"))
check("simulate_to_csv 存在", hasattr(mod, "simulate_to_csv"))

# 3. 实跑三层引擎
try:
    arr, dates, times = mod._generate_load_profile("电子制造", 10000, 800, 5, 96, seed=42)
    shape_ok = arr.shape == (5, 96)
    check("三层引擎形状 (5,96)", shape_ok, f"实际={arr.shape}")
    daily = np.sum(arr, axis=1) * (24.0 / 96)
    # 日均电量应≈10000 kWh（引擎末尾有归一化）
    check("日均电量归一化到目标", all(abs(d - 10000) < 500 for d in daily),
          f"日均={[round(float(x)) for x in daily]}")
    check("时间标签 96 个", len(times) == 96, f"{len(times)} 个，首={times[0]}，末={times[-1]}")
    check("日期递增 5 天", len(dates) == 5 and dates[0] == "2025-01-01", f"{dates[0]}..{dates[-1]}")
    check("负荷非负", float(np.nanmin(arr)) >= -1e-9, f"最小值={float(np.nanmin(arr)):.3f}")
except Exception as e:
    check("三层引擎实跑", False, repr(e))

# 4. 设备叠加生效（同一 seed 下，加设备应改变曲线形状）
try:
    arr_with, _, _ = mod._generate_load_profile("电子制造", 10000, 800, 5, 96, seed=7,
                                                equipment=[{'power': 50, 'count': 4, 'simul': 0.8, 'run_prob': 0.85}])
    arr_without, _, _ = mod._generate_load_profile("电子制造", 10000, 800, 5, 96, seed=7)
    diff = float(np.abs(arr_with - arr_without).mean())
    check("设备 Markov 叠加生效", diff > 0.01, f"平均差异={diff:.3f} kW")
except Exception as e:
    check("设备 Markov 叠加生效", False, repr(e))

# 5. simulate_to_csv 落盘
try:
    tmp = os.path.join(tempfile.gettempdir(), "_feat_restore_test.csv")
    out = mod.simulate_to_csv(tmp, "商业楼宇", 5000, 400, n_days=3, n_points=96, seed=1)

    with open(tmp, encoding="utf-8-sig") as f:
        lines = f.read().strip().split("\n")
    check("simulate_to_csv 生成文件", os.path.exists(tmp), f"{os.path.getsize(tmp)} 字节")
    check("CSV 行数=表头+3天", len(lines) == 4, f"{len(lines)} 行")
    check("CSV 表头含日期与时间点", lines[0].startswith("日期") and lines[0].count(",") == 96,
          f"列数={lines[0].count(',')+1}")
    os.remove(tmp)
except Exception as e:
    check("simulate_to_csv 落盘", False, repr(e))

# 6. P-29 归属复查
try:
    import ast
    tree = ast.parse(open(TARGET, encoding="utf-8").read())
    pw = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "ProfessionalWindow":
            pw = node
    names = [n.name for n in pw.body if isinstance(n, ast.FunctionDef)]
    check("ProfessionalWindow 方法数=117（112 + 特性同步 5）", len(names) == 117, f"{len(names)} 个")
    must = ["_generate_proposal", "_generate_html_report", "_run_settlement",
            "_show_cycle_btn", "_show_algo_compare", "_on_device_compare",
            "_on_import_pv", "_on_cost_scenario_changed", "_auto_diagnose"]
    missing = [m for m in must if m not in names]
    check("P-29 的 9 个关键方法全部归位", not missing, f"缺={missing}")
except Exception as e:
    check("P-29 归属复查", False, repr(e))

print("\n================ 汇总 ================")
ok_n = sum(1 for _, o, _ in results if o)
print(f"通过 {ok_n}/{len(results)}")
sys.exit(0 if ok_n == len(results) else 2)

