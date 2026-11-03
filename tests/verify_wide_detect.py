"""宽表检测的日期列判定 —— 防「户号被当成日期」的静默选错。

背景
----
_extract_ym 对无效值返回字符串「未知月份」（原来是 None/NaN）。
所有用 .notna() 判断的消费方都失效了 —— 字符串的 .notna() 恒为 True。
_detect_wide_matrix 因此把「户号」列（1002）当成了日期列，
而且两种列序里只有「户号在前」会暴露（left_idx 指到户号），
而真实 Excel 里「表号/户号在前、日期在后」是常见排法。

本测试锁三件事：
  1. _is_valid_ym 谓词对「未知月份」/ None / 空串都返回 False
  2. 两种列序下 _detect_wide_matrix 都选出真正的日期列
  3. 两种列序下 load_col 都不丢失（这是上一轮修复的漏洞：
     wdate 验证拒绝了户号，但 load_col 跟着一起被跳过了）
"""

import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pandas as pd

from core.ingest import (
    UNKNOWN_MONTH, _detect_wide_matrix, _extract_ym, _is_valid_ym,
)

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("[PASS] " if cond else "[FAIL] ") + name + (("  " + str(detail)) if detail else ""))


def make_csv(order):
    times = []
    for i in range(96):
        m = i * 15
        times.append("%02d:%02d" % (m // 60, m % 60))
    if order == "date_first":
        header = "日期,户号," + ",".join(times)
        mk = lambda d: "2026-01-%02d,1002," % d + ",".join("%.1f" % (300 + i + d) for i in range(96))
    else:
        header = "户号,日期," + ",".join(times)
        mk = lambda d: "1002,2026-01-%02d," % d + ",".join("%.1f" % (300 + i + d) for i in range(96))
    rows = [header] + [mk(d) for d in range(1, 6)]
    p = os.path.join(tempfile.mkdtemp(prefix="lg-wide-"), order + ".csv")
    io.open(p, "w", encoding="utf-8-sig").write("\n".join(rows))
    return p


def main():
    # 1. 谓词
    check("_is_valid_ym('2026-01')", _is_valid_ym("2026-01") is True)
    check("_is_valid_ym(UNKNOWN_MONTH) 为 False", _is_valid_ym(UNKNOWN_MONTH) is False)
    check("_is_valid_ym(None) 为 False", _is_valid_ym(None) is False)
    check("_is_valid_ym('') 为 False", _is_valid_ym("") is False)

    # 2. _extract_ym 关键值（回归锁）
    cases = {
        "2026-01-01": "2026-01",
        "46023": "2026-01",
        "1002": UNKNOWN_MONTH,
        "9999": UNKNOWN_MONTH,
        "2026": UNKNOWN_MONTH,
        "0": UNKNOWN_MONTH,
        "": UNKNOWN_MONTH,
    }
    for v, want in cases.items():
        got = _extract_ym(v)
        check("_extract_ym(%r) = %r" % (v, want), got == want, "got %r" % got)

    # 3. 两种列序
    for order in ("date_first", "asset_first"):
        p = make_csv(order)
        df = pd.read_csv(p, dtype=str)
        cols = list(df.columns)
        d, l = _detect_wide_matrix(df, cols, 20)
        check("%s: date_col 不是户号" % order, d != "户号", "date_col=%r" % d)
        check("%s: date_col 是日期" % order, d == "日期", "date_col=%r" % d)
        check("%s: load_col 96 列" % order, len(l or []) == 96, "%d 列" % len(l or []))

    ok = sum(1 for _, c in RESULTS if c)
    bad = len(RESULTS) - ok
    print("\n" + "=" * 56)
    print("PASS %d / FAIL %d" % (ok, bad))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
