"""异常输入的行为验证。

为什么单独一个文件
------------------
其余测试用的都是「正常数据」—— 96 列宽表、长表、转置表，每种都长得规整。
真实用户的文件什么形态都有：空的、只有表头的、日期是纯数字的、GBK 编码的、
列数只有一半的。

判据不是「能不能处理」（有些本来就该拒绝），而是 **报错能不能读懂**：

    好   「这个文件没有数据行」
         「时刻列只有 48 个，不符合 15 分钟粒度（需要 96 个）」
    坏   KeyError / ValueError: invalid literal / 空白页 / JSON 404

跑法：python tests/verify_bad_inputs.py
"""

from __future__ import annotations

import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("[PASS] %s %s" % (name, detail))
    else:
        FAIL += 1
        print("[FAIL] %s %s" % (name, detail))


def make(name, data, encoding="utf-8"):
    """在临时目录写一个文件，返回路径。"""
    d = tempfile.mkdtemp(prefix="lgm-bad-")
    p = os.path.join(d, name)
    with io.open(p, "w", encoding=encoding, newline="") as f:
        f.write(data)
    return p


def inspect(path, sheet=None):
    """直接调 inspect 的业务函数，不走 HTTP（少一层噪音）。"""
    from api.routes_ingest import _inspect_impl as impl  # 若有
    return impl(path, sheet) if impl else None


def main():
    from core.ingest import _smart_classify_columns  # noqa: F401  (确认可导入)
    from fastapi.testclient import TestClient
    from api.app import app

    c = TestClient(app)

    def post_inspect(p):
        # 白名单：inspect 只认 upload 目录或 LGM_ALLOW_ROOTS
        r = c.post("/api/ingest/inspect", json={"path": p})
        return r.status_code, r.json()

    # 让测试目录可用
    os.environ["LGM_ALLOW_ROOTS"] = tempfile.gettempdir()
    # 重新导入以让白名单生效（模块级读取）
    import importlib
    import api.routes_ingest as ri
    importlib.reload(ri)

    # ① 空文件
    p = make("empty.csv", "")
    code, body = post_inspect(p)
    ok = body.get("ok") is False and body.get("error", {}).get("code")
    msg = body.get("error", {}).get("message", "")
    check("① 空文件 → 结构化错误", ok, "[%s] %s" % (body.get("error", {}).get("code"), msg[:60]))
    check("① 空文件 → 消息可读", bool(msg) and "Traceback" not in msg, repr(msg[:50]))

    # ② 只有表头
    p = make("header_only.csv", "日期,户号,00:00,00:15\n")
    code, body = post_inspect(p)
    ok = body.get("ok") is False and body.get("error", {}).get("code")
    msg = body.get("error", {}).get("message", "")
    check("② 只有表头 → 结构化错误", ok, "[%s] %s" % (body.get("error", {}).get("code"), msg[:60]))

    # ③ 日期列是纯数字（Excel 序列号）
    rows = ["日期,户号," + ",".join("%02d:%02d" % (h, m) for h in range(24) for m in (0, 15, 30, 45))]
    for day in range(3):
        head = "%s,1002," % (46023 + day)
        rows.append(head + ",".join("300.0" for _ in range(96)))
    p = make("excel_serial.csv", "\n".join(rows) + "\n")
    code, body = post_inspect(p)
    ok = body.get("ok") is True
    if ok:
        sug = body["data"]["sheets"][0]["suggest"]
        check("③ Excel 序列号 → 识别成功", sug.get("format_type") == "wide",
              "format=%s" % sug.get("format_type"))
        # 月份应来自序列号换算，不是 1970
        mp = c.post("/api/ingest/mapping", json={
            "path": p, "sheet": body["data"]["sheets"][0]["name"],
            "mapping": sug})
        mb = mp.json()
        month = ""
        if mb.get("data"):
            g = mb["data"].get("groups") or []
            month = g[0].get("month", "") if g else ""
        check("③ 序列号换算正确月份", month == "2026-01", "month=%s" % month)
    else:
        check("③ Excel 序列号 → 识别成功", False, str(body.get("error"))[:80])

    # ④ GBK 编码的 CSV
    gbk_rows = ["日期,户号," + ",".join("%02d:%02d" % (h, m) for h in range(24) for m in (0, 15, 30, 45))]
    for day in range(3):
        gbk_rows.append("2026-01-0%d,1002," % (day + 1) + ",".join("300.0" for _ in range(96)))
    p = make("gbk.csv", "\r\n".join(gbk_rows) + "\r\n", encoding="gbk")
    code, body = post_inspect(p)
    if body.get("ok"):
        sug = body["data"]["sheets"][0]["suggest"]
        check("④ GBK CSV → 识别成功", bool(sug.get("date_col")), "date_col=%s" % sug.get("date_col"))
        check("④ GBK 中文列名没乱码", sug.get("date_col") == "日期",
              "date_col=%r" % sug.get("date_col"))
    else:
        # 失败也算可接受，但消息要可读
        msg = body.get("error", {}).get("message", "")
        check("④ GBK CSV → 明确错误", bool(msg) and "Traceback" not in msg, repr(msg[:50]))

    # ⑤ 48 列（只有 12 小时的数据）
    rows48 = ["日期,户号," + ",".join("%02d:%02d" % (h, m) for h in range(12) for m in (0, 15, 30, 45))]
    for day in range(3):
        rows48.append("2026-01-0%d,1002," % (day + 1) + ",".join("300.0" for _ in range(48)))
    p = make("half48.csv", "\n".join(rows48) + "\n")
    code, body = post_inspect(p)
    if body.get("ok"):
        sug = body["data"]["sheets"][0]["suggest"]
        n = len(sug.get("load_col") or [])
        check("⑤ 48 列 → 不崩溃且如实报告列数", True, "load_col=%d 列 format=%s" % (n, sug.get("format_type")))
        # 是否给出提示（不足 96）
        hint = (body["data"]["sheets"][0].get("confidence") or {}).get("reasons") or []
        check("⑤ 48 列 → 有说明或至少不假装是 96", n == 48, "reasons=%s" % (hint[:2],))
    else:
        msg = body.get("error", {}).get("message", "")
        check("⑤ 48 列 → 明确错误", bool(msg) and "Traceback" not in msg, repr(msg[:50]))

    # ⑥ 假 xlsx（内容是文本，扩展名是 .xlsx）
    # 用户的真实场景：从系统导出后改了扩展名，或者下载了一半的文件。
    # 报错必须能让人看懂「我这不是 Excel 吗」。
    d = tempfile.mkdtemp(prefix="lgm-bad-")
    fake = os.path.join(d, "假.xlsx")
    with io.open(fake, "w", encoding="utf-8", newline="") as f:
        f.write("这不是 excel，只是一段文字\n")
    code, body = post_inspect(fake)
    err = body.get("error") or {}
    msg = err.get("message", "")
    check("⑥ 假 xlsx → 结构化错误", body.get("ok") is False and bool(err.get("code")),
          "[%s]" % err.get("code"))
    check("⑥ 报错带文件名", "假.xlsx" in msg, repr(msg[:60]))
    check("⑥ 报错是中文人话", "格式无法识别" in msg, repr(msg[:60]))
    check("⑥ 不再说「CSV文件」", "CSV文件" not in msg, repr(msg[:60]))
    check("⑥ 原始异常保留在 detail", bool((err.get("detail") or {}).get("raw")),
          str((err.get("detail") or {}).get("raw"))[:50])

    print("\n" + "=" * 52)
    print("PASS %d  FAIL %d" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
