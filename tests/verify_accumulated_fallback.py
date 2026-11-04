# -*- coding: utf-8 -*-
"""示数表差分失败必须报错，不能拿累计示数当功率画（P1-8）。

缺陷（v2.3.9 之前）
--------------------
`_convert_accumulated_to_power` 有两个失败分支——月度累计表拒绝差分、
转换过程抛异常——都只是打一条日志，然后 `return df` 把**原始示数**
交回 `_parse_long_to_standard`。下游把它当功率画：

- 字符串示数（带千分位/单位）：曲线是 100、101、102…（原始读数）
- 月度累计表：曲线是 1000、1001、1002…（单调上升的读数）

两者都"看起来像一条曲线"，用户不会察觉量纲错了。

修复：失败时在 df.attrs 打 `accumulated_error`，由
`_parse_long_to_standard` 转成 ValueError；`_translate_error` 原样透出原因。

跑法：python tests/verify_accumulated_fallback.py
"""
import os
import sys
import logging

logging.disable(logging.CRITICAL)  # 本测试会主动触发失败分支，日志只噪不乱

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd  # noqa: E402

from core.ingest import _parse_long_to_standard, _translate_error  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}  {detail}")


TIMES = [f"{i // 4:02d}:{(i % 4) * 15:02d}" for i in range(96)]


def day_df(values):
    return pd.DataFrame({
        "日期": ["2026-01-01"] * 96,
        "时间": TIMES,
        "示数": values,
    })


def run(df):
    return _parse_long_to_standard(
        df.copy(), "日期", "时间", "示数",
        convert_accumulated=True, fallback_asset="x")


# 1) 正常日重置示数表：行为不变，仍差分成功率
try:
    out, ratio = run(day_df([float(i) for i in range(96)]))
    ok = len(out) == 95 and abs(float(out["__load__"].iloc[0]) - 4.0) < 1e-9
    check("正常示数表仍能差分（首点 4.0 kW，95 行）", ok,
          f"rows={len(out)} first={list(out['__load__'].head(2)) if len(out) else None}")
except Exception as e:
    check("正常示数表仍能差分（首点 4.0 kW，95 行）", False, f"{type(e).__name__}: {e}")

# 2) 字符串示数 → 必须抛 ValueError，不能静默返回读数
try:
    out, _ = run(day_df([f"{100 + i:,}" for i in range(96)]))
    check("字符串示数必须报错（不再静默回退）", False,
          f"未抛异常，返回 {len(out)} 行，负荷 {list(out['__load__'].head(2))}")
except ValueError as e:
    check("字符串示数必须报错（不再静默回退）", "累计示数转换失败" in str(e), str(e))
except Exception as e:
    check("字符串示数必须报错（不再静默回退）", False, f"{type(e).__name__}: {e}")

# 3) 月度累计（跨天不回落）→ 必须抛 ValueError，并点明原因
vals = [1000.0 + i for i in range(96 * 3)]
monthly = pd.DataFrame({
    "日期": ["2026-01-01"] * 96 + ["2026-01-02"] * 96 + ["2026-01-03"] * 96,
    "时间": TIMES * 3,
    "示数": vals,
})
try:
    out, _ = run(monthly)
    check("月度累计表必须报错", False,
          f"未抛异常，返回 {len(out)} 行，负荷 {list(out['__load__'].head(2))}")
except ValueError as e:
    msg = str(e)
    check("月度累计表必须报错", "累计示数转换失败" in msg and "月度累计" in msg, msg)
except Exception as e:
    check("月度累计表必须报错", False, f"{type(e).__name__}: {e}")

# 4) 错误翻译不能把原因盖成通用文案
msg = _translate_error(ValueError, "累计示数转换失败：数据可能是月度累计表")
check("_translate_error 原样透出累计示数原因", msg.startswith("累计示数转换失败"), msg)

# 5) 正常路径不受 translate 影响
check("_translate_error 其他 ValueError 仍走通用文案",
      "数值转换失败" in _translate_error(ValueError, "some other error"))

print()
print(f"结果：PASS {PASS}   FAIL {FAIL}")
sys.exit(1 if FAIL else 0)
