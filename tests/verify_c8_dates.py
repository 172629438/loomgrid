"""C8「日期无年份默认今年」的回归锁。

背景
----
C8 此前只是登记在案，代码里没有实现。实测发现它从未生效：
中文格式全不识别，'01-15' 被 pd.to_datetime 解析成公元 1 年（0001-01）——
**看起来像个日期，比直接报错更难发现**。

本测试锁住：
  1. 中文年月日 / 年月 的识别
  2. 明确无年份（M月D日 / M-D / M/D）→ 补今年 + warning
  3. 已在错误解析结果上（0001-01）不再猜，判无法识别
  4. 带年份的三段式（1/15/2026）不被 M/D 规则吃掉
  5. 月/日越界判无效（1/32、13/15）
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.ingest import UNKNOWN_MONTH, _extract_ym

RESULTS = []
CUR_YEAR = __import__("datetime").date.today().year


def check(raw, want):
    got = _extract_ym(raw)
    ok = got == want
    RESULTS.append(ok)
    print(("[PASS] " if ok else "[FAIL] ") + "%-16r -> %-12r (期望 %r)" % (raw, got, want))


def main():
    print("=== 带年份 ===")
    check("2026-01-15", "2026-01")
    check("2026/01/15", "2026-01")
    check("2026年1月15日", "2026-01")
    check("2026年1月", "2026-01")
    check("1/15/2026", "2026-01")       # 三段式，不能被 M/D 吃掉

    print("\n=== 无年份（补今年）===")
    y = str(CUR_YEAR)
    check("1月15日", y + "-01")
    check("1月", y + "-01")
    check("01-15", y + "-01")
    check("1/15", y + "-01")
    check("12/31", y + "-12")

    print("\n=== 无法识别 ===")
    check("0001-01-15", UNKNOWN_MONTH)   # 已在错误结果上，不再猜
    check("1/32", UNKNOWN_MONTH)         # 日号越界
    check("13/15", UNKNOWN_MONTH)        # 月号越界
    check("2026-13-01", UNKNOWN_MONTH)
    check("1002", UNKNOWN_MONTH)         # 户号这类纯数字
    check("0", UNKNOWN_MONTH)
    check("2026", UNKNOWN_MONTH)         # 纯年份不是日期
    check("", UNKNOWN_MONTH)
    check(None, UNKNOWN_MONTH)

    print("\n=== Excel 序列号 ===")
    check("46023", "2026-01")

    ok = sum(1 for r in RESULTS if r)
    bad = len(RESULTS) - ok
    print("\n" + "=" * 56)
    print("PASS %d / FAIL %d" % (ok, bad))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
