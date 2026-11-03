# -*- coding: utf-8 -*-
"""API 层：把 services 与 core 的能力暴露成 HTTP。

分层规则见仓库根 AGENTS.md「分层依赖禁令」：api/ 不得 import UI 框架。
用 tools/check_layering.py 校验。

不做的事：
  · 不放业务逻辑（业务在 services/，这里只做请求解析与响应封装）
  · 不碰 Qt（否则阶段 5 打包时 API 与 UI 分不开）
  · 不暴露内部结构（四个平行列表这类实现细节要在这一层重组）
"""
from __future__ import annotations

__all__ = ["app", "schemas", "routes_ingest"]
