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

1. **「月最小段总和」列的算法**：现在取的是本行所有值的最小值，
   而按列名理解应是「各段 min(充电空间, 放电空间) 之和」。
   两者差很多，属业务口径，**改之前必须问人**。
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
| 待办 | C7 `24h_belongs_current_month` 是个不生效的开关；C8 日期无年份默认今年 |

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
