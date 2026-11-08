# -*- coding: utf-8 -*-
"""窗口状态：记住每个对话框上次的几何尺寸与分栏位置。

这 4 个函数原来挂在 Config 上，但它们不是业务配置，是界面状态：
存的是 QByteArray（saveGeometry / saveState 的产物）的 base64 文本。
放进 services 会让将来的 API 冒出一个「窗口几何」接口，语义上不成立。

约定：直接读写 Config.cfg 字典本体（与 Config 共用同一个配置文件），
但不经过 Config 的业务方法——职责不同，不该混在一个类里。

v2.3.15fix 两条：
① set_* 原实现无条件 cfg.save()，而 save() 里存在旧文件就 copy 一份备份。
   窗口关闭、拖动分割条都会触发 set_*，于是「开关几次对话框」就能把
   「改配置前的历史备份」全挤掉（实测 10 次窗口操作吃掉 3 个）。
   改为：值没变就不写盘。
② dialog_id 与 splitter key 原来共用 window_geometry 一个扁平 dict，
   同 key 会互相覆盖（写几何再写分栏，读几何拿到的是分栏数据）。
   现在 splitter 走独立前缀，读取时兼容旧键（老配置不丢）。
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QByteArray

log = logging.getLogger(__name__)

__all__ = ["get_window_geometry", "set_window_geometry",
           "get_splitter_state", "set_splitter_state"]

# splitter 状态的键前缀：与 dialog_id 分开命名空间，避免同 key 覆盖
_SPLITTER_PREFIX = "splitter:"


def _decode(stored):
    """base64 文本 → QByteArray；解不出来就 None（几何恢复失败不该阻止启动）。"""
    if stored and isinstance(stored, str):
        try:
            return QByteArray.fromBase64(bytes(stored, "ascii"))
        except Exception:
            pass
    return None


def get_window_geometry(cfg, dialog_id):
    stored = cfg.cfg.get('window_geometry', {}).get(dialog_id)
    return _decode(stored)


def set_window_geometry(cfg, dialog_id, geometry):
    encoded = geometry.toBase64().data().decode()
    with cfg._lock:
        store = cfg.cfg.setdefault('window_geometry', {})
        if store.get(dialog_id) == encoded:
            return                      # 没变就不写盘（见模块说明①）
        store[dialog_id] = encoded
        cfg.save()


def get_splitter_state(cfg, key):
    store = cfg.cfg.get('window_geometry', {})
    # 先读新键（带前缀），再回落到旧键——老配置里存的是不带前缀的
    stored = store.get(_SPLITTER_PREFIX + key)
    if stored is None:
        stored = store.get(key)
    return _decode(stored)


def set_splitter_state(cfg, key, state):
    encoded = state.toBase64().data().decode()
    new_key = _SPLITTER_PREFIX + key
    with cfg._lock:
        store = cfg.cfg.setdefault('window_geometry', {})
        if store.get(new_key) == encoded:
            return                      # 已经是新键且没变，不用写
        store[new_key] = encoded
        # 注意：**不能**删旧键 store[key]。那个键可能是同名 dialog_id 的几何数据
        # （"main" 既是窗口也可能是分栏 key），删了就把几何丢了。
        # 旧键留着无害：get_splitter_state 优先读新键，旧值只作兼容回落。
        cfg.save()
