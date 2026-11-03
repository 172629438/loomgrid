# -*- coding: utf-8 -*-
"""LoomGrid 核心层：纯 Python，不依赖任何 UI 框架。

分层规则（方案 D 第 3 节，可机械检查）：
    core/      不许 import PySide6 / matplotlib / fastapi
    plotting/  不许 import PySide6 / fastapi
    services/  不许 import PySide6 / fastapi
    api/       不许 import PySide6
"""
from __future__ import annotations

__all__ = ["models"]
