# -*- coding: utf-8 -*-
"""校验 tou.py 全量数据：19 省 + 新增 14 省 = 33 个省份。

判据（每条都跑真代码，不看代码形态）：
  ① 每个省每个月，如果有时段数据，峰+谷+平 必须正好铺满 24 小时
  ② derive_periods 推出的 charge/discharge 必须有意义（charge 非空、discharge 非空）
  ③ 跨午夜时段能被正确处理（valley 写成 (22, 6) 形式）
  ④ 同一条数据里 charge 与 discharge 不能重叠（同一时刻既充又放是矛盾配置）
"""
import io
import os
import sys

REPO = r"C:\Users\LENOVO\.hanako\repos\172629438__loomgrid"
sys.path.insert(0, REPO)
sys.stdout.reconfigure(encoding="utf-8")

from services.tou import PROVINCE_TOU, derive_periods, list_provinces, periods_for

passed, failed = [], []


def check(name, cond, detail=""):
    if cond:
        passed.append(name)
    else:
        failed.append(name)
        print(f"  [FAIL] {name}  {detail}")


def _expand(ivs):
    out = []
    for a, b in ivs:
        a, b = float(a), float(b)
        if b > a:
            out.append((a, b))
        elif b < a:
            out.append((a, 24.0))
            out.append((0.0, b))
    return out


def _merge(ivs):
    flat = sorted(_expand(ivs))
    if not flat:
        return []
    out = [list(flat[0])]
    for a, b in flat[1:]:
        if a <= out[-1][1] + 1e-9:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


print("=" * 74)
print(f"省份总数：{len(PROVINCE_TOU)}")
for p in list_provinces():
    print(f"  · {p}")
print("=" * 74)

print()
print("① 24 小时覆盖（逐省逐月）")
bad_cover = []
for prov, info in PROVINCE_TOU.items():
    for m in range(1, 13):
        tou = info["months"].get(str(m))
        if not tou:
            continue          # 明确留空的（贵州/西藏枯水期）不算错
        peak = _merge(list(tou.get("peak") or []) + list(tou.get("sharp") or []))
        valley = _merge(list(tou.get("valley") or []) + list(tou.get("deep") or []))
        flat = _merge(tou.get("flat") or [])
        segs = sorted([(a, b) for a, b in peak] +
                      [(a, b) for a, b in valley] +
                      [(a, b) for a, b in flat])
        cur, total, probs = 0.0, 0.0, []
        for a, b in segs:
            if a > cur + 1e-6:
                probs.append(f"缺口{cur}-{a}")
            elif a < cur - 1e-6:
                probs.append(f"互盖{a}-{min(b,cur)}")
            cur = max(cur, b)
            total += b - a
        if cur < 24 - 1e-6:
            probs.append(f"尾缺{cur}-24")
        if abs(total - 24) > 1e-6:
            probs.append(f"合计{total}")
        if probs:
            bad_cover.append(f"{prov} {m}月: {'/'.join(probs)}")

check(f"所有有时段数据的省月都铺满 24h（{len(bad_cover)} 处异常）",
      not bad_cover, str(bad_cover[:5]))

print()
print("② derive_periods 推出的时段有意义")
# 西藏丰水期是政策原文写明「无高峰时段」，所以 5-10 月本来就只有充电没有放电。
# 把它当成例外列为已知情况，而不是把断言放宽到没意义。
KNOWN_NO_DISCHARGE = {("西藏", m) for m in (5, 6, 7, 8, 9, 10)}
bad_derive = []
for prov, info in PROVINCE_TOU.items():
    for m in range(1, 13):
        if not info["months"].get(str(m)):
            continue
        ps = periods_for(prov, m)
        types = {p["type"] for p in ps}
        if "charge" not in types:
            bad_derive.append(f"{prov} {m}月 无 charge")
        if "discharge" not in types and (prov, m) not in KNOWN_NO_DISCHARGE:
            bad_derive.append(f"{prov} {m}月 无 discharge")
        for p in ps:
            if not (0 <= p["start"] < 24 and 0 < p["end"] <= 24):
                bad_derive.append(f"{prov} {m}月 越界 {p}")

check(f"每省每月都能推出 charge（{len(bad_derive)} 处异常）",
      not bad_derive, str(bad_derive[:5]))
check("西藏丰水期确实无放电（政策原文写明无高峰）",
      all("discharge" not in {p["type"] for p in periods_for("西藏", m)}
          for m in (5, 6, 7, 8, 9, 10)))

print()
print("③ 跨午夜处理")
# derive_periods 对跨午夜区间保留 (22, 6) 这种原形式，不拆成两段——下游
# _segment_periods 本身就能处理 start > end。所以断言只要求「两种形式之一」。
cross = []
for prov, info in PROVINCE_TOU.items():
    for m in range(1, 13):
        tou = info["months"].get(str(m))
        if not tou:
            continue
        # 只查四个真参与推导的类型：valley/deep → charge，peak/sharp → discharge。
        # flat 不查——derive_periods 只在「该季节无谷段」时才用平段充电，
        # 有谷段时平段本就不进输出，这是设计不是丢失。
        for k in ("valley", "deep", "peak", "sharp"):
            for a, b in tou.get(k) or []:
                if b < a:      # 跨午夜区间
                    ps = periods_for(prov, m)
                    typ = "charge" if k in ("valley", "deep") else "discharge"
                    keep = any(abs(p["start"] - a) < 1e-6 and abs(p["end"] - b) < 1e-6
                               and p["type"] == typ for p in ps)
                    split_tail = any(abs(p["start"] - a) < 1e-6 and abs(p["end"] - 24) < 1e-6
                                     and p["type"] == typ for p in ps)
                    split_head = any(abs(p["start"]) < 1e-6 and abs(p["end"] - b) < 1e-6
                                     and p["type"] == typ for p in ps)
                    if not (keep or split_tail or split_head):
                        cross.append(f"{prov} {m}月 {k}({a},{b}) 丢失")
check(f"跨午夜区间被保留或正确拆分（{len(cross)} 处异常）", not cross, str(cross[:5]))

print()
print("④ charge 与 discharge 不重叠")
overlap = []
for prov, info in PROVINCE_TOU.items():
    for m in range(1, 13):
        if not info["months"].get(str(m)):
            continue
        ps = periods_for(prov, m)
        ch = _merge([(p["start"], p["end"]) for p in ps if p["type"] == "charge"])
        di = _merge([(p["start"], p["end"]) for p in ps if p["type"] == "discharge"])
        for a1, b1 in ch:
            for a2, b2 in di:
                if min(b1, b2) - max(a1, a2) > 1e-6:
                    overlap.append(f"{prov} {m}月 充({a1},{b1})×放({a2},{b2})")
check(f"无同时充放（{len(overlap)} 处异常）", not overlap, str(overlap[:5]))

print()
print("⑤ 缺月份的省，periods_for 返回空而不是报错")
for prov in ("贵州",):
    check(f"{prov} 全月返回空（时段未查到）",
          all(periods_for(prov, m) == [] for m in range(1, 13)))
check("西藏枯水期返回空", all(periods_for("西藏", m) == [] for m in (1, 2, 3, 4, 11, 12)))
check("西藏丰水期有数据", len(periods_for("西藏", 7)) > 0)

print()
print("⑥ 每年至少有一个可用的省份数（回归保护）")
usable = [p for p in list_provinces()
          if all(periods_for(p, m) for m in range(1, 13))]
print(f"  全年 12 个月都有时段的省份：{len(usable)} / {len(list_provinces())}")
check("可用省份 >= 30", len(usable) >= 30, f"只有 {len(usable)} 个")

print()
print("=" * 74)
print(f"结果：PASS {len(passed)}   FAIL {len(failed)}")
for f in failed:
    print("  -", f)
print("=" * 74)
sys.exit(1 if failed else 0)
