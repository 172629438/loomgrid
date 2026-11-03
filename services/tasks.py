# -*- coding: utf-8 -*-
"""任务队列：给异步端点用的轻量任务表与事件流。

为什么不用 Celery / RQ
--------------------
这是单机服务，任务量是一次导出几十张图，不是分布式队列。引入 broker 是负担。
这里只要三件东西：任务状态、事件序列、过期清理。

事件流怎么给 WebSocket
--------------------
任务跑在后台线程，WebSocket 在 asyncio 事件循环里。两者之间不做跨线程队列
（容易出竞态），而是让任务把事件 append 到一个带锁的列表，ws 端按序号增量读。
对外仍是推送语义：前端订阅后立刻收到回放，之后持续收到新事件。

事件类型（四阶段固定，与 ProcessingThread 的顺序一致）
--------------------------------------------------
  stage.parse    文件解析与映射
  stage.scan     数据扫描
  stage.preload  缓存预加载
  stage.generate 图表生成
  progress       每完成一张图
  log            普通日志
  stage.error    出错（payload 里的 failed 直接透传，前端不用再拼）
  done           结束
"""
from __future__ import annotations

import threading
import time
import uuid

__all__ = ["TaskStore", "TASK_STORE"]


class TaskStore:
    def __init__(self, ttl_seconds: int = 3600):
        self._tasks: dict[str, dict] = {}
        self._lock = threading.RLock()
        self.ttl = ttl_seconds

    def create(self, kind: str = "export") -> str:
        tid = uuid.uuid4().hex
        with self._lock:
            self._tasks[tid] = {
                "id": tid,
                "kind": kind,
                "status": "pending",
                "events": [],          # [{"seq": n, "type": ..., "payload": {...}, "t": ts}]
                "result": None,
                "error": None,
                "created_at": time.time(),
                "updated_at": time.time(),
            }
        self.cleanup()
        return tid

    def get(self, tid: str) -> dict | None:
        with self._lock:
            return self._tasks.get(tid)

    def set_status(self, tid: str, status: str) -> None:
        with self._lock:
            t = self._tasks.get(tid)
            if t:
                t["status"] = status
                t["updated_at"] = time.time()

    def emit(self, tid: str, etype: str, payload: dict | None = None) -> None:
        with self._lock:
            t = self._tasks.get(tid)
            if not t:
                return
            t["events"].append({
                "seq": len(t["events"]) + 1,
                "type": etype,
                "payload": payload or {},
                "t": time.time(),
            })
            t["updated_at"] = time.time()

    def finish(self, tid: str, result: dict) -> None:
        with self._lock:
            t = self._tasks.get(tid)
            if not t:
                return
            t["status"] = "done"
            t["result"] = result
            t["updated_at"] = time.time()
        self.emit(tid, "done", result)

    def fail(self, tid: str, error: str, failed: list | None = None) -> None:
        with self._lock:
            t = self._tasks.get(tid)
            if not t:
                return
            t["status"] = "failed"
            t["error"] = error
            t["updated_at"] = time.time()
        # failed 直接透传，前端不用再拼一次
        self.emit(tid, "stage.error", {"error": error, "failed": failed or []})

    def events_since(self, tid: str, seq: int) -> tuple[list[dict], bool]:
        """返回 (增量事件, 任务是否已结束)。"""
        with self._lock:
            t = self._tasks.get(tid)
            if not t:
                return [], True
            evs = [e for e in t["events"] if e["seq"] > seq]
            return evs, t["status"] in ("done", "failed")

    def cleanup(self) -> int:
        """清掉超过 ttl 的任务。返回清理数。

        _TASKS 原来是个裸 dict、只增不减；改成异步后任务会更多，
        不定期清会吃内存。这里在每次 create 时顺带扫一遍。
        """
        now = time.time()
        dead = []
        with self._lock:
            for tid, t in self._tasks.items():
                if t["status"] in ("done", "failed") and now - t["updated_at"] > self.ttl:
                    dead.append(tid)
            for tid in dead:
                self._tasks.pop(tid, None)
        return len(dead)

    def stats(self) -> dict:
        with self._lock:
            by_status: dict[str, int] = {}
            for t in self._tasks.values():
                by_status[t["status"]] = by_status.get(t["status"], 0) + 1
            return {"total": len(self._tasks), "by_status": by_status}


TASK_STORE = TaskStore()
