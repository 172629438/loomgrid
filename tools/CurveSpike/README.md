# CurveSpike · CurveViewer 能力回归验证器

验证「现有产品的 24 项出图语义」在 ScottPlot 上还成立。

用途不是出产品图，是**回归**：将来改 CurveViewer 时跑一遍，确认没有哪项能力悄悄消失。

## 跑法

```bash
cd tools/CurveSpike
dotnet run
```

产物在 `out/`：24 张 PNG + `results.json`（逐项判定与实现说明）。

## 判据

每项给三档：`能` / `有条件` / `不能`。

- `能` —— 该语义可用 ScottPlot 表达，且已出图确认
- `有条件` —— 能画出来，但需要额外计算或非默认配置
- `不能` —— 框架不支持（当前为 0 项）

## 三条已知结论（改代码前先读）

1. **中文字体**：必须 `Fonts.AddFontFile(name, path)` 注册字体文件 + `plot.Font.Set(name)` 一次接管全部文本。只设 `Label.FontName` 会 fallback 成 Segoe UI，中文渲染成方块（图例例外，它自动选字体）。
2. **`HorizontalSpan(x1, x2)` 是 x 区间**（视觉上是竖条），`VerticalSpan(y1, y2)` 才是横条。命名反直觉。
3. **图例不是自动的**：12 类条目与「>5 天合并」都要走 `Legend.ManualItems` 手工构建。

## 与主程序的关系

本工程**不引用** `LoomGrid.UI`，也不参与 `LoomGrid.sln` 构建。
它是独立探针，只依赖 `ScottPlot` 包（版本与主程序保持一致：5.1.59）。
主程序升级 ScottPlot 时，先在这里改版本号并重跑，确认 24 项判定没有退化。
