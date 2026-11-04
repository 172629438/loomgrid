using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using ScottPlot;

namespace CurveSpike;

public sealed record CaseResult(
    string Id,
    string Name,
    string Verdict,   // 能 / 不能 / 有条件
    string Note,
    string? Png);

public static class Program
{
    private static readonly string OutDir =
        Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "out");

    private static readonly List<CaseResult> Results = new();

    public static int Main()
    {
        Directory.CreateDirectory(OutDir);
        Console.OutputEncoding = Encoding.UTF8;

        var days = DemoData.Build();

        Run("01", "多天负荷曲线（每天一条）", p => Cases.MultiDayCurves(p, days));
        Run("02", "降采样（>500 点自动降）", p => Cases.Downsampling(p, days));
        Run("03", "平均曲线（黑色虚线）", p => Cases.AverageCurve(p, days));
        Run("04", "最大需量线（虚 + 数值标注）", p => Cases.MaxDemandLine(p, days));
        Run("05", "计费容量线（点线 + 数值标注）", p => Cases.BillingCapacityLine(p, days));
        Run("06", "拟合曲线（多项式，点划线）", p => Cases.FitCurve(p, days));
        Run("07", "异常值标记（红 × 散点）", p => Cases.OutlierMarkers(p, days));
        Run("08", "充放策略背景（区间填充 alpha）", p => Cases.ChargeDischargeSpans(p, days));
        Run("09", "周末背景阴影（alpha 可配）", p => Cases.WeekendShading(p, days));
        Run("10", "三色配置（工作日/周末/节假日）", p => Cases.ThreeColorScheme(p, days));
        Run("11", "高亮模式四种（none/weekend/holiday/grouped）", p => Cases.HighlightModes(p, days));
        Run("12", "tab10 调色板（每天不同色）", p => Cases.Tab10Palette(p, days));
        Run("13", "暗色主题", p => Cases.DarkTheme(p, days));
        Run("14", "X 轴 ≥25 点：整点标签 + 旋转 45°", p => Cases.HourTickLabels(p, days));
        Run("15", "X 轴 <25 点：原始 time_labels", p => Cases.RawTimeLabels(p));
        Run("16", "轴标签 + 标题 16pt", p => Cases.AxisLabelsAndTitle(p, days));
        Run("17", "网格 alpha 0.3", p => Cases.GridAlpha(p, days));
        Run("18", "图例位置可配（right / bottom）", p => Cases.LegendPositions(p, days));
        Run("19", "图例自动汇总（>5 天合并）", p => Cases.LegendAggregation(p, days));
        Run("20", "图例 12 类条目", p => Cases.LegendTwelveEntries(p, days));
        Run("21", "figsize 16×8 + DPI 可配", p => Cases.FigureSizeAndDpi(p, days));
        Run("22", "中文字体三级回退", p => Cases.CjkFontFallback(p, days));
        Run("23", "负号正常显示", p => Cases.NegativeSign(p, days));
        Run("24", "导出中途取消（cancel_event）", p => Cases.Cancellation(p, days));

        Report();
        return 0;
    }

    private static void Run(string id, string name, Func<Plot, CaseResult> body)
    {
        var plot = new Plot();
        CaseResult result;
        try
        {
            result = body(plot) with { Id = id, Name = name };
        }
        catch (Exception ex)
        {
            result = new CaseResult(id, name, "不能",
                $"异常 {ex.GetType().Name}: {ex.Message}", null);
        }

        string? png = null;
        try
        {
            png = Path.Combine(OutDir, $"{id}.png");
            plot.SavePng(png, 900, 450);
        }
        catch (Exception ex)
        {
            result = result with { Note = result.Note + $"（出图失败：{ex.Message}）" };
        }

        Results.Add(result);
        Console.WriteLine($"[{result.Verdict}] {id} {name} — {result.Note}");
    }

    private static void Report()
    {
        var opts = new JsonSerializerOptions
        {
            WriteIndented = true,
            Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
        };
        string json = JsonSerializer.Serialize(Results, opts);
        string md = Path.Combine(OutDir, "results.json");
        File.WriteAllText(md, json, Encoding.UTF8);

        Console.WriteLine();
        Console.WriteLine("=== 汇总 ===");
        foreach (var g in Results.GroupBy(r => r.Verdict))
            Console.WriteLine($"{g.Key}: {g.Count()}");
        Console.WriteLine($"输出目录：{Path.GetFullPath(OutDir)}");
    }
}
