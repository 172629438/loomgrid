# loomgrid-algos

**工商业储能测算的纯算法层** · Pure-Python algorithms for C&I battery storage sizing

从 [LoomGrid](https://github.com/172629438/loomgrid)（3.6 万行桌面主程序）里剥离出来的七个模块。不依赖 GUI、不依赖全局状态，能单独 import 就跑。

Seven modules extracted from LoomGrid, a full desktop application. No GUI, no global state — each module imports and runs on its own.

```bash
# PyPI 尚未上架，现在从 GitHub 直接装
pip install git+https://github.com/172629438/loomgrid.git

# 需要 LP 调度或 MILP 容量规划时，再补两个依赖
pip install scipy      # dispatch.optimal_dispatch_lp
pip install ortools    # milp 容量规划
```

---

## 三行上手

```python
from loomgrid_algos import dispatch

result = dispatch.optimal_dispatch_lp(
    load_kw=[120, 118, 115, 140, 160, 210],
    periods=[(0, 8, "valley"), (8, 11, "flat"), (11, 13, "peak"),
             (13, 17, "flat"), (17, 22, "peak"), (22, 24, "valley")],
    prices={"peak": 1.16, "flat": 0.67, "valley": 0.28},
    capacity_kwh=2000.0,
    power_kw=500.0,
)
```

---

## 七个模块

| 模块 | 做什么 | 额外依赖 |
|---|---|---|
| `periods` | 电价时段归一化、类型词表映射、分段合并、价格序列展开 | — |
| `spaces` | 充放电空间（逐日 / 分段两种口径） | — |
| `dispatch` | 贪心 / 逐日仿真 / 线性规划三种调度，同一组数据可对拍 | `scipy`（仅 LP） |
| `milp` | MILP 容量规划：多设备选型 + 更换周期 + 需量电费 | `ortools` |
| `finance` | NPV / IRR / 回收期 / LCOE，IRR 有二分法兜底 | — |
| `loadgen` | 三层叠加负荷生成：25 行业 × 省份基线 × 设备 Markov × 噪声平滑 | `scipy`（仅平滑） |
| `diagnostics` | 负荷指纹分析与行业匹配 | — |

模块之间**互不依赖**。`import loomgrid_algos` 本身不做任何子模块导入，用哪个加载哪个。

---

## 一个诚实的例子

`examples/quickstart.py` 会算出一组不那么好看的结论——**容量不是越大越好**：

| 容量 | 回收期 | NPV（6% / 15 年） |
|---|---|---|
| 500 kWh | 3.7 年 | +49.6 万 |
| 800 kWh | 6.5 年 | +20.0 万 |
| 1000 kWh | 9.3 年 | −5.4 万 |
| 1500 kWh | 22.3 年 | −68.8 万 |
| 2000 kWh | 73.1 年 | −132.3 万 |

这组参数（750 元/kWh、0.88 元价差、330 天）下，只有 500–800 kWh 两档 NPV 为正。瓶颈在负荷侧：峰段可削量与谷段余量把日循环锁在 484 kWh，装得再大也只能闲置。

算法不替你做判断。峰谷价差取多少、循环按几次、需量按哪个口径——这些得你自己定。它只把搬运那部分省下来。

---

## 三条调度引擎，同一组数据

同一条负荷、同一套电价（0.90 元价差）、同一个 2000 kWh 系统：

| 引擎 | 充 kWh | 放 kWh | 日套利 | 相对 LP |
|---|---|---|---|---|
| LP（线性规划） | 2,989 | 2,736 | ¥1,676 | 100% |
| 逐日仿真 | 2,189 | 1,653 | ¥1,199 | 72% |
| 贪心 | 0 | 38 | ¥11 | 1% |

差异不是实现细节，是场景不同。贪心设计上就是「新能源消纳优先」，没有光伏风电时它不主动从电网买电充电——用它算纯套利收益会得到接近零的结果。**选哪个引擎，取决于你在算什么。**

---

## 配套数据

33 个电网价区的分时电价、需量电费、分季节时段表（带政策文号出处）：
**[loomgrid-tariffs](https://github.com/172629438/loomgrid-tariffs)**

---

## 许可

MIT。
