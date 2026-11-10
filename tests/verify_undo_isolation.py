# -*- coding: utf-8 -*-
"""撤销栈独立性验证：push 之后原地改 dict，历史必须不受影响。

这条是反向证明——用旧的浅拷贝写法跑一遍，断言它确实会污染；
再用现在的写法跑一遍，断言干净。
"""
import ast
import io
import os
import sys

REPO = r"C:\Users\LENOVO\.hanako\repos\172629438__loomgrid"
MAIN = os.path.join(REPO, "apps", "负荷曲线批量生成工具 Pro 融合版 (v2.3).py")
sys.path.insert(0, REPO)
sys.stdout.reconfigure(encoding="utf-8")
src = io.open(MAIN, encoding="utf-8").read()
lines = src.splitlines()
tree = ast.parse(src)

ok, bad = [], []


def check(name, cond, detail=""):
    if cond:
        ok.append(name)
        print(f"  [PASS] {name}")
    else:
        bad.append(name)
        print(f"  [FAIL] {name}   {detail}")


print("=" * 76)
print("① 源码形态：三处 push 都必须是 dict 级复制")
print("=" * 76)
for fname in ("_push_history", "_undo", "_redo"):
    seg = ""
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == fname:
            seg = "\n".join(lines[n.lineno - 1:n.end_lineno])
            break
    # 只看代码行，排除注释（注释里会提到旧写法作为说明）
    code_lines = [l for l in seg.splitlines() if not l.strip().startswith("#")]
    code = "\n".join(code_lines)
    shallow = "current_periods[:]" in code
    deep = "[dict(p) for p in self.current_periods]" in code
    print(f"  {fname}: 浅拷贝={shallow}  dict级复制={deep}")
    check(f"{fname} 不再用浅拷贝", not shallow, "仍是 current_periods[:]")
    check(f"{fname} 用 dict 级复制", deep)

print()
print("=" * 76)
print("② 反向证明：旧写法确实会污染历史")
print("=" * 76)
cur = [{"type": "peak", "start": 8, "end": 12}]
undo = [cur[:]]                      # 旧写法
cur[0]["type"] = "valley"            # “修改类型”那一步
polluted = undo[0][0]["type"] == "valley"
print(f"  旧写法：改完后 undo[0][0]['type'] = {undo[0][0]['type']}")
check("旧写法确实污染（证明 bug 真实存在过）", polluted,
      "旧写法没污染说明这个 bug 不成立")

print()
print("=" * 76)
print("③ 新写法：同样操作历史保持原样")
print("=" * 76)
cur2 = [{"type": "peak", "start": 8, "end": 12}]
undo2 = [[dict(p) for p in cur2]]    # 新写法
cur2[0]["type"] = "valley"
print(f"  新写法：改完后 undo2[0][0]['type'] = {undo2[0][0]['type']}")
check("新写法历史不被污染", undo2[0][0]["type"] == "peak")

print()
print("=" * 76)
print("④ 撤销后类型真的能改回去")
print("=" * 76)
cur3 = [{"type": "peak", "start": 8, "end": 12}]
undo3 = []
redo3 = []
# 模拟：push → 改类型 → undo
undo3.append([dict(p) for p in cur3])
cur3[0]["type"] = "valley"
print(f"  改类型后 cur3 = {cur3[0]['type']}")
redo3.append([dict(p) for p in cur3])
cur3 = undo3.pop()
print(f"  undo 后 cur3 = {cur3[0]['type']}")
check("undo 把类型改回 peak", cur3[0]["type"] == "peak")

print()
print("=" * 76)
print("⑤ 多段 + 多层撤销")
print("=" * 76)
cur4 = [{"type": "peak", "start": 8, "end": 12},
        {"type": "valley", "start": 0, "end": 6}]
undo4 = []
undo4.append([dict(p) for p in cur4])
cur4[0]["start"] = 9
undo4.append([dict(p) for p in cur4])
cur4[1]["end"] = 7
print(f"  三层状态历史: {[[ (p['start'], p['end']) for p in h] for h in undo4]}")
st1 = undo4.pop()
st2 = undo4.pop()
print(f"  undo 一次 → {[(p['start'], p['end']) for p in st1]}")
print(f"  undo 两次 → {[(p['start'], p['end']) for p in st2]}")
check("第一次撤销回到改 start 后", st1[0]["start"] == 9 and st1[1]["end"] == 6)
check("第二次撤销回到最初", st2[0]["start"] == 8 and st2[1]["end"] == 6)

print()
print("=" * 76)
print(f"结果：PASS {len(ok)}   FAIL {len(bad)}")
for b in bad:
    print("  -", b)
print("=" * 76)
sys.exit(1 if bad else 0)
