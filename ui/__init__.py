# -*- coding: utf-8 -*-
"""界面层：允许依赖 Qt，但不存放业务逻辑。

目前只有 window_state.py——对话框几何与分栏状态的存取。
它必须在 UI 层而不是 services，因为存的是界面自己的样子，不是业务配置。
"""
from __future__ import annotations

__all__ = ["window_state"]
