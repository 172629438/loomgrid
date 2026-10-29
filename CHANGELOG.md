# Changelog

本文件记录现役版本的变更。历史快照（v1.1 ~ v2.3.0、v2.29）见 [Releases](https://github.com/172629438/loomgrid/releases)。

---

> ## 版本重新编号说明（2026-10-28）
>
> v3.2.2 ~ v3.2.7 与 v2.3.1 ~ v2.3.3 这些中间版本有两个共同问题：
> **源码从未提交进仓库**（改完直接打包，下一版覆盖本地文件），
> **Release 上的包又打不开**（cx_Freeze 排除 `unittest` 导致，见下）。
> 两者叠加的结果是这些版本号既无源码可追溯、又无可用安装包可验证。
>
> 按「每一个发布版本都必须有源码、都必须有能打开的 exe」这条标准，这些版本号被撤销并重新使用：
>
> | 原版本 | 新版本 | 依据 |
> |---|---|---|
> | Ultra v3.2.8（最终代码） | **v3.2.2** | v3.2.8 是 v3.2.7 的超集，v3.2.7 又是 v3.2.2 起全部修复的累积 |
> | Fusion v2.3.4（最终代码） | **v2.3.1** | v2.3.4 是 v2.3.1 起全部修复的累积 |
>
> 原各版本的更新说明**全部合并进新版本条目，一条未删**。版本号变了，工作记录没有丢。

---

## v3.2.2 — 2026-10-28（Ultra，合并原 v3.2.2 ~ v3.2.8）

**本版是 Ultra 的累积版。** 原 v3.2.2 到 v3.2.8 七个版本的全部修复都在里面，下面按主题归类。

### 〇、专业分析窗口打开即崩（2026-10-29 追加）

这是补 README 截图时意外挖出来的：想给「专业分析」界面拍一张图，窗口一构造就抛异常。
顺着查下去，发现是 v3.2fix 重构留下的残留，**任何用户点「专业分析」都会崩**。

五处硬伤：

| 位置 | 症状 |
|---|---|
| `_init_ui` | `tokens['BG_CARD']` —— 这个键在 LIGHT/DARK 里从不存在，直接下标就崩 |
| `_init_ui` | `right_layout = right_widget = None` 之后再没创建过，却用了两次 |
| `_init_ui` | `_month_checks` 只在回调里初始化，启动阶段就有人读它 |
| `_build_left_panel` / `_init_ui` | `_step_labels` 被定义两次（dict 和 list 互相覆盖） |
| `_recalc_spaces` | 对已销毁的 QLabel 调 setText，抛 libshiboken 异常 |

后两条是同一类问题：重构时属性被覆盖、控件被重建，读的一方拿到的还是旧对象。
修法是统一键名、把 list 那套改名 `_step_bar_items`、并加 `_safe_step_label()` 包一层。

修完后用注入数据构造窗口，能显示完整界面并跑出真实计算：
`[自动需量] 1604.7kW, ¥64,188`。

**仍未修**：窗口关闭时 `closeEvent` 读已销毁的 `pv_algo_combo` / `rated_power_spin`，
会抛 RuntimeError（被 Qt 吞掉，不崩程序）。同一批重构残留的清理顺序问题。

### 一、打包与启动（原 v3.2.8）

**Release 上的 exe 从 v3.2.0 起就打不开。这版把它修好了。**

双击即退，退出码 `0xC0000005`，无窗口、无日志、无任何输出。两层根因：

**第一层：cx_Freeze 把 ImportError 吞成访问冲突**

```
_setup_font()  →  import matplotlib.pyplot
               →  rcsetup → _fontconfig_pattern
               →  pyparsing → pyparsing/testing.py
               →  import unittest
               →  ModuleNotFoundError
```

打包脚本的 `EXCLUDES` 从 v3.2.x 起就排了 `unittest`（当「测试用的」省体积），而 cx_Freeze 的 `base="gui"` 把这个 ImportError 吞成了 `0xC0000005` —— **错误信息从未输出过**。这是为什么前十个排查方向（zip 损坏、DLL 缺失、OpenGL 设置、RecursionError…）全都摸不到边。定位方式是换 PyInstaller 打 console 版，traceback 直接打出来。

**第二层：windowed 下 `sys.stderr` 是 `None`**

模块级那行 `log.add(sys.stderr, ...)` 在无控制台打包后 `sys.stderr` 为 `None`，loguru 抛 `TypeError: Cannot log to objects of type 'NoneType'`。这也是 console 版能跑、`--windowed` 版不能的原因。

| 位置 | 修法 |
|---|---|
| `_setup_font()` | 包 `try/except`：一个可选的字体注入不该能让整个程序起不来 |
| loguru 挂 sink | `if sys.stderr is not None:` 再挂；标准 logging 分支同样判空 |

**打包链整体换 PyInstaller**，打包脚本收进仓库（`tools/pack_release.py`），末尾有硬性启动检查——包起不来直接判打包失败，不再交出「测试全绿但双击没反应」的包。

### 二、主题与界面（原 v3.2.7）

两个静默错配，都属于「界面看着不对，但没有任何报错」。

**M1 · 缺依赖时主题静默降级**：`main()` 里 `try: from qt_material import ...` / `except Exception: pass`，缺依赖时 ImportError 被吃掉，表现出来只是「界面看着怪」。改为分开捕 `ImportError` 与一般异常，各写一行 warn 并说明会退回内置主题。

**M2 · 主题状态与 QSS 脱节**：`_toggle_theme` 调 `set_global_theme()` 会让 `DesignTokens` 立即返回另一套 tokens，而已生成的 QSS 还是旧主题 —— 典型症状是切到深色后大标题变成白底浅灰字。改为：`_toggle_theme` 只存偏好、不改全局主题（它本来就写着「重启后生效」）；`Config.__init__` 里主题真的变了才 `invalidate_qss_cache()` + `apply_global_stylesheet()`。

**M3 · 主题提示措辞**：补一句「当前界面不会立刻变化」。

**界面实拍**：README 新增 5 张真跑出来的图（Ultra 空态 / 导入后 / 负荷曲线深色 / 浅色 / Fusion 导入后），由脚本造数据注入后抓控件，不 P 图不摆拍。

### 三、报告导出（原 v3.2.6）

| 编号 | 问题 | 修法 |
|---|---|---|
| **L1** | **任一经济指标缺失，整份 HTML 报告生成不出来**。`daily_cycles` 两个键都没有时塞进字符串 `'-'`，`'{:.1f}'.format('-')` 立刻抛 `ValueError`。而「某个指标没算出来」恰恰是最常见的情况 | 函数开头统一预清洗：7 个数值字段缺失或非数值一律置 0，`daily_cycles` 由 `annual_cycles/365` 折算再兜底 |

新增 `tests/verify_report.py` 五组 28 项：HTML 结构、数据注入、XSS 转义与缺失容错、Excel 契约、文件清单报告。XSS 已验证生效（公司名 `<script>alert(1)</script>` → `&lt;script&gt;`）。

两条记录下来的边界：`report_template.html` 从未进过仓库（`.gitignore` 排掉了 `*.html`），jinja2 分支永不生效；`_export_report_xlsx` 依赖主题字典，脱 GUI 难以构造，测试降级为契约级。

### 四、测算链路（原 v3.2.5）

| 编号 | 问题 | 修法 |
|---|---|---|
| **K1** | **零/负负荷也产出 `-inf%` 方案**。`scan_capacity` 不过滤无收益方案，全零负荷时 IRR 退化为 `-inf` 照样入列 | 过滤年省 ≤ 0 或 IRR 非有限的方案；全被滤掉时返回空列表 |

新增 `tests/verify_pipeline.py` 八组 43 项：数学不变量、物理边界、单调性。

证实了一个设计事实：`power_factor` 默认 0.30，储能功率恒为峰值 30%，不随负荷形状优化 —— 所以「容量测算」实际是「按峰值比例套公式」。单点负荷 `[500]` 和 `[1e9]×96` 算出的 IRR 完全相同，系统是线性的。

### 五、端到端与数据接入（原 v3.2.2 ~ v3.2.4）

**端到端测试（原 v3.2.4）** 新增 `tests/verify_e2e.py` 九组 61 项，覆盖六种负荷形态 × 多种表格式，关键是**逐点比对**（宽表里每个数必须等于源文件里那个数，误差 ≤1e-9）。

| 编号 | 问题 |
|---|---|
| **J1** | 转置宽表被误判成宽表（「数值缓慢递增的列当时刻列」的兜底把日期列当时刻列了） |
| **J2** | 「总电量」列拿不到 `load_col`（累计词表缺这个词） |
| **J3** | 转置后日期列变成 index，`existing` 恒为空，整表被跳过 |
| **J4** | `accumulated` 长表形被送进宽表分支，扫出 0 个分组 |
| **J5** | **Excel 引擎选择的三重错**：`'python-calamine'.split('-')[0]` 得到 `'python'` 永远 import 失败（calamine 装了也白装）；`.xlsb` 被归到 openpyxl 分支；包里既无 calamine 也无 pyxlsb |
| **J6** | Fusion 拖入任意文件都被接受（不检查扩展名） |

**数据接入层（原 v3.2.3）** 新增 `tests/verify_ingest.py` 九组 97 项：

| 编号 | 问题 |
|---|---|
| **I1** | 无功/电流列抢占负荷列（含「功率」字就命中强匹配词，先到先得） |
| **I2** | 8 位数字日期（`20261001`）解析为 None |
| **I3** | Fusion 示数转换无日重置/月累计判别 |
| **I4** | Fusion 示数差没做 `shift(-1)`，整条曲线右偏一个采样步长 |
| **I5** | 日重置判据只认「日初读数接近 0」，基准非 0 的日累计表被误判 |
| **I6** | `time_kw` 里的 `点`/`时` 误吃「测点1」这类列 |
| **I7** | 跨天回落被当成换表/清零，每天丢一个点 |
| **I8** | 列名明确是「功率」的表被数值判据改判成示数表 |
| **I9** | 单日示数表被拒绝转换（只导一天是最常见用法） |

**数据接入层修复（原 v3.2.2）**：

| 编号 | 问题 |
|---|---|
| **N6** | 多值列长表被整表误判成转置宽表（「正向有功功率」被累计词踢出负荷列，随后数值列被当日期列） |
| **N9** | Fusion 列分类词表是旧版（无功/电流/电压与有功并列，累计词里混进时间词） |

同时新增 `tests/verify_alg_parity.py`（19 项，算法层与主程序 26 个同名函数逐字比对）与 `tests/verify_invariants.py`（13 项数值不变量）。

### 累积修复清单

| 编号 | 影响 | 一句话 |
|---|---|---|
| N1 | Ultra | 「结算明细」按钮自加入起 100% 失败（参数个数错 + 返回值类型错） |
| N2 | Ultra | LP 放电收益只按卖电价计价，纯自用场景完全不碰电池 |
| N3 | 双端 | 快算充电空间多乘了 η，与精算口径分叉 |
| N4 | 双端 | `segmented_spaces` 单侧追加段，segs 与 labels 不等长 |
| N5 | Fusion | 平段被当放电段统计，放电空间虚高 |
| N6 | 双端 | 多值列长表被误判成转置宽表 |
| N9 | Fusion | 列分类词表旧版 |
| I1~I9 | 双端 | 接入层九条（见上） |
| J1~J6 | 双端 | 端到端六条（见上） |
| K1 | Ultra | 零/负负荷产出 -inf% 方案 |
| L1 | Ultra | 报告导出格式炸弹 |
| M1~M3 | Ultra | 主题静默降级、状态脱节、措辞 |
| — | Ultra | exe 从 v3.2.0 起打不开（cx_Freeze + sys.stderr 双层根因） |

### 验证

16 套脚本 **433 项断言全绿**：`verify_ingest` 97、`verify_e2e` 61、`verify_pipeline` 43、`sweep_algorithms3` 30、`verify_report` 28、`verify_gui_signal` 26、`verify_algorithms_deep` 25、`verify_gui_flow` 24、`verify_alg_parity` 19、`verify_feature_restore` 15、`verify_invariants` 13、`verify_fixes_batch2` 13、`verify_algorithms_math` 12、`verify_fixes_batch1` 12、`verify_gui_smoke2` 10、`verify_feature_sync` 5。

打包产物另有启动闸门：真启动 + 检查进程存活 + 读日志确认无依赖缺失。

### 已知未修

| 编号 | 现象 | 为什么不修 |
|---|---|---|
| N7 | 精算充电空间比快算低约 1.59% | 方向保守，改它会翻全部历史结果。已写成断言，偏差超 3% 报警 |
| N8 | `FinanceEngine.irr` 异常返回 `0.0`，`calc_irr` 返回 `None` | 触发极罕见，改动需连带梳理全部调用方 |
| — | `enforce_terminal_soc` 默认 False | 真实调度里库存有价值，但评估项目收益时等同于「变卖库存」。已写进测试注释，默认值留给使用者决定 |
| — | `report_template.html` 不在仓库 | jinja2 分支永不生效，实际走 f-string 回退。输出正确，但「模板可定制」是空的 |

---

## v2.3.1 — 2026-10-28（Fusion，合并原 v2.3.1 ~ v2.3.4）

**本版是 Fusion 的累积版。** 原 v2.3.1 到 v2.3.4 的全部修复都在里面。

Fusion 与 Ultra 共享大部分接入层代码，所以 v3.2.2 条目里的 I1、I3、I4、I5、I7、I9、J1~J6、N3、N4、N6、N9 同样适用于本版。以下是 Fusion 独有的部分：

| 编号 | 问题 | 修法 |
|---|---|---|
| **I3** | 示数转换是旧版：无日重置/月累计判别，月度累计表被直接差分，跨天跳变污染整条曲线 | 移植 Ultra v2.9c 判别链 |
| **I4** | 示数差没做 `shift(-1)`：差值记在后一个时间点，整条曲线右偏一个采样步长 | 移植 Ultra v2.28 的 diff 前移 + 换表负跳变标记 |
| **J6** | 拖入任意文件都被接受（模块里甚至没有 `_is_supported_file`），.txt / .zip / .exe 也进列表 | 补 `_is_supported_file` + 文件夹递归 + 扩展名过滤（仅 csv/xlsx/xls/xlsm/xlsb） |
| **N5** | 时段分类用 `else` 兜底，把平段（flat）也当放电段统计 | 改为 `elif DISCHARGE`，平段两侧都计零 |
| **N9** | 列分类词表旧版：`load_kw` 把无功/电流/电压与有功并列，`accumulated_kw` 里混进 `点`/`时` | 强/弱分层：有功类强匹配，无功/电流/电压仅作候补；剔除时间词 |

### 验证

与 Ultra 共用 `verify_ingest.py`（97 项，每项双端各断言一次）与 `verify_e2e.py`（61 项，含双端一致组）。

### 版本标识

本版窗口标题与关于对话框统一为 `v2.3.1`。此前 Fusion 内部一直是「Pro 融合版 v3.2」，与 Release tag（v2.3.x）长期错位；重新编号时一并修正。

---

## v3.2.1 — 2026-10-27

两个现役版本同一批修复。这条版本号只修缺陷，不加功能。

### 修复

| 编号 | 影响版本 | 问题 | 修法 |
|---|---|---|---|
| **N1** | Ultra | 「结算明细」按钮**自加入起 100% 失败**：调用 `calc_monthly_settlement` 时只传 1 个位置参数（需 8 个）且传了两个不存在的关键字；返回值是 `SettlementPeriod` 数据类却按 dict 调 `.get()`。被 `try/except` 吞成「结算失败」弹窗，从未出过一次结果 | 重写为复用 `calc_economic_summary` 的统一经济口径，不再维护第四套结算实现 |
| **N2** | Ultra | LP 调度的放电收益只按卖电价计价（`-sell_price[t] * dt`），但放电实质是替代购电、省的是**买电价**。纯自用场景下 LP 完全不碰电池，或把初值 SOC 白送掉 | 改为 `max(买价, 卖价)` |
| **N3** | Ultra + Fusion | 快算 `compute_daily_spaces` 的 Mode A 充电乘了 `CHARGING_EFFICIENCY`，而精算 `compute_segmented_spaces` 与经济模型的注释口径都定义为「电网侧吸收量、不乘 η」 | 快算去掉 `× η`，与精算对齐 |
| **N4** | Ultra + Fusion | `compute_segmented_spaces` 只在单侧追加段数组，充电段时放电列表不长、反之亦然，导致 segs 与 labels 不等长，下游按索引取值错位 | 四个列表（充/放电 × 值/标签）同步补零占位 |
| **N5** | Fusion | 时段分类用 `else` 兜底，把平段（flat）也当放电段统计，放电空间虚高 | 改为 `elif DISCHARGE`，平段两侧都计零 |
| **示例** | 仓库 | `examples/quickstart.py` 三处：① `calc_npv` 参数顺序传反，NPV 显示 +640 万、真值 −5.4 万；② LP 返回的 ch/dis 是功率数组，打印时未乘 dt，报出「放电 > 充电」的反物理数字；③ 用 `greedy_dispatch_annual` 演示峰谷套利，而该函数文档写死「仅适配新能源消纳优先场景」 | 三处全修，README 示例表按实跑结果重算 |

### 数值影响（新旧基准对照）

固定场景：平坦 500 kW、变压器 800 kVA、谷段 11 h、96 点。

| 项 | v3.2.0 | v3.2.1 | 变化 |
|---|---:|---:|---|
| 快算 Mode A 充电空间 | 3,069.00 kWh/日 | 3,300.00 kWh/日 | **+7.53%** |
| 快算 Mode A 放电空间 | 3,255.00 kWh/日 | 3,255.00 kWh/日 | 不变 |
| segmented 充电段 / 放电段 | 5 / 4 | 6 / 6 | 结构对齐 |
| LP 收益（sell=0，纯自用） | −0.00 元/日 | 1,069.93 元/日 | 从失效到正确 |
| LP 收益（sell=0.35×买价） | 187.07 元/日 | 1,069.93 元/日 | **+472%** |
| quickstart 示例 NPV | +6,405,314 元 | −53,795 元 | 符号纠正 |

**对既有方案的影响**：充电空间 +7.53% 会让依赖快算的容量建议偏大一点；LP 侧收益上调意味着此前用 LP 做的容量基准偏保守。已经交付的方案不必回溯，但新方案请用 v3.2.1 及之后版本重跑。

### 兼容性

配置格式（`CONFIG_VERSION=20`）未变，无破坏性变更。旧项目文件可直接打开。

### 验证

- 7 套脚本共 **97 项断言全绿**（12 手算对拍 + 30 项广度巡查 + 55 项靶向回归）
- 两个主程序 `py_compile` 通过

---

## v3.2.0 — 2026-10（上一轮）

缺陷修复 31 条 + 特性回填，详见 [`docs/缺陷修复报告.md`](docs/缺陷修复报告.md)。

## v2.3.0 / v3.2.0 之前

见 [`docs/版本演进.md`](docs/版本演进.md)。
