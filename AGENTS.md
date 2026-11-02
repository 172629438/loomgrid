# 协作约定 · 两台 Agent 交替迭代

> 这个项目由两台机器上的 Agent 轮流迭代：家里那台和主力那台。
> 谁在改，改完就推；**仓库是唯一真源，本地磁盘不是。**

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

---

## 二、铁律：改完必须上传

改完代码**当场**做完这三步，不要攒：

```bash
git add "负荷曲线批量生成工具 Pro 融合版 (v2.3).py" CHANGELOG.md
git commit -m "fix(fusion): <一句话说清改了什么>"
git push origin main
```

### 上传后必须自证

不能只看 `git push` 的成功提示——要确认远端文件真的变了：

```bash
# 拉远端文件，与本地比对
python -c "
import urllib.request, urllib.parse, hashlib
f = urllib.parse.quote('负荷曲线批量生成工具 Pro 融合版 (v2.3).py')
u = 'https://raw.githubusercontent.com/172629438/loomgrid/main/' + f
r = urllib.request.urlopen(u).read().decode('utf-8')
l = open('负荷曲线批量生成工具 Pro 融合版 (v2.3).py', encoding='utf-8').read()
print('远端 == 本地:', r == l)
print('SHA256:', hashlib.sha256(r.encode()).hexdigest()[:16])
"
```

**判据**：输出 `远端 == 本地: True`。不是 True 就没上传成功，别往下走。

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

**不要用 `tools/build_all_exe.py`**（PyInstaller，本机跑不动，脚本头部已标废弃）。
**不要用 `tools/build_exe_cx.py`** 直接出 Fusion 包——它一次打 12 个目标、共享一套 lib，
抽出来的 Fusion 包会到 299 MB。用 `tools/build_fusion_only.py`，只收集 Fusion 自己的依赖。

### 打包后必须验证

**进程存活不算通过**——卡在错误对话框里的进程同样"存活"。

```python
# 正确判据：读窗口标题
# 正常 = 只有 1 个窗口「欢迎 — LoomGrid-Fusion-vX.Y.Z」
# 异常 = 出现 2 个窗口，其中一个是「cx_Freeze: Python error in main script」
```

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

---

## 六、已知待确认（下一个接手的别当成 bug 改）

1. **「月最小段总和」列的算法**：现在取的是本行所有值的最小值，
   而按列名理解应是「各段 min(充电空间, 放电空间) 之和」。
   两者差很多，属业务口径，**改之前必须问人**。
2. **平段占列但恒为 0**：平段不参与充放，这些列只是为了保持时段完整。
   删不删是界面取舍。

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
