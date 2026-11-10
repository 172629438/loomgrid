# -*- coding: utf-8 -*-
"""节假日年份越界：要降级，但必须告警。

chinese_calendar 1.11.0 只覆盖 2004–2026。
越界年份抛 NotImplementedError，原来被 except Exception 吞成 debug，
用户看到的是 2027-10-01（国庆，周五）被当成「工作日」——完全无提示。
"""
import io
import logging
import sys

REPO = r"C:\Users\LENOVO\.hanako\repos\172629438__loomgrid"
sys.path.insert(0, REPO)
sys.stdout.reconfigure(encoding="utf-8")

from core import timegrid as tg

ok, bad = [], []


def check(name, cond, detail=""):
    if cond:
        ok.append(name)
        print(f"  [PASS] {name}")
    else:
        bad.append(name)
        print(f"  [FAIL] {name}   {detail}")


print("=" * 78)
print("① 年份范围探测")
print("=" * 78)
lo, hi = tg._ensure_holiday_year_range()
print(f"  chinese_calendar 覆盖: {lo}–{hi}")
check("探测到范围", lo is not None and hi is not None, f"{lo}–{hi}")
check("范围包含当前年份",
      lo is not None and lo <= 2026 <= (hi or 0), f"{lo}–{hi}")

print()
print("=" * 78)
print("② 范围内：正常判定")
print("=" * 78)
CASES_OK = [
    ("2026-10-01", "holiday", "国庆（周四）"),
    ("2026-01-01", "holiday", "元旦（周四）"),
    ("2026-02-17", "holiday", "春节（周二）"),
    ("2026-06-19", "holiday", "端午（周五）"),
]
for d, expect, desc in CASES_OK:
    got = tg._get_day_type(d, enable_holiday=True)
    print(f"  {d} {desc:16s} → {got}")
    check(f"{desc} 判为 holiday", got == expect, got)

# 普通工作日
r = tg._get_day_type("2026-11-10", enable_holiday=True)   # 周二
print(f"  2026-11-10（周二，普通工作日）→ {r}")
check("普通工作日判为 weekday", r == "weekday", r)

print()
print("=" * 78)
print("③ 越界年份：降级 + 告警")
print("=" * 78)
buf = io.StringIO()
h = logging.StreamHandler(buf)
h.setLevel(logging.WARNING)
lg = logging.getLogger("core.timegrid")
lg.addHandler(h)
lg.setLevel(logging.WARNING)

r2027 = tg._get_day_type("2027-10-01", enable_holiday=True)
lg.removeHandler(h)
msgs = [l for l in buf.getvalue().splitlines() if "节假日数据不包含" in l]

print(f"  2027-10-01 → {r2027}（降级按普通周末）")
print(f"  捕获 {len(msgs)} 条告警")
for m in msgs:
    print(f"    {m[:120]}")
check("越界年份仍给出结果（不抛）", r2027 in ("weekday", "weekend", "holiday"),
      r2027)
check("有明确告警", bool(msgs), "没告警")
if msgs:
    check("告警里写明是 2027 年", "2027" in msgs[0], msgs[0][:60])
    check("告警里写明覆盖范围", "2004" in msgs[0] and "2026" in msgs[0],
          msgs[0][:80])
    check("告警里说明后果（底色与统计不符）", "底色" in msgs[0] or "统计" in msgs[0],
          msgs[0][:80])

print()
print("=" * 78)
print("④ 同一年只警告一次（不刷屏）")
print("=" * 78)
buf2 = io.StringIO()
h2 = logging.StreamHandler(buf2)
h2.setLevel(logging.WARNING)
lg.addHandler(h2)
lg.setLevel(logging.WARNING)
for d in ("2027-01-01", "2027-05-01", "2027-10-01", "2027-12-25"):
    tg._get_day_type(d, enable_holiday=True)
lg.removeHandler(h2)
n2 = len([l for l in buf2.getvalue().splitlines() if "节假日数据不包含" in l])
print(f"  连续判 4 个 2027 年日期，告警 {n2} 条（应为 0，因为 ③ 已警告过）")
check("同一年不重复告警", n2 == 0, f"{n2} 条")

print()
print("=" * 78)
print("⑤ 不启用节假日时不该触发告警")
print("=" * 78)
buf3 = io.StringIO()
h3 = logging.StreamHandler(buf3)
h3.setLevel(logging.WARNING)
lg.addHandler(h3)
lg.setLevel(logging.WARNING)
r = tg._get_day_type("2029-10-01", enable_holiday=False)
lg.removeHandler(h3)
n3 = len([l for l in buf3.getvalue().splitlines() if "节假日数据不包含" in l])
import datetime as _dt
_wd = _dt.date(2029, 10, 1).weekday()
_expect = "weekend" if _wd >= 5 else "weekday"
print(f"  2029-10-01（{'一二三四五六日'[_wd]}，不启用节假日）→ {r}，告警 {n3} 条")
check("不启用时不告警", n3 == 0, f"{n3} 条")
check("不启用时只按 weekday() 判周末", r == _expect, f"{r} vs {_expect}")

print()
print("=" * 78)
print("⑥ 反向证明：旧的 except Exception 会吞掉这是一个专门类型")
print("=" * 78)
import datetime as dt
import chinese_calendar as cc
try:
    cc.is_holiday(dt.date(2027, 10, 1))
    check("旧路径会抛 NotImplementedError", False, "没抛")
except NotImplementedError as e:
    print(f"  NotImplementedError: {str(e)[:70]}")
    check("旧路径会抛 NotImplementedError", True)
    check("异常信息含范围", "2004" in str(e) and "2026" in str(e))

print()
print("=" * 78)
print(f"结果：PASS {len(ok)}   FAIL {len(bad)}")
for b in bad:
    print("  -", b)
print("=" * 78)
sys.exit(1 if bad else 0)
