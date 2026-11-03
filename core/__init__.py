# -*- coding: utf-8 -*-
"""LoomGrid 核心层：纯 Python，不依赖任何 UI 框架。

分层规则（方案 D 第 3 节，用 tools/check_layering.py 机械校验）：

    core/      不得 import UI 框架 / 绘图库 / Web 框架
    plotting/  不得 import UI 框架 / Web 框架
    services/  不得 import UI 框架 / Web 框架
    api/       不得 import UI 框架

已完成搬入：
    models.py    纯数据类与绘制默认值（5 类 + 5 常量）
    timegrid.py  时间/日期解析、节假日判定、时段校验（24 符号 / 302 行闭包）

搬移前先用 tools/analyze_deps.py 量依赖闭包，不用入口函数行数估；
搬移后用 tools/verify_move_fidelity.py 做 AST 级忠实度核验。
"""
from __future__ import annotations

__all__ = ["models", "timegrid"]
