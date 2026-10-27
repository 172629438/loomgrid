# -*- coding: utf-8 -*-
"""特性同步验证：8 个搬入方法的落位 / 全局依赖 / 宿主属性 / 可调用性"""
import os as _os
_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import importlib.util, sys, io, contextlib, ast, traceback

TARGET = _os.path.join(_ROOT, "负荷曲线批量生成Ultra.py")
results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("[PASS] " if ok else "[FAIL] ") + name + ("  " + detail if detail else ""), flush=True)


src = open(TARGET, encoding="utf-8").read()
tree = ast.parse(src)

NEW = {
    "DataProvider": ["register_simulated"],
    "ChargeDischargeManagerDialog": ["_on_save_strategy"],
    "ProfessionalWindow": ["_show_cycle_analysis", "_refresh_scenario_combo",
                           "_on_scenario_selected", "_run_optimization", "_run_full_diagnose"],
    "PreviewDialog": ["_on_batch_fuse"],
}

# 1) 全部落位
found = {}
for node in tree.body:
    if isinstance(node, ast.ClassDef) and node.name in NEW:
        for n in node.body:
            if isinstance(n, ast.FunctionDef) and n.name in NEW[node.name]:
                found[(node.name, n.name)] = n
total = sum(len(v) for v in NEW.values())
check(f"8 个方法全部落位", len(found) == total, f"{len(found)}/{total}")

# 2) 导入并检查全局依赖
spec = importlib.util.spec_from_file_location("ultra_sync", TARGET)
mod = importlib.util.module_from_spec(spec)
sys.modules["ultra_sync"] = mod
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    spec.loader.exec_module(mod)
check("模块导入（8 个新方法不引入导入期错误）", True)

missing_all = {}
for (cls, mname), fn in found.items():
    used = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and n.id[:1].isupper()}
    miss = sorted(n for n in used if not hasattr(mod, n) and not hasattr(__builtins__, n)
                  and n not in ("True", "False", "None"))
    if miss:
        missing_all[f"{cls}.{mname}"] = miss
check("全局符号依赖齐备", not missing_all, str(missing_all) if missing_all else "无缺失")

# 3) 方法可调用性（不触发的属性级检查：只看是否 FunctionDef 且首个参数为 self）
bad_sig = [f"{c}.{m}" for (c, m), fn in found.items()
           if not fn.args.args or fn.args.args[0].arg != "self"]
check("方法签名合法（首参 self）", not bad_sig, str(bad_sig) if bad_sig else "8/8")

# 4) 宿主类是否真的存在这些属性依赖（抽检关键属性）
CRITICAL = {
    "ProfessionalWindow": ["_get_work_data", "_recalc_spaces", "_populate_space_table",
                            "_populate_daily_month_combo", "_switch_scenario",
                            "_do_update_economic_label", "_populate_assessment_tab",
                            "settings_model", "summaries"],
    "PreviewDialog": ["_on_fuse_curves"],
    "DataProvider": ["_group_cache", "_group_cache_lock", "_max_group_cache"],
    "ChargeDischargeManagerDialog": ["cfg", "refresh_tree", "tree"],
}
print("\n[宿主属性抽检] AST 层面找类内 def 与 self.xxx 赋值")

def class_symbols(node):
    defs, attrs = set(), set()
    for n in ast.walk(node):
        if isinstance(n, ast.FunctionDef):
            defs.add(n.name)
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self":
                    attrs.add(t.attr)
        elif isinstance(n, ast.AnnAssign):
            t = n.target
            if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self":
                attrs.add(t.attr)
    return defs | attrs


CLASS_NODES = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
host_ok = True
for cls, attrs in CRITICAL.items():
    node = CLASS_NODES.get(cls)
    if node is None:
        print(f"  [warn] 类 {cls} 不存在")
        host_ok = False
        continue
    syms = class_symbols(node)
    for a in attrs:
        if a not in syms:
            print(f"  [warn] {cls}.{a} 未在类内找到定义/赋值")
            host_ok = False
check("宿主属性抽检通过", host_ok, "全部齐备" if host_ok else "见上 warn")

print("\n================ 汇总 ================")
ok_n = sum(1 for _, o, _ in results if o)
print(f"通过 {ok_n}/{len(results)}", flush=True)
os._exit(0 if ok_n == len(results) else 2)

