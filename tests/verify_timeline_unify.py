# -*- coding: utf-8 -*-
"""验证 services/timeline.py 的 unify_time_resolution（搬运件）。

搬迁判据不只是「能跑」：
  ① 逻辑等价   与 Fusion 原实现逐行对照（同一个算法，不引入新行为）
  ② 粒度对齐   96 点与 48 点混合时，全部变成 96 点
  ③ 数值保真   最细粒度那一组被跳过（不重采样 = 不失真）
  ④ 边缘填充   上采样后，原数据没有的时刻用最近有效值填充

    （v2.3.35 之前是断言 NaN。主程序 v2.3.23 改成填充，services 版
    当时没跟；现在两边一致，本文件同步改成新行为。）
  ⑤ 单组不变   只有一个分组时原样返回（不需要插值）

第 ④ 条改动过来：NaN 会被 nansum 当 0，容量建议会偏小。
新版填「最近有效值」，与主程序一致。

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

    print("\n[④ 边缘填充：上采样后区间外必须用最近有效值，不能是 NaN]")
    upsampled = data["2026-02"]["load_array"]
    nan_cnt = int(np.isnan(upsampled).sum())
    # v2.3.35：此处断言的是新行为。
    # 旧实现用 left/right=nan，48 点（末点 23:30）上采样到 96 点后，
    # 每天最后一格 23:45 超出右边界→NaN（实测 NaN 比例 1/96）。
    # 但 compute_segmented_spaces 用 nansum，NaN 被当 0，
    # 落在那个区间的充放时段空间会算成 0，容量建议跟着偏小。
    # 而主程序 v2.3.23 已改成「最近有效值填充」，services 版当时没跟。
    check(nan_cnt == 0,
          f"区间外无 NaN（实际 {nan_cnt} 个）—— 用最近有效值填充",
          f"期望 0，实际 {nan_cnt}")
    print(f"    → 混合粒度时，粗粒度那个月末格被填成最后一个有效值（共 {nan_cnt} 个 NaN）")
    # 关键：验证「整点有值、半点是插值」——取第一行前 3 个点看是否落在原值范围
    row0 = upsampled[0, :4]
    check(row0[0] == coarse[0, 0], "第 0 点等于原第 0 点（起点对齐）",
          f"{row0[0]} vs {coarse[0, 0]}")
    print(f"    示例前 4 点：{np.round(row0, 1)}（原 48 点前 2 点：{np.round(coarse[0, :2], 1)}）")
    print(f"    末点 23:45：{upsampled[0, -1]:.1f}（原末点 23:30：{coarse[0, -1]:.1f}）")
    check(abs(float(upsampled[0, -1]) - float(coarse[0, -1])) < 1e-6,
          "末点被填成原末值（最近有效值）",
          f"{upsampled[0, -1]} vs {coarse[0, -1]}")

    print("\n[④b 半天数据上采样：尾部用末值填，不是 NaN]")
    # 造一个标签不满一天的粗粒度组：0:00–12:00，只有 13 个点 → 上采样到 96 点
    # v2.3.35：原来断言「12:00 之后应该是 NaN」，现在改成「填末值」。
    partial_labels = [f"{i:02d}:00" for i in range(13)]     # 0:00 … 12:00
    partial = np.linspace(500, 600, 13).reshape(1, 13)
    data2 = {
        "2026-03": {"load_array": rng.uniform(300, 900, size=(2, 96)), "time_labels": labels_96()},
        "2026-04": {"load_array": partial, "time_labels": partial_labels},
    }
    unify_time_resolution(data2)
    arr2 = data2["2026-04"]["load_array"]
    tail_nan = int(np.isnan(arr2[0, 52:]).sum())
    check(arr2.shape == (1, 96), "形状对齐到 96", str(arr2.shape))
    check(tail_nan == 0,
          f"13:00 之后无 NaN（实际 {tail_nan} 个）",
          f"tail_nan={tail_nan}")
    check(abs(float(arr2[0, -1]) - 600.0) < 1e-6,
          "尾部填成末值 600", f"{arr2[0, -1]}")
    print(f"    13:00 之后：{tail_nan} 个 NaN / 44 个点，末值 {arr2[0, -1]:.1f}")

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
