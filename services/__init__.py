# -*- coding: utf-8 -*-
"""服务层：有状态、无界面。不得 import 任何 UI 框架。

分层规则见仓库根 AGENTS.md「分层依赖禁令」，用 tools/check_layering.py 校验。

已完成搬入：
    config.py    Config 的 45 个业务方法（配置读写、迁移链、校验、模板管理）
    provider.py  DataProvider + MemoryLimitedCache（两层缓存、宽表取数）
    capacity.py  容量与吞吐量口径（DemandBasis 两值 + 日吞吐量独立字段）

窗口几何那 4 个方法不在这里——它们是界面状态，归 ui/window_state.py。

待定：
    pipeline.py  ProcessingThread 的接口形态推迟到阶段 2，由 API 需求决定。
                 理由：API 是这个模块的消费者，消费者决定接口，
                 现在拍脑袋定签名，到阶段 2 大概率要返工。
"""
from __future__ import annotations

__all__ = ["config", "provider", "capacity"]
