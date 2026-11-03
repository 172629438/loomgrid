# -*- coding: utf-8 -*-
"""WebSocket 进度推送：把任务事件流实时推给前端。

对应真值表 ⑧。

为什么用 WebSocket 而不是轮询
----------------------------
四阶段顺序固定，进展是「一个一个事件」而不是「一个百分比数字」。
轮询要么太快拿到空转、要么太慢看不到阶段切换；事件流天然对得上。

连接时先回放已有事件（seq 从 0 开始），所以中途接入也能看到全程；
之后按 seq 增量推送，任务结束自动关闭。
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from services.tasks import TASK_STORE

log = logging.getLogger(__name__)
router = APIRouter()

POLL_INTERVAL = 0.1      # 服务端内部的增量检查间隔；对前端仍是推送
MAX_IDLE_SECONDS = 600   # 任务卡住时的最长等待


@router.websocket("/ws/progress")
async def progress(ws: WebSocket, task_id: str):
    await ws.accept()

    task = TASK_STORE.get(task_id)
    if not task:
        await ws.send_json({"type": "stage.error",
                            "payload": {"error": f"任务不存在或已过期：{task_id}"}})
        await ws.close()
        return

    seq = 0
    idle = 0.0
    try:
        while True:
            events, finished = TASK_STORE.events_since(task_id, seq)
            for e in events:
                await ws.send_json({"seq": e["seq"], "type": e["type"], "payload": e["payload"]})
                seq = e["seq"]

            if finished and not events:
                break
            if events:
                idle = 0.0
            else:
                idle += POLL_INTERVAL
                if idle >= MAX_IDLE_SECONDS:
                    await ws.send_json({"type": "log",
                                        "payload": {"level": "warning",
                                                    "message": "长时间无进展，连接关闭"}})
                    break
            await asyncio.sleep(POLL_INTERVAL)
    except WebSocketDisconnect:
        log.debug("ws 客户端断开 task=%s seq=%s", task_id, seq)
        return
    except Exception as e:
        log.exception("ws 推送异常 task=%s", task_id)
        try:
            await ws.send_json({"type": "stage.error", "payload": {"error": str(e)}})
        except Exception:
            pass
    finally:
        try:
            await ws.close()
        except Exception:
            pass
