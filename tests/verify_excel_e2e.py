"""Excel 端到端：日期列是真 datetime 而非文本，跑完 upload → inspect → mapping
→ spaces → capacity → export → PNG。

为什么单独测
------------
现有端到端全用 CSV（文本日期）。Excel 走 _read_file_full / openpyxl 另一条路径，
而且**日期列在 Excel 里是 datetime 类型**，会落到 _extract_ym 的另一个分支：
    str(datetime(2026,1,1)) = '2026-01-01 00:00:00'
它既不是「无年份格式」（不该触发 C8 补年份），也要能被 pd.to_datetime 正确解析。

这条不验，打包出来的 exe 遇到真实 Excel 可能就挂。
"""

import io
import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta

import openpyxl
import requests

ROOT = r"C:\Users\LENOVO\.hanako\repos\172629438__loomgrid"


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def build_excel(path):
    """96 时刻列；A 列写真正 datetime，B 列户号，其余负荷。"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"

    times = []
    for i in range(96):
        m = i * 15
        times.append("%02d:%02d" % (m // 60, m % 60))

    ws.cell(row=1, column=1, value="日期")
    ws.cell(row=1, column=2, value="户号")
    for j, t in enumerate(times, start=3):
        ws.cell(row=1, column=j, value=t)

    for d in range(5):
        r = d + 2
        # 真 datetime —— 这是本次测试的核心
        ws.cell(row=r, column=1, value=datetime(2026, 1, 15) + timedelta(days=d))
        ws.cell(row=r, column=2, value="1002")
        for j in range(96):
            h = j * 0.25
            v = -149 if 10 <= h < 14 else (600 if h >= 14 else 200)
            ws.cell(row=r, column=j + 3, value=round(v + d, 1))

    wb.save(path)
    return path


def main():
    port = free_port()
    base = "http://127.0.0.1:%d" % port
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api.app:app", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "warning"],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    results = []

    def check(name, cond, detail=""):
        results.append((name, bool(cond)))
        print(("[PASS] " if cond else "[FAIL] ") + name + (("  " + str(detail)) if detail else ""))

    try:
        up = False
        for _ in range(60):
            try:
                if requests.get(base + "/api/health", timeout=1).status_code == 200:
                    up = True
                    break
            except Exception:
                time.sleep(0.3)
        check("服务启动", up, base)
        if not up:
            raise RuntimeError("uvicorn 未就绪")

        xlsx = os.path.join(tempfile.mkdtemp(prefix="lg-xlsx-"), "Excel真日期.xlsx")
        build_excel(xlsx)
        check("Excel 已生成", os.path.exists(xlsx), "%d bytes" % os.path.getsize(xlsx))

        with open(xlsx, "rb") as f:
            up_j = requests.post(base + "/api/ingest/upload",
                                 files={"file": ("Excel真日期.xlsx", f,
                                                 "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                                 timeout=30).json()
        check("① upload", up_j.get("ok") is True,
              json_err(up_j.get("error")))
        path = (up_j.get("data") or {}).get("path")

        ins = requests.post(base + "/api/ingest/inspect", json={"path": path}, timeout=60).json()
        check("② inspect", ins.get("ok") is True, json_err(ins.get("error")))
        sh = ((ins.get("data") or {}).get("sheets") or [{}])[0]
        sug = sh.get("suggest") or {}
        check("② 识别为 wide", sug.get("format_type") == "wide", str(sug.get("format_type")))
        check("② date_col = 日期", sug.get("date_col") == "日期", repr(sug.get("date_col")))
        check("② asset_col = 户号", sug.get("asset_col") == "户号", repr(sug.get("asset_col")))
        check("② load_col 96 列", len(sug.get("load_col") or []) == 96,
              "%d 列" % len(sug.get("load_col") or []))

        # datetime 不是「无年份格式」，不该触发 C8
        yl = sh.get("yearless_samples") or []
        check("② datetime 不触发补年份", len(yl) == 0, str(yl))
        hints = (ins.get("data") or {}).get("hints") or {}
        check("② 无 guessed_year hint", "guessed_year" not in hints, str(hints))

        smp = sh.get("sample_rows") or []
        check("② 样本首行有值", bool(smp) and bool(smp[0][0]), str(smp[0][:2] if smp else None))

        mp = requests.post(base + "/api/ingest/mapping", json={
            "path": path, "sheet": sh.get("name"),
            "mapping": {k: sug.get(k) for k in
                        ("date_col", "time_col", "asset_col", "load_col",
                         "format_type", "header_row", "transpose")},
        }, timeout=120).json()
        check("③ mapping", mp.get("ok") is True, json_err(mp.get("error")))
        g = ((mp.get("data") or {}).get("groups") or [{}])[0]
        check("③ month_str = 2026-01", g.get("month") == "2026-01", repr(g.get("month")))
        check("③ date_count = 5", g.get("date_count") == 5, str(g.get("date_count")))
        check("③ dates 有 31 天", len(g.get("dates") or []) == 31, str(len(g.get("dates") or [])))

        sid = (mp.get("data") or {}).get("session_id")
        segments = [
            {"start": 0.0, "end": 8.0, "type": "charge", "label": "充电1"},
            {"start": 8.0, "end": 11.0, "type": "discharge", "label": "放电1"},
            {"start": 11.0, "end": 14.0, "type": "charge", "label": "充电2"},
            {"start": 14.0, "end": 18.0, "type": "discharge", "label": "放电2"},
            {"start": 18.0, "end": 21.0, "type": "discharge", "label": "放电3"},
        ]
        sp = requests.post(base + "/api/calc/spaces", json={
            "session_id": sid, "group": 0, "segments": segments,
            "mode": "B", "rated_power": 100.0, "ignore_negative": False,
        }, timeout=120).json()
        check("④ calc/spaces", sp.get("ok") is True, json_err(sp.get("error")))
        segs = (sp.get("data") or {}).get("segments") or []
        vals = {}
        for s in segs:
            vals[s["label"]] = round((s["charge"][0] if s["type"] == "charge" else s["discharge"][0]), 3)
        check("④ 充电2 = 267.375", vals.get("充电2") == 267.375, str(vals.get("充电2")))
        check("④ 放电2 = 372.0", vals.get("放电2") == 372.0, str(vals.get("放电2")))

        out_dir = tempfile.mkdtemp(prefix="lg-xlsxout-")
        ex = requests.post(base + "/api/export/run", json={
            "session_id": sid, "output_dir": out_dir,
            "filename_template": "{table_no}_{year_month}_负荷曲线",
        }, timeout=60).json()
        check("⑤ export/run 提交", ex.get("ok") is True, json_err(ex.get("error")))
        tid = (ex.get("data") or {}).get("task_id")

        for _ in range(180):
            st = requests.get(base + "/api/export/status?task_id=" + str(tid), timeout=10).json()
            if (st.get("data") or {}).get("status") in ("done", "failed"):
                break
            time.sleep(0.5)
        res = (st.get("data") or {}).get("result") or {}
        check("⑥ 导出成功 1 张", res.get("succeeded") == 1, str(res.get("succeeded")))

        pngs = []
        for r2, _, fs in os.walk(out_dir):
            pngs += [os.path.join(r2, f) for f in fs if f.lower().endswith(".png")]
        check("⑥ PNG 落盘", len(pngs) > 0, "png=%d" % len(pngs))
        if pngs:
            with open(pngs[0], "rb") as f:
                magic = f.read(8)
            check("⑥ PNG 魔数", magic == b"\x89PNG\r\n\x1a\n", repr(magic))
            check("⑥ 文件名含模板展开", "负荷曲线" in os.path.basename(pngs[0]),
                  os.path.basename(pngs[0]))

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()

    ok = sum(1 for _, c in results if c)
    bad = len(results) - ok
    print("\n" + "=" * 60)
    print("PASS %d / FAIL %d" % (ok, bad))
    return 0 if bad == 0 else 1


def json_err(e):
    if not e:
        return ""
    return "code=" + str(e.get("code")) + " msg=" + str(e.get("message"))[:60]


if __name__ == "__main__":
    sys.exit(main())
