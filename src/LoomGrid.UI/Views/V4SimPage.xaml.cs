using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;
using Microsoft.UI.Xaml.Navigation;
using ScottPlot;
using LoomGrid.Bridge;

namespace LoomGrid.UI;

/// <summary>
/// 负荷模拟：25 个行业模板 → 参数化生成负荷曲线 → 应用为 V4/V3 的数据源。
///
/// **模板来源**：`services/loadsim.py` 的 25 个（原样搬自 Fusion 主程序）。
/// `algorithms/loadgen.py` 里另有 10 个行业的一套引擎，曲线形状不同，不要混用。
///
/// **与 Fusion 的一处有意差异**：预览**带噪声**。
/// Fusion 的预览固定 `noise_std=0`（平滑），代价是用户拖「噪声」滑杆时画面不动 ——
/// 那个滑杆就成了瞎的。而本页的承诺是「看到的就是落地的」，所以预览与落地同参数同 seed。
///
/// **seed 契约**：预览与「应用」共用 `_seed`。点「换一条」只换 seed，
/// 因此「同参数另一条样本」与「换参数」在视觉上可区分。
/// 落地后 `applied_seed` 回显在页脚，用户想复现同一条时不用翻日志。
/// </summary>
public sealed partial class V4SimPage : Page
{
    private readonly List<SimTemplate> _templates = new();
    private readonly DispatcherTimer _debounce;

    private int _seed = Random.Shared.Next(1, int.MaxValue);

    /// <summary>最近一次预览的数据。应用时以同一 seed 重新生成，第 0 天与预览逐点一致。</summary>
    private SimLoadResult? _preview;

    private bool _suppress;
    private bool _ready;

    public V4SimPage()
    {
        InitializeComponent();

        _debounce = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(300) };
        _debounce.Tick += async (_, _) =>
        {
            _debounce.Stop();
            await PreviewAsync();
        };

        Loaded += OnLoaded;
    }

    private async void OnLoaded(object sender, RoutedEventArgs e)
    {
        if (_ready) return;
        _ready = true;

        var services = AppServices.Current;
        if (services is null)
        {
            HintText.Text = "后端还没就绪";
            return;
        }

        PointsCombo.ItemsSource = new List<string>
        {
            "96 · 15 分钟", "48 · 30 分钟", "24 · 小时",
        };
        PointsCombo.SelectedIndex = 0;

        try
        {
            var api = new SimulateApi(services.Api);
            var list = await api.TemplatesAsync();
            _templates.Clear();
            _templates.AddRange(list);

            _suppress = true;
            IndustryCombo.ItemsSource = _templates;
            IndustryCombo.DisplayMemberPath = nameof(SimTemplate.Name);
            IndustryCombo.SelectedIndex = _templates.FindIndex(t => t.Key == "电子制造") is int i and >= 0
                ? i : 0;
            _suppress = false;

            FillFromTemplate();
            AppLog.Write($"V4 负荷模拟：模板 {_templates.Count} 个，默认 {CurrentTemplate()?.Name}");
            await PreviewAsync();
        }
        catch (Exception ex)
        {
            AppLog.Write("V4 负荷模拟：模板加载失败 " + ex.Message);
            HintText.Text = "模板加载失败：" + ex.Message;
        }
    }

    private SimTemplate? CurrentTemplate()
    {
        int i = IndustryCombo.SelectedIndex;
        return i >= 0 && i < _templates.Count ? _templates[i] : null;
    }

    /// <summary>选模板后把三个系数填成模板值，并把模板原值显示在最右列。</summary>
    private void FillFromTemplate()
    {
        var t = CurrentTemplate();
        if (t is null) return;

        _suppress = true;
        LfSlider.Value = t.DailyLoadFactor;
        PvSlider.Value = t.PeakValleyRatio;
        WfSlider.Value = t.WeekendFactor;
        _suppress = false;

        LfTemplate.Text = $"模板 {t.DailyLoadFactor:0.00}";
        PvTemplate.Text = $"模板 {t.PeakValleyRatio:0.00}";
        WfTemplate.Text = $"模板 {t.WeekendFactor:0.00}";
        TemplateSourceText.Text = $"来源：{t.Description}　班制：{t.ScheduleText}";
        UpdateSliderReadouts();
    }

    private void UpdateSliderReadouts()
    {
        LfValue.Text = $"{LfSlider.Value:0.00}";
        PvValue.Text = $"{PvSlider.Value:0.00}";
        WfValue.Text = $"{WfSlider.Value:0.00}";
        NoiseValue.Text = $"{NoiseSlider.Value:0.00}";
        PvKwValue.Text = $"{PvKwSlider.Value:0}";
    }

    private void OnIndustryChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_suppress || !_ready) return;
        FillFromTemplate();
        SchedulePreview();
    }

    private void OnSliderChanged(object sender, RangeBaseValueChangedEventArgs e)
    {
        if (!_ready) return;
        UpdateSliderReadouts();
        if (_suppress) return;
        SchedulePreview();
    }

    private void OnPointsChanged(object sender, SelectionChangedEventArgs e)
    {
        if (!_ready) return;
        SchedulePreview();
    }

    private void OnParamChanged(object sender, TextChangedEventArgs e)
    {
        if (!_ready) return;
        SchedulePreview();
    }

    private void SchedulePreview()
    {
        _debounce.Stop();
        _debounce.Start();
    }

    private int Points()
    {
        string s = PointsCombo.SelectedItem as string ?? "96";
        int sp = s.IndexOf(' ');
        string head = sp > 0 ? s[..sp] : s;
        return int.TryParse(head, out int n) && n > 0 ? n : 96;
    }

    private int Days()
    {
        return int.TryParse(DaysBox.Text?.Trim(), out int n) && n >= 1 && n <= 366 ? n : 31;
    }

    /// <summary>拼请求。预览与落地共用它，只有 n_days 不同。</summary>
    private SimLoadRequest BuildRequest(int nDays)
    {
        var t = CurrentTemplate();
        double avgPower = double.TryParse(AvgPowerBox.Text?.Trim(), out double p) && p > 0 ? p : 500;

        return new SimLoadRequest
        {
            Industry = t?.Key ?? "电子制造",
            AvgPowerKw = avgPower,
            // 只在用户把滑杆拖离模板值时才显式覆盖，否则传 null 让服务端用模板值 ——
            // 这样「没动过的参数」与「模板」永远一致，不会因为默认值造成悄悄偏离。
            DailyLoadFactor = t is null || Math.Abs(LfSlider.Value - t.DailyLoadFactor) < 0.005
                ? null : LfSlider.Value,
            PeakValleyRatio = t is null || Math.Abs(PvSlider.Value - t.PeakValleyRatio) < 0.005
                ? null : PvSlider.Value,
            WeekendFactor = t is null || Math.Abs(WfSlider.Value - t.WeekendFactor) < 0.005
                ? null : WfSlider.Value,
            NoiseStd = NoiseSlider.Value,
            PvKw = PvKwSlider.Value,
            NDays = nDays,
            NPoints = Points(),
            Seed = _seed,
        };
    }

    private async Task PreviewAsync()
    {
        var services = AppServices.Current;
        if (services is null) return;

        try
        {
            var api = new SimulateApi(services.Api);
            // 预览取 1 天：够看形状，且第 0 天的噪声序列与落地那条相同（同 seed 顺序抽取）。
            var r = await api.GenerateAsync(BuildRequest(1));
            _preview = r;
            DrawPreview(r);
            ApplyButton.IsEnabled = true;
        }
        catch (Exception ex)
        {
            AppLog.Write("V4 负荷模拟：预览失败 " + ex.Message);
            HintText.Text = "预览失败：" + ex.Message;
            ApplyButton.IsEnabled = false;
        }
    }

    private void DrawPreview(SimLoadResult r)
    {
        if (r.LoadArray.Count == 0) return;

        var row = r.LoadArray[0];
        double[] xs = Enumerable.Range(0, row.Count).Select(i => (double)i).ToArray();
        double[] ys = row.ToArray();

        var plot = PreviewPlot.Plot;
        plot.Clear();

        var sc = plot.Add.Scatter(xs, ys);
        sc.Color = ScottPlot.Color.FromHex("#4EA8DE");
        sc.LineWidth = 2.0f;
        sc.MarkerSize = 0;

        // 用索引作 X，人工给刻度标签；直接传时刻字符串在 ScottPlot 里坑更多。
        int step = Math.Max(1, row.Count / 8);
        var ticks = new List<Tick>();
        for (int i = 0; i < row.Count; i += step)
            ticks.Add(new Tick(i, r.TimeLabels.Count > i ? r.TimeLabels[i] : i.ToString()));
        plot.Axes.Bottom.TickGenerator = new ScottPlot.TickGenerators.NumericManual(ticks.ToArray());
        plot.Axes.Bottom.TickLabelStyle.Rotation = 45;

        plot.Title($"{r.Industry} · 典型日（seed #{r.AppliedSeed}）");
        plot.XLabel("时刻");
        plot.YLabel("负荷 kW");
        plot.Axes.AutoScale();

        PlotFontBootstrap.Apply(plot);
        PreviewPlot.Refresh();

        StatsText.Text = $"峰值 {r.Stats.Max:N0} · 谷值 {r.Stats.Min:N0} · 均值 {r.Stats.Mean:N0} kW";
        SeedText.Text = $"#{r.AppliedSeed}";
        HintText.Text = "";
    }

    private async void OnReshuffleClick(object sender, RoutedEventArgs e)
    {
        _seed = Random.Shared.Next(1, int.MaxValue);
        AppLog.Write($"V4 负荷模拟：换一条 seed=#{_seed}");
        await PreviewAsync();
    }

    private async void OnApplyClick(object sender, RoutedEventArgs e)
    {
        var services = AppServices.Current;
        if (services is null || _preview is null) return;

        int days = Days();
        ApplyButton.IsEnabled = false;
        try
        {
            var api = new SimulateApi(services.Api);

            // 用同一个 seed 生成完整天数。第 0 天与预览那一行逐点一致（噪声按天顺序抽取）。
            var full = await api.GenerateAsync(BuildRequest(days));

            var ws = Workspace.Current;
            string original = ws.OriginalSessionId ?? ws.SessionId
                              ?? throw new InvalidOperationException("没有可替换的 session");

            var applied = await api.ApplyAsync(new SimApplyRequest
            {
                OriginalSessionId = original,
                LoadArray = full.LoadArray,
                TimeLabels = full.TimeLabels,
                DateLabels = full.DateLabels,
                Industry = full.Industry,
                Seed = full.AppliedSeed,
                Asset = full.Industry,
            });

            ws.OriginalSessionId = original;
            ws.SimulatedSessionId = applied.SessionId;
            ws.SimOriginIndustry = full.Industry;
            ws.SimOriginSeed = full.AppliedSeed;
            ws.SimAsset = applied.Asset;
            ws.SimMonth = applied.MonthStr;
            ws.SimDays = full.DateLabels.Count;
            ws.SimPoints = full.Shape.Count > 1 ? full.Shape[1] : full.TimeLabels.Count;
            ws.SessionId = applied.SessionId;

            AppLog.Write($"V4 负荷模拟：应用 {full.Industry} · seed #{full.AppliedSeed} · " +
                         $"{days} 天 × {full.Shape.LastOrDefault()} 点 · " +
                         $"模拟 session={applied.SessionId}（原始 {original} 保留）");

            if (Frame.CanGoBack) Frame.GoBack();
        }
        catch (Exception ex)
        {
            AppLog.Write("V4 负荷模拟：应用失败 " + ex.Message);
            HintText.Text = "应用失败：" + ex.Message;
            ApplyButton.IsEnabled = true;
        }
    }

    private void OnCancelClick(object sender, RoutedEventArgs e)
    {
        if (Frame.CanGoBack) Frame.GoBack();
        else Frame.Navigate(typeof(V4CapacityPage));
    }
}
