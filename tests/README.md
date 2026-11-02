# 验证脚本

16 个脚本，全部是**独立可运行**的：`python tests/<脚本名>.py`，不依赖 pytest，
不需要先安装这个项目。脚本自己会把 `algorithms/` 加进 `sys.path`。

每个脚本末尾打印 `PASS n / FAIL m`，**退出码 0 = 全过**。

## 先跑这三个

刚 clone 下来，想确认「这套代码在我机器上是好的」：

```bash
pip install numpy pandas scipy loguru
python tests/verify_invariants.py
python tests/verify_algorithms_math.py
python tests/verify_pipeline.py
```

三个都退出码 0，说明算法层、数理口径、端到端链路都通。加起来约 10 秒。

## 全部清单

### 纯算法（无需 Qt，CI 覆盖）

| 脚本 | 断言 | 验什么 |
|---|---:|---|
| `verify_invariants.py` | 13 | 物理不变量：能量守恒、SOC 边界、NPV↔IRR 自洽、空间口径交叉 |
| `verify_algorithms_math.py` | 12 | 数理公式：手算对拍，每个公式有推导对照 |
| `verify_alg_parity.py` | 19 | 三个调度引擎在同一组数据下的行为差异是否在预期内 |
| `verify_algorithms_deep.py` | 25 | 边界与退化场景：空输入、极值、零容量、全零曲线 |
| `verify_pipeline.py` | 43 | 端到端：负荷 → 电价 → 空间 → 调度 → 财务，全链路串通 |
| `verify_feature_sync.py` | 5 | 两个现役版本（Fusion / Ultra）的特性是否同步 |
| `verify_feature_restore.py` | 15 | 历史上丢失过的特性是否仍在位（防回退） |

### 需要 PySide6 或外部数据（仅本地）

| 脚本 | 断言 | 验什么 | 额外依赖 |
|---|---:|---|---|
| `verify_gui_flow.py` | 24 | GUI 主流程点击链路 | PySide6 + 显示器 |
| `verify_gui_signal.py` | 26 | 信号连接是否正确、无重复触发 | PySide6 + 显示器 |
| `verify_gui_smoke2.py` | 10 | 对话框冒烟 | PySide6 + 显示器 |
| `verify_report.py` | 28 | 报告生成与导出 | PySide6（部分路径） |
| `verify_e2e.py` | — | 真实 Excel 文件的读取链路 | 样例数据 |
| `verify_ingest.py` | — | 数据摄入与分析引擎模式切换 | 样例数据 |
| `verify_fixes_batch1.py` | 12 | 第一批缺陷修复的回归 | — |
| `verify_fixes_batch2.py` | 13 | 第二批缺陷修复的回归 | — |
| `sweep_algorithms3.py` | — | 算法函数批量扫描（探索性，非断言） | — |

## 一条命令全跑

```bash
python - <<'PY'
import pathlib, subprocess, sys
fails = []
for p in sorted(pathlib.Path("tests").glob("verify_*.py")):
    rc = subprocess.run([sys.executable, str(p)]).returncode
    print(f"{p.name:34s} rc={rc}")
    if rc:
        fails.append(p.name)
sys.exit(1 if fails else 0)
PY
```

（Windows 的 cmd 不支持 heredoc，用 PowerShell 或写成 .py 文件跑。）

## CI

`.github/workflows/ci.yml` 跑上表「纯算法」那 7 个，两个 Python 版本
（3.10 / 3.12）各一遍，外加一个 wheel 构建与内容检查 job。

GUI 相关的四个**不进 CI**：GitHub 的 ubuntu runner 没有显示器，装 PySide6
也跑不起来。这四个脚本靠本地跑。

## 加新脚本的约定

1. 文件名 `verify_<主题>.py`
2. 自己处理 `sys.path`，不依赖调用方的工作目录
3. 用 `check(name, cond, detail)` 计数，末尾打印汇总
4. **失败时 `sys.exit(1)`**——否则 CI 看不出红
5. 不写文件到仓库（临时文件用 `tempfile`）
