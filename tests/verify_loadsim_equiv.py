# -*- coding: utf-8 -*-
"""数值等价：services/loadsim.py（新）vs 参考程序.py 内嵌原函数（旧）。

判据
----
① 同 config + 同 seed           -> 两个 load_array 逐元素相等（atol=0）
   seed 对齐方式：旧函数只有全局 np.random，所以先 np.random.seed(s) 再调旧函数；
   新函数传 seed=s（内部 RandomState(s)），两条随机序列相同。
② seed=None（新）/ 不设种子（旧）-> 分布一致：都非负、峰值量级接近、均值接近
③ 25 个模板键完全一致，字段个数与取值一致
④ pv_kw > 0 的光伏叠加路径也参与比对
⑤ 边界：未知行业回退、n_points = 24 / 48 / 96

跑法：python tests/verify_loadsim_equiv.py
"""
import io
import os
import re
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from services import loadsim  # noqa: E402

FUSION = os.path.join(ROOT, "参考程序.py")

PASS = []
FAIL = []


def check(name, ok, detail=""):
    if ok:
        PASS.append(name)
        print(f"  PASS  {name}  {detail}")
    else:
        FAIL.append(name)
        print(f"  FAIL  {name}  {detail}")


def load_legacy():
    """从 Fusion 主程序里取出原版模板与函数，在独立命名空间 exec。"""
    text = io.open(FUSION, encoding="utf-8", errors="ignore").read()
    start = text.find("INDUSTRY_TEMPLATES = {")
    end = text.find("def extract_load_features(")
    block = text[start:end]
    ns = {"__name__": "legacy_loadsim"}
    exec(compile(block, "<legacy_loadsim>", "exec"), ns)
    return ns


print("[0] 载入原版")
legacy = load_legacy()
check("原版模板数 25", len(legacy["INDUSTRY_TEMPLATES"]) == 25,
      f"实际 {len(legacy['INDUSTRY_TEMPLATES'])}")

print("[1] 模板定义一致性")
check("模板键集合一致",
      set(legacy["INDUSTRY_TEMPLATES"]) == set(loadsim.INDUSTRY_TEMPLATES),
      f"新 {len(loadsim.INDUSTRY_TEMPLATES)} / 旧 {len(legacy['INDUSTRY_TEMPLATES'])}")

diff_fields = []
for k in legacy["INDUSTRY_TEMPLATES"]:
    if legacy["INDUSTRY_TEMPLATES"][k] != loadsim.INDUSTRY_TEMPLATES.get(k):
        diff_fields.append(k)
check("25 项字段值逐一相等", not diff_fields, f"不一致: {diff_fields}")

print("[2] 同 seed 数值等价（10 个模板）")
TEMPLATES = ["电解铝", "铁合金", "钢铁(电炉)", "化工(连续)", "造纸",
             "水泥(粉磨)", "纺织/印染", "制药(原料药)", "半导体封装",
             "机械加工(单班)", "商业楼宇", "数据中心", "医院", "居民小区", "冷链物流"]

for ind in TEMPLATES:
    cfg = {"industry": ind, "avg_power_kw": 800.0, "n_days": 20, "n_points": 96,
           "noise_std": 0.05}
    seed = 20261104

    np.random.seed(seed)
    old = legacy["generate_load_profile"](dict(cfg))
    new = loadsim.generate_load_profile(dict(cfg), seed=seed)

    same = np.array_equal(old["load_array"], new["load_array"])
    maxdiff = float(np.max(np.abs(old["load_array"] - new["load_array"])))
    check(f"同 seed 逐元素相等 · {ind}", same, f"maxdiff={maxdiff:.3e}")

print("[3] 无 seed 分布一致")
for ind in ("电解铝", "商业楼宇", "机械加工(单班)"):
    cfg = {"industry": ind, "avg_power_kw": 500.0, "n_days": 30, "n_points": 96,
           "noise_std": 0.05}
    old = legacy["generate_load_profile"](dict(cfg))["load_array"]
    new = loadsim.generate_load_profile(dict(cfg))["load_array"]
    ok = (np.all(new >= 0)
          and abs(new.mean() - old.mean()) / max(old.mean(), 1e-9) < 0.05
          and abs(new.max() - old.max()) / max(old.max(), 1e-9) < 0.10)
    check(f"均值/峰值/非负 · {ind}", ok,
          f"均值 {old.mean():.1f}→{new.mean():.1f} 峰 {old.max():.1f}→{new.max():.1f}")

print("[4] 光伏叠加路径")
cfg = {"industry": "电子制造", "avg_power_kw": 400.0, "n_days": 15, "n_points": 96,
       "noise_std": 0.03, "pv_kw": 300.0}
np.random.seed(7)
old = legacy["generate_load_profile"](dict(cfg))
new = loadsim.generate_load_profile(dict(cfg), seed=7)
check("pv_kw=300 逐元素相等", np.array_equal(old["load_array"], new["load_array"]),
      f"maxdiff={float(np.max(np.abs(old['load_array'] - new['load_array']))):.3e}")

# 光伏叠加是否真的生效：同 seed 下带 pv 的均值必须低于不带 pv 的。
# 注意：avg_power=400 时午间负荷仍高于 pv_kw=300，**不会倒送**——
# 早先这条断言写的是「min<0」，是把「叠加生效」与「pv 大到能倒送」混为一谈。
base = loadsim.generate_load_profile(
    {"industry": "电子制造", "avg_power_kw": 400.0, "n_days": 15,
     "n_points": 96, "noise_std": 0.03, "pv_kw": 0.0}, seed=7)["load_array"]
check("pv 叠加真的生效（均值下降）", float(new["load_array"].mean()) < float(base.mean()),
      f"{base.mean():.1f} -> {new['load_array'].mean():.1f}")

# 倒送路径：pv 大于午间负荷时才会出现负值。
big = loadsim.generate_load_profile(
    {"industry": "电子制造", "avg_power_kw": 200.0, "n_days": 10,
     "n_points": 96, "noise_std": 0.0, "pv_kw": 900.0}, seed=3)["load_array"]
check("pv 足够大时午间出现倒送（min < 0）", float(big.min()) < 0,
      f"min={float(big.min()):.1f}")

print("[5] 边界")
cfg = {"industry": "不存在的行业", "avg_power_kw": 300.0, "n_days": 5, "n_points": 96,
       "noise_std": 0.0}
old = legacy["generate_load_profile"](dict(cfg))
new = loadsim.generate_load_profile(dict(cfg), seed=1)
check("未知行业回退到同一模板", np.array_equal(old["load_array"], new["load_array"]),
      f"回落 {cfg['industry']}")

for npnt in (24, 48, 96):
    cfg = {"industry": "造纸", "avg_power_kw": 600.0, "n_days": 7, "n_points": npnt,
           "noise_std": 0.0}
    old = legacy["generate_load_profile"](dict(cfg))
    new = loadsim.generate_load_profile(dict(cfg), seed=2)
    check(f"n_points={npnt} 等价",
          np.array_equal(old["load_array"], new["load_array"])
          and len(new["time_labels"]) == npnt,
          f"labels={len(new['time_labels'])}")

check("time_labels 首尾正确",
      loadsim.generate_load_profile({"n_days": 1, "n_points": 96})["time_labels"][:2] == ["00:00", "00:15"],
      "00:00 / 00:15")

print()
print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
if FAIL:
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
