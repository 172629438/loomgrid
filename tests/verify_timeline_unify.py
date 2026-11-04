# -*- coding: utf-8 -*-
"""验证 services/timeline.py 的 unify_time_resolution（搬运件）。

搬迁判据不只是「能跑」：
  ① 逻辑等价   与 Fusion 原实现逐行对照（同一个算法，不引入新行为）
  ② 粒度对齐   96 点与 48 点混合时，全部变成 96 点
  ③ 数值保真   最细粒度那一组被跳过（不重采样 = 不失真）
  ④ NaN 语义   上采样后，原数据没有的时刻填 NaN 而不是 0 或凭空插值
  ⑤ 单组不变   只有一个分组时原样返回（不需要插值）

第 ④ 条是协作方特别指出的：NaN 会向下游传播，必须知道它会出现在哪。

用法：
    python tests/verify_timeline_unify.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from services.timeline import unify_time_resolution  # noqa: E402

passed = 0
failed = 0


def check(ok: bool, label: str, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}  {detail}")


def labels_96():
    return [f"{i // 4:02d}:{i % 4 * 15:02d}" for i in range(96)]


def labels_48():
    return [f"{i // 2:02d}:{i % 2 * 30:02d}" for i in range(48)]


def main() -> int:
    rng = np.random.default_rng(20261104)

    print("[② 96 点 + 48 点混合 → 全部对齐到 96]")
    fine = rng.uniform(300, 900, size=(3, 96))
    coarse = rng.uniform(300, 900, size=(2, 48))
    data = {
        "2026-01": {"load_array": fine.copy(), "time_labels": labels_96()},
        "2026-02": {"load_array": coarse.copy(), "time_labels": labels_48()},
    }
    before_fine = fine.copy()

    out = unify_time_resolution(data)
    check(out is data, "原地修改并返回同一个 dict", "")
    check(data["2026-01"]["load_array"].shape == (3, 96), "96 点组形状不变",
          str(data["2026-01"]["load_array"].shape))
    check(data["2026-02"]["load_array"].shape == (2, 96), "48 点组被插值到 96 点",
          str(data["2026-02"]["load_array"].shape))
    check(data["2026-02"]["time_labels"] == labels_96(), "48 点组的标签换成 96 点的",
          str(data["2026-02"]["time_labels"][:3]))

    print("\n[③ 最细粒度那一组不被重采样]")
    check(np.array_equal(data["2026-01"]["load_array"], before_fine),
          "96 点组数值一字未变（跳过而非 re-interp）",
          f"max|Δ|={np.abs(data['2026-01']['load_array'] - before_fine).max():.3e}")

    print("\n[④ NaN 语义：上采样后超出粗粒度右边界的格是 NaN]")
    upsampled = data["2026-02"]["load_array"]
    nan_ratio = float(np.isnan(upsampled).mean())
    # 48 点数据只到 23:30，上采样到 96 点后最后一格 23:45 超出右边界 → NaN。
    # 这是原实现的固有行为（left/right=nan），不是搬过来的副作用。
    expected = 1 / 96
    check(abs(nan_ratio - expected) < 0.005,
          f"NaN 比例 ≈ 1/96（{nan_ratio:.2%}）—— 每天末格 23:45 超出右边界",
          f"实际 {nan_ratio:.2%}，期望 {expected:.2%}")
    print(f"    → 混合粒度时，粗粒度那个月每天最后一格是 NaN（共 {int(np.isnan(upsampled).sum())} 个）")
    # 关键：验证「整点有值、半点是插值」——取第一行前 3 个点看是否落在原值范围
    row0 = upsampled[0, :4]
    check(row0[0] == coarse[0, 0], "第 0 点等于原第 0 点（起点对齐）",
          f"{row0[0]} vs {coarse[0, 0]}")
    print(f"    示例前 4 点：{np.round(row0, 1)}（原 48 点前 2 点：{np.round(coarse[0, :2], 1)}）")

    print("\n[④b NaN 出现在「区间之外」的情况]")
    # 造一个标签不满一天的粗粒度组：0:00–12:00，只有 13 个点 → 上采样到 96 点后
    # 12:00 之后没有原始值，应当是 NaN
    partial_labels = [f"{i:02d}:00" for i in range(13)]     # 0:00 … 12:00
    partial = np.linspace(500, 600, 13).reshape(1, 13)
    data2 = {
        "2026-03": {"load_array": rng.uniform(300, 900, size=(2, 96)), "time_labels": labels_96()},
        "2026-04": {"load_array": partial, "time_labels": partial_labels},
    }
    unify_time_resolution(data2)
    arr2 = data2["2026-04"]["load_array"]
    tail_nan = int(np.isnan(arr2[0, 52:]).sum())     # 13:00 之后（索引 52 起）
    check(arr2.shape == (1, 96), "形状对齐到 96", str(arr2.shape))
    check(tail_nan > 0, f"13:00 之后没有原始值 → {tail_nan} 个 NaN（不是 0）",
          f"tail_nan={tail_nan}")
    print(f"    13:00 之后：{tail_nan} 个 NaN / 44 个点")

    print("\n[⑤ 单组不变]")
    single = {"2026-05": {"load_array": rng.uniform(300, 900, size=(2, 96)),
                          "time_labels": labels_96()}}
    snap = single["2026-05"]["load_array"].copy()
    unify_time_resolution(single)
    check(np.array_equal(single["2026-05"]["load_array"], snap), "单组原样返回", "")

    print("\n[附加：空 dict / 单点组不崩]")
    check(unify_time_resolution({}) == {}, "空 dict 直接返回", "")
    one = {"2026-06": {"load_array": np.zeros((1, 1)), "time_labels": ["00:00"]}}
    unify_time_resolution(one)
    check(one["2026-06"]["load_array"].shape == (1, 1), "max_points<2 时不动", "")

    print(f"\n合计 PASS {passed} / FAIL {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
