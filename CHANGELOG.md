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

## v3.2.9 — 2026-11-02（Ultra）

> 与 Fusion v2.3.6 的 C1/C2/C4/C5/C6 对齐。
> v3.2.3~v3.2.8 是历史编号（修复已并入 v3.2.2），故跳到 v3.2.9。

### C1 无法识别的时间点不再被静默丢弃

与 Fusion 同源：`_parse_min` 失败返回 `9999`，`/60 = 166.65` → `argsort` 排到末尾
→ 所有时段都在 0~24 内，`searchsorted` 找不到 → 该点连同负荷数据静默消失。

Ultra 版在入口多了形状检查，其余结构一致。同样改为入口显式检出 `> 24.0`、
计数、`log.warning`、从 `hours` 与 `load_array` 同步剔除；全无效时记 error 并
返回 `[], [], [], []`（保持 Ultra 自己的返回风格，与 Fusion 的 `[empty], [empty], [], []` 不同）。

### C2 删除 compute_daily_spaces（79 行死代码）

零调用。删前全库确认只在定义行出现一次。三处引用一并处理：
① 函数本体删除；② 原文备份 `_tmp/ultra_compute_daily_spaces.removed.txt`；
③ L17542 的口径注释原本写着「见 compute_segmented_spaces / compute_daily_spaces」，
已改为只指向现役实现，并注明另一函数已删——否则后来的人会去找一个不存在的函数。

### C4 全零段列不再占位（顺带消除重复列）

**先发现的问题比 C4 本身更严重**：`compute_segmented_spaces` 的三个分支
（充电段 9754/9759、放电段 9777/9780、平段 9786/9787）都向 `charge_labels` 与
`discharge_labels` 同步 append，两个列表因此**内容完全相同**；
而表头是 `['月份'] + charge_labels + discharge_labels`——**每个段出现两次**。

按 C4 的判据（所有月份该段之和为 0 则隐藏）扫描后：

- 充电段 → 充电列有值保留，放电列全零隐藏
- 放电段 → 放电列有值保留，充电列全零隐藏
- 平段 → 两边全零，两列都隐藏

**净效果：每个段只出现一次，平段消失。**

判据提成 `_visible_seg_indices()`，表格、Excel 导出、CSV 导出三处共用一个实现——
否则界面上看不到的列，导出文件里还在。

**连带效果**：「月最小值」原本取 `min(seg_values)`，而 `seg_values` 含大量占位零列，
所以恒等于 0；跳过全零列后它才等于真实的最小段值。

### C5 编码探测的 chardet 从未真正运行

**这是 Ultra 独有的严重缺陷**：`_detect_enc` 把 chardet 检测整段写在
`except (OSError, IOError)` 里，而**打开成功的文件根本不会进入那个分支**。

```
try:
    with open(fp,'rb') as f:
        if f.read(3) == BOM: return 'utf-8-sig'
except (OSError, IOError):        # ← 只有打不开才进来
    ...
    chardet.detect(raw)           # ← 而这里再次 open 同样会失败
```

正常路径：读 3 字节 → 非 BOM → try 块正常结束、不抛异常 → **except 不执行**
→ chardet 从不参与 → 直接落到 fallback 顺序尝试。

UTF-8 / GBK 靠 fallback 循环能兜住，掩盖了问题；**Big5、Shift-JIS 这类
既非 UTF-8 也非 GBK 的编码只可能靠 chardet 识别**，对它们彻底失明，
最后落到 latin1，整表乱码且无声。

修法：chardet 移到正常路径（BOM 检查之后），保留原有的 mtime 缓存机制，
识别出的 GB 系统一升 `gb18030`，落到 `latin1` 时 `log.warning`。

**另修两处**：
① 整块路径的编码回退链顺序是 `['utf-8-sig','gbk','gb18030','gb2312','latin1']`——
`gbk` 排在 `gb18030` 前面（超集应优先），且缺 `utf-8`。已改为
`['utf-8-sig','utf-8','gb18030','gbk','gb2312','latin1']`，与 Fusion 对齐。
② 回退链落到 `latin1` 时同样加 `log.warning`。

### C6 CSV 坏行不再拖垮整表

主路径 `on_bad_lines='skip'`。同 Fusion 踩过的坑：callable 只有 python/pyarrow
引擎支持，C 引擎会抛 `ValueError`，故主路径用字符串，回退分支才挂 callable。

**另修一处**：`fsize` 取大小失败时原设 `0`（当成小文件整块读入），
一个 2GB 网络盘文件可能直接 OOM。改为 `-1` 并让分块判断包含 `fsize < 0`——
与 Fusion 侧早先的修法对齐。

---

## v2.3.6 — 2026-11-02（Fusion）

> 用户确认后的口径落地。五项都是「代码现在这么做、也可以那么做」的地方，
> C3（月最小段总和取整行最小值）经确认保持不动。

### C1 无法识别的时间点不再被静默丢弃

**原行为**：`_parse_min` 解析失败返回 `INVALID_MINUTES(9999)`，`/60 = 166.65`。
`compute_segmented_spaces` 里 `np.diff` 非单调触发 `argsort`，把它排到末尾；
所有时段都在 0~24 之间，`searchsorted` 永远找不到它 → **该点连同负荷数据一起静默消失**。

96 点里丢 1 点，曲线看不出缺口，但日均值已经偏了。用户不会怀疑数据，只会怀疑工具。

**现行为**：入口显式检出 `> 24.0` 的值，计数后 `log.warning`，从 `hours` 与
`load_array` 同步剔除。若全部时刻都无效，记 error 并返回空结果。

⚠️ **双实现提醒**：该函数注释写明「Pro(95行) 与 Ultra(200行) 独立实现，
修改任一侧需同步更新」。本次只改 Fusion，**Ultra 侧未同步**，需人工对齐。

### C2 删除 compute_daily_spaces（85 行死代码）

零调用，且口径与现役 `compute_segmented_spaces` 差 7.53%（函数内自述）。
原文备份在 `_tmp/compute_daily_spaces.removed.txt`。

删除前全库确认：`compute_daily_spaces` 只在定义行出现一次，无任何调用点。

函数内三条历史修复注释已迁出留档：

- `v2.3.1fix`: 原为 else，把平段（flat）也置成放电段，导致放电空间虚高。
- `v2.3.1fix(N3)`: 充电空间是「电网侧还能灌进去多少」，不再乘充电效率。
- `v3.0fix`: 先设负负荷掩码(2D), 再叠加时段约束

### C4 全零段列不再占位

平段在时段表里恒为 0，却占一列。现改为：先扫全部月份，**只有所有月份的所有段实例
都为 0 的段才隐藏**；任一月出现非零值就照常显示，避免藏掉真实数据。

计算层（`c_segs`/`d_segs`）完全不动，只是不显示。

**连带效果**：「月最小段总和」原本取 `min(seg_values)`，而 `seg_values` 包含平段的 0，
所以有时段含平段时该值**恒等于 0**。跳过全零列后它才等于真实的段最小值。
这不是本次引入的问题，是原本就有的表现被顺带修正。（C3 那条「应取各段 min 之和」
的口径本身仍未动。）

### C5 编码兜底不再静默

**原行为**：`DEFAULT_ENCODING_LIST` 末位是 `latin1`，它对任何字节都能解码、永不抛异常。
落到它意味着「前面全部识别失败」，但读出来只是满表错字——**看起来完全正常**。

**现行为**：① 落到 `latin1` 时 `log.warning` 明确记录；
② `_read_file_full` 用 `latin1` 读取时显式 `encoding_errors='replace'`，
让坏字节变成替换符而非抛异常中断。

### C6 CSV 坏行不再拖垮整表

**原行为**：`pd.read_csv` 未设 `on_bad_lines`，pandas 1.3+ 默认 `'error'`——
一行列数不匹配，整张表读不出来。

**现行为**：主路径设 `on_bad_lines='skip'`。

**实现上踩了一个坑**：第一版写成 callable 记行号，实测在 C 引擎上直接
`ValueError: on_bad_line can only be a callable function if engine='python' or 'pyarrow'`
——callable 只有 Python/pyarrow 引擎支持。而 C 引擎比 Python 引擎快数倍，
为记行号降级整条链路（尤其分块路径处理大文件）代价不成比例。

**最终**：主路径（C 引擎）`'skip'`，日志报「存在坏行已跳过」；
回退分支（本就是 Python 引擎）挂 callable，能记下具体行内容。
真正需要排查的情形恰好在回退时，那时能拿到行号。

### K5 版本号三处硬编码，其中一处串到了 Ultra 的号段

**为什么现在才发现**：以前所有打包验证都是「进程存活 + 读日志」，这两项都不在判据里。
按 `AGENTS.md` 的「读窗口标题」一验，第一个窗口就是欢迎弹窗，标题和正文全露出来了。

```
主窗口标题    "负荷曲线批量生成工具 Pro 融合版 v2.3.1"   ← 当前 v2.3.6，差五个版本
欢迎弹窗     "负荷曲线批量生成工具 Pro v3.2"            ← v3.2 是 Ultra 的号段
关于对话框    "负荷曲线批量生成工具 Pro 融合版 v2.3.1 (全面优化修复版)"
```

**修法**：定义 `FUSION_VERSION` / `FUSION_APP_NAME` 两个常量，三处都引用它。
以后发版只改一处。

**打包验证**（这是 `pack_release.py` 打出来的真实产物）：

```
产物    1368 文件 / 308.0 MB   压缩后 135.3 MB
冒烟    启动 20s 存活 ✓
窗口    '欢迎 — LoomGrid Fusion v2.3.6'   最终窗口数 1   ✓
```

同时新增 `tools/verify_exe_window.py`——按窗口标题判定，不用进程存活。

---

---

## v2.3.5 — 2026-11-02（Fusion）

> 上一个可用版本号是 v2.3.5：v2.3.3 / v2.3.4 是历史版本，其修复已并入 v2.3.1，
> 代码里留下的 `v2.3.3fix` / `v2.3.4fix` 注释属于历史标记。

### 修复：三个对话框处理器缺异常保护，异常时线程永久卡死

**这是本轮最严重的一处**——它不是算错数，是**整个程序失去响应**。

**三个 handler 的共同模式**：`_handle_mapping_request` / `_handle_preview_request` /
`_handle_export_confirm_request` 都是

```python
dlg = XDialog(...)                    # ← 可能抛
if dlg.exec() == QDialog.Accepted:
    self._thread.set_X_result(...)
else:
    self._thread.set_X_result(None)
self._dialog_active = False
self._set_ui_state('processing')
```

**线程侧**是无超时等待：

```python
self.signals.X_request.emit(...)
self.dialog_event.clear()
self.dialog_event.wait()              # 没有 timeout
```

**三者原本都没有 try/except。** 只要中间任何一步抛出——对话框构造失败、
`_detect_file_format_type` 读文件出错、`_handle_export_confirm_request` 里
`data['filename_tpl']` 这类十余个直接取键缺一个（KeyError）——`set_X_result`
**永远不会被调用**，`dialog_event` **永远不会被 set**，线程**永久阻塞**，
UI 停在 `dialog_open`，用户只能强杀进程。

**修法**：新增 `_dialog_handshake` 装饰器，挂在三个 handler 上。

它不改 handler 的 body，只在外面套一层：

1. 临时把 `self._thread.set_X_result` 换成记账包装，记录"已回复"
2. 异常时记日志 + 弹一次提示（提示本身失败也不影响后续）
3. `finally` 里若仍未回复则补 fallback；并把原本也会被跳过的
   `_dialog_active = False` / `_set_ui_state('processing')` 收进 finally

**为什么用装饰器**：改 body 要整段缩进重排 + 替换所有 `set_*` 调用点，
diff 大且容易改错缩进；装饰器只新增一个函数加三行 `@`，body 一个字符不动。

**验证**（四个路径，用 AST 摘出装饰器独立跑）：

```
A 正常回复        → 回复 [{'mapped': True}]，UI 恢复 processing   ✓
B 抛 KeyError     → 兜底回复 ['cancel']，UI 恢复 processing       ✓
C 什么也没回      → 兜底回复 ['cancel']，UI 恢复 processing       ✓
D _thread 方法缺失 → 不崩，UI 恢复 processing                     ✓
```

---

### 修复：进程管理器不可用时，取消按钮静默失效

**机理**：`ProcessingThread.run` 里 `mp.Manager()` 可能创建失败（内存紧张或权限受限），
此时 `_mp_cancel_event = None`；`_execute_plot_tasks` 的取消检查写成

```python
_ce = cancel_event if cancel_event is not None else threading.Event()
if _ce.is_set(): ...
```

**这个 `threading.Event()` 是在循环内每轮新建的**，新建的永远 unset，
`is_set()` 恒为 False。注释写着「进程管理器创建失败时仍可取消」，实际是死代码。

**后果**：管理器不可用时，用户点取消，正在跑的绘图任务不会提前退出，
只能等全部完成——观感是「取消没反应」。

**修法**：`_generate_charts` 里用 `self.stop_event` 兜底（`cancel()` 会同时 set 它），
`_execute_plot_tasks` 的回退 Event 提到循环外。

---

### 修复：时间列被「测点编号」「节点」抢占

**现象**：列名为 `测点编号` / `节点` 的表格，整表时刻解析失败。

**根因**：`_smart_classify_columns` 的 `time_kw` 里有单字 `'点'`，
而 `time_col` 用 `setdefault`——第一个命中的列留下。
`测点编号` 同时命中 `asset_kw`（编号）和 `time_kw`（点），
若排在真正的 `时间` 列之前就把它抢走了。
（v2.3.2fix(N9) 已把 `'点'`/`'时'` 从累计词表摘掉，时间词表里还留着，这是同一根因的另一半。）

**修法**：从 `time_kw` 删掉 `'点'`。它原本想匹配的「时间点」已被 `'时间'` 覆盖，
真正会命中的只有「测点」「节点」「采样点」——全是非时间列。

**验证**：6 组列名组合，`time_col` 全部落在 `时间`/`时刻` 上，`测点编号` / `节点` / `采样点` 不再抢占。

### 修复：长表分支的资产兜底与扫描阶段不同源

**现象**：文件名带表号（如 `表号123.xlsx`）、数据里没有资产列的表格，
报「资产号 '123' 在月份 'xxx' 无数据」，整表空。

**根因**：这是 v2.3.2 那个事故在**长表分支的遗漏**。

```
扫描阶段 _scan_long_chunk      → _extract_asset_from_name(fp, sheet) → '123'
加载阶段 _parse_long_to_standard → 写死 df_clean['__asset__'] = '未命名'
筛选                          → '未命名' == '123' 恒 False → 空
```

**修法**：给 `_parse_long_to_standard` 加可选参数 `fallback_asset`（默认 `'未命名'`，
保持原有行为），两个调用方传入与扫描阶段同一个函数的结果。

**验证**：五个场景全部通过——
无资产列 + fallback='123' → `'123'`；fallback='未命名' → `'未命名'`；
有资产列 → 用列值；不传参 → `'未命名'`；fallback 传空串 → 回落 `'未命名'`。

---

## v2.3.2 — 2026-11-02（Fusion）

### 修复：文件名带年份时整表无法加载

**现象**：导入 `2026年1月.xlsx` 这类文件，报「无法加载数据：未找到月份 '2026-01' 且资产 '2026' 的数据」。文件本身完全正常——31 行 × 97 列的宽表，首列 `数据日期`，后接 96 个 15 分钟时刻列。

**根因**：两处兜底逻辑不一致。

1. `_extract_asset_from_name()` 的最后一条规则是 `re.search(r'(\d{4,})', basename)`。对 `2026年1月` 这种文件名，它匹配到了**年份 2026**，于是把年份当成了表号。
2. 扫描阶段（`_scan_wide_chunk`）在没有表号列时用它作品牌名，记下 `资产='2026'`；
   加载阶段（`_load_group_data`）却写死 `pd.Series('未命名')`。

两边一比对：`'未命名' == '2026'` 恒为 False，mask 全空，整表报未找到。

**修法**：

- 年份排除：4 位纯数字且落在 1900–2100 区间时，不再当作表号。`2026年1月` → `未命名`，`表号12345_…` → `12345`，`资产20250099` → `20250099` 均正确。
- 兜底同源：加载阶段改用与扫描阶段同一个函数，不再写死常量。

**验证**：以用户提供的 `2026年1月.xlsx` 实测，扫描得 `asset='未命名' month='2026-01' days=31`，加载返回 `load_array shape=(31,96)`，日期 2026-01-01 至 01-31，96 个时刻，数值范围 [0.0, 993.9]。

### 修复：冻结环境下启动即弹错误框

打包后的 exe 启动就报 `AttributeError: 'NoneType' object has no attribute 'isatty'`，位置 `_check_and_install_dependencies`。

冻结环境（cx_Freeze / PyInstaller 的 GUI 模式）下 `sys.stdin` 为 `None`，而代码直接调 `.isatty()`。同类风险还包括 `sys.stdout` / `sys.stderr` 为 `None` 时任何 `print` 都会抛异常。

修法：import 之后立刻把三个标准流补到 `os.devnull`，`isatty()` 探测外再包一层 `try`。

### 修复：充放电空间表标签重复

表里出现两个「充电1」两个「放电1」。`compute_segmented_spaces` 为了让四个列表等长，
把每个段的标签同时追加到充电标签和放电标签，两个列表内容完全相同；表头又
拼成 `['月份'] + charge_labels + discharge_labels`，于是每个段名出现两遍。

正确结构是**一个段一列**：充电1 是第一段充电窗口、放电1 是第一段放电窗口、
充电2 是第二段充电窗口。充电段显示可充量，放电段显示可放量，由段自己的类型决定，
与列在左半边还是右半边无关。

修法：只取一份标签（两个列表内容相同），同名段（典型一天有三个「平段」）加序号。
列数从 14 降到 8。

每列取值 = `c_segs[i] + d_segs[i]`——同一索引下两者只有一个是非零（充段在
`charge_segments` 有值、`discharge_segments` 为零数组，放段反之），相加不会重复计入。

**实测**（浙江夏季六段）：充电1 3599.57 / 充电2 250.12 / 放电1 2966.85，平段均为 0。

### 发现但未改的两处（属于设计口径）

1. 平段占三列，但平段按定义不参与充放，值恒为 0。
2. 最后一列「月最小段总和」用的是 `min(本行全部值)`，而非「各段 min(充,放) 之和」。
   两者差很多：后者才是「这个月实际能循环多少度电」。待确认。

---

## v2.3.2 构建环境说明

本版用 **Python 3.12.13** 打包。本机默认的 3.14.5 打出的 exe 启动即 `0xC0000005` ACCESS_VIOLATION（依赖文件不缺，是 PySide6 6.11.1 在 3.14 冻结环境下的兼容问题）。换 3.12 后正常，体积也小一半（286 MB vs 649 MB）。

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
