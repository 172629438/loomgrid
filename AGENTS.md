# 协作约定 · 两台 Agent 交替迭代

> 这个项目由两台机器上的 Agent 轮流迭代：家里那台和主力那台。
> 谁在改，改完就推；**仓库是唯一真源，本地磁盘不是。**
>
> **两份文档分工：**
> - 本文（`AGENTS.md`）：**怎么做**——接手流程、上传铁律、版本号、打包环境、发布步骤
> - [`docs/ITERATION-CHECKLIST.md`](docs/ITERATION-CHECKLIST.md)：**为什么这么做**——v2.3.2 背后六个事故的完整复盘与自查清单
>
> 遇到具体操作查本文；想弄明白某条规矩的血泪来源，去读那份。
> 两份都改过时，以本文的操作步骤为准，冲突要当场消掉不要并存。

---

## 一、真源在哪

| 内容 | 位置 |
|---|---|
| Fusion 主程序（生产推荐） | `负荷曲线批量生成工具 Pro 融合版 (v2.3).py`（仓库根） |
| Ultra 主程序（功能最全） | `负荷曲线批量生成Ultra.py`（仓库根） |
| 算法模块（可独立 import） | `algorithms/` |
| 站点与在线介绍页 | `docs/`（GitHub Pages 源目录） |
| 变更记录 | `CHANGELOG.md` |

**接手第一步**：`git pull`，读 `CHANGELOG.md` 最近三条，确认你手上的认知和仓库一致。

### 不只是接手时——**动手改代码前必拉一次**

长时间干活时，clone 下来的副本会变陈。本机曾从早上 08:38 clone 后一直用到晚上 22:37
才第一次 pull，中间十四个小时没确认过远端。那次能确认“没覆盖别人”是靠事后翻 log 反推的，
不是动手前查过的——这是运气，不是流程。

```bash
# 改代码之前，先确认自己的 HEAD 就是远端 HEAD
git fetch origin
git log --oneline HEAD..origin/main    # 有输出 = 你落后了，先 rebase
```

**为什么不能靠 push 失败来发现**：如果对方改的是你没碰过的文件，你的 push 会成功，
两台就这样惄惄分叉了，而 log 上看着一切正常。

---

## 二、铁律：改完必须上传

改完代码**当场**做完这三步，不要攒：

```bash
git add "负荷曲线批量生成工具 Pro 融合版 (v2.3).py" CHANGELOG.md
git commit -m "fix(fusion): <一句话说清改了什么>"
git push origin main
```

### 上传后必须自证

不能只看 `git push` 的成功提示——要确认远端文件真的变了。

```bash
git ls-tree origin/main -- "负荷曲线批量生成工具 Pro 融合版 (v2.3).py"
git hash-object "负荷曲线批量生成工具 Pro 融合版 (v2.3).py"
```

两行输出的 blob hash 必须一致。

**用 git 对象哈希，不要用 raw.githubusercontent.com 比对内容。**
那个地址有 CDN 缓存，刚 push 完去拉可能还是旧文件，
2026-11-03 因此误判过一次「没推上去」，实际已经推了。

若确实想看内容是否含某段代码，等几分钟或直接读本地文件——
本地与 origin 的 blob hash 一致，内容就一致。

---

### schema 是契约，写调用前必须先读

**踩过三次，都是同一类错**：

```
阶段 2  协作方找 sheets[0].format_type，实际在 sheets[0].suggest.format_type
阶段 4.5 我读 sheets[0].suggest.confidence，实际在 sheets[0].confidence
阶段 4.5 我写 {path, sheets:[...]}，实际是 {path, sheet, mapping}；
         字段名是 load_col 不是 load_cols
```

三次都不是实现错，是**凭印象写请求体 / 读响应体**。

**规则**：
- 调任何 API 前，先 `read` `api/schemas.py` 确认字段名与嵌套层级
- 写测试时尤其容易犯——测试一旦读错层级，会把「响应正确」报成「功能缺失」
- 前端接线同理：字段名写错不报错，只是拿到 `undefined` 然后静默渲染空

**判据**：你说得出 `confidence` 挂在哪一层吗？说不出就是没读。

### 读实现之前，先读常量

**schema 是契约**覆盖了「接口形状」，但没覆盖「环境约束」。

踩过的具体例子（协作方）：

```js
// web/views/*.html 里这行决定了后端地址
const API_BASE = (location.protocol === 'file:' || location.port === '8899')
  ? 'http://127.0.0.1:8000' : '';
```

**只用 8899 端口起前端、8000 起后端**，否则 `API_BASE` 是空字符串，
POST 会打到静态服务器上，得到 `501 Unsupported method ('POST')` ——
**看起来像后端没实现，实际是前端根本没说清该发给谁。**

**规则**：调任何接口前，除了读 schema，还要读**决定「发给谁」的那个常量**
（API_BASE / 端口 / 协议判断）。环境约束和接口形状是两回事。

---

## 三、版本号规则

- 主程序 docstring 第 4 行有版本声明，改功能必改
- Fusion 当前 `v2.3.2`，修一次递增末位：`v2.3.3`、`v2.3.4`
- **改了计算逻辑**（影响测算结果）→ 末位 +1 并在 CHANGELOG 里单列
- **只改显示/文案** → 也 +1，但要标注「显示层改动，不影响测算」
- 版本号必须与 `CHANGELOG.md` 的条目、Release 的 tag 三者一致

---

## 三之二、打包前必须收紧的安全项

当前 `api/app.py` 的 CORS 是 `allow_origins=["*"]`，**这是给开发期用的**。

**风险不是理论上的**：这个 API 能读本机任意路径的文件、往任意目录写文件。
桌面程序里后端跑在用户本机，全开放意味着**用户浏览器里打开的任何网页都能调它**——
往 `C:\Windows\Temp\` 写文件、探测本机有哪些 Excel。

**打包前两件必做**：

```python
# 1. 收紧 CORS：只允许自身前端来源，不用 "*"
allow_origins=["http://127.0.0.1:<固定端口>", "http://localhost:<固定端口>"]

# 2. 路径白名单：/api/ingest/inspect 与 /api/export/run 的 path
#    必须落在允许的根目录内，拒绝系统路径
```

**验收判据**：从另一个源的页面 `fetch` 这个 API，应当被 CORS 拒；
传 `C:\Windows\win.ini` 作为 path，应当返回结构化错误而不是列名。

---

## 四、打包环境（重要，踩过坑）

**必须用 Python 3.12，不要用 3.14。**

| 环境 | 结果 |
|---|---|
| Python 3.14.5（本机默认） | exe 启动即 `0xC0000005` ACCESS_VIOLATION，依赖文件一个不缺，是 PySide6 6.11.1 在 3.14 冻结环境下的兼容问题 |
| Python 3.12.13（uv 管理的那个） | 正常，且包体积小一半（286 MB vs 649 MB） |

打包用隔离 venv，不要动 uv 管的那个 Python：

```bash
py -V:Astral/CPython3.12.13 -m venv <临时目录>
<临时目录>\Scripts\python.exe -m pip install cx_Freeze PySide6 numpy pandas matplotlib scipy openpyxl loguru chardet chinese_calendar psutil
<临时目录>\Scripts\python.exe tools\build_fusion_only.py
```

**不要用 `tools/build_all_exe.py`**（PyInstaller `--onefile`，本机跑不动，脚本头部已标废弃）。
**不要用 `tools/build_exe_cx.py`** 直接出 Fusion 包——它一次打 12 个目标、共享一套 lib，
抽出来的 Fusion 包会到 299 MB。

**Fusion / Ultra 单版本打包用 `tools/pack_release.py`：**

```bash
python tools/pack_release.py fusion 2.3.7
python tools/pack_release.py ultra 3.2.9
```

它走 PyInstaller `--onedir`（不是 `--onefile`，所以本机跑得动），
产出 `dist/LoomGrid-Fusion-vX.Y.Z-win64.zip`（约 135 MB / 解压 308 MB），
脚本末尾自带冒烟闸门（真启动 20s + 存活检查）。

> **两台环境不同，两条打包路径都可用，按机器选：**
>
> | 环境 | 工具 | 实测 |
> |---|---|---|
> | Python 3.12.13 + cx_Freeze | `tools/build_fusion_only.py` | 286 MB / zip 114 MB |
> | Python 3.12.9 + PyInstaller | `tools/pack_release.py` | 308 MB / zip 135 MB |
>
> 两条路径都以「产物能过窗口验证」为准，不规定必须用哪个。
>
> 2026-11-03 更正：上一版这里曾写「build_fusion_only.py 从未提交到仓库」，
> 那是搜错了——`git ls-tree origin/main tools/` 能看到该文件，
> `git log --diff-filter=D` 也没有任何删除记录。当时只搜了本地工作区，没先 pull。

### 打包后用窗口标题验证，不要只看进程存活

```bash
python tools/verify_exe_window.py "build_release/<tag>/dist/<tag>/<tag>.exe" --expect "LoomGrid Fusion"
```

判据：**无窗口、标题不含 `LoomGrid`、或标题含 error / 错误 / Fatal 才算失败。**

**不要用窗口数量判断。** Fusion 正常启动是 1 个窗口（`欢迎 — LoomGrid Fusion vX.Y.Z`），
但 Ultra 的正常序列就是 3 个：splash + 主窗 + 模态框——按窗口数算会把 Ultra 误判成失败。

真正要防的是「卡在错误对话框里，进程还活着」。那个特征在标题上：
`cx_Freeze: Python error in main script` / `Fatal error`。

> 2026-11-02：这条判据第一次真正跑起来就抓到了两个问题——主窗口标题停在 v2.3.1、
> 欢迎弹窗写着 Ultra 的 v3.2。以前「进程存活 + 读日志」两项都是绿的。

---

## 五、Release 发布

```bash
gh release create fusion-vX.Y.Z "<zip路径>" \
  --title "Fusion vX.Y.Z — <一句话>" \
  --notes-file <说明文件> --latest
```

- 资产名固定：`LoomGrid-Fusion-vX.Y.Z-win64.zip`
- **只传 zip，不附 CHANGELOG.md**（2026-11-03 用户定）：优化和更新直接写在 Release 正文里。
  旧 Release 里那些 `CHANGELOG.md` 资产是历史遗留，不必回删，但新发的不要再加。
- 重新打包后覆盖：`gh release upload fusion-vX.Y.Z <zip> --clobber`
- **每次覆盖都要更新说明里的 SHA-256**，否则下载者没法校验
- `--latest` 只给 Fusion 的正式修复版；Ultra 的 Lite 标记要手动清
- **发完新版必跑这一条：**

  ```bash
  python tools/release_hints.py --fix-existing --force
  ```

  不带 `--force` 是幂等的（只补缺失的段），**旧版的推荐指向不会跟着更新**——
  于是 17 个 Release 会继续挂着旧指引，用户点进去看到「你正在看的这一版就是
  当前生产版本」，然后下载了旧包。

### 大资产上传：拆成两步，不要用带资产的 create

`gh release create <tag> <zip>` 会先建 draft，**资产传完才 publish**。
272 MB 的包在这台机器上要传十分钟以上，一旦命令超时，**draft 连同 tag 和正文
全部被回收**——一次失败丢掉全部工作（实测过一次）。

改成：

```bash
# ① 只建壳，秒级完成，tag 与正文立即落地
gh release create <tag> --title "..." --notes-file <文件>

# ② 单独传资产，失败可重试，不影响已发布的东西
gh release upload <tag> <zip> --clobber
```

上传期间 `gh release upload` **不输出进度**，别拿日志大小判生死——看进程在不在、
最后查 `assets` 里有没有 `state=uploaded`。

---

## 五之二、calc 接口的业务口径（schema 里没写，必须知道）

### 充电空间有两套算法，取决于 mode（不是「都看负负荷」）

```python
# core/spaces.py L105-121
if mode == 'A':
    space += sum(max(cap - sub_load, 0) * seg_dt)          # 容量线余量
elif mode in ('B', 'C'):
    neg_load = max(-sub_load, 0)
    space += sum(min(rated_power, neg_load) * seg_dt) * 0.93   # 负负荷驱动
```

```
mode A（需量模式）    充电 = Σ max(容量线 − 负荷, 0) × dt
                      正负荷也能充，只要低于容量线。**与负值无关。**
                      缺 capacity → 全 0

mode B/C（功率模式）  充电 = Σ min(额定功率, max(−负荷, 0)) × dt × η
                      **只吃负负荷。** 缺 rated_power → 全 0
```

**两者都受 `ignore_negative=True` 影响** —— 该开关会把负负荷清零，
于是 B/C 的充电空间归 0。

**V4 界面左上「忽略负荷负值（光伏返送）」默认是勾上的**，
所以实测充电段为 0 时，第一嫌疑是这个开关，不是数据。

业务上「充电」也有两种含义（光伏消纳 vs 峰谷套利），
两者在代码里都叫 charge_space，但输入形态相反 —— 造数据前先想清要测哪一种。

### `seg_dt` 用梯形权重，时长不是整数小时

N 个采样点的时段，总时长 = `(N-2) × 0.25 + 2 × 0.125`。

```
「3 小时时段（12 个点）」  →  实际 dt 总和 = 2.875 h
「4 小时时段（16 个点）」  →  实际 dt 总和 = 3.875 h
```

**手算核对必须用这个值，用 3 会差 4%** —— 这个错我犯过一次。

例（mode B，rated=100，11:00–14:00 负荷 −149）：
`min(100, 149) × 2.875 × 0.93 = 267.375` ✓ 与实测一致

### 两个接口的契约细节

```
POST /api/calc/spaces
  segments[].start / end    float 小时（0.0 / 8.0），不是 "00:00" 字符串
  segments[].type           "charge" | "discharge"
  mode                      "A" = 需量（吃容量线）
                            "B" / "C" = 功率（吃 rated_power）
  rated_power               功率模式必填，否则充电/放电空间全 0

POST /api/calc/capacity
  daily_charge[]            逐日序列 —— 不是 session_id + periods
  daily_discharge[]
  basis                     min_feasible | full_load
  dod_percent               默认 95
```

**这两个接口的 description 目前没写清上述语义**，
所以每个接的人都要猜几次。给端点补 description 是待办。

---

## 六、已知待确认（下一个接手的别当成 bug 改）

1. ~~**「月最小段总和」列的算法**：现在取的是本行所有值的最小值，
   而按列名理解应是「各段 min(充电空间, 放电空间) 之和」。
   两者差很多，属业务口径，**改之前必须问人**。~~

   **已定（2026-11-04）：保持 min 口径，列名改为「最小段空间」。**

   理由：该算法在生产中运行一年多，历史截图与报告均基于此值。
   改为 Σ min 会使历史数据与新版不可比，**回溯性不一致比列名措辞更难解释**。

   **若将来要改口径，需同时提供「历史数据换算说明」**——改是允许的，但有前置条件。
   界面上的列头带可见副标：「本月各段中最小的那一段（充+放）」，不进 tooltip。
2. ~~**平段占列但恒为 0**：平段不参与充放，这些列只是为了保持时段完整。~~
   **2026-11-02 已按用户要求修改（C4）**，Fusion v2.3.6 / Ultra v3.2.9 起生效：
   全零段列不再显示。判据是「该段在所有月份的和都为 0 则隐藏」，
   任一月非零仍显示。Ultra 侧顺带消除了重复列——它的 charge_labels 与
   discharge_labels 内容完全相同（三个分支都向两列表同步 append），
   而表头是两者相加，每个段原本出现两次。
   **2026-11-03 补修**：C4 当时只改了月汇总表 `_populate_space_table`，
   日明细表 `_fill_daily_table` 漏了（v2.3.7）。日表同样把两个标签列表相加，
   而且 `vals` 里混入充电段的放电值（恒为 0）——导致「日最小值」永远是 0。
   现在两张表的列结构一致。
   连带效果：「月/日最小值」原本被占位零列压住恒等于 0，现在等于真实最小段值；
   但第 1 条那个取 min 的方式本身仍未动。

---

### Ultra 侧方法数漂移（已处置，不属 Fusion 重构范围）

`tests/verify_feature_restore.py` 原来断言 `ProfessionalWindow` 有 **117** 个方法
（112 基座 + 特性同步 5）。实际 Ultra 后续又多了一个，`bc15e6d` / `e32737f` /
HEAD 三个版本实测都是 **118** —— 不是某次改动弄丢的。

**处置**：断言从 `== 117` 改成 `>= 118`。

```
删方法 → 仍然会红（保住原意）
加方法 → 不会红（不用每加一个就改测试）
```

**三种处理方式的区别**（记在这里，供后来人参考）：

```
红着不解释      训练人忽略红叉（最坏）
静默删除        丢失「这里曾经有过断言」的信息
改成范围断言    保留信息，同时不干扰判断（采用）
```

**归属**：Ultra 不在 Fusion 重构范围内。改 Ultra 代码会引入新变量，
而阶段 5 的目标是把 Fusion 打包成能双击跑的 exe —— 任何 Ultra 改动都会污染它。

---

### 能自己绕开的，不要让用户绕

端口被占时不是报「8000 已占用，请关闭其他程序」，而是自动换一个能用的继续跑。
实测：占住 8000 后启动 exe，自动改用 8001，`/api/health` 照常 200。

判据：**用户不关心端口号是多少，他要的是能打开。**「报错 → 让用户处理」等于把
程序能解决的问题丢回给人。

同一类还有：

```
配置文件读不出来     用默认值 + 提示，而不是拒绝启动
输出目录不存在       自动创建，而不是报错
临时文件残留         下次启动清理，而不是让用户手动删
依赖库缺失（节假日）  能力探测 + 降级说明，而不是把按钮变灰了事
```

最后一条对应的实现在 `api/app.py` 的 `capabilities`：没装 chinesecalendar 时
返回 `holiday: false`，前端提示「将按周末规则处理」——**说清降级后的实际行为，
而不是一个死掉的按钮**。

---

### 每一层验收只能证明它那一层

这句话是这次重构最贵的教训。四次踩坑都是同一个形状：**上一层的绿掩盖了下一层的红**。

```
基线            测 compute            UI 层坏了它不知道
check_components  扫 CSS            页面 JS 崩了它不知道（真实发生过）
check_skeleton    扫 HTML 类名       运行时选择器错了它不知道
check_js_selectors 扫 <script> 选择器  打包产物旧了它不知道
verify_bad_inputs  走 TestClient     exe 没重打包它不知道
```

对应补的五个工具，每一个都是上一层暴露盲区之后才加的：

```
基线            → 数值等价
check_components  → 零裸样式
check_skeleton    → 骨架类名有定义
check_js_selectors→ 选择器指向存在的元素
verify_prod_artifact → 产物行为 == 源码行为
```

**「检查器越补越多」不是工具癖，是正常的**：验收层的数量应该和信任链的层数相等。
每多一层信任（源码 → 构建产物 → 打包产物 → 用户机器），就多一个可能静默失效的地方。

**判据**：问「这个检查证明了什么、证明不了什么」。答不上后半句的检查器，会让人
以为它覆盖的面积比实际大。

---

## 六之二、已知未验证（代码已写，场景未造）
这一节记的**不是缺陷**，是「写了但没验过的分支」。
写下来比让它悬着强：下一个人至少知道该不该信它。

### 隐性规矩（编译器不提醒、运行时不报错、症状只是「没反应」）

这一类坑的共同点：**代码完全合法，不报警，只是结果不对**。
共同解法：**先加一行日志确认事件/函数到不到，再改代码**。
不要用「改了再跑看看」代替诊断——那次跑流程要好几分钟，一行日志两秒。

| 坑 | 症状 | 修法 / 判断 |
|---|---|---|
| `Background=null` 的 Panel | WinUI 里**不参与命中测试**，指针事件永不到达；日志 0 行 | `Background="Transparent"`（**Transparent 能命中，null 不能**） |
| ScottPlot `Add.Scatter` | 图例正常、标题正常，但图是空的；Y 轴停在 -10~10 | 它**不触发自动缩放**，必须显式 `plot.Axes.AutoScale()` |
| ScottPlot 字体 | 中文渲染成方块；`FontName` 设了、读回也对 | 必须 `Fonts.AddFontFile(名, 文件路径)` + `plot.Font.Set(名)`，逐 Label 设无效 |
| `Canvas.SetLeft` 用于 Grid 子元素 | 属性存在但**不生效**（那是 Canvas 的附加属性） | 用 `RenderTransform = TranslateTransform` 改 X/Y |
| `Window.Frame.GoBack()` | 返回后页面重建，列表显示 0 条 | 在 `Loaded` 里重新 `RefreshList()` |
| `PointerPressed` 不释捕获 | **首点正常，后续点击与 hover 全部卡在同一个坐标**（不同屏幕位置点两下，PlotView 坐标完全相同） | 处理完调 `ReleasePointerCaptures()`；这是真 bug，不是测试环境问题 |

### 探针与双点对比（V3）：**已验证**（2026-11-04 收口）

结论推翻前面“环境限制”的假设：

```
20:59:30  V3 探针：PointerMoved (229,122) data=ok
20:59:30  V3 探针浮层：pos=(243,78) vis=Visible text="2026-01-19 03:30" / "470.4 kW"
```

三件事同时被证明：**事件到达 / 浮层可见 / 内容正确**。
之前的失败全是验证方法问题（窗口非前台、坐标估算、只读半截日志），不是代码。

**浮层看不见不代表没显示**：它是叠在 ScottPlot SwapChainPanel 上的 XAML Border，
`PrintWindow` 和 pillow 截屏都抓不到。**判浮层只能读 `Visibility`，不能看截图。**

后来还拎出一个真 bug：`OnPointerPressed` 没调 `ReleasePointerCaptures()`，
导致首点之后所有指针事件坐标被锁死在按下点（详见隐性规矩表）。

#### 历史记录（保留，别再重复踩）

```
第 1 次   PointerMoved 绑 scott:WinUIPlot     → 浮层不出现（猜，无证据）
第 2 次   PointerMoved 绑外层 Grid            → 浮层仍不出现（猜，无证据）
第 3 次   加 AppLog 日志                       → 日志 0 行，确诊事件未触发
第 4 次   Grid 加 Background="Transparent"     → 日志仍 0 行
```

**卡点**：验证时 LoomGrid 窗口**不在前台**（截图工具报 `No active window found`），
而鼠标位置是程序化移动的（`SetCursorPos`）。**WinUI 的 PointerMoved 在窗口非前台 +
程序化输入下未必触发** —— 所以这可能是**验证方法的限制**，不是功能不可行。

**因此不标「暂不可用」**，改为：
- 人工自测判据：把鼠标移到曲线上，看是否出现带「日期 时刻 / 数值 kW」的浮层
- 若人工也不出：问题在命中测试的更深层（可能有元素盖住）；先查 `PlotView` 是否被其他 Border 遮住

**教训**：UI 自动化能验「点击」（点击会激活窗口），但验不了「hover」。
以后碰到鼠标悬停类功能，直接把人工判据交给用户，不自己循环试。

### UIPI 拒绝路径（提权进程拖入）

```
位置    src\LoomGrid.UI\Views\V1WorkbenchPage.xaml.cs  OnDrop()
场景    源进程权限高于本程序（任务管理器、提权后的资源管理器）
        拖入时 DataPackageView 不含 StandardDataFormats.StorageItems，
        UIPI 把数据拦掉了。
代码    已处理：写日志 + 给可读提示「请先复制到普通文件夹再从资源管理器拖入」
未验    造这个场景需要一个真实提权进程，成本不成比例
验证方法 用管理员身份开一个资源管理器，拖一个 xlsx 进来，
        日志里应出现「Drop 里没有 StorageItems —— 大概率是提权进程拖来的」
影响    若真遇到，用户看到我们准备的提示，而不是静默无反应或崩溃
```

---

## 六之三、WinUI 迁移待办（V4 时做）

### 参数持久化：三个参数必须能恢复

Fusion 会「恢复上次关闭时保存的参数」，只存三个：

```
pw_rated_power        额定功率
pw_capacity_mode      容量模式（一充一放 / 两充两放）
pw_ignore_negative    是否忽略负荷负值
```

这三项不在 24 项可视化清单里（它们是参数持久化，不是出图能力），
但属于「功能等价」，必须做。

**实现位置**：`Workspace` 或 `AppServices`，写 `%LOCALAPPDATA%\LoomGrid\settings.json`。
**注意**：unpackaged 模式下 `ApplicationData.Current` 会抛异常，不能用它。

**时机**：V4 容量测算做到这三个参数时一起做。

**文件列表本身不持久化**——与 Fusion 一致（它只存那三个参数，不存文件清单）。
所以「重启后 Workspace 清空」是等价行为，不是缺陷。

---

## 六之四、计算口径约定（实测确认，勿凭直觉推）
### 时段区间**含左含右**：0:00–8:00 取 33 点

`core/spaces.py` 的切片：

```python
left  = np.searchsorted(hours, s_start, side='left')
right = np.searchsorted(hours, s_end,   side='right')     # ← side='right' = 含右端点
sub_hours = hours[left:right]                             # 0:00 … 8:00，共 33 点
```

权重是**首尾各半格 + 中间整格**：

```
seg_dt[0]  = (0 + 0.25)/2 - 0       = 0.125
seg_dt[-1] = 8.0 - (8.0 + 7.75)/2   = 0.125
中间 31 个                            = 0.25
合计                                  = 0.125 + 7.75 + 0.125 = 8.0
```

**推论：Σseg_dt 恒等于 `s_end − s_start`**（8h → 8.0，4h → 4.0）。

**手算核对时**：算「整段总和」直接乘**时段长度**；只有算「单点贡献」才需逐点权重。

⚠️ **自测陷阱（比结论本身更重要）**：
用**恒负荷**验证端点约定是**无效的**——

```
33 点：0.125 + 31×0.25 + 0.125 = 8.0
32 点：0.125 + 30×0.25 + 0.375 = 8.0
```

两者 Σdt 都是 8.0，末点权重差 0.25 被中间少一个点抵消；恒负荷下「权重×负荷」的差也抵消。
**只有变负荷（末点 8:00 与 7:45 负荷不同）才能区分两个假设。**

实测证据（W1 首日，容量线 800）：

| 假设 | 手算 | 与 API 2,951.600 差 |
|---|---|---|
| **33 点（含 8:00）+ 首尾半格** | **2,951.600** | **0.000000%** |
| 32 点（不含 8:00）+ 中点法 | 2,958.646 | 0.238690% |

验证脚本：`tests/verify_v4_handcalc.py`

### `/api/calc/spaces` 的 `segments[].charge` 是**逐日数组**

长度 = 天数，既不是单日值也不是总和。

```python
segs[0]["charge"][0]     # 第 1 天（与每日表对应）
sum(segs[0]["charge"])   # 全月合计（5 天时就差 5 倍）
```

拿 `sum()` 去和单日手算比对，必然 FAIL——**先确认比对对象，再怀疑实现**。

**验证**：`tests/verify_v4_handcalc.py` → 手算 2,951.600 == API 第 1 天 2,951.600，相对差 0.000000%。

---

## 六之五、验证工具自身的已知失效模式

这一节记的**不是产品 bug，是「验证方法本身在骗人」**。
共同特征：**我以为在验 A，实际在验 B，或者什么都没验**。

### ★ 最先看的一条：「每次重启 + 只做一次」

```
重启 → 点一下 → 看到对 → 关程序 → 下一个功能
```

这种验法把每个功能都**只使用一次**，于是「第二次使用失效」整类 bug 永远测不到。
已经踩过的同族：

- 第一次打开正常，第二次打开崩（资源未释放）
- 第一次导出正常，第二次覆盖（状态未重置）
- 第一次点图正常，第二次点击与 hover 全锁死（**PointerPressed 未释放捕获**，2026-11-04 实测）

**判据**：验「有状态的功能」时，**必须在同一个进程里连续做两次以上，且中途不重启**。

### 位置链路是否被冻结：一个比「有没有反应」更早的判据

功能没反应可能是逻辑问题；**控件内坐标对不上，一定是位置链路问题**。

```
点两下不同屏幕位置 → 读控件内坐标
  两个值不同  → 位置链路正常
  两个值相同  → 被冻结（指针捕获 / 变换矩阵未重算 / 滚动偏移缓存）
```

实发：两个不同的屏幕坐标都报 `PlotView (195,71)`——根因是 `PointerPressed` 后没调
`ReleasePointerCaptures()`。**“两个不同输入映射到同一个输出”比“输出错了”信息量大得多。**

### 逻辑验证 ≠ 视觉验证

算对了不等于显示得下。实测案例：异号分支的 Δ / 百分比 / Δt **三个数全对**，
但“（A、B 异号，不计百分比）”这段文案把一行撑爆，自动折行后与下一行贴在一起。

**判据**：数值正确 → 读日志/独立重算；版面正确 → 必须截图。两件事分开验。


| 验证工具 | 失效形式 | 判据 / 正确做法 |
|---|---|---|
| PowerShell 管道 | `python ... \| Select-Object` 拿到的是**管道最后一环**的退出码 | 要验 Python 退出码就不接管道；单独跑一条 |
| 截图量坐标 | **图像坐标 ≠ 屏幕坐标**，差一个缩放比 + 窗口原点 | 先看 `Coordinate Scale`，再乘回图像尺寸算窗口原点 |
| `PrintWindow` 坐标 | 只在**窗口左上角位于屏幕 (0,0)** 时等于屏幕坐标 | 窗口有偏移时不能用 |
| 父级 `opacity:0` | 子元素「看起来存在但不可交互」，断言全过 | 验可交互性要看 `IsHitTestVisible` / 实际命中，不看子树属性 |
| 恒负荷测边界 | 32 点与 33 点的 Σdt **都是时段长度**，区分不出来 | 测端点约定必须用**变负荷** |
| 只测两端 | 「小容量全满足 / 大容量全不满足」都过，中间的过渡态未验 | 加一个「部分满足」的中间点（实测 2700 kWh 才暴露） |
| 程序化鼠标 | 窗口**非前台**时 hover 事件未必触发（`No active window found`） | hover 类功能交人工验；自动化只验点击 |
| 程序化鼠标（`SetCursorPos`） | **只改光标位置，不投递 `WM_MOUSEMOVE`**；WinUI 的 PointerMoved 依赖 WM_POINTER* 系列消息 | hover 类功能不能这样测；要真事件得用 `SendInput` / `mouse_event`，或交人工 |
| 程序化鼠标 + 窗口非前台 | 即使投递了事件，窗口不在前台时 hover 也未必触发 | 同上：交人工 |
| 用截图判断界面状态 | 截图看着对 ≠ 实际对（PrintWindow 抓不到 Mica / 某些合成层）；截图看着错 ≠ 实际错（最小化时是黑块） | 视觉判断用截图；**状态判断走 API**（`ActualTheme` / `SystemBackdrop` 类型 / DOM 属性） |
| 只读了一半日志 | 看到 A 有日志、B 没日志，就判「B 无效」；实际是「B 未观测」 | 判无效必须证明「它的日志没出现」，不是「另一件事的日志出现了」 |
| 跑旧二进制 | 改完代码没效果，因为启动的是另一个 build 目录 | 先核对「跑的是不是刚编的那个 exe」（Debug vs Release、bin 路径） |
| DPI 缩放与坐标不同源 | 系统 175% 缩放下，UI Tree 坐标、`GetWindowRect` 物理像素、mcp `click` 的 loc 三套数字不相等；同一位置点两次可能得到不同结果 | 用 `displayinventory` 先确认 scale；用一次**已知成功的点击**反推「屏幕→控件内」映射，再用相对偏移推第二个点 |
| 双击间隔内的连点 | 短时间内同位置连点会被归为双击，`PointerPressed` 不再触发 | 点第二下前换位置或等 500ms |

### 三种坐标的定义（混用必错）

```
snapshot / element_bounds     屏幕绝对坐标        ← 直接用，**不要加窗口偏移**
screenshot / 截图            图像坐标（可能缩放）  ← 乘 scale，再加窗口原点
PrintWindow                  窗口内容坐标        ← 仅当窗口在 (0,0) 时等于屏幕坐标
```

**点之前先问：这个坐标是谁给的？**

```
快照给的    直接用
截图量的    先查实际图像尺寸与窗口尺寸的比例
凭眼睛估的  去拿 snapshot，不要估
```

实发案例：从 1440 宽截图里读 (1360, 858)，实际屏幕坐标为 (1697, 1216)
—— 差的 (337, 358) 正好是窗口原点。另一个案例：截图返回 `Coordinate Scale: 2.0`，
按截图坐标点击会偏一倍（连续三次点不中同一个按钮）。

### 坐标与 UI 自动化的真实情况（2026-11-04 实测）

本机系统缩放 **175%**。实测到的硬数据（F11 坐标自检输出）：

```
窗口物理原点 (44,44)   根 Grid 逻辑尺寸 809×518   scale=1.75   标题栏逻辑高 32
```

**结论：鼠标点击通道在这个环境里不可靠。** 五次尝试全没落中：

```
UI Tree 坐标直接点            → 落空
UI Tree ÷ 1.75              → 落空
截图坐标 + 窗口原点            → 落空
inject_click.ps1（SendInput）→ 落空
Enter（假定焦点在按钮上）     → 落空
```

**但键盘事件能到窗口**（F11 自检能触发）。所以窗口是活的、前台的，
问题在「位置 → 目标」这一环，不在「事件能不能到」。

**实用结论**：
1. 要拿坐标就按 **F11**（MainWindow 的 KeyboardAccelerator），不要推算。
2. 验证 UI 优先用**键盘可达路径**（Tab / 加速键），不要堆点击次数。
3. 点击类验证交给**用户手动操作 + 读日志**，比自动化猜坐标快得多。

（下面这组旧数据已废弃，仅作历史：窗口位置每次启动都不同，任何“记下来的坐标”都是错的。
要坐标就按 F11。）

```
【已废弃】窗口原点 (858, 229) · V3 曲线区域 屏幕 (1464, 409)–(2251, 1003)
```

---

## 七、提交信息写法

```
<type>(<scope>): <一句话说清改了什么>

<为什么要改 / 根因是什么>
<怎么验证的 / 具体数字>
```

`type`：`fix` / `feat` / `refactor` / `docs` / `test` / `chore`

**禁止**只写「修复bug」「优化」——下一个人看不出改了什么。

---

## 八、会话与机器分工

### 两台机器

| 机器 | Python | 打包工具 | 备注 |
|---|---|---|---|
| 本机（LENOVO） | 3.12.9 | PyInstaller `--onedir` | 走 `tools/pack_release.py` |
| 公司（SZ635581） | 3.12.13 | cx_Freeze | 走 `tools/build_fusion_only.py` |

**两条路径都可用，按机器选，不规定必须用哪个。** 都以「产物能过窗口验证」为准。

### 同一台机器上跑多个会话时

两个会话共用同一个工作区，**都改 `.py` 就会互相覆盖**。2026-11-03 撞过一次：
一边修报告 bug、一边修转置 bug，两份改动同时躺在工作区，幸好位置不同没覆盖。

| 角色 | 管什么 | 不管什么 |
|---|---|---|
| **主程序会话**（重构/开发） | `.py` 主程序、`core/`、`baseline/`、`tests/` | Release 发布、CHANGELOG 发版条目、AGENTS.md |
| **统筹会话**（发布/文档） | Release、CHANGELOG、AGENTS.md、报告 | **不改主程序代码** |

**铁律**：改主程序前先 `git fetch` + `git log --oneline HEAD..origin/main`，
改完**立刻提交，不攒着**。工作区里躺着未提交的改动时，别人一保存就没了。

### 分层依赖禁令（`tools/check_layering.py` 会扫 AST 强制）

| 层 | 禁止 import |
|---|---|
| `core/` | PySide6、matplotlib、fastapi、starlette、webview |
| `plotting/` | PySide6、fastapi、starlette、webview |
| `services/` | PySide6、fastapi、starlette、webview |
| `api/` | PySide6 |

检查用 AST 扫 `Import` / `ImportFrom` 节点，**不靠文本匹配**——
模块文档里写「不许 import 某某」不会误报。

```bash
python tools/check_layering.py        # 零违规退出码 0
python tools/check_layering.py -v     # 同时打印每层实际引入了什么
```

清单写在这里而不是写在 `core/` 的模块文档里：文档里的清单会被文本 grep 误判，
而 AGENTS.md 不在被扫描的层目录下；且它是所有协作者必读的入口，规则不会失传。

---

### 骨架类名白名单必须可核对（`tools/check_skeleton.py`）

**每条白名单项都要能指出它在 `app-shell.css` 的定义行号。**

指不出行号的，不该在白名单里。这条来自一次真实误删：
清理骨架残留时，`.v2__opts` / `.v2__opt` 被当成骨架删掉了——
**它们听起来像骨架（底栏选项区），但 app-shell.css 里没有定义，
是 V2 独有内容类。**

判据的演化过程值得记：

```
第一版  正向枚举「我知道的骨架类名都要删」  → 漏了 file / pane-note / foot-note
第二版  反向扫描「不在白名单里的都留下」     → 方向对了，但白名单凭感觉定
第三版  白名单每项必须指得出 CSS 行号        → 白名单可核对，不再靠猜
```

反向扫描比正向枚举安全，因为漏掉的范围从「全部世界」缩到「白名单本身」。
但**白名单必须可审查**，否则只是把猜测换了个位置。

---

## 九、当前状态（每次接手时核对）

| 项 | 值 |
|---|---|
| 最新 Commit | 见 `git log -1` |
| Fusion 版本 | v2.3.9 |
| Ultra 版本 | v3.2.9 |
| Release 资产 SHA-256 | 见对应 Release 说明 |
| 进行中 | Fusion 彻底重构（方案 D：Qt → Web），阶段 0 基线已完成 |
| 待办 | C7 已标「预留·未实现」；C8 已修（详见 §六 第 3、4 条） |

### C7 / C8 处置（2026-11-04）

**C7 `24h_belongs_current_month` 是个看起来能用的开关**：

```
定义在 core/timegrid.py，配套 setter `_set_24h_belong`
全库零读取 —— 设置它不会改变任何行为
```

**处置：标「预留·未实现」，不补实现（改动面不明）、不删除（不确定有无外部调用）。**

⚠️ **本应在定义处加注释，但 `core/` 是「一行不改」红线，注释也算改**，
所以说明写在这里，**不改 core**。

若要让 24 点归入次月，需改 `core/timegrid` 的日期归属逻辑，**改动前先评估影响面**。

**C8 日期无年份默认今年：已修（代码领先于文档）。**

`_extract_yaml` 现在能识别 `1月15日` / `01-15` / `1/15`，且 `1/32` 会被拒（非法日期不静默通过）。
文档此前停留在「待确认」，已更正。

### C3 已定案（2026-11-03）

**「月最小段总和」不是容量，是吞吐量。** 用户确认的 API 口径：

```
容量口径（枚举，kWh）        min_feasible  最小可行容量：min(所有段的值)
                          full_load     满载利用率：max(min(每段的充,放))

日吞吐量（独立字段，kWh/天）  sum(min(充电空间, 放电空间)) 逐段求和
```

**两者不同量纲，不可互换。** 前两个回答「电池装多大」，第三个回答「一天能搬多少能量」。
API 里必须分两个字段（`daily_demand` 与 `daily_throughput_kwh_per_day`），
不能做进同一个枚举——否则调用方会当同义词选，差一倍以上。

> 最后更新：2026-11-03
