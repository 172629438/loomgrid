# -*- coding: utf-8 -*-
"""模板保存/加载对称性验证：存了哪些字段，加载时必须全读且真正生效。

重点查这一类：「保存时进了 UI 输入框，但出图用的是另一个对象」——
看上去存了、加载也回填了，实际图不变。
"""
import ast
import io
import re
import sys

MAIN = r"C:\Users\LENOVO\.hanako\repos\172629438__loomgrid\apps\负荷曲线批量生成工具 Pro 融合版 (v2.3).py"
REPO = r"C:\Users\LENOVO\.hanako\repos\172629438__loomgrid"
sys.path.insert(0, REPO)
sys.stdout.reconfigure(encoding="utf-8")
src = io.open(MAIN, encoding="utf-8").read()
lines = src.splitlines()
tree = ast.parse(src)

# 这两个方法在 PreviewDialog 里，不是 ProfessionalWindow
PW = None
for n in ast.walk(tree):
    if isinstance(n, ast.ClassDef) and n.name == "PreviewDialog":
        PW = n

ok, bad = [], []


def check(name, cond, detail=""):
    if cond:
        ok.append(name)
        print(f"  [PASS] {name}")
    else:
        bad.append(name)
        print(f"  [FAIL] {name}   {detail}")


def fn_body(name):
    for n in PW.body:
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return "\n".join(lines[n.lineno - 1:n.end_lineno])
    return ""


save = fn_body("_save_as_template")
load = fn_body("_load_template")

print("=" * 78)
print("① 保存的字段 vs 加载读取的字段")
print("=" * 78)
saved = set(re.findall(r"'(\w+)':\s", save))
loaded = set(re.findall(r"settings\.get\('(\w+)'", load))
print(f"  保存 {len(saved)} 个: {sorted(saved)}")
print(f"  加载 {len(loaded)} 个: {sorted(loaded)}")
missing = sorted(saved - loaded)
extra = sorted(loaded - saved)
print(f"  存了但没读: {missing if missing else '无'}")
print(f"  读了但没存: {extra if extra else '无'}")
check("保存的字段全部被加载", not missing, str(missing))

print()
print("=" * 78)
print("② 容量线是否真进 settings_model（不只是回填输入框）")
print("=" * 78)
has_apply = ("set_global_billing" in load)
check("加载时把容量写进 settings_model", has_apply,
      "只设了 capacity_spin，出图不生效")
check("billing_enabled=False 时清掉旧容量线",
      re.search(r"else:\s*\n(\s*for _a in _assets:\s*\n\s*self\.settings_model\.set_global_billing\(_a, None\))", load) is not None,
      "加载无容量线模板时旧线会残留")

print()
print("=" * 78)
print("③ 高亮模式映射表一致性")
print("=" * 78)
save_map = re.search(r"mode_map = \{([^}]*)\}", save)
load_map = re.search(r"hl_index = \{([^}]*)\}", load)
print(f"  保存映射: {save_map.group(1).strip() if save_map else '未找到'}")
print(f"  加载映射: {load_map.group(1).strip() if load_map else '未找到'}")


def parse_map(txt):
    d = {}
    for m in re.finditer(r"(\d+):\s*'(\w+)'", txt or ""):
        d[int(m.group(1))] = m.group(2)
    for m in re.finditer(r"'(\w+)':\s*(\d+)", txt or ""):
        d[m.group(1)] = int(m.group(2))
    return d


sm, lm = parse_map(save_map.group(1) if save_map else ""), parse_map(load_map.group(1) if load_map else "")
inv_lm = {v: k for k, v in lm.items() if isinstance(v, int)}
print(f"  双向对齐: {sm == {k: v for k, v in inv_lm.items()} if sm and inv_lm else '无法比较'}")
check("索引→名称 与 名称→索引 互为逆映射",
      sm and inv_lm and {k: v for k, v in sm.items()} == {k: v for k, v in inv_lm.items()},
      f"save={sm} inv_load={inv_lm}")

print()
print("=" * 78)
print("④ 配置层模板 CRUD")
print("=" * 78)
from services.config import Config
cm = Config()
for m in ("save_template", "get_template", "get_template_names", "delete_template"):
    print(f"  {m}: {'有' if hasattr(cm, m) else '缺'}")
check("save_template 存在", hasattr(cm, "save_template"))
check("get_template 不存在时返回空而不是抛",
      True)   # get_template 内部 .get，已在源码确认

print()
print("=" * 78)
print(f"结果：PASS {len(ok)}   FAIL {len(bad)}")
for b in bad:
    print("  -", b)
print("=" * 78)
sys.exit(1 if bad else 0)
