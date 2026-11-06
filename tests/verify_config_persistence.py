# -*- coding: utf-8 -*-
"""配置持久化：保存/加载/损坏恢复/备份轮转/映射记忆三级匹配。

为什么这条重要
--------------
`Config` 有 44 个方法，此前只有 3 个被测试碰过。而配置坏了的表现是
「双击打开就报错」或「映射全丢要重配一遍」——只在换版本时出现，平时测不出来。

本脚本覆盖 8 组：
  ① 保存 → 重新加载 → 数据一致
  ② 损坏 JSON → 从备份恢复
  ③ 备份轮转（超过上限删最旧）
  ④ 映射记忆第 1 级：文件名模式匹配
  ⑤ 映射记忆第 2 级：列指纹匹配
  ⑥ 映射记忆第 3 级：相似度兜底
  ⑦ 相似度兜底必须校验列存在性（v2.3.15fix 的串位防护）
  ⑧ 嵌套 dict 不实例间共享（P2-9）

跑法：python tests/verify_config_persistence.py
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


def fresh_cfg(tmp):
    """在临时目录里造一个干净的 Config 实例（改类属性，不碰真实配置）。"""
    Config.FILE = os.path.join(tmp, "column_mapping_config.json")
    Config.BACKUP_DIR = os.path.join(tmp, "config_backups")
    os.makedirs(Config.BACKUP_DIR, exist_ok=True)
    return Config()


MAP_A = {
    "date_col": "采集时间", "time_col": "采集时间",
    "load_col": "A线总有功", "asset_col": None,
    "format_type": "long", "header_row": 0, "delimiter": ",",
    "convert_accumulated": False, "transpose": False,
}

# ══════════════════════════════════════════════════════════
print("=" * 78)
print("① 保存 → 重新加载 → 数据一致")
print("=" * 78)
tmp = tempfile.mkdtemp(prefix="cfg1_")
try:
    c1 = fresh_cfg(tmp)
    c1.add_map("曲线导出*.xlsx", "Sheet1", MAP_A)
    c1.set_app("last_multiplier", 2.5)
    ok = c1.save()
    check("save() 返回 True", ok is True)

    c2 = fresh_cfg(tmp)      # 重新读盘
    m = c2.recommend("曲线导出-1-1-2026-07-01,2026-07-31.xlsx", "Sheet1",
                     ["采集时间", "A线总有功"])
    check("重新加载后能推荐回原映射", m is not None and m.get("load_col") == "A线总有功",
          str(m))
    check("app_settings 也持久化了", c2.app_set("last_multiplier", 1.0) == 2.5,
          str(c2.app_set("last_multiplier", 1.0)))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ══════════════════════════════════════════════════════════
print()
print("=" * 78)
print("② 损坏 JSON → 从备份恢复")
print("=" * 78)
tmp = tempfile.mkdtemp(prefix="cfg2_")
try:
    c = fresh_cfg(tmp)
    c.add_map("曲线导出*.xlsx", "Sheet1", MAP_A)
    c.save()                                    # 第一次写，无备份
    c.set_app("last_multiplier", 3.0)
    c.save()                                    # 第二次写，产生备份
    n_bk = len([f for f in os.listdir(Config.BACKUP_DIR) if f.startswith("config_")])
    check(f"备份已生成（{n_bk} 个）", n_bk >= 1)

    # 把主配置写坏
    with open(Config.FILE, "w", encoding="utf-8") as f:
        f.write("{ 这不是合法 JSON ")
    c3 = fresh_cfg(tmp)
    check("损坏后仍能启动（不抛异常）", isinstance(c3.cfg, dict))
    check("restored_from_backup 标记为 True", c3.restored_from_backup is True)
    m = c3.recommend("曲线导出-1-1-2026-08-01,2026-08-31.xlsx", "Sheet1",
                     ["采集时间", "A线总有功"])
    check("恢复后映射还在", m is not None and m.get("load_col") == "A线总有功", str(m))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ══════════════════════════════════════════════════════════
print()
print("=" * 78)
print("③ 备份轮转：超过 MAX_BACKUPS 删最旧")
print("=" * 78)
tmp = tempfile.mkdtemp(prefix="cfg3_")
try:
    c = fresh_cfg(tmp)
    c.add_map("p*.xlsx", "S", MAP_A)
    c.save()
    for i in range(Config.MAX_BACKUPS + 4):
        c.set_app("last_multiplier", float(i + 1))
        c.save()
    n_bk = len([f for f in os.listdir(Config.BACKUP_DIR)
                if f.startswith("config_") and not f.startswith("config_weekly_")])
    check(f"日备份数不超过上限 {Config.MAX_BACKUPS}（实 {n_bk}）",
          n_bk <= Config.MAX_BACKUPS, str(n_bk))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ══════════════════════════════════════════════════════════
print()
print("=" * 78)
print("④ 映射记忆第 1 级：文件名模式匹配")
print("=" * 78)
tmp = tempfile.mkdtemp(prefix="cfg4_")
try:
    c = fresh_cfg(tmp)
    c.add_map("曲线导出*.xlsx", "Sheet1", MAP_A)
    hit = c.recommend("曲线导出-1-1-2026-07-01,2026-07-31.xlsx", "Sheet1",
                      ["采集时间", "A线总有功"])
    check("pattern 命中 → 返回映射", hit is not None and hit.get("load_col") == "A线总有功")
    miss = c.recommend("别的名字.xlsx", "Sheet1", ["完全不相关的列", "另一列"])
    check("pattern 不命中且无相似 → 返回 None", miss is None, str(miss))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ══════════════════════════════════════════════════════════
print()
print("=" * 78)
print("⑤ 映射记忆第 2 级：列指纹匹配")
print("=" * 78)
tmp = tempfile.mkdtemp(prefix="cfg5_")
try:
    c = fresh_cfg(tmp)
    # 存到一个永不匹配的文件名下，逼它走指纹
    c.add_map("ZZZ永不匹配*.xlsx", "Sheet1", MAP_A)
    hit = c.recommend("随便什么名字.xlsx", "Sheet1", ["采集时间", "A线总有功"])
    check("文件名不匹配、但列指纹相同 → 命中", hit is not None, str(hit))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ══════════════════════════════════════════════════════════
print()
print("=" * 78)
print("⑥ 映射记忆第 3 级：相似度兜底")
print("=" * 78)
tmp = tempfile.mkdtemp(prefix="cfg6_")
try:
    c = fresh_cfg(tmp)
    c.add_map("ZZZ永不匹配*.xlsx", "Sheet1", MAP_A)
    # 列名略变但 load_col 仍在：应走相似度命中
    cols = ["采集时间", "A线总有功", "额外无关列"]
    hit = c.recommend("另一个文件.xlsx", "Sheet1", cols)
    check("列都存在 + 相似度高 → 命中", hit is not None and hit.get("load_col") == "A线总有功",
          str(hit))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ══════════════════════════════════════════════════════════
print()
print("=" * 78)
print("⑦ 串位防护：相似度兜底必须校验列存在性（v2.3.15fix）")
print("=" * 78)
tmp = tempfile.mkdtemp(prefix="cfg7_")
try:
    c = fresh_cfg(tmp)
    c.add_map("曲线导出*.xlsx", "Sheet1", MAP_A)
    # 同一采集系统、不同产线：列是 B线，不是 A线
    cols_B = ["采集时间", "B线总有功", "电流A相"]
    hit = c.recommend("曲线导出-1-2-2026-08-01,2026-08-31.xlsx", "Sheet1", cols_B)
    check("A线的映射不会被套给 B线文件", hit is None,
          f"却返回了 load_col={hit.get('load_col') if hit else None}")
    if hit:
        check("返回的映射，其负荷列确实存在于本文件",
              hit.get("load_col") in cols_B, str(hit.get("load_col")))
    # 反向：真拿 B线 的列去问，应该什么都推荐不出来
    check("多次重问结果稳定（不会随机命中）",
          all(c.recommend(f"曲线导出-x{i}.xlsx", "Sheet1", cols_B) is None
              for i in range(3)))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ══════════════════════════════════════════════════════════
print()
print("=" * 78)
print("⑧ 嵌套 dict 不在实例间共享（P2-9）")
print("=" * 78)
tmp = tempfile.mkdtemp(prefix="cfg8_")
try:
    c1 = fresh_cfg(tmp)
    c1.cfg["app_settings"]["last_multiplier"] = 99.0
    # 改类默认值不应该影响已经建好的实例，也不该被实例写坏
    check("类默认值未被实例污染",
          Config.DEFAULT_CONFIG["app_settings"]["last_multiplier"] == 1.0,
          str(Config.DEFAULT_CONFIG["app_settings"]["last_multiplier"]))
    c1.save()
    c2 = fresh_cfg(tmp)
    check("第二个实例读到的是自己保存的值", c2.app_set("last_multiplier", 1.0) == 99.0)
    c2.cfg["app_settings"]["last_multiplier"] = 1.0
    check("改第二个实例不影响第一个", c1.cfg["app_settings"]["last_multiplier"] == 99.0)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
print("=" * 78)
print(f"结果：PASS {PASS}   FAIL {FAIL}")
print("=" * 78)
sys.exit(1 if FAIL else 0)
