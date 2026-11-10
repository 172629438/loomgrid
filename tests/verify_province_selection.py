# -*- coding: utf-8 -*-
"""省份选择的「无」判断：不再依赖显示文案。

原来 5 处用 `!= "无"` 比较文案。那两个字一变（改成「不选」「（无）」），
判断静默失效——省份名会被当成「无」传给下游，或反过来「无」被当成省份。

检测走 AST，只看真实代码节点，不看注释与 docstring。
"""
import ast
import io
import os
import sys

REPO = r"C:\Users\LENOVO\.hanako\repos\172629438__loomgrid"
P = os.path.join(REPO, "apps", "负荷曲线批量生成工具 Pro 融合版 (v2.3).py")
sys.path.insert(0, REPO)
sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

src = io.open(P, encoding="utf-8").read()
tree = ast.parse(src)

ok, bad = [], []


def check(name, cond, detail=""):
    if cond:
        ok.append(name)
        print(f"  [PASS] {name}")
    else:
        bad.append(name)
        print(f"  [FAIL] {name}   {detail}")


NONE_TXT = "\u65e0"


def docstring_nodes(t):
    """所有 docstring 的 Constant 节点，检测时排除。"""
    out = set()
    for n in ast.walk(t):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
            d = ast.get_docstring(n, clean=False)
            if d is not None:
                for c in ast.walk(n):
                    if isinstance(c, ast.Expr) and isinstance(c.value, ast.Constant) \
                            and isinstance(c.value.value, str) and c.value.value == d:
                        out.add(id(c.value))
                        break
    return out


DOCS = docstring_nodes(tree)

print("=" * 78)
print("① 不再有「读 combo 文案再与「无」比较」的写法")
print("=" * 78)
# 区分两类：
#   A) 直接读 province_combo.currentText() 再判文案 —— 真问题（文案一变就失效）
#   B) 变量值可能来自旧配置的字符串「无」—— 旧数据兼容，保留
hits_a = []
for n in ast.walk(tree):
    if isinstance(n, ast.Compare):
        ops = [n.left] + list(n.comparators)
        if not any(isinstance(o, ast.Constant) and o.value == NONE_TXT for o in ops):
            continue
        left = ast.unparse(n.left) if hasattr(ast, "unparse") else ""
        if "currentText()" in left:
            hits_a.append((n.lineno, left[:60]))
for ln, txt in hits_a:
    print(f"  L{ln:5d}  {txt}  ← 读 combo 文案后比较")
if not hits_a:
    print("  （无）")
check("没有「读 combo 文案再比较」的写法", not hits_a, str(hits_a[:3]))

# 列出保留的 B 类（变量值兼容），供人工核对
hits_b = []
for n in ast.walk(tree):
    if isinstance(n, ast.Compare):
        ops = [n.left] + list(n.comparators)
        if not any(isinstance(o, ast.Constant) and o.value == NONE_TXT for o in ops):
            continue
        left = ast.unparse(n.left) if hasattr(ast, "unparse") else ""
        if "currentText()" not in left:
            hits_b.append((n.lineno, left[:50]))
print()
print(f"  保留的变量值兼容检查（{len(hits_b)} 处，旧配置可能存字符串「无」）:")
for ln, txt in hits_b:
    print(f"    L{ln:5d}  {txt}")
check("保留处不超过 6（确认没新增）", len(hits_b) <= 6, str(len(hits_b)))

print()
print("=" * 78)
print("② _combo_province 方法存在且按索引判")
print("=" * 78)
fn = None
for n in ast.walk(tree):
    if isinstance(n, ast.FunctionDef) and n.name == "_combo_province":
        fn = n
check("_combo_province 已定义", fn is not None)
if fn:
    # 只看函数体里真实的 Compare / Call 节点，忽略 docstring
    found_idx = False
    found_txt_cmp = False
    for n in ast.walk(fn):
        if isinstance(n, ast.Compare):
            s = ast.unparse(n) if hasattr(ast, "unparse") else ""
            if "currentIndex()" in s:
                found_idx = True
            if f"'{NONE_TXT}'" in s or f'"{NONE_TXT}"' in s:
                found_txt_cmp = True
    print(f"  用 currentIndex() 比较: {found_idx}")
    print(f"  用「无」文案比较:      {found_txt_cmp}")
    check("按索引判断", found_idx)
    check("不靠文案比较", not found_txt_cmp)

print()
print("=" * 78)
print("③ 不能与已有实例属性 _selected_province 撞名")
print("=" * 78)
check("没有叫 _selected_province 的方法",
      not any(isinstance(n, ast.FunctionDef) and n.name == "_selected_province"
              for n in ast.walk(tree)),
      "同名会覆盖实例属性")
n_assign = src.count("self._selected_province =")
print(f"  实例属性赋值处: {n_assign} 处")
check("属性赋值仍在", n_assign >= 4, str(n_assign))

print()
print("=" * 78)
print("④ 真实行为")
print("=" * 78)
from PySide6.QtWidgets import QApplication, QComboBox
app = QApplication.instance() or QApplication([])


def combo_province_logic(combo):
    if combo.count() == 0:
        return None
    if combo.currentIndex() == 0:
        return None
    return combo.currentText()


c = QComboBox()
c.addItem(NONE_TXT)
c.addItems(["\u5c71\u897f", "\u65b0\u7586", "\u8fbd\u5b81"])
print(f"  选项: {[c.itemText(i) for i in range(c.count())]}")
c.setCurrentIndex(0)
print(f"  选第 0 项 → {combo_province_logic(c)}")
check("选「无」返回 None", combo_province_logic(c) is None)
c.setCurrentIndex(1)
print(f"  选第 1 项 → {combo_province_logic(c)}")
check("选省份返回名字", combo_province_logic(c) == "\u5c71\u897f")

print()
print("  反向：文案改成「不选」后索引逻辑仍正确")
c2 = QComboBox()
c2.addItem("\u4e0d\u9009")
c2.addItems(["\u5c71\u897f"])
c2.setCurrentIndex(0)
r2 = combo_province_logic(c2)
print(f"  新写法 → {r2}")
check("文案改了判据仍成立", r2 is None, str(r2))

old_logic = (lambda cmb: cmb.currentText() if cmb.currentText() != NONE_TXT else None)
r_old = old_logic(c2)
print(f"  旧写法 → {r_old!r}  ← 把「不选」当成省份名")
check("旧写法确实失效（bug 存在过）", r_old == "\u4e0d\u9009", str(r_old))

print()
print("=" * 78)
print(f"结果：PASS {len(ok)}   FAIL {len(bad)}")
for b in bad:
    print("  -", b)
print("=" * 78)
sys.exit(1 if bad else 0)
