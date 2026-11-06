# -*- coding: utf-8 -*-
"""8 进程出图压测：同名文件、同一输出目录、真多进程。

为什么这条重要
--------------
v2.3.9 修过一个竞态：两个 worker 算出同一个文件名会互相覆盖。
但当时的压测只做了 2 进程 × 5 轮 —— 而用户机器是 8 核，实际会开 8 进程。

2 进程撞车是偶然，8 进程撞车是必然。本脚本压 3 组：
  ① 8 进程同轮并发，全部必须成功且产出 8 个互不覆盖的文件
  ② 重复 5 轮（共 40 次出图），统计成功率
  ③ 取消事件置位后不产出半成品

跑法：python tests/verify_export_concurrency.py

注意：Windows 是 spawn 启动方式，进程池必须在 __main__ 保护下启动，
否则子进程重新 import 本模块时会再次创建进程池，抛 RuntimeError。
"""
import hashlib
import multiprocessing as mp
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np  # noqa: E402

from plotting.curves import _plot_single_worker  # noqa: E402

PASS = FAIL = 0
TITLE = "同名压测"
FILENAME = "同名压测"
DATES_3 = ["2026-07-01", "2026-07-02", "2026-07-03"]
TIMES_96 = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 15, 30, 45)]


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}  {detail}")


def make_payload(seed):
    """造一份绘图载荷。seed 不同 → 曲线不同，便于事后区分文件内容。"""
    rng = np.random.default_rng(seed)
    load = rng.uniform(100 + seed * 10, 500 + seed * 10, size=(len(DATES_3), 96))
    return {
        "month_str": "2026-07",
        "table_no": f"T{seed}",
        "asset": f"资产{seed}",
        "sheet": "Sheet1",
        "load_array": load.tolist(),
        "date_labels": DATES_3,
        "time_labels": TIMES_96,
    }


def run_batch(outdir, n_workers, cancel_event, n_tasks=None):
    n_tasks = n_tasks or n_workers
    args = []
    for i in range(n_tasks):
        args.append((
            make_payload(i), TITLE, FILENAME, outdir, False,
            {}, None, None, 1.0, None, cancel_event,
        ))
    with mp.Pool(n_workers) as pool:
        return pool.starmap(_plot_single_worker, args)


def png_files(outdir):
    return sorted(f for f in os.listdir(outdir) if f.lower().endswith(".png"))


def main():
    # ① 8 进程同轮
    print("=" * 78)
    print("① 8 进程同轮：全部成功，产出 8 个互不覆盖的文件")
    print("=" * 78)
    tmp = tempfile.mkdtemp(prefix="conc1_")
    try:
        ev = mp.Manager().Event()
        results = run_batch(tmp, 8, ev)
        ok = [r for r in results if r[0]]
        bad = [r for r in results if not r[0]]
        print(f"  成功 {len(ok)} / {len(results)}")
        for r in bad[:3]:
            print(f"    失败原因: {r[1]}")
        check("8 个任务全部成功", len(ok) == 8, f"{len(bad)} 个失败")

        pngs = png_files(tmp)
        print(f"  产出 {len(pngs)} 个文件")
        check(f"产出恰好 8 个 png（实 {len(pngs)}）", len(pngs) == 8, str(pngs))

        sizes = [os.path.getsize(os.path.join(tmp, f)) for f in pngs]
        check("无 0 字节文件", all(s > 0 for s in sizes), str(sizes))

        hashes = []
        for f in pngs:
            with open(os.path.join(tmp, f), "rb") as fh:
                hashes.append(hashlib.sha256(fh.read()).hexdigest())
        check(f"8 个文件内容互不相同（{len(set(hashes))} 种）",
              len(set(hashes)) == 8, f"{len(set(hashes))} 种")

        tmps = [f for f in os.listdir(tmp) if f.endswith(".tmp")]
        check("无 .tmp 残留", not tmps, str(tmps))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ② 5 轮 × 8 进程
    print()
    print("=" * 78)
    print("② 重复 5 轮 × 8 进程 = 40 次出图")
    print("=" * 78)
    total_ok = total = 0
    round_files = []
    for r in range(5):
        tmp = tempfile.mkdtemp(prefix=f"conc2_{r}_")
        try:
            ev = mp.Manager().Event()
            res = run_batch(tmp, 8, ev)
            okn = sum(1 for x in res if x[0])
            total_ok += okn
            total += len(res)
            pngs = png_files(tmp)
            round_files.append(len(pngs))
            if okn != 8 or len(pngs) != 8:
                print(f"  第 {r+1} 轮：成功 {okn}/8，产出 {len(pngs)} 个 png")
            tmps = [f for f in os.listdir(tmp) if f.endswith(".tmp")]
            if tmps:
                print(f"  第 {r+1} 轮有 tmp 残留: {tmps}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    print(f"  40 次中成功 {total_ok} 次；每轮产出 {round_files}")
    check(f"40 次全部成功（实 {total_ok}）", total_ok == 40, str(total_ok))
    check("每轮都恰好 8 个 png", all(n == 8 for n in round_files), str(round_files))

    # ③ 取消
    print()
    print("=" * 78)
    print("③ 取消事件：置位后不产出半成品")
    print("=" * 78)
    tmp = tempfile.mkdtemp(prefix="conc3_")
    try:
        ev = mp.Manager().Event()
        ev.set()
        res = run_batch(tmp, 4, ev, n_tasks=4)
        okn = sum(1 for x in res if x[0])
        pngs = png_files(tmp)
        print(f"  取消后成功 {okn} 个，产出 {len(pngs)} 个 png")
        check("取消后没有 0 字节半成品",
              all(os.path.getsize(os.path.join(tmp, f)) > 0 for f in pngs), str(pngs))
        tmps = [f for f in os.listdir(tmp) if f.endswith(".tmp")]
        check("取消后无 .tmp 残留", not tmps, str(tmps))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print("=" * 78)
    print(f"结果：PASS {PASS}   FAIL {FAIL}")
    print("=" * 78)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
