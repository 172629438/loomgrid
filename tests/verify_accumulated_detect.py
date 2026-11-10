# -*- coding: utf-8 -*-
"""累计示数检测的双向验证。

正向：各种量程的累计示数表都要识别出来（之前小量程漏判）
反向：各种形态的真实功率曲线都不能被误判成累计示数（误判比漏判更糟——
      真实负荷被差分一次，曲线变成噪声）
"""
import sys

import numpy as np
import pandas as pd

REPO = r"C:\Users\LENOVO\.hanako\repos\172629438__loomgrid"
sys.path.insert(0, REPO)
sys.stdout.reconfigure(encoding="utf-8")

from core.ingest import _detect_accumulated_pattern

ok, bad = [], []


def check(name, cond, detail=""):
    if cond:
        ok.append(name)
        print(f"  [PASS] {name}")
    else:
        bad.append(name)
        print(f"  [FAIL] {name}   {detail}")


def df_of(vals, date="2026-01-01"):
    return pd.DataFrame({
        "日期": [date] * len(vals),
        "时间": [f"{i//4:02d}:{(i%4)*15:02d}" for i in range(len(vals))],
        "负荷": vals,
    })


print("=" * 78)
print("正向：累计示数表必须识别（含小量程）")
print("=" * 78)
ACC = [
    ("1000→1380 每步 4", np.arange(96) * 4 + 1000),
    ("100→500 每步 4.2", np.arange(96) * 4.2 + 100),
    ("50→950 每步 9.4", np.arange(96) * 9.4 + 50),
    ("0→96 每步 1", np.arange(96) * 1.0),
    ("5000→9800 每步 50", np.arange(96) * 50 + 5000),
    ("200→1200 每步 10.4", np.arange(96) * 10.4 + 200),
    ("阶梯增长 0→950", np.repeat(np.arange(0, 1000, 10), 1)[:96] * 1.0),
]
for name, vals in ACC:
    got = _detect_accumulated_pattern(df_of(vals), "负荷", "日期", "时间")
    print(f"  {name:24s} → {'识别 ✓' if got else '漏判 ✗'}")
    check(f"识别累计示数：{name}", got)

print()
print("=" * 78)
print("反向：真实功率曲线绝不能被误判")
print("=" * 78)
rng = np.random.default_rng(7)
POWER = [
    ("恒 500（完全平直）", np.full(96, 500.0)),
    ("双峰 120↔540", np.concatenate([
        np.linspace(120, 540, 32), np.linspace(540, 400, 16),
        np.linspace(400, 540, 16), np.linspace(540, 120, 32)])),
    ("随机 100~600", rng.uniform(100, 600, 96)),
    ("正弦", 300 + 200 * np.sin(np.linspace(0, 4 * np.pi, 96))),
    ("小量程波动 10~30", 20 + 10 * np.sin(np.linspace(0, 6 * np.pi, 96))),
    ("阶跃：白天 500 夜间 100", np.concatenate([np.full(48, 100.0), np.full(48, 500.0)])),
    ("缓慢下降", np.linspace(600, 300, 96)),
    ("低负荷有小尖峰", np.concatenate([np.full(90, 50.0), np.full(6, 200.0)])),
]
for name, vals in POWER:
    got = _detect_accumulated_pattern(df_of(vals), "负荷", "日期", "时间")
    print(f"  {name:24s} → {'误判 ✗' if got else '正常 ✓'}")
    check(f"不误判功率曲线：{name}", not got)

print()
print("=" * 78)
print("边界：数据太少 / 全 NaN / 缺列")
print("=" * 78)
check("少于 10 点不判", not _detect_accumulated_pattern(
    df_of(np.arange(5) * 4.0 + 100), "负荷", "日期", "时间"))
check("全 NaN 不判", not _detect_accumulated_pattern(
    df_of([np.nan] * 96), "负荷", "日期", "时间"))
check("缺 load_col 不抛", not _detect_accumulated_pattern(
    df_of(np.arange(96) * 4.0), "不存在的列", "日期", "时间"))
# 缺日期列时函数走 sort_index() 分支，仍按数值特征判断。
# 这份数据本身就是单调递增的示数形态，返回 True 是正确行为——
# 这里只要求「不抛异常」。
try:
    _r = _detect_accumulated_pattern(df_of(np.arange(96) * 4.0), "负荷", "不存在的列", "时间")
    print(f"  缺日期列 → 返回 {_r}（按索引顺序判断，不抛异常即通过）")
    check("缺日期列不抛", True)
except Exception as e:
    check("缺日期列不抛", False, f"{type(e).__name__}: {e}")

print()
print("=" * 78)
print(f"结果：PASS {len(ok)}   FAIL {len(bad)}")
for b in bad:
    print("  -", b)
print("=" * 78)
sys.exit(1 if bad else 0)
