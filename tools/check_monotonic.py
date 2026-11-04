# -*- coding: utf-8 -*-
"""单调性与中间态检查器。

为什么需要它
------------
点值断言（0 / 极大 / 边界）验不出「差一位」的错——`>=` 写成 `>`、
`int()` 写成 `round()`、`<` 与 `<=` 互换，这些只在**连续区间的中间那一段**暴露。

典型症状：扫一串输入，输出永远只有两个值（0 和最大值），
说明「部分满足」这个状态根本没实现，而业务逻辑正是为它写的。

三条判据
--------
  ① 单调      xs 递增时输出按 expect 方向单调（nonincreasing / nondecreasing）
  ② 中间态    输出不能只有两端值（除非调用方明确声明数据本身只有两端）
  ③ 端点      首尾符合预期（调用方给 expect_first / expect_last）

用法
----
    from tools.check_monotonic import scan, assert_monotonic

    r = scan(lambda cap: satisfied_days(cap), list(range(200, 3001, 100)),
             expect="nonincreasing")
    assert_monotonic(r)          # 抛 AssertionError 并带诊断

    python tools/check_monotonic.py            # 自检：用几个已知函数跑一遍
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MonotonicReport:
    label: str = ""
    xs: list = field(default_factory=list)
    ys: list = field(default_factory=list)
    expect: str = "nonincreasing"
    monotonic: bool = True
    violations: list = field(default_factory=list)   # [(i, y[i-1], y[i])]
    distinct: list = field(default_factory=list)
    interior: list = field(default_factory=list)
    first: object = None
    last: object = None

    @property
    def has_interior(self) -> bool:
        """是否存在「既不是首值也不是尾值」的中间输出。"""
        return len(self.interior) > 0

    def summary(self) -> str:
        return (f"[{self.label or 'scan'}] n={len(self.xs)} "
                f"distinct={self.distinct} " \
                f"monotonic={self.monotonic} interior={self.interior}")


def scan(fn, xs, expect: str = "nonincreasing", label: str = "") -> MonotonicReport:
    """对 fn 在 xs 上逐个求值并检查单调性。

    fn 抛异常会原样向上抛——那是调用方该处理的事，这里不吞。
    """
    if expect not in ("nonincreasing", "nondecreasing"):
        raise ValueError(f"expect 只能是 nonincreasing / nondecreasing，收到 {expect}")

    ys = [fn(x) for x in xs]
    rep = MonotonicReport(label=label, xs=list(xs), ys=ys, expect=expect)

    for i in range(1, len(ys)):
        prev, cur = ys[i - 1], ys[i]
        bad = (cur > prev) if expect == "nonincreasing" else (cur < prev)
        if bad:
            rep.monotonic = False
            rep.violations.append((i, prev, cur))

    rep.distinct = sorted(set(ys))
    if ys:
        rep.first, rep.last = ys[0], ys[-1]
        # 中间值 = 既不是首值也不是尾值
        reps = {ys[0], ys[-1]}
        rep.interior = [v for v in rep.distinct if v not in reps]

    return rep


def assert_monotonic(
    rep: MonotonicReport,
    require_interior: bool = False,
    expect_first=None,
    expect_last=None,
) -> None:
    """把诊断变成断言。失败时抛 AssertionError，消息里带足以定位的信息。"""
    problems = []

    if not rep.monotonic:
        head = rep.violations[:3]
        problems.append(f"单调性被破坏（expect={rep.expect}），前几处：{head}")

    if require_interior and not rep.has_interior:
        problems.append(
            f"输出只有两端值 {rep.distinct}，没有中间态——"
            "说明「部分满足」这类中间状态没有实现，或数据范围没跨过拐点")

    if expect_first is not None and rep.first != expect_first:
        problems.append(f"首值 {rep.first} != 期望 {expect_first}")

    if expect_last is not None and rep.last != expect_last:
        problems.append(f"尾值 {rep.last} != 期望 {expect_last}")

    if problems:
        raise AssertionError(
            f"{rep.summary()}\n  " + "\n  ".join(problems))


def format_table(rep: MonotonicReport, max_rows: int = 20) -> str:
    """把扫描结果排成可读的两列表（过长时抽样）。"""
    rows = list(zip(rep.xs, rep.ys))
    if len(rows) > max_rows:
        step = max(1, len(rows) // max_rows)
        rows = rows[::step]
    width = max((len(str(x)) for x, _ in rows), default=1)
    return "\n".join(f"  {str(x):>{width}} → {y}" for x, y in rows)


# ── 自检：用几个已知函数确认工具本身是对的 ────────────────────

if __name__ == "__main__":
    xs = list(range(1, 11))

    # ① 严格递减：单调通过；每个值都不同 → 中间态非空（阶跃才是「没有中间态」）
    r1 = scan(lambda x: 100 - x * 10, xs, "nonincreasing", "严格线性递减")
    assert r1.monotonic and r1.violations == []
    assert len(r1.interior) > 0        # 首值 90、尾值 0 之外还有 8 个值
    print("① 线性递减：", r1.summary())

    # ② 阶跃：先高后低，只有 100 和 0 两个值 → 恰好是首尾，没有中间态
    r2 = scan(lambda x: 100 if x < 5 else 0, xs, "nonincreasing", "阶跃")
    assert r2.monotonic, r2.summary()
    assert r2.interior == []           # distinct = [0, 100] 恰为首尾
    print("② 阶跃（只有两端）：", r2.summary())

    # ③ 反例：故意让它在中间弹一下，必须被抓到
    def wobbly(x):
        base = 100 - x * 5
        return base + 30 if x == 6 else base

    r3 = scan(wobbly, xs, "nonincreasing", "中间反弹")
    assert not r3.monotonic and r3.violations, "反例应当被抓到"
    print("③ 中间反弹（应当报错）：", r3.summary())

    # ④ 断言接口：require_interior 在阶跃上必须失败
    try:
        assert_monotonic(r2, require_interior=True)
        raise SystemExit("④ 应当抛 AssertionError 却没抛")
    except AssertionError as e:
        print("④ require_interior 在阶跃上正确报错：", str(e).splitlines()[1].strip())

    print("\n工具自检通过：能抓反弹、能识别「只有两端」、断言接口可用。")
