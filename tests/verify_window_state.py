# -*- coding: utf-8 -*-
"""窗口状态：几何/分栏的存取、不重复写盘、键命名空间。

为什么这条重要
--------------
这 4 个函数此前**零测试**。而它们的失败方式都是「常用操作积累出来的慢性问题」：
  · 每次关闭对话框都写盘 → 生成备份 → 把「改配置前的历史备份」挤掉
  · dialog_id 与 splitter key 共用扁平 dict → 同 key 互相覆盖

实测（修前）：
  6 次窗口操作 → 备份 0 → 5
  10 次窗口操作 → 3 个历史备份全被挤掉
  同 key 写几何再写分栏 → 读几何拿到的是分栏数据

跑法：python tests/verify_window_state.py
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
sys.stdout.reconfigure(encoding="utf-8")

from PySide6.QtCore import QByteArray  # noqa: E402

from services.config import Config  # noqa: E402
from ui.window_state import (  # noqa: E402
    _SPLITTER_PREFIX, get_splitter_state, get_window_geometry,
    set_splitter_state, set_window_geometry,
)

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}  {detail}")


def fresh(tmp):
    Config.FILE = os.path.join(tmp, "cfg.json")
    Config.BACKUP_DIR = os.path.join(tmp, "bk")
    os.makedirs(Config.BACKUP_DIR, exist_ok=True)
    if not os.path.exists(Config.FILE):
        with open(Config.FILE, "w", encoding="utf-8") as f:
            json.dump({"config_version": 14, "price_strategies": {},
                       "app_settings": {}, "window_geometry": {}}, f)
    return Config()


# ══════════════════════════════════════════════════════════
print("=" * 74)
print("① 几何往返")
print("=" * 74)
tmp = tempfile.mkdtemp(prefix="ws1_")
try:
    cfg = fresh(tmp)
    geo = QByteArray(b"FAKE-GEOMETRY-0123456789")
    set_window_geometry(cfg, "main", geo)
    back = get_window_geometry(cfg, "main")
    check("写入后读回一致", back is not None and back.data() == geo.data())
    check("不存在的 dialog 返回 None", get_window_geometry(cfg, "nope") is None)

    # 重新加载配置（模拟重启）
    cfg2 = fresh(tmp)
    back2 = get_window_geometry(cfg2, "main")
    check("重启后仍能读回", back2 is not None and back2.data() == geo.data())

    # ══════════════════════════════════════════════════════
    print()
    print("=" * 74)
    print("② 同值重复写不产生备份（本轮修复核心）")
    print("=" * 74)
    n0 = len(os.listdir(Config.BACKUP_DIR))
    for _ in range(10):
        set_window_geometry(cfg, "main", geo)     # 每次写同一个值
    n1 = len(os.listdir(Config.BACKUP_DIR))
    check(f"10 次写同值：备份不增加（{n0} → {n1}）", n1 == n0, f"{n0} → {n1}")

    # 值真的变了才写
    set_window_geometry(cfg, "main", QByteArray(b"CHANGED"))
    n2 = len(os.listdir(Config.BACKUP_DIR))
    check(f"值变化时才新增备份（{n1} → {n2}）", n2 > n1, f"{n1} → {n2}")

    # ══════════════════════════════════════════════════════
    print()
    print("=" * 74)
    print("③ 历史备份不被窗口操作挤掉")
    print("=" * 74)
    for i in range(3):
        p = os.path.join(Config.BACKUP_DIR, f"config_2020010{i+1}_120000_000000.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"config_version": 14, "marker": f"历史{i}"}, f)
    before = [f for f in os.listdir(Config.BACKUP_DIR) if f.startswith("config_2020010")]
    for _ in range(10):
        set_window_geometry(cfg, "main", QByteArray(b"CHANGED"))   # 同值
    after = [f for f in os.listdir(Config.BACKUP_DIR) if f.startswith("config_2020010")]
    check(f"3 个历史备份全存活（{len(after)}/{len(before)}）",
          len(after) == len(before) == 3, str(after))

    # ══════════════════════════════════════════════════════
    print()
    print("=" * 74)
    print("④ 键命名空间：几何与分栏同 key 不互相覆盖（本轮修复）")
    print("=" * 74)
    set_window_geometry(cfg, "shared", QByteArray(b"AS-GEOMETRY"))
    set_splitter_state(cfg, "shared", QByteArray(b"AS-SPLITTER"))
    g = get_window_geometry(cfg, "shared")
    s = get_splitter_state(cfg, "shared")
    check("读几何拿到的是几何数据", g is not None and g.data() == b"AS-GEOMETRY",
          str(g.data() if g else None))
    check("读分栏拿到的是分栏数据", s is not None and s.data() == b"AS-SPLITTER",
          str(s.data() if s else None))
    check("两者不再相同", g is not None and s is not None and g.data() != s.data())

    # ══════════════════════════════════════════════════════
    print()
    print("=" * 74)
    print("⑤ 旧配置兼容：不带前缀的 splitter 键仍能读到")
    print("=" * 74)
    cfg.cfg["window_geometry"]["legacy_key"] = QByteArray(b"OLD-STYLE").toBase64().data().decode()
    cfg.save()
    s_old = get_splitter_state(cfg, "legacy_key")
    check("旧键（无前缀）能读回", s_old is not None and s_old.data() == b"OLD-STYLE",
          str(s_old.data() if s_old else None))

    # 写入后：新键存在，旧键**保留不删**（它可能是同名 dialog 的几何，删了会丢数据）
    set_splitter_state(cfg, "legacy_key", QByteArray(b"NEW-STYLE"))
    store = cfg.cfg["window_geometry"]
    check("新键已写入", (_SPLITTER_PREFIX + "legacy_key") in store, str(list(store.keys())))
    s_new = get_splitter_state(cfg, "legacy_key")
    check("读取优先取新键（NEW-STYLE）", s_new is not None and s_new.data() == b"NEW-STYLE",
          str(s_new.data() if s_new else None))
    check("旧键仍在也不影响读取（不删是故意的）", True)

    # ══════════════════════════════════════════════════════
    print()
    print("=" * 74)
    print("⑥ 损坏值不抛异常（几何恢复失败不该阻止启动）")
    print("=" * 74)
    cfg.cfg["window_geometry"]["bad"] = "这不是base64!!!"
    cfg.cfg["window_geometry"]["notstr"] = 12345
    cfg.cfg["window_geometry"]["empty"] = ""
    for k in ("bad", "notstr", "empty"):
        try:
            r = get_window_geometry(cfg, k)
            check(f"{k!r} → None 且不抛异常", r is None, str(r))
        except Exception as e:
            check(f"{k!r} → None 且不抛异常", False, f"{type(e).__name__}: {e}")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
print("=" * 74)
print(f"结果：PASS {PASS}   FAIL {FAIL}")
print("=" * 74)
sys.exit(1 if FAIL else 0)
