"""CI 用：检查构建出的 wheel 内容是否符合预期。

独立成文件而不是写在 workflow 的 run 里，因为 heredoc 的缩进
会被原样带进 Python，导致 IndentationError。
"""
import glob
import sys
import zipfile

EXPECTED = {
    "loomgrid_algos/__init__.py",
    "loomgrid_algos/periods.py",
    "loomgrid_algos/spaces.py",
    "loomgrid_algos/dispatch.py",
    "loomgrid_algos/milp.py",
    "loomgrid_algos/finance.py",
    "loomgrid_algos/loadgen.py",
    "loomgrid_algos/diagnostics.py",
    # package-data: 标记该包带类型信息，让 mypy/pyright 认它
    "loomgrid_algos/py.typed",
}

wheels = glob.glob("dist/*.whl")
if len(wheels) != 1:
    sys.exit(f"FAIL: expected exactly 1 wheel, got {wheels}")

names = sorted(zipfile.ZipFile(wheels[0]).namelist())
modules = {n for n in names if n.startswith("loomgrid_algos/")}

print(wheels[0])
for n in names:
    print(f"  {n}")

missing = EXPECTED - modules
if missing:
    sys.exit(f"FAIL: missing modules in wheel: {sorted(missing)}")

extra = modules - EXPECTED
if extra:
    sys.exit(f"FAIL: unexpected files in wheel: {sorted(extra)}")

print(f"\nOK: {len(modules)} files packaged, nothing extra")
