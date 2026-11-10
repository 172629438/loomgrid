# -*- coding: utf-8 -*-
"""v2.3.19 三件事的回归：
  ① 管理员启动时路径不飘（相对路径依赖 cwd 导致的「处处受限」）
  ② 映射窗口三个格式选项各带「查看示例」按钮，示例文件真能被程序解析
  ③ 格式顺序：长表 → 宽表 → 正向有功示数表

判据说明：
  · ① 与 ② 都是真跑（真切 cwd、真造文件、真喂扫描器），不是源码切片
  · ③ 只能查源码顺序（布局在 __init__ 里，无显示环境无法截图）
"""
import ast
import importlib.util
import io
import os
import sys
import tempfile
import threading

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(REPO, "apps", "负荷曲线批量生成工具 Pro 融合版 (v2.3).py")

sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "apps"))
sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

passed, failed = [], []


def check(name, cond, detail=""):
    if cond:
        passed.append(name)
        print(f"  [PASS] {name}")
    else:
        failed.append(name)
        print(f"  [FAIL] {name}  {detail}")


src = io.open(MAIN, encoding="utf-8").read()
lines = src.splitlines()

print("=" * 76)
print("① 管理员启动：路径不依赖 cwd")
print("=" * 76)
check("主程序有 _app_dir()", "def _app_dir(" in src)
check("模块级 chdir 到程序目录", "os.chdir(_APP_DIR)" in src)

# services/config.py 的路径必须是绝对
cfg_src = io.open(os.path.join(REPO, "services", "config.py"), encoding="utf-8").read()
check("Config 用 _config_home() 算路径", "def _config_home(" in cfg_src)
check("Config.FILE 不再是裸文件名",
      'FILE = "column_mapping_config.json"' not in cfg_src,
      "还是相对路径 → 管理员启动会写进 System32")
check("Config.BACKUP_DIR 不再是裸目录名",
      'BACKUP_DIR = "config_backups"' not in cfg_src)

# 真跑：切到 System32 再看路径
orig_cwd = os.getcwd()
try:
    os.chdir(r"C:\Windows\System32")
    for m in list(sys.modules):
        if m.startswith("services."):
            del sys.modules[m]
    from services.config import Config

    check("实测：cwd 在 System32 时 Config.FILE 仍是绝对路径",
          os.path.isabs(Config.FILE), Config.FILE)
    check("实测：Config.FILE 不落在 System32",
          "system32" not in Config.FILE.lower(), Config.FILE)
    check("实测：Config.BACKUP_DIR 不落在 System32",
          "system32" not in Config.BACKUP_DIR.lower(), Config.BACKUP_DIR)
except OSError as e:
    print(f"  [SKIP] 无法切到 System32（{e}）")
finally:
    try:
        os.chdir(orig_cwd)
    except Exception:
        pass

print()
print("=" * 76)
print("② 格式顺序：长表 → 宽表 → 示数表")
print("=" * 76)
i_long = src.find("fmt_layout.addLayout(_fmt_row(\n            self.fmt_long")
if i_long < 0:
    i_long = src.find("self.fmt_long, \"long\"")
i_wide = src.find("self.fmt_wide, \"wide\"")
i_acc = src.find("self.fmt_accumulated, \"accumulated\"")
print(f"  源码位置: long={i_long}  wide={i_wide}  accum={i_acc}")
check("三个选项都能找到", min(i_long, i_wide, i_acc) > 0)
check("顺序：长表在宽表之前", -1 < i_long < i_wide, f"{i_long} vs {i_wide}")
check("顺序：宽表在示数表之前", -1 < i_wide < i_acc, f"{i_wide} vs {i_acc}")

print()
print("=" * 76)
print("③ 查看示例按钮 + 示例文件真能被解析")
print("=" * 76)
check("有 _open_sample_file", "_open_sample_file" in src)
check("有 _build_sample_frame", "_build_sample_frame" in src)
check("三个格式都有示例规格", src.count('"name": "示例_') == 3,
      f"{src.count(chr(34) + 'name' + chr(34) + ': ' + chr(34) + '示例_')} 个")
check("长表示数勾选旁已无问号",
      'make_help_icon("long_accumulated"' not in src,
      "用户要求删掉这个问号")

# 真跑：造三种示例，喂给扫描器
try:
    spec = importlib.util.spec_from_file_location("fm_test_2319", MAIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    class Shell:
        _build_sample_frame = mod.MappingDialog._build_sample_frame
        _SAMPLE_SPECS = mod.MappingDialog._SAMPLE_SPECS

    sh = Shell()
    tmp = os.path.join(REPO, "samples")
    os.makedirs(tmp, exist_ok=True)
    frames = {k: sh._build_sample_frame(k) for k in ("long", "wide", "accumulated")}
    paths = {}
    for k, df in frames.items():
        p = os.path.join(tmp, sh._SAMPLE_SPECS[k]["name"])
        df.to_excel(p, index=False)
        paths[k] = p

    check("三种示例都能造出来",
          all(len(f) > 0 for f in frames.values()),
          str({k: len(v) for k, v in frames.items()}))

    from core.ingest import _scan_file_worker

    time_cols = [c for c in frames["wide"].columns if ":" in str(c)]
    MAPPINGS = {
        "long": {"date_col": "采集时间", "time_col": "采集时间", "asset_col": "",
                 "load_col": "赵钢1#线十总降213#总有功", "header_row": 0,
                 "format_type": "long", "convert_accumulated": False},
        "wide": {"date_col": "日期", "time_col": "__none__", "asset_col": "",
                 "load_col": time_cols, "header_row": 0,
                 "format_type": "wide", "convert_accumulated": False},
        "accumulated": {"date_col": "采集日期", "time_col": "采集时间", "asset_col": "",
                        "load_col": "正向有功电度", "header_row": 0,
                        "format_type": "accumulated", "convert_accumulated": True},
    }

    vals = {}
    for kind, mp in MAPPINGS.items():
        ev = threading.Event()
        r = _scan_file_worker((paths[kind], "Sheet1", mp, "示例", ev, {}))
        ok = isinstance(r, list) and len(r) > 0
        check(f"{kind} 示例被扫描器接受", ok, str(r)[:160] if not ok else "")
        if ok:
            row = r[0]
            check(f"{kind} 月份 2026-07", row.get("month_str") == "2026-07",
                  str(row.get("month_str")))
            check(f"{kind} 2 天数据", row.get("date_count") == 2,
                  str(row.get("date_count")))
            vals[kind] = (round(row.get("min_val"), 2), round(row.get("max_val"), 2))

    # 三种格式读出来应该是同一份真值（差分后少一点，但值域一致）
    if len(vals) == 3:
        print(f"  三种格式读出的值域: {vals}")
        check("long 与 wide 值域完全一致",
              vals["long"] == vals["wide"], f"{vals['long']} vs {vals['wide']}")
        check("accumulated 差分后值域与原值一致",
              vals["accumulated"] == vals["long"],
              f"{vals['accumulated']} vs {vals['long']}")
except Exception as e:
    import traceback
    traceback.print_exc(file=sys.stdout)
    check("示例文件实测未抛异常", False, f"{type(e).__name__}: {e}")

print()
print("=" * 76)
print(f"结果：PASS {len(passed)}   FAIL {len(failed)}")
for f in failed:
    print("  -", f)
print("=" * 76)
sys.exit(1 if failed else 0)
