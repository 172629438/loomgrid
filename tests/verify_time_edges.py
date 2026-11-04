# -*- coding: utf-8 -*-
"""时间解析与显示的两个边界（P2-4）。

- `_parse_min("23.999")`：h=23、m=round(59.94)=60，原实现不处理进位，
  返回 1440 分钟、标签 "23:60"。时间列是 0~23.99 小数小时时，最后一点
  被归到 24:00，与「24:00 表示次日零点」的约定冲突。
- `_fmt_time("8时")`：`_parse_min` 存缓存用的 key 是规范化后的 "8:00"，
  而 `_fmt_time` 查的是原始字符串，永远查不到，直接返回 "8时"。
  图表点数 <25 时把它画到 X 轴，用户看到的是 "8时" 而不是 "08:00"。

跑法：python tests/verify_time_edges.py
"""
import logging
import os
import sys

logging.disable(logging.CRITICAL)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

from core.timegrid import (  # noqa: E402
    INVALID_MINUTES, TIME_CACHE, _fmt_time, _parse_min,
)

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}  {detail}")


TIME_CACHE.clear()

# 1) 小数小时进位
check("_parse_min('23.999') == 1440", _parse_min("23.999") == 1440, _parse_min("23.999"))
check("_fmt_time('23.999') == '24:00'", _fmt_time("23.999") == "24:00", _fmt_time("23.999"))
check("_parse_min('23.75') == 1425", _parse_min("23.75") == 1425, _parse_min("23.75"))
check("_fmt_time('23.75') == '23:45'", _fmt_time("23.75") == "23:45", _fmt_time("23.75"))

# 2) 中文小时列名显示规范化
check("_parse_min('8时') == 480", _parse_min("8时") == 480, _parse_min("8时"))
check("_fmt_time('8时') == '08:00'", _fmt_time("8时") == "08:00", _fmt_time("8时"))
check("_parse_min('8时30分') == 510", _parse_min("8时30分") == 510, _parse_min("8时30分"))
check("_fmt_time('8时30分') == '08:30'", _fmt_time("8时30分") == "08:30", _fmt_time("8时30分"))

# 3) 回归：常见格式不受影响
check("_fmt_time('08:00') == '08:00'", _fmt_time("08:00") == "08:00", _fmt_time("08:00"))
check("_parse_min('24:00') == 1440", _parse_min("24:00") == 1440, _parse_min("24:00"))
check("_fmt_time('24:00') == '24:00'", _fmt_time("24:00") == "24:00", _fmt_time("24:00"))

# 4) 非法值原样返回，不崩
check("_parse_min('abc') 是 INVALID_MINUTES", _parse_min("abc") == INVALID_MINUTES)
check("_fmt_time('abc') == 'abc'", _fmt_time("abc") == "abc", _fmt_time("abc"))

print()
print(f"结果：PASS {PASS}   FAIL {FAIL}")
sys.exit(1 if FAIL else 0)
