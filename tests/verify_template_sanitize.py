# -*- coding: utf-8 -*-
"""_sanitize_template_settings 的行为验证。

核心：畸形模板进来，出来的每个字段都必须是控件能接的类型。
"""
import ast
import io
import os
import sys

REPO = r"C:\Users\LENOVO\.hanako\repos\172629438__loomgrid"
MAIN = os.path.join(REPO, "apps", "负荷曲线批量生成工具 Pro 融合版 (v2.3).py")
sys.path.insert(0, REPO)
sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

src = io.open(MAIN, encoding="utf-8").read()
lines = src.splitlines()

ok, bad = [], []


def check(name, cond, detail=""):
    if cond:
        ok.append(name)
        print(f"  [PASS] {name}")
    else:
        bad.append(name)
        print(f"  [FAIL] {name}   {detail}")


# 直接从源码抽函数执行（避免导入整个主程序要建 Qt 窗口）
tree = ast.parse(src)
node = None
for n in tree.body:
    if isinstance(n, ast.FunctionDef) and n.name == "_sanitize_template_settings":
        node = n
if node is None:
    print("未找到 _sanitize_template_settings")
    sys.exit(1)

ns = {}
exec(compile(ast.Module(body=[node], type_ignores=[]), "<s>", "exec"), ns)
san = ns["_sanitize_template_settings"]

print("=" * 78)
print("① 畸形值全部回落成默认")
print("=" * 78)
BAD = {
    "show_daily": True,          # 最低门槛
    "multiplier": "abc",
    "capacity_value": None,
    "weekend_line_width": "很宽",
    "holiday_line_width": [1],
    "show_avg": "yes",
    "show_max_demand": 1,
    "mark_anomalies": "true",
    "billing_enabled": "on",
    "show_weekend_bg": None,
    "dark_theme": {},
    "title_template": 123,
    "filename_template": None,
    "weekday_color": 456,
    "weekend_color": "蓝色",
    "holiday_color": None,
    "highlight_mode": "???",
    "legend_position": "left",
}
r = san(BAD)
print(f"  输入 {len(BAD)} 个字段，输出 {len(r)} 个")
for k in ("multiplier", "capacity_value", "weekend_line_width"):
    check(f"{k} 回落为数值", isinstance(r[k], (int, float)) and not isinstance(r[k], bool), repr(r[k]))
for k in ("show_avg", "show_max_demand", "mark_anomalies",
          "billing_enabled", "show_weekend_bg", "dark_theme"):
    check(f"{k} 回落为 bool", isinstance(r[k], bool), repr(r[k]))
for k in ("title_template", "filename_template"):
    check(f"{k} 回落为 str", isinstance(r[k], str), repr(r[k]))
for k in ("weekday_color", "weekend_color", "holiday_color"):
    check(f"{k} 是 # 开头的颜色", isinstance(r[k], str) and r[k].startswith("#"), repr(r[k]))
check("highlight_mode 回落 none", r["highlight_mode"] == "none", repr(r["highlight_mode"]))
check("legend_position 回落 right", r["legend_position"] == "right", repr(r["legend_position"]))

print()
print("=" * 78)
print("② 合法值不能被改掉")
print("=" * 78)
GOOD = {
    "show_daily": False,
    "multiplier": 1.75,
    "capacity_value": 850.5,
    "weekend_line_width": 3,
    "holiday_line_width": 2,
    "show_avg": True,
    "show_max_demand": False,
    "mark_anomalies": True,
    "billing_enabled": True,
    "show_weekend_bg": True,
    "dark_theme": True,
    "title_template": "{table_no} 负荷",
    "filename_template": "{year_month}",
    "weekday_color": "#123456",
    "weekend_color": "#ABCDEF",
    "holiday_color": "#FF0000",
    "highlight_mode": "grouped",
    "legend_position": "bottom",
}
g = san(GOOD)
diff = {k: (GOOD[k], g.get(k)) for k in GOOD if GOOD[k] != g.get(k)}
print(f"  改动: {diff if diff else '无'}")
check("合法值零改动", not diff, str(diff))

print()
print("=" * 78)
print("③ 边界")
print("=" * 78)
check("非 dict 输入返回空 dict", san(None) == {} and san("abc") == {} and san([]) == {})
check("空 dict 不抛异常", isinstance(san({}), dict))
check("True 不被当数字", san({"multiplier": True})["multiplier"] == 1.0)

print()
print("=" * 78)
print("④ 规范化后喂给真控件（不在崩）")
print("=" * 78)
from PySide6.QtWidgets import QApplication, QDoubleSpinBox, QCheckBox, QSpinBox, QComboBox, QLineEdit
app = QApplication.instance() or QApplication([])

r = san(BAD)
sp = QDoubleSpinBox()
cb = QCheckBox()
ip = QSpinBox()
le = QLineEdit()
combo = QComboBox()
combo.addItems(["none", "weekend", "holiday", "grouped"])
try:
    sp.setValue(r["multiplier"])
    sp.setValue(r["capacity_value"])
    ip.setValue(r["weekend_line_width"])
    ip.setValue(r["holiday_line_width"])
    for k in ("show_daily", "show_avg", "show_max_demand", "mark_anomalies",
              "billing_enabled", "show_weekend_bg", "dark_theme"):
        cb.setChecked(r[k])
    le.setText(r["title_template"])
    le.setText(r["filename_template"])
    idx = combo.findText(r["highlight_mode"])
    ok_idx = idx >= 0
    print(f"  全部 setValue/setChecked/setText 成功，highlight 索引 {idx}")
    check("规范化后的值能被真控件接受", True)
    check("highlight_mode 在控件里能找到", ok_idx, str(idx))
except Exception as e:
    print(f"  抛 {type(e).__name__}: {e}")
    check("规范化后的值能被真控件接受", False, str(e))

print()
print("=" * 78)
print(f"结果：PASS {len(ok)}   FAIL {len(bad)}")
for b in bad:
    print("  -", b)
print("=" * 78)
sys.exit(1 if bad else 0)
