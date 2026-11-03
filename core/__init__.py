# -*- coding: utf-8 -*-
"""LoomGrid 核心层：纯 Python，不依赖任何 UI 框架。

分层规则见仓库根 `AGENTS.md`「分层依赖禁令」一节（清单写在那里，不写在这里，
避免文档措辞被文本 grep 误判）。用 AST 校验：

    python tools/check_layering.py

已完成搬入（按依赖方向自下而上）：
    models.py    纯数据类与绘制默认值（5 类 + 5 常量）
    timegrid.py  时间/日期解析、节假日判定、时段校验（24 符号 / 302 行闭包）
    spaces.py    充放电空间与容量换算（6 符号 / 145 行闭包）
    periods.py   时段切分（1 符号 / 75 行闭包）

依赖方向严格单向：periods → timegrid + models；spaces → timegrid + models；
models 不依赖任何人。core 内不得反向 import 主程序。

搬移前用 tools/analyze_deps.py 量闭包，搬完后用 tools/verify_move_fidelity.py
做 AST 级忠实度核验（27 / 5 / 1 个符号结构等价）。
"""
from __future__ import annotations

__all__ = ["models", "timegrid", "spaces", "periods"]
