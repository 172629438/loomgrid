# -*- coding: utf-8 -*-
"""多进程出图不得因同名文件互相覆盖（P1-6）。

缺陷（v2.3.9 之前）
--------------------
`_plot_single_worker` 选文件名用的是「先 os.path.exists 判断，再写」：

    base, ext = os.path.splitext(outpath)
    for counter in range(1, MAX_FILENAME_ATTEMPTS + 1):
        if not os.path.exists(outpath):
            break
        outpath = f"{base}_{counter}{ext}"

两个绘图进程几乎同时到达这里，都认为同名文件不存在，于是写同一个
outpath。随后 `os.replace` 后写者覆盖先写者——**报告说成功 N 张，
磁盘上只有 N-1 张**。两者还共用同一个 `outpath.tmp`，一个进程的清理
会把另一个的临时文件删掉，表现为随机一张失败。

修复：`_reserve_output_path` 用 O_CREAT|O_EXCL 原子占位；临时文件名
带 pid/线程标识；失败或取消时把占位空文件一并清掉。

跑法：python tests/verify_export_race.py
"""
import multiprocessing as mp
import os
import shutil
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import plotting.curves as C  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}  {detail}")


def _task(out_dir):
    data = {
        "load_array": [[100.0, 120.0, 90.0, 110.0]],
        "date_labels": ["2026-01-01"],
        "time_labels": ["00:00", "06:00", "12:00", "18:00"],
        "month_str": "2026-01",
        "table_no": "1",
        "asset": "1",
        "sheet": "Sheet1",
    }
    return (data, "{table_no}_{year_month}_负荷曲线", "{table_no}_{year_month}_负荷曲线",
            out_dir, False, None, None, {}, 1.0, None, None)


def test_reserve_atomic():
    tmp = tempfile.mkdtemp(prefix="loomgrid_race_reserve_")
    try:
        target = os.path.join(tmp, "a.png")
        got = []
        lock = threading.Lock()
        barrier = threading.Barrier(2)

        def worker():
            barrier.wait()
            path, err = C._reserve_output_path(target)
            with lock:
                got.append((path, err))

        ts = [threading.Thread(target=worker) for _ in range(2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()

        paths = [p for p, e in got if e is None]
        check("并发占位：两次都成功", len(paths) == 2, got)
        check("并发占位：得到两个不同路径", len(set(paths)) == 2, got)
        check("并发占位：占位文件都真实存在", all(os.path.exists(p) for p in paths), paths)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_two_workers():
    out = tempfile.mkdtemp(prefix="loomgrid_race_pool_")
    try:
        args = _task(out)
        with mp.get_context("spawn").Pool(2) as pool:
            res = pool.starmap(C._plot_single_worker, [args, args])
        ok = sum(1 for r in res if r and r[0])
        pngs = [f for f in os.listdir(out) if f.lower().endswith(".png")]
        check("两个 worker 都报告成功", ok == 2, res)
        check("磁盘上有两张图（不再互相覆盖）", len(pngs) == 2, pngs)
        check("两张图文件名不同", len(set(pngs)) == 2, pngs)
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_failure_cleanup():
    out = tempfile.mkdtemp(prefix="loomgrid_race_fail_")
    orig = C._plot_curves
    try:
        def boom(*a, **k):
            raise RuntimeError("绘图失败（测试注入）")

        C._plot_curves = boom
        ok, msg = C._plot_single_worker(*_task(out))
        check("绘图异常时返回失败", ok is False, (ok, msg))
        left = [f for f in os.listdir(out) if f.lower().endswith((".png", ".tmp"))]
        check("绘图异常时不留占位文件与临时文件", left == [], left)
    finally:
        C._plot_curves = orig
        shutil.rmtree(out, ignore_errors=True)


def main():
    test_reserve_atomic()
    test_two_workers()
    test_failure_cleanup()
    print()
    print(f"结果：PASS {PASS}   FAIL {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
