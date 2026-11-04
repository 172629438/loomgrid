using System.Diagnostics;
using ScottPlot;

namespace CurveSpike;

public static class Cases
{
    private static readonly double[] HoursX =
        Enumerable.Range(0, DemoData.PointsPerDay).Select(i => i * 0.25).ToArray();

    /// <summary>点划线：ScottPlot 只内置 Solid/Dashed/DenselyDashed/Dotted，点划线要自己拼 intervals。</summary>
    private static readonly LinePattern DashDot = new(new[] { 7f, 4f, 1.5f, 4f }, 0, "DashDot");

    // ── 中文字体：必须「注册字体文件」+「plot.Font.Set」两步，逐 Label 设 FontName 对标题无效 ──
    private const string CjkFamily = "LoomCjk";

    private static readonly string? CjkFontFile = ResolveCjkFontFile();
    private static bool _fontLoaded;

    private static string? ResolveCjkFontFile()
    {
        foreach (var name in new[] { "msyh.ttc", "msyh.ttf", "simhei.ttf", "simsun.ttc" })
        {
            string p = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Fonts), name);
            if (File.Exists(p)) return p;
        }
        return null;
    }

    private static void EnsureCjkFont()
    {
        if (_fontLoaded || CjkFontFile is null) return;
        try
        {
            ScottPlot.Fonts.AddFontFile(CjkFamily, CjkFontFile);
            _fontLoaded = ScottPlot.Fonts.GetTypeface(CjkFamily, false, false) is not null;
        }
        catch
        {
            _fontLoaded = false;
        }
    }

    private static void SetCjk(Plot p)
    {
        EnsureCjkFont();
        if (_fontLoaded) p.Font.Set(CjkFamily);
    }

    // ── 共用 ────────────────────────────────────────────────

    private static CaseResult Ok(string note) => new("", "", "能", note, null);
    private static CaseResult Cond(string note) => new("", "", "有条件", note, null);
    private static CaseResult No(string note) => new("", "", "不能", note, null);

    // ── 01 多天曲线 ─────────────────────────────────────────
    public static CaseResult MultiDayCurves(Plot p, List<DemoData.DayProfile> days)
    {
        SetCjk(p);
        foreach (var d in days)
        {
            var s = p.Add.SignalXY(HoursX, d.Load);
            s.LineWidth = 1.5f;
            s.LegendText = d.Date.ToString("MM-dd");
        }
        p.ShowLegend(Alignment.UpperRight);
        return Ok("Add.SignalXY 每天一条，7 条叠加同轴；图例逐天列出");
    }

    // ── 02 降采样 ──────────────────────────────────────────
    public static CaseResult Downsampling(Plot p, List<DemoData.DayProfile> days)
    {
        const int n = 100_000;
        var xs = new double[n];
        var ys = new double[n];
        for (int i = 0; i < n; i++)
        {
            xs[i] = i * 0.001;
            ys[i] = 600 + 400 * Math.Sin(i * 0.0008) + 60 * Math.Sin(i * 0.05);
        }

        var s = p.Add.SignalXY(xs, ys);
        s.LineWidth = 1;

        var sw = Stopwatch.StartNew();
        _ = p.GetImage(900, 450);
        sw.Stop();

        return sw.ElapsedMilliseconds < 800
            ? Ok($"10 万点首帧 {sw.ElapsedMilliseconds} ms；SignalXY 内部按像素列取 min/max 抽稀，无需自己写降采样")
            : Cond($"10 万点首帧 {sw.ElapsedMilliseconds} ms，超过 800 ms；需要手动分段或降低采样");
    }

    // ── 03 平均曲线 ────────────────────────────────────────
    public static CaseResult AverageCurve(Plot p, List<DemoData.DayProfile> days)
    {
        SetCjk(p);
        foreach (var d in days)
            p.Add.SignalXY(HoursX, d.Load).LineWidth = 1;

        var avg = new double[DemoData.PointsPerDay];
        for (int i = 0; i < avg.Length; i++)
            avg[i] = days.Average(d => d.Load[i]);

        var line = p.Add.SignalXY(HoursX, avg);
        line.Color = Colors.Black;
        line.LineWidth = 2.4f;
        line.LinePattern = LinePattern.Dashed;
        line.LegendText = "平均曲线";

        p.ShowLegend(Alignment.UpperRight);
        return Ok("LinePattern.Dashed + 黑色，逐点均值由调用方计算后作为一条 SignalXY");
    }

    // ── 04 最大需量线 ──────────────────────────────────────
    public static CaseResult MaxDemandLine(Plot p, List<DemoData.DayProfile> days)
    {
        SetCjk(p);
        var (xs, ys) = DemoData.Flatten(days);
        p.Add.SignalXY(xs, ys).LineWidth = 1;

        double max = ys.Max();
        var h = p.Add.HorizontalLine(max);
        h.Color = Colors.Blue;
        h.LineWidth = 1.6f;
        h.LinePattern = LinePattern.Dashed;

        p.Add.Text($"最大需量 {max:F0} kW", xs[xs.Length / 3], max + 40);
        return Ok("Add.HorizontalLine + Add.Text 数值标注，颜色与线型可控");
    }

    // ── 05 计费容量线 ──────────────────────────────────────
    public static CaseResult BillingCapacityLine(Plot p, List<DemoData.DayProfile> days)
    {
        SetCjk(p);
        var (xs, ys) = DemoData.Flatten(days);
        p.Add.SignalXY(xs, ys).LineWidth = 1;

        double cap = Math.Ceiling(ys.Max() / 100) * 100 + 60;
        var h = p.Add.HorizontalLine(cap);
        h.Color = Colors.Red;
        h.LineWidth = 1.6f;
        h.LinePattern = LinePattern.Dotted;

        p.Add.Text($"计费容量 {cap:F0} kW", xs[xs.Length / 4], cap + 30);
        return Ok("LinePattern.Dotted + 红色 + 文本标注，与需量线并存不冲突");
    }

    // ── 06 拟合曲线 ────────────────────────────────────────
    public static CaseResult FitCurve(Plot p, List<DemoData.DayProfile> days)
    {
        SetCjk(p);
        var (xs, ys) = DemoData.Flatten(days);
        p.Add.SignalXY(xs, ys).LineWidth = 1;

        // 二次多项式最小二乘：正常方程 3×3，自己解，不引第三方数值库
        var coef = PolyFit(xs, ys, 2);
        var fitY = xs.Select(x => coef[0] + coef[1] * x + coef[2] * x * x).ToArray();

        var fit = p.Add.SignalXY(xs, fitY);
        fit.Color = Colors.OrangeRed;
        fit.LineWidth = 2.2f;
        fit.LinePattern = DashDot;
        fit.LegendText = "拟合曲线（二次）";
        p.ShowLegend(Alignment.UpperRight);

        return Cond("画线本身完全支持（DashDot + 任意采样）。**拟合系数要自己算**：ScottPlot 只自带线性回归，多项式/指数需在 C# 侧实现或复用后端计算结果");
    }

    private static double[] PolyFit(double[] x, double[] y, int degree)
    {
        int m = degree + 1;
        var a = new double[m, m];
        var b = new double[m];

        for (int i = 0; i < m; i++)
        {
            for (int j = 0; j < m; j++)
            {
                double sum = 0;
                for (int k = 0; k < x.Length; k++) sum += Math.Pow(x[k], i + j);
                a[i, j] = sum;
            }
            double sy = 0;
            for (int k = 0; k < x.Length; k++) sy += y[k] * Math.Pow(x[k], i);
            b[i] = sy;
        }

        // 高斯消元
        for (int i = 0; i < m; i++)
        {
            int pivot = i;
            for (int r = i + 1; r < m; r++)
                if (Math.Abs(a[r, i]) > Math.Abs(a[pivot, i])) pivot = r;

            for (int c = 0; c < m; c++)
                (a[i, c], a[pivot, c]) = (a[pivot, c], a[i, c]);
            (b[i], b[pivot]) = (b[pivot], b[i]);

            for (int r = i + 1; r < m; r++)
            {
                double f = a[r, i] / a[i, i];
                for (int c = i; c < m; c++) a[r, c] -= f * a[i, c];
                b[r] -= f * b[i];
            }
        }

        var x2 = new double[m];
        for (int i = m - 1; i >= 0; i--)
        {
            double s = b[i];
            for (int j = i + 1; j < m; j++) s -= a[i, j] * x2[j];
            x2[i] = s / a[i, i];
        }
        return x2;
    }

    // ── 07 异常值标记 ──────────────────────────────────────
    public static CaseResult OutlierMarkers(Plot p, List<DemoData.DayProfile> days)
    {
        SetCjk(p);
        var (xs, ys) = DemoData.Flatten(days);
        p.Add.SignalXY(xs, ys).LineWidth = 1;

        var badX = new List<double>();
        var badY = new List<double>();
        for (int i = 0; i < ys.Length; i++)
        {
            if (ys[i] <= 0) { badX.Add(xs[i]); badY.Add(ys[i]); }
        }

        var sc = p.Add.Scatter(badX.ToArray(), badY.ToArray());
        sc.Color = Colors.Red;
        sc.MarkerShape = MarkerShape.Eks;
        sc.MarkerSize = 9;
        sc.LineWidth = 0;
        sc.LegendText = "异常值（零/负）";

        p.ShowLegend(Alignment.UpperRight);
        return Ok($"Add.Scatter + MarkerShape.Eks（ScottPlot 里 X 形叫 Eks，Cross 是十字）+ 红色 + LineWidth=0；本次命中 {badX.Count} 个零/负值");
    }

    // ── 08 充放背景 ────────────────────────────────────────
    public static CaseResult ChargeDischargeSpans(Plot p, List<DemoData.DayProfile> days)
    {
        SetCjk(p);
        var (xs, ys) = DemoData.Flatten(days);
        p.Add.SignalXY(xs, ys).LineWidth = 1;

        for (int d = 0; d < days.Count; d++)
        {
            double baseX = d * 24;

            var charge = p.Add.HorizontalSpan(baseX + 11, baseX + 14);
            charge.FillColor = Colors.DodgerBlue.WithAlpha(0.15);
            charge.LineWidth = 0;

            var discharge = p.Add.HorizontalSpan(baseX + 18, baseX + 21);
            discharge.FillColor = Colors.Orange.WithAlpha(0.15);
            discharge.LineWidth = 0;
        }

        return Ok("Add.HorizontalSpan(x1,x2) + FillColor(alpha 0.15) + LineWidth=0，任意多段可叠加（本图 7 天×2 段=14 个色带）");
    }

    // ── 09 周末阴影 ────────────────────────────────────────
    public static CaseResult WeekendShading(Plot p, List<DemoData.DayProfile> days)
    {
        SetCjk(p);
        var (xs, ys) = DemoData.Flatten(days);
        p.Add.SignalXY(xs, ys).LineWidth = 1;

        for (int d = 0; d < days.Count; d++)
        {
            if (!days[d].IsWeekend) continue;
            double x1 = d * 24;
            double x2 = x1 + 24;
            var span = p.Add.HorizontalSpan(x1, x2);
            span.FillColor = Colors.Gray.WithAlpha(0.18);
            span.LineWidth = 0;
        }

        return Ok("同 HorizontalSpan，逐周末区间叠加；alpha 由调用方传入，支持任意透明度");
    }

    // ── 10 三色配置 ────────────────────────────────────────
    public static CaseResult ThreeColorScheme(Plot p, List<DemoData.DayProfile> days)
    {
        SetCjk(p);
        foreach (var d in days)
        {
            var s = p.Add.SignalXY(HoursX, d.Load);
            if (d.IsHoliday) { s.Color = Colors.OrangeRed; s.LineWidth = 2.6f; }
            else if (d.IsWeekend) { s.Color = Colors.SteelBlue; s.LineWidth = 1.6f; }
            else { s.Color = Colors.RoyalBlue; s.LineWidth = 1.2f; }
        }

        return Ok("每天一条 plottable，颜色与线宽独立可设；三类各自成组，图例可分别出条目");
    }

    // ── 11 高亮模式四种 ────────────────────────────────────
    public static CaseResult HighlightModes(Plot p, List<DemoData.DayProfile> days)
    {
        SetCjk(p);
        var palettes = new ScottPlot.Palettes.Category10();

        // 用 grouped 模式示意：当前组高亮，其余压暗
        foreach (var d in days)
        {
            var s = p.Add.SignalXY(HoursX, d.Load);
            bool highlight = d.IsWeekend;
            s.Color = palettes.GetColor(d.Index);
            s.LineWidth = highlight ? 2.4f : 0.9f;
            if (!highlight) s.Color = s.Color.WithAlpha(0.35);
        }

        return Ok("四种模式都是「配色 + 线宽 + alpha」的组合，属调用方逻辑；ScottPlot 侧只需能逐条改颜色/线宽/透明度，已验证可行");
    }

    // ── 12 tab10 ──────────────────────────────────────────
    public static CaseResult Tab10Palette(Plot p, List<DemoData.DayProfile> days)
    {
        SetCjk(p);
        var palette = new ScottPlot.Palettes.Category10();
        foreach (var d in days)
        {
            var s = p.Add.SignalXY(HoursX, d.Load);
            s.Color = palette.GetColor(d.Index);
            s.LineWidth = 1.4f;
            s.LegendText = d.Date.ToString("MM-dd");
        }
        p.ShowLegend(Alignment.UpperRight);
        return Ok("内置 ScottPlot.Palettes.Category10，取色顺序与 matplotlib tab10 同族（色值不完全相同）");
    }

    // ── 13 暗色主题 ────────────────────────────────────────
    public static CaseResult DarkTheme(Plot p, List<DemoData.DayProfile> days)
    {
        SetCjk(p);
        p.FigureBackground.Color = Color.FromHex("#1e1e1e");
        p.DataBackground.Color = Color.FromHex("#2e2e2e");
        p.Axes.Color(Color.FromHex("#FFFFFF"));
        p.Grid.MajorLineColor = Color.FromHex("#FFFFFF").WithAlpha(0.3);

        foreach (var d in days)
        {
            var s = p.Add.SignalXY(HoursX, d.Load);
            s.Color = Color.FromHex("#4EA8DE");
            s.LineWidth = 1.3f;
        }
        return Ok("FigureBackground / DataBackground / Axes.Color / Grid.MajorLineColor 四项即等价 matplotlib 的 fig+ax 暗色");
    }

    // ── 14 整点刻度 + 旋转 ─────────────────────────────────
    public static CaseResult HourTickLabels(Plot p, List<DemoData.DayProfile> days)
    {
        SetCjk(p);
        var (xs, ys) = DemoData.Flatten(days);
        p.Add.SignalXY(xs, ys).LineWidth = 1;

        var positions = new List<double>();
        var labels = new List<string>();
        int totalHours = days.Count * 24;
        for (int h = 0; h <= totalHours; h += 6)
        {
            positions.Add(h);
            labels.Add($"{h % 24:00}:00");
        }

        p.Axes.Bottom.TickGenerator =
            new ScottPlot.TickGenerators.NumericManual(positions.ToArray(), labels.ToArray());
        p.Axes.Bottom.TickLabelStyle.Rotation = -45;
        p.Axes.Bottom.TickLabelStyle.FontSize = 10;

        return Ok("NumericManual(positions, labels) 自定义刻度；TickLabelStyle.Rotation = -45 支持旋转");
    }

    // ── 15 原始 time_labels ────────────────────────────────
    public static CaseResult RawTimeLabels(Plot p)
    {
        SetCjk(p);
        var day = DemoData.Build()[0];

        var xs = Enumerable.Range(0, 24).Select(i => (double)i).ToArray();
        var ys = day.Load.Where((_, i) => i % 4 == 0).ToArray();

        p.Add.SignalXY(xs, ys).LineWidth = 1.6f;

        var labels = DemoData.TimeLabelStrings().Where((_, i) => i % 4 == 0).ToArray();
        p.Axes.Bottom.TickGenerator =
            new ScottPlot.TickGenerators.NumericManual(xs, labels);

        return Ok("24 点数据用原始 HH:mm 标签，NumericManual 直接接管刻度文本");
    }

    // ── 16 轴标签 + 标题 ───────────────────────────────────
    public static CaseResult AxisLabelsAndTitle(Plot p, List<DemoData.DayProfile> days)
    {
        SetCjk(p);
        var (xs, ys) = DemoData.Flatten(days);
        p.Add.SignalXY(xs, ys).LineWidth = 1;

        p.Title("负荷曲线");
        p.XLabel("时间");
        p.YLabel("负荷值");

        p.Axes.Title.Label.FontSize = 16;
        p.Axes.Bottom.Label.FontSize = 13;
        p.Axes.Left.Label.FontSize = 13;

        return Ok("Title/XLabel/YLabel + Label.FontSize 逐项可设；标题 16pt 与 matplotlib 版一致");
    }

    // ── 17 网格 alpha ──────────────────────────────────────
    public static CaseResult GridAlpha(Plot p, List<DemoData.DayProfile> days)
    {
        SetCjk(p);
        p.Grid.MajorLineColor = Colors.Gray.WithAlpha(0.3);
        p.Grid.MajorLineWidth = 1;

        var (xs, ys) = DemoData.Flatten(days);
        p.Add.SignalXY(xs, ys).LineWidth = 1;

        return Ok("Grid.MajorLineColor 用 WithAlpha(0.3)；主网格与次网格分别可控");
    }

    // ── 18 图例位置 ────────────────────────────────────────
    public static CaseResult LegendPositions(Plot p, List<DemoData.DayProfile> days)
    {
        SetCjk(p);
        foreach (var d in days.Take(3))
            p.Add.SignalXY(HoursX, d.Load).LegendText = d.Date.ToString("MM-dd");

        p.ShowLegend(Alignment.MiddleRight);

        // 额外出一张底部图例，证明两种位置都行
        var p2 = new Plot();
        SetCjk(p2);
        foreach (var d in days.Take(3))
            p2.Add.SignalXY(HoursX, d.Load).LegendText = d.Date.ToString("MM-dd");
        p2.ShowLegend(Alignment.LowerCenter);
        p2.Legend.Orientation = ScottPlot.Orientation.Horizontal;

        string path = Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "out", "18b.png");
        p2.SavePng(path, 900, 450);

        return Ok("ShowLegend(Alignment.MiddleRight / LowerCenter)，底部图例配 Orientation.Horizontal 横排；两种位置都可用");
    }

    // ── 19 图例汇总 ────────────────────────────────────────
    public static CaseResult LegendAggregation(Plot p, List<DemoData.DayProfile> days)
    {
        SetCjk(p);
        foreach (var d in days)
            p.Add.SignalXY(HoursX, d.Load).LineWidth = 1.2f;

        // 7 天 > 5 天：不给每天单独条目，用一个手动条目概括
        p.Legend.ManualItems.Clear();
        p.Legend.ManualItems.Add(new ScottPlot.LegendItem
        {
            Label = $"每日曲线（{days.Count} 条）",
            LineColor = Colors.RoyalBlue,
            LineWidth = 1.2f,
        });

        p.ShowLegend(Alignment.UpperRight);
        return Ok("Legend.ManualItems 可手动构造条目；「N 条合并」不依赖自动生成，逻辑由调用方控制");
    }

    // ── 20 12 类图例条目 ───────────────────────────────────
    public static CaseResult LegendTwelveEntries(Plot p, List<DemoData.DayProfile> days)
    {
        SetCjk(p);
        var (xs, ys) = DemoData.Flatten(days);
        p.Add.SignalXY(xs, ys).LineWidth = 1;

        p.Legend.ManualItems.Clear();
        var entries = new (string Label, ScottPlot.Color Color, LinePattern Pattern)[]
        {
            ("每日曲线", Colors.RoyalBlue, LinePattern.Solid),
            ("平均曲线", Colors.Black, LinePattern.Dashed),
            ("最大需量", Colors.Blue, LinePattern.Dashed),
            ("计费容量", Colors.Red, LinePattern.Dotted),
            ("拟合曲线", Colors.OrangeRed, DashDot),
            ("充电段", Colors.DodgerBlue, LinePattern.Solid),
            ("放电段", Colors.Orange, LinePattern.Solid),
            ("工作日", Colors.RoyalBlue, LinePattern.Solid),
            ("周末", Colors.SteelBlue, LinePattern.Solid),
            ("节假日", Colors.OrangeRed, LinePattern.Solid),
            ("周末背景", Colors.Gray, LinePattern.Solid),
            ("异常值", Colors.Red, LinePattern.Solid),
        };

        foreach (var e in entries)
        {
            p.Legend.ManualItems.Add(new ScottPlot.LegendItem
            {
                Label = e.Label,
                LineColor = e.Color,
                LineWidth = 1.4f,
                LinePattern = e.Pattern,
            });
        }

        p.ShowLegend(Alignment.UpperRight);
        p.Legend.FontSize = 9f;

        return Ok("ManualItems 可放 12 条（含线型区分）；条目文字与顺序完全由调用方决定");
    }

    // ── 21 figsize + DPI ──────────────────────────────────
    public static CaseResult FigureSizeAndDpi(Plot p, List<DemoData.DayProfile> days)
    {
        SetCjk(p);
        var (xs, ys) = DemoData.Flatten(days);
        p.Add.SignalXY(xs, ys).LineWidth = 1;

        string d1 = Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "out", "21_100dpi.png");
        string d2 = Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "out", "21_200dpi.png");

        // 16×8 英寸：100 dpi → 1600×800；200 dpi → 3200×1600
        p.SavePng(d1, 1600, 800);
        p.SavePng(d2, 3200, 1600);

        return Ok("GetImage/SavePng 直接给像素尺寸：16×8 inch @100dpi=1600×800，@200dpi=3200×1600；DPI 用像素缩放表达（无独立 DPI 属性）");
    }

    // ── 22 中文字体回退 ────────────────────────────────────
    public static CaseResult CjkFontFallback(Plot p, List<DemoData.DayProfile> days)
    {
        string fontsDir = Environment.GetFolderPath(Environment.SpecialFolder.Fonts);
        var candidates = new[] { "msyh.ttc", "msyh.ttf", "simhei.ttf", "simsun.ttc" };
        var probe = string.Join(" / ", candidates.Select(c => $"{c}={File.Exists(Path.Combine(fontsDir, c))}"));

        SetCjk(p);
        var (xs, ys) = DemoData.Flatten(days);
        p.Add.SignalXY(xs, ys).LineWidth = 1;
        p.Title("中文字体回退 · 负荷曲线");
        p.XLabel("时间");
        p.YLabel("负荷值（kW）");
        p.ShowLegend(Alignment.UpperRight);

        return Ok($"三级回退按字体文件路径探测（{probe}），命中 {Path.GetFileName(CjkFontFile ?? "无")} 后 AddFontFile 注册 + plot.Font.Set 一次接管全部文本。注意：只设 Label.FontName 字体会 fallback 成 Segoe UI（中文变方块）");
    }

    // ── 23 负号 ───────────────────────────────────────────
    public static CaseResult NegativeSign(Plot p, List<DemoData.DayProfile> days)
    {
        SetCjk(p);
        var day = days.First(d => d.Load.Any(v => v < 0));
        p.Add.SignalXY(HoursX, day.Load).LineWidth = 1.6f;

        p.Add.HorizontalLine(0).Color = Colors.Gray;

        p.Axes.Left.TickLabelStyle.FontSize = 11;
        p.YLabel("负荷值（kW，含负值）");

        return Ok("负值刻度正常渲染（SkiaSharp 直接输出 ASCII '-'，不受 matplotlib 的 unicode_minus 问题影响）");
    }

    // ── 24 取消 ───────────────────────────────────────────
    public static CaseResult Cancellation(Plot p, List<DemoData.DayProfile> days)
    {
        using var cts = new CancellationTokenSource();
        int drawn = 0;

        foreach (var d in days)
        {
            if (cts.IsCancellationRequested) break;
            p.Add.SignalXY(HoursX, d.Load).LineWidth = 1;
            drawn++;

            if (drawn == 3) cts.Cancel(); // 模拟用户在第 3 天按下取消
        }

        return Ok($"CancellationToken 逐天检查：本次画到第 {drawn} 天响应取消。C# 侧天然支持，不需要框架级支持");
    }
}
