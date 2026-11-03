# -*- coding: utf-8 -*-
"""绘图层：允许依赖 matplotlib，不允许依赖 UI 框架。

分层规则见仓库根 AGENTS.md「分层依赖禁令」，用 tools/check_layering.py 校验。

模块划分：
    fonts.py   中文字体探测与设置
    naming.py  文件名 / 标题生成与清洗
    curves.py  曲线绘制、拟合、降采样、单图任务
"""
from __future__ import annotations

__all__ = ["fonts", "naming", "curves"]
