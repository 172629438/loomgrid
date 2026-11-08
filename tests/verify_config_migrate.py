# -*- coding: utf-8 -*-
"""配置迁移：旧格式升级、启动不写盘、备份不被挤。

为什么这条重要
--------------
迁移错了的表现是「升级后配置全丢」或「策略看着在、实际没用」。
而且它不是每次启动都会走——只有从旧版本升上来的那一次，所以平时测不出来。

本轮修掉两条：
  ① _migrate 末尾无条件 self.save()，每次启动都写盘 + 生成一个备份。
     备份上限 5 个，启动 10 次就能把「改配置前的历史备份」全挤掉——
     而那些正是配置坏了要回滚时唯一的依靠。实测：3 个历史备份剩 0。
  ② 判「旧格式」原来是看 `'type' not in strat_data`。若旧策略里恰好带一个
     type 键（手改过 / 某历史版本写入的），会被当成新格式原样保留，
     后续 get_periods_for_month 取不到 groups → 静默返回 0 条时段。
     「看着在、实际没用」。改为看 `'groups' not in`。

跑法：python tests/verify_config_migrate.py
"""
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
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


def setup(tmp, cfg_dict):
    Config.FILE = os.path.join(tmp, "cfg.json")
    Config.BACKUP_DIR = os.path.join(tmp, "bk")
    os.makedirs(Config.BACKUP_DIR, exist_ok=True)
    with open(Config.FILE, "w", encoding="utf-8") as f:
        json.dump(cfg_dict, f, ensure_ascii=False)


# ══════════════════════════════════════════════════════════
print("=" * 74)
print("① 旧格式迁移：{月份: periods} → {type:'province', groups:[...]}")
print("=" * 74)
tmp = tempfile.mkdtemp(prefix="mg1_")
try:
    setup(tmp, {
        "config_version": 5,
        "price_strategies": {
            "河南": {
                "7": [{"start": 0, "end": 8, "type": "charge"},
                      {"start": 10, "end": 12, "type": "discharge"}],
                "8": [{"start": 0, "end": 8, "type": "charge"}],
            }
        },
        "active_strategy": "河南",
    })
    c = Config()
    ps = c.cfg.get("price_strategies", {})
    h = ps.get("河南", {})
    check("策略名保留", list(ps.keys()) == ["河南"], str(list(ps.keys())))
    check("type 变成 province", h.get("type") == "province", str(h.get("type")))
    check("生成 2 个 group（每月一组）", len(h.get("groups", [])) == 2,
          str(len(h.get("groups", []))))
    check("config_version 升到 14", c.cfg.get("config_version") == 14)
    check("active_province 从 active_strategy 补上",
          c.cfg.get("active_province") == "河南", str(c.cfg.get("active_province")))
    check("能取到 7 月时段（2 条）", len(c.get_periods_for_month("河南", 7)) == 2,
          str(len(c.get_periods_for_month("河南", 7))))
    check("能取到 8 月时段（1 条）", len(c.get_periods_for_month("河南", 8)) == 1)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ══════════════════════════════════════════════════════════
print()
print("=" * 74)
print("② 启动不写盘、不产生备份（本轮修复核心）")
print("=" * 74)
tmp = tempfile.mkdtemp(prefix="mg2_")
try:
    setup(tmp, {"config_version": 14, "price_strategies": {},
                "app_settings": {"last_multiplier": 3.0}})
    Config()                       # 第一次构造（可能补默认值，允许写一次）
    n0 = len(os.listdir(Config.BACKUP_DIR))
    m0 = os.path.getmtime(Config.FILE)
    time.sleep(0.05)
    for _ in range(5):
        Config()                   # 再启动 5 次
    n1 = len(os.listdir(Config.BACKUP_DIR))
    m1 = os.path.getmtime(Config.FILE)
    check(f"启动 5 次不新增备份（{n0} → {n1}）", n1 == n0, f"{n0} → {n1}")
    check("启动 5 次主文件未被改写", m0 == m1, f"{m0} != {m1}")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ══════════════════════════════════════════════════════════
print()
print("=" * 74)
print("③ 历史备份不被启动挤掉")
print("=" * 74)
tmp = tempfile.mkdtemp(prefix="mg3_")
try:
    setup(tmp, {"config_version": 14, "price_strategies": {}, "app_settings": {}})
    Config()
    # 造 3 个「用户改配置前的历史备份」
    for i in range(3):
        p = os.path.join(Config.BACKUP_DIR, f"config_2020010{i+1}_120000_000000.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"config_version": 14, "marker": f"历史{i}"}, f)
    for _ in range(10):
        Config()
    hist = [f for f in os.listdir(Config.BACKUP_DIR) if f.startswith("config_2020010")]
    check(f"3 个历史备份全部存活（实 {len(hist)}）", len(hist) == 3, str(hist))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ══════════════════════════════════════════════════════════
print()
print("=" * 74)
print("④ 旧策略带 type 键仍能迁移（本轮修复）")
print("=" * 74)
tmp = tempfile.mkdtemp(prefix="mg4_")
try:
    setup(tmp, {
        "config_version": 5,
        "price_strategies": {
            "怪策略": {"type": "something",
                       "7": [{"start": 0, "end": 8, "type": "charge"}]}
        },
    })
    c = Config()
    n = len(c.get_periods_for_month("怪策略", 7))
    check(f"带 type 键的旧策略能取到 1 条时段（实 {n}）", n == 1, str(n))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ══════════════════════════════════════════════════════════
print()
print("=" * 74)
print("⑤ 新格式（已有 groups）不被重复迁移")
print("=" * 74)
tmp = tempfile.mkdtemp(prefix="mg5_")
try:
    newfmt = {
        "config_version": 5,          # 版本号旧但结构已是新格式
        "price_strategies": {
            "已是新格式": {"type": "province",
                          "groups": [{"name": "全月", "months": list(range(1, 13)),
                                      "periods": [{"start": 0, "end": 8, "type": "charge"}]}]}
        },
    }
    setup(tmp, newfmt)
    c = Config()
    g = c.cfg["price_strategies"]["已是新格式"]["groups"]
    check("groups 仍是 1 个（没被拆成 12 个）", len(g) == 1, str(len(g)))
    check("months 仍是 1..12", g[0]["months"] == list(range(1, 13)), str(g[0]["months"]))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ══════════════════════════════════════════════════════════
print()
print("=" * 74)
print("⑥ 只读配置文件：能构造、save 返回 False、不崩")
print("=" * 74)
tmp = tempfile.mkdtemp(prefix="mg6_")
try:
    setup(tmp, {"config_version": 14, "price_strategies": {}, "app_settings": {}})
    os.chmod(Config.FILE, 0o444)
    try:
        c = Config()
        check("只读配置下能构造", True)
        check("save 返回 False 而不是抛异常", c.save() is False)
    except Exception as e:
        check("只读配置下能构造", False, f"{type(e).__name__}: {e}")
    finally:
        os.chmod(Config.FILE, 0o666)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
print("=" * 74)
print(f"结果：PASS {PASS}   FAIL {FAIL}")
print("=" * 74)
sys.exit(1 if FAIL else 0)
