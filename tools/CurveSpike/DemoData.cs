namespace CurveSpike;

/// <summary>
/// 模拟数据：7 天 96 点，含周末、含零值与负值（用于异常值标记）、
/// 含明显充放时段（11:00-14:00 / 18:00-21:00）。
/// </summary>
public static class DemoData
{
    public const int Days = 7;
    public const int PointsPerDay = 96;

    public sealed record DayProfile(int Index, DateTime Date, bool IsWeekend, bool IsHoliday, double[] Load);

    public static double[] TimeLabels { get; } = BuildTimeLabels();

    public static List<DayProfile> Build()
    {
        var start = new DateTime(2026, 3, 2); // 周一
        var list = new List<DayProfile>();
        var rnd = new Random(20260302);

        for (int d = 0; d < Days; d++)
        {
            var date = start.AddDays(d);
            bool weekend = date.DayOfWeek is DayOfWeek.Saturday or DayOfWeek.Sunday;
            bool holiday = d == 4; // 假装中间有一天节假日，用于三色配置

            var load = new double[PointsPerDay];
            for (int i = 0; i < PointsPerDay; i++)
            {
                double h = i * 0.25;

                // 双峰：早 9:30、晚 19:00
                double morning = 620 * Math.Exp(-Math.Pow((h - 9.5) / 2.6, 2));
                double evening = 700 * Math.Exp(-Math.Pow((h - 19.0) / 3.0, 2));
                double baseLoad = weekend ? 300 : 380;
                double noise = (rnd.NextDouble() - 0.5) * 26;

                double v = baseLoad + Math.Max(morning, evening) + noise;

                // 夜间低谷
                if (h < 6) v *= 0.55;

                load[i] = Math.Round(v, 1);
            }

            // 异常值：第 2 天放一个 0，第 5 天放两个负值（模拟光伏倒送）
            if (d == 1) load[40] = 0;
            if (d == 4) { load[30] = -80; load[72] = -45; }

            list.Add(new DayProfile(d, date, weekend, holiday, load));
        }

        return list;
    }

    private static double[] BuildTimeLabels()
    {
        var labels = new string[PointsPerDay];
        for (int i = 0; i < PointsPerDay; i++)
            labels[i] = $"{i / 4:00}:{i % 4 * 15:00}";
        return new double[0];
    }

    public static string[] TimeLabelStrings()
    {
        var labels = new string[PointsPerDay];
        for (int i = 0; i < PointsPerDay; i++)
            labels[i] = $"{i / 4:00}:{i % 4 * 15:00}";
        return labels;
    }

    /// <summary>把 7×96 摊平成一条连续曲线（多天查看模式）。</summary>
    public static (double[] xs, double[] ys) Flatten(IReadOnlyList<DayProfile> days)
    {
        int n = days.Count * PointsPerDay;
        var xs = new double[n];
        var ys = new double[n];
        int k = 0;
        foreach (var day in days)
        {
            for (int i = 0; i < PointsPerDay; i++)
            {
                xs[k] = k * 0.25;
                ys[k] = day.Load[i];
                k++;
            }
        }
        return (xs, ys);
    }
}
