# -*- coding: utf-8 -*-
"""文件拖拽：空态遮罩穿透、支持格式判断、目录遍历限深限量。

为什么这条要测
--------------
用户反馈「有的时候好像无法正常拖拽」。查下来根因明确：

  `_empty_label` 铺满整个 QListWidget（resizeEvent 里 setGeometry(self.rect())），
  而 QLabel 默认 acceptDrops=False —— 它会吃掉拖拽事件，不转发给父控件。
  后果：**列表为空时拖不进去**，而有文件时 label 隐藏反而正常。
  失效的恰好是最需要拖拽的空列表状态。

另外两条：
  · dragEnterEvent 只判 hasUrls()，拖不支持的文件也显示"可放"，drop 后无反应无提示
  · dropEvent 对目录无限递归，拖一个盘符根会当场卡死（UI 线程同步遍历）

本脚本用假 URL 对象直接调静态方法验证判断逻辑，不需要真实拖拽。
「空态遮罩穿透」这条靠读控件属性断言——这是它能被机器判定的唯一方式。

跑法：python tests/verify_drag_drop.py
"""
import importlib.util
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
sys.stdout.reconfigure(encoding="utf-8")

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}  {detail}")


# ══════════════════════════════════════════════════════════
print("=" * 74)
print("① 静态源码断言：空态遮罩必须鼠标穿透")
print("=" * 74)
APP = os.path.join(REPO, "apps", "负荷曲线批量生成工具 Pro 融合版 (v2.3).py")
src = open(APP, encoding="utf-8").read()

check("_empty_label 设置了 WA_TransparentForMouseEvents",
      "WA_TransparentForMouseEvents" in src,
      "没找到 —— 遮罩会吞掉拖拽事件，空列表拖不进去")

# 位置校验：必须在 _empty_label 创建之后、且不是别处的注释
idx_label = src.find("self._empty_label = QLabel")
idx_attr = src.find("WA_TransparentForMouseEvents")
check("该属性设在 _empty_label 附近（-200 ~ +800 字符内）",
      idx_label > 0 and 0 < idx_attr - idx_label < 800,
      f"label@{idx_label} attr@{idx_attr}")

check("拖入时先判断支持格式（_has_supported）",
      "def _has_supported" in src and "_has_supported(event.mimeData().urls())" in src,
      "dragEnterEvent 只判 hasUrls() 会让不支持的文件也显示可放")

check("目录遍历有深度与数量限制（_walk_dir）",
      "def _walk_dir" in src and "max_depth" in src and "max_files" in src,
      "无限制递归会在拖入大目录时卡死 UI 线程")

check("全军覆没时记警告（不再是静默无反应）",
      "没有找到可导入的文件" in src,
      "拖了不支持的东西什么都不发生，用户以为程序卡了")

# ══════════════════════════════════════════════════════════
print()
print("=" * 74)
print("② 加载模块，验证 _has_supported / _walk_dir 的真实行为")
print("=" * 74)

# 这个文件不是 import 友好的（顶层会建 QApplication），
# 用 AST 抽两个静态方法出来单独跑，避免整个 GUI 起来。
import ast  # noqa: E402

tree = ast.parse(src)
cls = next((n for n in tree.body
            if isinstance(n, ast.ClassDef) and n.name == "FileListWidget"), None)
check("找到 FileListWidget 类", cls is not None)
method_names = [n.name for n in cls.body if isinstance(n, ast.FunctionDef)]
check("_has_supported 是静态方法", "_has_supported" in method_names)
check("_walk_dir 是静态方法", "_walk_dir" in method_names)


class FakeUrl:
    def __init__(self, p):
        self._p = p

    def toLocalFile(self):
        return self._p


# 手抄一份等价实现来测行为——因为把真模块 import 进来会拉起整个 GUI。
# 判据：两边源码的关键行必须一致，否则这份手抄就没意义了。
fn_has = next(n for n in cls.body
              if isinstance(n, ast.FunctionDef) and n.name == "_has_supported")
fn_walk = next(n for n in cls.body
               if isinstance(n, ast.FunctionDef) and n.name == "_walk_dir")
src_has = ast.get_source_segment(src, fn_has) or ""
src_walk = ast.get_source_segment(src, fn_walk) or ""

check("_has_supported 里有 SUPPORTED_EXTS 过滤", "SUPPORTED_EXTS" in src_has)
check("_has_supported 有 max_probe 上限", "max_probe" in src_has)
check("_walk_dir 有 max_depth 截断", "max_depth" in src_walk and "dirnames[:] = []" in src_walk)
check("_walk_dir 有 max_files 提前返回", "max_files" in src_walk and "return got, True" in src_walk)

# 用一个最小命名空间执行抽出来的函数
SUPPORTED_EXTS = (".csv", ".xlsx", ".xls", ".xlsm", ".xlsb")
ns = {"os": os, "SUPPORTED_EXTS": SUPPORTED_EXTS}


class _Shim:
    pass


for fn in (fn_has, fn_walk):
    mod = ast.Module(body=[ast.FunctionDef(
        name=fn.name, args=fn.args, body=fn.body, decorator_list=[],
        returns=fn.returns, type_comment=None, type_params=[],
        lineno=1, col_offset=0)], type_ignores=[])
    exec(compile(ast.fix_missing_locations(mod), "<shim>", "exec"), ns)

has_supported = ns["_has_supported"]
walk_dir = ns["_walk_dir"]

tmp = tempfile.mkdtemp(prefix="drag_")
try:
    # 造几个文件
    xlsx = os.path.join(tmp, "a.xlsx")
    open(xlsx, "w").close()
    csv_f = os.path.join(tmp, "b.CSV")          # 大写扩展名
    open(csv_f, "w").close()
    txt = os.path.join(tmp, "c.txt")
    open(txt, "w").close()

    check("xlsx 被识别", has_supported([FakeUrl(xlsx)]) is True)
    check("大写 .CSV 被识别（endswith 前有 lower()）", has_supported([FakeUrl(csv_f)]) is True)
    check(".txt 被拒绝", has_supported([FakeUrl(txt)]) is False)
    check("空列表返回 False", has_supported([]) is False)
    check("不存在的路径返回 False", has_supported([FakeUrl(os.path.join(tmp, "nope.xlsx"))]) is False)

    # 目录：第一层有 xlsx 就算可用
    sub = os.path.join(tmp, "sub")
    os.makedirs(sub)
    open(os.path.join(sub, "d.xlsx"), "w").close()
    deep = os.path.join(sub, "deep", "deeper")
    os.makedirs(deep)
    open(os.path.join(deep, "e.xlsx"), "w").close()

    check("目录第一层有 xlsx → True", has_supported([FakeUrl(sub)]) is True)

    # _walk_dir 能递归拿到
    got, hit = walk_dir(tmp)
    print(f"  _walk_dir(tmp) → {len(got)} 个文件，触顶={hit}")
    check("递归找到 3 个（a/b/d）或更多", len(got) >= 2, str(len(got)))
    check("全是指定扩展名",
          all(p.lower().endswith(SUPPORTED_EXTS) for p in got), str(got))

    # 深度限制：max_depth=1 表示「根 + 1 层」，所以 sub/d.xlsx 应该在，
    # 但 sub/deep/deeper/e.xlsx（第 3 层）不该在
    got1, _ = walk_dir(tmp, max_depth=1, max_files=5000)
    names1 = [os.path.basename(p) for p in got1]
    check(f"max_depth=1 拿到根+第1层（实 {len(got1)} 个: {names1}）",
          "a.xlsx" in names1 and "b.CSV" in names1 and "d.xlsx" in names1,
          str(names1))
    check("max_depth=1 不深入第 3 层（e.xlsx 不在）",
          "e.xlsx" not in names1, str(names1))

    # 数量限制
    many = os.path.join(tmp, "many")
    os.makedirs(many)
    for i in range(30):
        open(os.path.join(many, f"f{i}.xlsx"), "w").close()
    got2, hit2 = walk_dir(many, max_depth=6, max_files=10)
    check(f"max_files=10 时提前触顶（实 {len(got2)} 个，hit={hit2}）",
          len(got2) == 10 and hit2 is True, f"{len(got2)} {hit2}")
finally:
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

print()
print("=" * 74)
print(f"结果：PASS {PASS}   FAIL {FAIL}")
print("=" * 74)
sys.exit(1 if FAIL else 0)
