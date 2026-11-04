# -*- coding: utf-8 -*-
"""session 取数统一入口：文件来源与模拟来源走同一个口。

为什么要有这个文件
----------------
`/api/preview` 与 `/api/calc/*` 原来各自写了一遍「查 session → 取分组 →
provider 读文件」。负荷模拟引入第二种 session 来源（内存里的模拟曲线）之后，
如果只在其中一处加分支，另一处就会「模拟了但预览还是旧数据」——**不会报错，
只是数字不对**，是最难发现的那类。

所以把取数收敛到这里，两个路由都调它。将来再加来源也只改一处。

provider 为什么不动
------------------
`services/provider.get_group_data(summary)` 拿到的 `GroupSummary` **没有
session_id**（`core/models.py` 里是 `__slots__` 定长结构，且 core/ 不改）。
provider 因此无从判断「这个分组是不是模拟的」。与其给 provider 加一条它拿不到
信息的判断，不如把分支放在**知道 session_id 的层**。模拟数据的数组在
`/api/simulate/load` 生成时就已经存进 session，这里只负责原样取出，不重新生成。
"""
from __future__ import annotations

from typing import Any

from api.routes_ingest import get_session
from core.models import GroupSummary
from services.provider import DataProvider

_PROVIDER = DataProvider()


class ResolveError(Exception):
    """取数失败。携带原始错误码与文案，由调用方决定怎么包进 Envelope。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def load_group(session_id: str, group: int) -> tuple[GroupSummary, dict[str, Any]]:
    """取某个 session 下某分组的宽表数据。

    返回 `(GroupSummary, data)`，`data` 至少含 `load_array`；
    失败抛 `ResolveError`（调用方按自己的错误码风格转换）。

    模拟来源的 session 只有 1 个分组，数据直接存在 `session["simulated_data"]` 里。
    """
    sess = get_session(session_id)
    if not sess:
        raise ResolveError("PREVIEW_GROUP_NOT_FOUND", f"session 不存在或已过期：{session_id}")

    groups = sess.get("groups", [])
    if not groups or group < 0 or group >= len(groups):
        raise ResolveError(
            "PREVIEW_GROUP_NOT_FOUND",
            f"分组序号越界：{group}（共 {len(groups)} 组）")

    try:
        gs = GroupSummary.from_dict(groups[group])
    except Exception as e:  # noqa: BLE001 - 分组信息残缺要回可读原因
        raise ResolveError("PREVIEW_NO_DATA", f"分组信息不完整：{e}") from e

    if sess.get("source") == "simulated":
        data = sess.get("simulated_data")
        if not data or data.get("load_array") is None:
            raise ResolveError("CALC_NO_DATA", "模拟 session 里没有数据")
        return gs, data

    data = _PROVIDER.get_group_data(gs)
    if not data or data.get("load_array") is None:
        raise ResolveError(
            "CALC_NO_DATA",
            _PROVIDER.get_load_error(gs) or "该分组没有可用的宽表数据")
    return gs, data
