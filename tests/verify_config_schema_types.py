# -*- coding: utf-8 -*-
"""新校验器的向后兼容验证：现有配置、内置省份、各种边界都不能误杀。"""
import json
import os
import sys

REPO = r"C:\Users\LENOVO\.hanako\repos\172629438__loomgrid"
sys.path.insert(0, REPO)
sys.stdout.reconfigure(encoding="utf-8")

from services.config import _validate_config_schema, Config, _is_number
from services.tou import PROVINCE_TOU

ok_n, bad_n = [], []


def check(name, cond, detail=""):
    if cond:
        ok_n.append(name)
        print(f"  [PASS] {name}")
    else:
        bad_n.append(name)
        print(f"  [FAIL] {name}   {detail}")


print("=" * 78)
print("① 真实配置文件（本机现存）")
print("=" * 78)
cfg_path = Config.FILE
print(f"  {cfg_path}")
if os.path.exists(cfg_path):
    with open(cfg_path, encoding="utf-8") as f:
        real = json.load(f)
    n_prov = len(real.get("price_strategies", {}))
    n_grp = sum(len(v.get("groups", []))
                for v in real.get("price_strategies", {}).values())
    print(f"  {n_prov} 个省、{n_grp} 个分组")
    check("现有配置通过新校验", _validate_config_schema(real))
else:
    print("  （文件不存在，跳过）")

print()
print("=" * 78)
print("② 33 个内置省份转成 groups 形态")
print("=" * 78)
converted = {"price_strategies": {}, "app_settings": {}}
for prov, data in PROVINCE_TOU.items():
    groups = []
    for mon_str, tou in (data.get("months") or {}).items():
        periods = []
        for typ, key in (("peak", "peak"), ("valley", "valley"),
                         ("flat", "flat"), ("sharp", "sharp"),
                         ("deep", "deep")):
            for a, b in (tou.get(key) or []):
                periods.append({"type": typ, "start": float(a),
                                "end": float(b)})
        groups.append({"name": f"{mon_str}月", "months": [int(mon_str)],
                       "periods": periods})
    converted["price_strategies"][prov] = {"groups": groups}
check(f"33 省转换后通过校验（{len(converted['price_strategies'])} 个）",
      _validate_config_schema(converted))

print()
print("=" * 78)
print("③ 合法边界（不能误杀）")
print("=" * 78)
CASES_OK = {
    "空 groups": {"price_strategies": {"测": {"groups": []}}, "app_settings": {}},
    "无 price_strategies": {"app_settings": {}},
    "空 periods": {"price_strategies": {"测": {"groups": [
        {"name": "夏", "months": [6], "periods": []}]}}, "app_settings": {}},
    "start=0 end=24": {"price_strategies": {"测": {"groups": [
        {"name": "夏", "months": [1, 12], "periods": [
            {"type": "valley", "start": 0, "end": 24}]}]}}, "app_settings": {}},
    "float 时刻 0.25": {"price_strategies": {"测": {"groups": [
        {"name": "夏", "months": [6], "periods": [
            {"type": "peak", "start": 8.25, "end": 12.75}]}]}},
        "app_settings": {}},
    "跨午夜 23→7": {"price_strategies": {"测": {"groups": [
        {"name": "夏", "months": [6], "periods": [
            {"type": "valley", "start": 23, "end": 7}]}]}},
        "app_settings": {}},
    "period 带额外字段": {"price_strategies": {"测": {"groups": [
        {"name": "夏", "months": [6], "periods": [
            {"type": "peak", "start": 8, "end": 12, "note": "x"}]}]}},
        "app_settings": {}},
    "group 带额外字段": {"price_strategies": {"测": {"groups": [
        {"name": "夏", "months": [6], "periods": [], "color": "#fff"}]}},
        "app_settings": {}},
    "全 12 个月": {"price_strategies": {"测": {"groups": [
        {"name": "全年", "months": list(range(1, 13)), "periods": []}]}},
        "app_settings": {}},
}
for name, cfg in CASES_OK.items():
    check(f"合法：{name}", _validate_config_schema(cfg))

print()
print("=" * 78)
print("④ 非法边界（必须拦住）")
print("=" * 78)
CASES_BAD = {
    "月份 0": {"price_strategies": {"测": {"groups": [
        {"name": "夏", "months": [0], "periods": []}]}}, "app_settings": {}},
    "月份 13": {"price_strategies": {"测": {"groups": [
        {"name": "夏", "months": [13], "periods": []}]}}, "app_settings": {}},
    "月份是字符串": {"price_strategies": {"测": {"groups": [
        {"name": "夏", "months": ["6"], "periods": []}]}}, "app_settings": {}},
    "月份是 bool True": {"price_strategies": {"测": {"groups": [
        {"name": "夏", "months": [True], "periods": []}]}}, "app_settings": {}},
    "name 是空串": {"price_strategies": {"测": {"groups": [
        {"name": "  ", "months": [6], "periods": []}]}}, "app_settings": {}},
    "name 是数字": {"price_strategies": {"测": {"groups": [
        {"name": 1, "months": [6], "periods": []}]}}, "app_settings": {}},
    "start 越界 25": {"price_strategies": {"测": {"groups": [
        {"name": "夏", "months": [6], "periods": [
            {"type": "peak", "start": 25, "end": 26}]}]}},
        "app_settings": {}},
    "start 负数": {"price_strategies": {"测": {"groups": [
        {"name": "夏", "months": [6], "periods": [
            {"type": "peak", "start": -1, "end": 12}]}]}},
        "app_settings": {}},
    "start 是 bool": {"price_strategies": {"测": {"groups": [
        {"name": "夏", "months": [6], "periods": [
            {"type": "peak", "start": True, "end": 12}]}]}},
        "app_settings": {}},
    "type 是数字": {"price_strategies": {"测": {"groups": [
        {"name": "夏", "months": [6], "periods": [
            {"type": 1, "start": 8, "end": 12}]}]}}, "app_settings": {}},
    "period 缺 end": {"price_strategies": {"测": {"groups": [
        {"name": "夏", "months": [6], "periods": [
            {"type": "peak", "start": 8}]}]}}, "app_settings": {}},
}
for name, cfg in CASES_BAD.items():
    check(f"非法：{name}", not _validate_config_schema(cfg))

print()
print("=" * 78)
print(f"结果：PASS {len(ok_n)}   FAIL {len(bad_n)}")
for b in bad_n:
    print("  -", b)
print("=" * 78)
sys.exit(1 if bad_n else 0)
