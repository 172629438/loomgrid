# -*- coding: utf-8 -*-
"""配置服务的三个健壮性缺口（P2-2 / P2-3 / P2-9）。

- P2-2 `_valid_map` 硬取 `m['format_type']`：v13 及更早写下的
  column_fingerprints 条目没有这个键，`recommend()` 的指纹循环没有 try，
  直接 KeyError 冒到 UI。
- P2-3 配置损坏且 `config_backups/` 不存在时：`__init__` 先 `_load()` 再
  `makedirs`，`_restore_backup` 的 `os.listdir` 抛 FileNotFoundError，启动即崩。
- P2-9 `DEFAULT_CONFIG` 浅拷贝：`setdefault` 把类属性的嵌套 dict 直接挂到
  实例上，实例改 `app_settings` 会污染类默认值。

跑法：python tests/verify_config_robustness.py
"""
import copy
import json
import logging
import os
import shutil
import sys
import tempfile

logging.disable(logging.CRITICAL)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

from services.config import Config  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}  {detail}")


class _InTmp:
    """把 Config 的文件路径重定向到临时目录。

    v2.3.19：Config.FILE / BACKUP_DIR 从裸文件名改成了绝对路径
    （管理员启动时 cwd 会变成 System32，相对路径会把配置写进系统目录）。
    所以隔离环境不能再靠 os.chdir——那样改不到类属性。
    这里改成真改类属性，退出时还原。
    """

    def __enter__(self):
        self.old_cwd = os.getcwd()
        self.old_file = Config.FILE
        self.old_backup = Config.BACKUP_DIR
        self.path = tempfile.mkdtemp(prefix="loomgrid_cfg_")
        Config.FILE = os.path.join(self.path, "column_mapping_config.json")
        Config.BACKUP_DIR = os.path.join(self.path, "config_backups")
        os.chdir(self.path)
        return self.path

    def __exit__(self, *a):
        Config.FILE = self.old_file
        Config.BACKUP_DIR = self.old_backup
        os.chdir(self.old_cwd)
        shutil.rmtree(self.path, ignore_errors=True)


# 1) 损坏配置 + 无备份目录：不抛，回落默认
with _InTmp() as tmp:
    with open(Config.FILE, "w", encoding="utf-8") as f:
        f.write("{bad json")
    try:
        cfg = Config()
        ok = cfg.restored_from_backup and isinstance(cfg.cfg.get("app_settings"), dict)
        check("配置损坏且无备份目录时不崩、回落默认", ok,
              f"restored={cfg.restored_from_backup}")
    except Exception as e:
        check("配置损坏且无备份目录时不崩、回落默认", False, f"{type(e).__name__}: {e}")

# 2) 备份目录不存在时 _restore_backup 本身也不崩
with _InTmp():
    c = Config.__new__(Config)
    c.BACKUP_DIR = os.path.join(tempfile.gettempdir(), "loomgrid_no_such_dir_xyz")
    c.restored_from_backup = False
    try:
        d = c._restore_backup()
        check("_restore_backup 目录不存在时返回默认", isinstance(d, dict) and "app_settings" in d)
    except Exception as e:
        check("_restore_backup 目录不存在时返回默认", False, f"{type(e).__name__}: {e}")

# 3) 旧映射条目缺 format_type：_valid_map 不 KeyError
with _InTmp():
    c = Config.__new__(Config)
    legacy = {"date_col": "日期", "load_col": "负荷"}   # 没有 format_type
    try:
        ok = c._valid_map(legacy, ["日期", "负荷"])
        check("旧映射缺 format_type 不再 KeyError", ok is True, f"返回 {ok}")
    except Exception as e:
        check("旧映射缺 format_type 不再 KeyError", False, f"{type(e).__name__}: {e}")

# 4) 实例不再污染类默认值
with _InTmp():
    before = copy.deepcopy(Config.DEFAULT_CONFIG["app_settings"])
    cfg = Config()
    cfg.cfg.setdefault("app_settings", {})["dark_theme"] = True
    after = Config.DEFAULT_CONFIG["app_settings"]
    check("实例改 app_settings 不影响类默认值",
          after.get("dark_theme") == before.get("dark_theme"),
          f"before={before.get('dark_theme')} after={after.get('dark_theme')}")
    check("实例与类默认值是不同对象",
          cfg.cfg["app_settings"] is not Config.DEFAULT_CONFIG["app_settings"])

print()
print(f"结果：PASS {PASS}   FAIL {FAIL}")
sys.exit(1 if FAIL else 0)
