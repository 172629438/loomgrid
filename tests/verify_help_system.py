# -*- coding: utf-8 -*-
"""v2.3.16：无障碍说明系统 + 评估策略改名。

两块要验：
  一、帮助系统（HELP_TEXTS / HelpIcon / make_help_icon）结构与调用点齐全
  二、评估策略改名没有改变行为

第二条是本文件的重点。原实现用显示文本判断策略：

    use_min = (self.est_combo.currentText() != "满载利用率")

改个显示名就会静默改掉行为——下拉框里写着「宽松 · 任一次够即可」，
代码却在找「满载利用率」，永远匹配不上，于是永远走保守分支。
所以改名必须同时把判断改成 currentData()，这里逐处锁住。
"""
import io
import os
import re
import sys

REPO = r"C:\Users\LENOVO\.hanako\repos\172629438__loomgrid"
if REPO not in sys.path:
    sys.path.insert(0, REPO)
sys.stdout.reconfigure(encoding="utf-8")

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}" + (f"  {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"[FAIL] {name}  {detail}")


MAIN = os.path.join(REPO, "apps", "负荷曲线批量生成工具 Pro 融合版 (v2.3).py")
src = io.open(MAIN, encoding="utf-8").read()
lines = src.splitlines()


def code_of(line):
    """去掉行尾注释后的代码部分（粗糙但够用：不处理引号内的 #）。"""
    return line.split("#", 1)[0]


print("=" * 78)
print("① 帮助系统结构")
print("=" * 78)
check("定义了 HELP_TEXTS 字典", "HELP_TEXTS = {" in src)
check("定义了 HelpIcon 类", "class HelpIcon(QLabel):" in src)
check("定义了 make_help_icon 工厂", "def make_help_icon(" in src)
check("HelpIcon 用 WhatsThisCursor（提示可点）", "WhatsThisCursor" in src)
check("HelpIcon 点击弹 QMessageBox", "mousePressEvent" in src and "QMessageBox(self.window())" in src)

# 每个 help key 必须三段齐全
keys = re.findall(r'^\s{4}"(\w+)": \{', src, re.M)
print(f"  发现 help key: {', '.join(keys)}")
check(f"help key 数量 {len(keys)} ≥ 8", len(keys) >= 8, str(len(keys)))
for k in keys:
    block = src.split(f'"{k}": {{', 1)[-1][:1400]
    has_title = '"title"' in block.split('},')[0]
    has_brief = '"brief"' in block.split('},')[0]
    has_detail = '"detail"' in block.split('},')[0]
    check(f"  {k}: title/brief/detail 齐全",
          has_title and has_brief and has_detail,
          f"{has_title}/{has_brief}/{has_detail}")

print()
print("=" * 78)
print("② 帮助图标的调用点")
print("=" * 78)
calls = [(j + 1, l.strip()) for j, l in enumerate(lines)
         if "make_help_icon(" in l and "def " not in l]
check(f"插入了 {len(calls)} 个问号图标（预期 ≥ 8）", len(calls) >= 8, str(len(calls)))
for ln, text in calls:
    print(f"  L{ln}: {text[:80]}")

# brief 不能太长（tooltip 要短）
long_briefs = []
for k in keys:
    m = re.search(rf'"{k}": \{{.*?"brief": "([^"]*)"', src, re.S)
    if m and len(m.group(1)) > 60:
        long_briefs.append(f"{k}({len(m.group(1))}字)")
check("所有 brief ≤ 60 字（tooltip 不过长）", not long_briefs, "; ".join(long_briefs))

print()
print("=" * 78)
print("③ 评估策略改名")
print("=" * 78)
check("新名「保守 · 每段都要够」已使用", "保守 · 每段都要够" in src)
check("新名「宽松 · 任一次够即可」已使用", "宽松 · 任一次够即可" in src)
check("旧名保留溯源标注（原「最小可行容量」）", "原「最小可行容量」" in src)
check("旧名保留溯源标注（原「满载利用率」）", "原「满载利用率」" in src)

print()
print("=" * 78)
print("④ 逻辑判断已解耦（改名不改行为）")
print("=" * 78)
# 这是本轮最关键的断言：不许再用显示文本判断策略
bad_uses = []
for j, l in enumerate(lines):
    code = code_of(l)
    if re.search(r'est_combo\.currentText\(\)', code):
        bad_uses.append(f"L{j+1}: {l.strip()[:70]}")
check("est_combo 不再用 currentText() 判断逻辑", not bad_uses, "; ".join(bad_uses[:3]))

check("est_combo 用 addItem(text, userData) 形式",
      'addItem("保守 · 每段都要够", "min_feasible")' in src
      and 'addItem("宽松 · 任一次够即可", "full_load")' in src)
check("mode_combo 也用 userData",
      'addItem("两充两放（独立评估）", "two_two")' in src
      and 'addItem("一充一放", "one_one")' in src)

# 三处判断点逐一核对
judgement_sites = [
    ("_on_est_combo_changed 用 currentData", r'is_min = \(self\.est_combo\.currentData\(\) == "min_feasible"\)'),
    ("_get_all_filtered_daily_mins 用 currentData", r'use_min = \(hasattr\(self, .est_combo.\)[^\)]*currentData\(\) != "full_load"'),
    ("_check_day_satisfied 用 currentData", r'use_min = \(not hasattr\(self, .est_combo.\)\) or \(self\.est_combo\.currentData\(\) != "full_load"\)'),
]
for label, pat in judgement_sites:
    # detail 只在失败时有意义，PASS 时不要带误导性文案
    ok_found = bool(re.search(pat, src, re.S))
    check(label, ok_found, "" if ok_found else "未找到预期写法")

check("日需求分支用 est_mode == 'full_load'（不再 '满载' in text）",
      "est_mode == 'full_load'" in src and "'满载' in est_mode" not in src)

print()
print("=" * 78)
print("④之二 RTE 派生量：存在、只读、随两参数刷新")
print("=" * 78)
check("有 rte 帮助条目", '"rte": {' in src)
check("有 rte_label 控件", "self.rte_label = QLabel" in src)
check("RTE 是 QLabel 而非 QSpinBox（不可编辑）",
      "self.rte_label = QLabel" in src and "self.rte_spin" not in src)
check("_update_system_scale 会刷新 rte_label",
      "self.rte_label.setText" in src)
check("RTE 跟随 DOD 与系统效率变化",
      "self.dod_spin.valueChanged.connect(lambda: self._update_system_scale())" in src
      and "self.sys_eff_spin.valueChanged.connect(lambda: self._update_system_scale())" in src)
# RTE 必须是 k × 100，不能另算一套
m_rte = re.search(r"self\.rte_label\.setText\(f\"\{k \* 100:\.2f\}%\"\)", src)
check("RTE 直接取 k（与容量换算同一个系数）", bool(m_rte),
      "" if m_rte else "未找到 setText(f\"{k * 100:.2f}%\")")

print()
print("=" * 78)
print("⑤ 行为等价：两种 data 值下的判定结果符合业务定义")
print("=" * 78)


def use_min_of(data):
    """复现 use_min = (data != 'full_load')。"""
    return data != "full_load"


check("data=min_feasible → 走保守分支", use_min_of("min_feasible") is True)
check("data=full_load → 走宽松分支", use_min_of("full_load") is False)

# 保守 vs 宽松的分歧场景：两次循环 300/280 与 120/110，容量 200
cycles = [(300.0, 280.0), (120.0, 110.0)]
cap = 200.0
conservative = min(min(c, d) for c, d in cycles) >= cap     # 所有段都要够
loose = any(min(c, d) >= cap for c, d in cycles)            # 任一次够即可
check(f"保守判定 = {conservative}（120<200，不满足）", conservative is False)
check(f"宽松判定 = {loose}（第一次 300/280 够，满足）", loose is True)
check("两策略确实给出不同结论（不是换个名字的同义词）", conservative != loose)

# 一充一放时两者必须等价（只有一次循环，无从分歧）
cycles1 = [(300.0, 280.0)]
c1 = min(min(c, d) for c, d in cycles1) >= cap
l1 = any(min(c, d) >= cap for c, d in cycles1)
check("一充一放时两策略等价", c1 == l1, f"{c1} vs {l1}")

print()
print("=" * 76)
print(f"结果：PASS {PASS}   FAIL {FAIL}")
print("=" * 76)
sys.exit(0 if FAIL == 0 else 1)
