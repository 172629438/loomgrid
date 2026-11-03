# -*- coding: utf-8 -*-
"""窗口状态：记住每个对话框上次的几何尺寸与分栏位置。

这 4 个函数原来挂在 Config 上，但它们不是业务配置，是界面状态：
存的是 QByteArray（saveGeometry / saveState 的产物）的 base64 文本。
放进 services 会让将来的 API 冒出一个「窗口几何」接口，语义上不成立。

约定：直接读写 Config.cfg 字典本体（与 Config 共用同一个配置文件），
但不经过 Config 的业务方法——职责不同，不该混在一个类里。
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QByteArray

log = logging.getLogger(__name__)

__all__ = ["get_window_geometry", "set_window_geometry",
           "get_splitter_state", "set_splitter_state"]


def get_window_geometry(cfg, dialog_id):
    stored = cfg.cfg.get('window_geometry', {}).get(dialog_id)
    if stored and isinstance(stored, str):
        try:
            return QByteArray.fromBase64(bytes(stored, 'ascii'))
        except Exception:
            pass
    return None


def set_window_geometry(cfg, dialog_id, geometry):
    with cfg._lock:
        cfg.cfg.setdefault('window_geometry', {})[dialog_id] = geometry.toBase64().data().decode()
        cfg.save()


def get_splitter_state(cfg, key):
    stored = cfg.cfg.get('window_geometry', {}).get(key)
    if stored and isinstance(stored, str):
        try:
            return QByteArray.fromBase64(bytes(stored, 'ascii'))
        except Exception:
            pass
    return None


def set_splitter_state(cfg, key, state):
    with cfg._lock:
        cfg.cfg.setdefault('window_geometry', {})[key] = state.toBase64().data().decode()
        cfg.save()
