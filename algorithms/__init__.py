# -*- coding: utf-8 -*-
"""LoomGrid algorithms — 工商业储能测算的纯算法层。

七个模块，从 3.6 万行桌面主程序里剥离而来。标准是：不依赖 GUI、不依赖全局
状态、能单独 import 就跑。

用法::

    from loomgrid_algos import periods, spaces, dispatch

    # 或者只取需要的函数
    from loomgrid_algos.dispatch import optimal_dispatch_lp
    from loomgrid_algos.finance import npv, irr, payback_period

模块之间互不依赖，可以只装需要的那部分。模块按需加载——``import loomgrid_algos``
本身不做任何重依赖的导入。

    ==============  ====================================================
    periods         电价时段归一化、类型词表映射、分段合并、价格序列
    spaces          充放电空间（逐日 / 分段两种口径）
    dispatch        贪心 / 逐日仿真 / 线性规划三种调度
    milp            MILP 容量规划（多设备选型 + 更换周期 + 需量电费）
    finance         NPV / IRR / 回收期 / LCOE
    loadgen         三层叠加负荷生成引擎
    diagnostics     负荷指纹分析与行业匹配
    ==============  ====================================================
"""

from importlib import import_module

__version__ = "3.2.2"

_MODULES = (
    "periods",
    "spaces",
    "dispatch",
    "milp",
    "finance",
    "loadgen",
    "diagnostics",
)

__all__ = list(_MODULES)


def __getattr__(name):
    """按需加载子模块（PEP 562）。"""
    if name in _MODULES:
        module = import_module(f".{name}", __name__)
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(__all__)
