# -*- coding: utf-8 -*-
"""v2.3.18：内置省份分时电价 → 充放电策略推导。

业务口径（用户明确）：
  · 峰时段全部放电，谷时段全部充电
  · 尖峰按峰处理（同样放电）
  · 只在「完全无谷段」时才评估峰平价差，够大则平段充电
  · 有谷段时平段一律不参与

这组测试锁四件事：
  ① 推导规则本身（谷充 / 峰放 / 平段默认不动）
  ② 无谷段时的平段兜底（价差阈值两个方向都验）
  ③ 内置数据的完整性（每月都有策略、时段不重叠、至少一充一放）
  ④ Config 接入（内置省份能被列出、能取到 groups、用户自建优先）
"""
import sys
import threading

REPO = r"C:\Users\LENOVO\.hanako\repos\172629438__loomgrid"
if REPO not in sys.path:
    sys.path.insert(0, REPO)
sys.stdout.reconfigure(encoding="utf-8")

from services.config import Config  # noqa: E402
from services.tou import PROVINCE_TOU, derive_periods, periods_for  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}" + (f"  {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"[FAIL] {name}  {detail}")


def seg(p):
    return [(x["start"], x["end"], x["type"]) for x in p]


print("=" * 78)
print("① 推导规则：谷充峰放，平段默认不动")
print("=" * 78)
t1 = {
    "peak": [(14, 22)],
    "flat": [(6, 11), (13, 14), (22, 24)],
    "valley": [(11, 13), (0, 6)],
    "ratios": {"peak": 1.8, "flat": 1.0, "valley": 0.35},
}
r = seg(derive_periods(t1))
check("谷段 → charge（两段）",
      (0.0, 6.0, "charge") in r and (11.0, 13.0, "charge") in r, str(r))
check("峰段 → discharge", (14.0, 22.0, "discharge") in r)
check("平段不参与（共 3 段，不是 6 段）", len(r) == 3, f"{len(r)} 段")

print()
print("=" * 78)
print("② 尖峰按峰处理")
print("=" * 78)
t2 = {
    "sharp": [(20, 22)],
    "peak": [(8, 11), (17, 20)],
    "flat": [(0, 8), (11, 17), (22, 24)],
    "valley": [(0, 8)],
    "ratios": {"sharp": 2.0, "peak": 1.7, "flat": 1.0, "valley": 0.3},
}
r = seg(derive_periods(t2))
dis = [x for x in r if x[2] == "discharge"]
# 17-20（峰）与 20-22（尖峰）会合并成 17-22，所以不能直接找 (20,22)
def _covered(intervals, a, b):
    """检查 [a,b] 是否被 intervals 中某个区间完整覆盖。"""
    return any(s <= a + 1e-9 and e >= b - 1e-9 for s, e, _ in intervals)

check("尖峰 20-22 时段在放电覆盖内", _covered(dis, 20.0, 22.0), str(dis))
check("常规峰 8-11 在放电内", _covered(dis, 8.0, 11.0), str(dis))
check("常规峰 17-20 在放电内", _covered(dis, 17.0, 20.0), str(dis))

print()
print("=" * 78)
print("③ 无谷段：按峰平价差决定平段是否充电")
print("=" * 78)
no_valley = {
    "peak": [(8, 11), (17, 22)],
    "flat": [(0, 8), (11, 17), (22, 24)],
    "valley": [],
    "ratios": {"peak": 1.60, "flat": 1.0},
}
# 峰平倍数差 0.60
r_low = seg(derive_periods(no_valley, flat_spread_threshold=0.30))
check("阈值 0.30 < 价差 0.60 → 平段充电",
      any(x[2] == "charge" for x in r_low), str(r_low[:3]))
r_high = seg(derive_periods(no_valley, flat_spread_threshold=0.80))
check("阈值 0.80 > 价差 0.60 → 不充电（返回空）",
      len(r_high) == 0, str(r_high))
# 边界：价差正好等于阈值
r_eq = seg(derive_periods(no_valley, flat_spread_threshold=0.60))
check("阈值等于价差 → 取等号，充电", any(x[2] == "charge" for x in r_eq))
# 无谷段且价差不够时，连放电也不给（没有充电来源）
check("价差不够时放电也为空（没充就没得放）", len(r_high) == 0)

print()
print("=" * 78)
print("④ 内置数据完整性")
print("=" * 78)
for name, spec in PROVINCE_TOU.items():
    print(f"\n{name}")
    check(f"  有 source", bool(spec.get("source")), spec.get("source", "")[:40])
    check(f"  有 checked", bool(spec.get("checked")), spec.get("checked", ""))
    months = spec.get("months") or {}
    check(f"  覆盖 12 个月", len(months) == 12, f"{len(months)} 个月")
    all_ok = True
    for m, tou in months.items():
        ps = derive_periods(tou)
        if not ps:
            all_ok = False
            print(f"    {m} 月无策略")
            continue
        has_c = any(p["type"] == "charge" for p in ps)
        has_d = any(p["type"] == "discharge" for p in ps)
        if not (has_c and has_d):
            all_ok = False
            print(f"    {m} 月缺充或缺放")
        # 检查同向时段不重叠
        for t in ("charge", "discharge"):
            iv = sorted((p["start"], p["end"]) for p in ps if p["type"] == t)
            for i in range(len(iv) - 1):
                a0, a1 = iv[i]
                b0, b1 = iv[i + 1]
                if b0 < a1 and not (a0 > a1):   # 不跨午夜时才算重叠
                    all_ok = False
                    print(f"    {m} 月 {t} 时段重叠: {iv[i]} / {iv[i+1]}")
    check(f"  每月都有充有放、时段不重叠", all_ok)

print()
print("=" * 78)
print("⑤ Config 接入")
print("=" * 78)
c = Config.__new__(Config)
c._lock = threading.RLock()
c.cfg = {"price_strategies": {}}
listed = c.provinces()
check(f"内置省份能列出来（{len(listed)} 个）", len(listed) >= 1, str(listed))
for name in listed:
    g = c.get_province_groups(name)
    check(f"  {name} 能取到 groups", len(g) >= 1, f"{len(g)} 组")
    total_months = sum(len(x["months"]) for x in g)
    check(f"  {name} 月份合计 = 12", total_months == 12, f"{total_months}")

# 用户自建覆盖内置
c.cfg["price_strategies"]["江苏省"] = {
    "type": "province",
    "groups": [{"name": "我的自定义", "months": [7],
                "periods": [{"start": 0.0, "end": 4.0, "type": "charge"}]}],
}
g = c.get_province_groups("江苏省")
check("用户自建的优先于内置",
      len(g) == 1 and g[0]["name"] == "我的自定义", str(g[0].get("name")))

print()
print("=" * 78)
print("⑥ periods_for 便捷入口")
print("=" * 78)
for name in PROVINCE_TOU:
    for m in (1, 7):
        ps = periods_for(name, m)
        check(f"  {name} {m}月 → {len(ps)} 段", len(ps) > 0)
    break   # 只验第一个省，其余在④里已全覆盖

print()
print("=" * 76)
print(f"结果：PASS {PASS}   FAIL {FAIL}")
print("=" * 76)
sys.exit(0 if FAIL == 0 else 1)
