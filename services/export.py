# -*- coding: utf-8 -*-
"""导出服务：出图、中间宽表、HTML 报告。

从 UI 类里抽出来的，原来分别是：
  PreviewDialog._export_midwide
  MainWindow._generate_report
  ProcessingThread._generate_charts（出图编排）

抽的原则：**只吃参数，不吃 self**。原来依赖的 self.current_index / self.all_summaries /
self.output_dir / self.progress_bar / self._thread 全部改成显式入参。

这一步顺带是「services 是否真能脱离 UI」的实测：
如果某处发现参数拿不到、只能从 UI 里捞，那说明数据流还有断点——
在阶段 2 发现比在阶段 4 便宜得多。
"""
from __future__ import annotations

import csv
import html as html_mod
import logging
import os
from datetime import datetime

import numpy as np
import pandas as pd

from core.models import GroupSummary
from plotting.curves import _plot_curves
from plotting.naming import _sanitize_name, generate_filename, parse_title_template

log = logging.getLogger(__name__)

# 默认模板必须与主程序一致（L2855 / L2871 处的 QLineEdit 初始值）：
# 用 API 和不传模板的用户，拿到的文件名该跟界面里跑出来的一模一样。
# 只在这里定义一份，api 层从这里 import，不要各写各的。
DEFAULT_TITLE_TEMPLATE = "{table_no}_{year_month}_负荷曲线"
DEFAULT_FILENAME_TEMPLATE = "{table_no}_{year_month}_负荷曲线"

__all__ = ["export_midwide", "generate_report", "plot_group",
           "export_csv", "DEFAULT_TITLE_TEMPLATE", "DEFAULT_FILENAME_TEMPLATE"]


def export_midwide(summary: GroupSummary, data: dict, output_dir: str) -> str:
    """把某个分组的宽表导出成 xlsx，返回文件路径。

    data 来自 DataProvider.get_group_data(summary)。
    """
    os.makedirs(output_dir, exist_ok=True)
    mstr = (summary.month_str or "").replace("-", "")
    asset = summary.asset_value or "未命名"
    filepath = os.path.join(output_dir, f"中间宽表_{asset}_{mstr}.xlsx")
    df = pd.DataFrame(data["load_array"],
                      index=data["date_labels"],
                      columns=data["time_labels"])
    df.index.name = "日期"
    df.to_excel(filepath)
    return filepath


def plot_group(summary: GroupSummary, data: dict, output_dir: str,
               title_template: str | None = None,
               filename_template: str | None = None,
               plot_options: dict | None = None,
               billing: dict | None = None,
               price_periods: list | None = None,
               multiplier: float = 1.0) -> str:
    """给一个分组画一张图，返回 PNG 路径。

    与界面上的出图走同一个 _plot_curves，只是把参数显式传进来。
    """
    os.makedirs(output_dir, exist_ok=True)
    asset = summary.asset_value or "未命名"
    month = summary.month_str or ""

    load = np.asarray(data["load_array"], dtype=float)
    if multiplier and multiplier != 1.0:
        load = load * float(multiplier)

    # 模板渲染必须走 plotting.naming（主程序原样实现），不要自己再写一遍：
    #   · 占位符是 year_month / table_no / asset / sheet 四 个
    #   · 逐字 replace，未知占位符原样保留（用 .format() 会 KeyError）
    #   · 文件名要过 _sanitize_name（非法字符 / Windows 保留名 / 长度截断）
    # 自己写一份的后果：资产名 "A/B栋" 会往路径里插斜杠，"CON" 在 Windows 写不进去。
    ctx = {
        "year_month": summary.month_str or "",
        "table_no": summary.asset_value or "未命名",
        "asset": summary.asset_value or "未命名",
        "sheet": summary.sheet_name or "",
    }
    title = parse_title_template(title_template or DEFAULT_TITLE_TEMPLATE, ctx)
    fname = generate_filename(filename_template or DEFAULT_FILENAME_TEMPLATE, ctx)
    outpath = os.path.join(output_dir, fname)

    _plot_curves(list(data["date_labels"]), load, list(data["time_labels"]),
                 title, outpath, opts=plot_options, bill=billing, periods=price_periods)
    return outpath


def generate_report(paths: list[str], failed: list, summaries: list[GroupSummary],
                    output_dir: str) -> str:
    """生成批量出图的 HTML 报告，返回文件路径。

    与原 MainWindow._generate_report 的逻辑一致，只是把 self.output_dir /
    self._thread.all_summaries / self._log 换成了显式参数。

    注意 html 变量名：本模块用 html_mod 别名，因为函数内有局部变量 html。
    原程序曾因此抛 UnboundLocalError（v2.3.7 引入，v2.3.9 修）。
    """
    os.makedirs(output_dir, exist_ok=True)
    report_path = os.path.join(output_dir, "生成报告.html")
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    success_count = len(paths)
    fail_count = len(failed)
    total = success_count + fail_count

    body = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>负荷曲线生成报告</title>
<style>
body {{ font-family: 'Microsoft YaHei', sans-serif; margin: 40px; }}
h1 {{ color: #333; }}
.success {{ color: green; }}
.failure {{ color: red; }}
table {{ border-collapse: collapse; width: 100%; margin-top: 20px; }}
th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
th {{ background-color: #4CAF50; color: white; }}
tr:nth-child(even) {{ background-color: #f2f2f2; }}
</style>
</head>
<body>
<h1>负荷曲线批量生成报告</h1>
<p>生成时间: {now}</p>
<p>输出目录: {html_mod.escape(str(output_dir))}</p>
<p>成功 <span class="success">{success_count}</span> 张，失败 <span class="failure">{fail_count}</span> 张，总计 {total} 张。</p>
"""

    if success_count > 0:
        body += ("<h2>成功列表</h2><table><tr><th>序号</th><th>文件名</th>"
                 "<th>表号</th><th>月份</th><th>源文件</th></tr>")
        for i, p in enumerate(paths, 1):
            info = {}
            base = os.path.basename(p)
            for s in summaries or []:
                if base.startswith(_sanitize_name(s.asset_value)):
                    info = {"asset": s.asset_value, "month": s.month_str,
                            "file": os.path.basename(s.file_path)}
                    break
            body += (f"<tr><td>{i}</td>"
                     f"<td>{html_mod.escape(base)}</td>"
                     f"<td>{html_mod.escape(str(info.get('asset', '?')))}</td>"
                     f"<td>{html_mod.escape(str(info.get('month', '?')))}</td>"
                     f"<td>{html_mod.escape(str(info.get('file', '?')))}</td></tr>")
        body += "</table>"

    if fail_count > 0:
        body += "<h2>失败列表</h2><ul>"
        for f in failed:
            body += f"<li class='failure'>{html_mod.escape(str(f))}</li>"
        body += "</ul>"

    body += "</body></html>"

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(body)
    log.info(f"生成报告已保存至: {report_path}")
    return report_path


def export_csv(rows: list[dict], output_dir: str, name: str = "导出.csv") -> str:
    """把结构化行导成 CSV（供空间表等使用）。"""
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, name)
    if not rows:
        open(path, "w", encoding="utf-8-sig", newline="").close()
        return path
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return path
