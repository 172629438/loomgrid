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

## 三、版本号规则

- 主程序 docstring 第 4 行有版本声明，改功能必改
- Fusion 当前 `v2.3.2`，修一次递增末位：`v2.3.3`、`v2.3.4`
- **改了计算逻辑**（影响测算结果）→ 末位 +1 并在 CHANGELOG 里单列
- **只改显示/文案** → 也 +1，但要标注「显示层改动，不影响测算」
- 版本号必须与 `CHANGELOG.md` 的条目、Release 的 tag 三者一致

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

## 七、提交信息写法

```
<type>(<scope>): <一句话说清改了什么>

<为什么要改 / 根因是什么>
<怎么验证的 / 具体数字>
```

`type`：`fix` / `feat` / `refactor` / `docs` / `test` / `chore`

**禁止**只写「修复bug」「优化」——下一个人看不出改了什么。

---

## 八、当前状态（每次接手时核对）

| 项 | 值 |
|---|---|
| 最新 Commit | 见 `git log -1` |
| Fusion 版本 | v2.3.2 |
| Ultra 版本 | v3.2.2 |
| Release 资产 SHA-256 | 见对应 Release 说明 |
| 待办 | 「月最小段总和」算法待确认；平段列去留待定 |

> 最后更新：2026-11-02
