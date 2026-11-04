using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;
using LoomGrid.Bridge;
using ScottPlot;

namespace LoomGrid.UI;

/// <summary>分组列表项：把 (session, group, 资产, 月份) 打包。</summary>
public sealed class V3GroupItem
{
    public required string SessionId { get; init; }
    public required int GroupIndex { get; init; }
    public required string Asset { get; init; }
    public required string Month { get; init; }

    public string Display => $"{Asset} · {Month}";
}

/// <summary>
/// V3 预览与策略（P0）。
///
/// **渲染引擎的取舍（有意为之，不是 bug）**：
///   预览用 ScottPlot —— 为了交互（缩放、探针、双点对比、键盘翻页）；
///   导出用后端 matplotlib —— 保证成品质量与 Fusion 一致。
///   两边色值同源（见 CurveColors），但渲染引擎不同，字体与刻度细节会有细微差异。
///   选 PNG 方案的代价是把 V3 变成看图器，探针根本做不了 —— 那是功能倒退。
/// </summary>
public sealed partial class V3PreviewPage : Page
{
    private readonly List<V3GroupItem> _groups = new();
    private int _index = -1;
    private bool _busy;

    public V3PreviewPage()
    {
        InitializeComponent();
        Loaded += OnLoaded;
        KeyDown += OnKeyDown;
    }

    private async void OnLoaded(object sender, RoutedEventArgs e)
    {
        _groups.Clear();
        foreach (var f in Workspace.Current.Files)
        {
            if (f.SessionId is null) continue;
            for (int i = 0; i < f.Groups.Count; i++)
            {
                var g = f.Groups[i];
                _groups.Add(new V3GroupItem
                {
                    SessionId = f.SessionId,
                    GroupIndex = i,
                    Asset = string.IsNullOrWhiteSpace(g.Asset) ? "未命名" : g.Asset,
                    Month = string.IsNullOrWhiteSpace(g.Month) ? "—" : g.Month,
                });
            }
        }

        GroupList.ItemsSource = null;
        GroupList.ItemsSource = _groups;

        if (_groups.Count == 0)
        {
            SubtitleText.Text = "没有可预览的分组 —— 先在工作台添加文件并完成列映射";
            StatusText.Text = "0 个分组";
            CounterText.Text = "0 / 0";
            UpdateNav();
            return;
        }

        await SelectAsync(0);
    }

    private async Task SelectAsync(int idx)
    {
        if (idx < 0 || idx >= _groups.Count || _busy) return;
        _index = idx;
        GroupList.SelectedIndex = idx;
        UpdateNav();
        await RenderAsync();
    }

    private void UpdateNav()
    {
        bool has = _groups.Count > 0;
        PrevButton.IsEnabled = has && _index > 0;
        NextButton.IsEnabled = has && _index >= 0 && _index < _groups.Count - 1;
        CounterText.Text = has ? $"{_index + 1} / {_groups.Count}" : "0 / 0";
    }

    private async Task RenderAsync()
    {
        if (_index < 0 || _index >= _groups.Count) return;
        var g = _groups[_index];

        _busy = true;
        try
        {
            StatusText.Text = $"读取 {g.Display}…";
            var services = await Boot.EnsureAsync();
            var api = new PreviewApi(services.Api);
            var data = await api.GetAsync(g.SessionId, g.GroupIndex);

            Draw(g, data);
            ShapeText.Text = data.ShapeText;
            StatusText.Text =
                $"{g.Display} · {data.DateLabels.Count} 天 × {data.TimeLabels.Count} 点" +
                $"（{data.ShapeText}）";
        }
        catch (ApiException ex)
        {
            StatusText.Text = $"预览失败：{ex.Message}" + (ex.Code is null ? "" : $"（{ex.Code}）");
            AppLog.Write($"V3：预览失败 {ex.Code} {ex.Message}");
            PlotView.Plot.Clear();
            PlotView.Refresh();
        }
        catch (Exception ex)
        {
            StatusText.Text = $"预览出错：{ex.GetType().Name}";
            AppLog.Write($"V3：预览异常 {ex}");
        }
        finally
        {
            _busy = false;
        }
    }

    private void Draw(V3GroupItem g, PreviewResult data)
    {
        var plot = PlotView.Plot;
        plot.Clear();

        int nDays = data.LoadArray.Count;
        int nPts = data.TimeLabels.Count;
        if (nPts == 0 || nDays == 0)
        {
            plot.Title("没有数据");
            PlotView.Refresh();
            return;
        }

        // X 用「采样点序号」而不是小时：粒度可能是 96 也可能是 24/48，
        // 序号对所有粒度都成立；刻度标签再映射回 time_labels。
        double[] xs = Enumerable.Range(0, nPts).Select(i => (double)i).ToArray();

        for (int d = 0; d < nDays; d++)
        {
            var row = data.LoadArray[d];
            if (row.Count < nPts) continue;
            double[] ys = row.Take(nPts).ToArray();
            var sc = plot.Add.Scatter(xs, ys);
            sc.LegendText = d < data.DateLabels.Count ? data.DateLabels[d] : $"第 {d + 1} 天";
            sc.LineWidth = 1.4f;
            sc.MarkerSize = 0;
            sc.Color = CurveColors.ForDay(d);
        }

        // 刻度：最多 12 个，均匀取，标签用真实时刻
        int step = Math.Max(1, nPts / 12);
        var ticks = new List<Tick>();
        for (int i = 0; i < nPts; i += step)
            ticks.Add(new Tick(i, data.TimeLabels[i]));
        plot.Axes.Bottom.TickGenerator = new ScottPlot.TickGenerators.NumericManual(ticks.ToArray());
        plot.Axes.Bottom.TickLabelStyle.Rotation = 45;
        plot.Axes.Bottom.TickLabelStyle.FontSize = 9;

        plot.Axes.Title.Label.Text = $"{g.Asset} · {g.Month}";
        plot.Axes.Left.Label.Text = "负荷";
        plot.Axes.Bottom.Label.Text = "时刻";
        plot.ShowLegend(Alignment.UpperRight);

        // 字体由 PlotFontBootstrap 在启动时一次接管（AddFontFile + plot.Font.Set），
        // 这里不再逐个 Label 指定 —— 实测对标题和轴标签无效。
        PlotFontBootstrap.Apply(plot);

        PlotView.Refresh();
    }

    private async void OnGroupSelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (GroupList.SelectedIndex < 0 || GroupList.SelectedIndex == _index) return;
        // 选中变化由用户点击触发时 _index 会不同；程序设置时已在 SelectAsync 里赋值。
        if (GroupList.SelectedIndex != _index)
        {
            _index = GroupList.SelectedIndex;
            UpdateNav();
            await RenderAsync();
        }
    }

    private async void OnPrevClick(object sender, RoutedEventArgs e) => await SelectAsync(_index - 1);

    private async void OnNextClick(object sender, RoutedEventArgs e) => await SelectAsync(_index + 1);

    private async void OnKeyDown(object sender, Microsoft.UI.Xaml.Input.KeyRoutedEventArgs e)
    {
        if (e.Key == Windows.System.VirtualKey.Left) { await SelectAsync(_index - 1); e.Handled = true; }
        else if (e.Key == Windows.System.VirtualKey.Right) { await SelectAsync(_index + 1); e.Handled = true; }
    }

    private void OnBackClick(object sender, RoutedEventArgs e)
    {
        if (Frame.CanGoBack) Frame.GoBack();
    }

    private void OnGotoExportClick(object sender, RoutedEventArgs e)
        => Frame.Navigate(typeof(V5ExportPage));
}

/// <summary>
/// 曲线配色。与后端 matplotlib 出图同源（深色主题下的日曲线色序）。
/// **改这里要同步改 plotting 侧的色序**，否则预览与导出会长得不一样。
/// </summary>
internal static class CurveColors
{
    private static readonly ScottPlot.Color[] Palette =
    {
        ScottPlot.Color.FromHex("#4EA8DE"),
        ScottPlot.Color.FromHex("#3FB950"),
        ScottPlot.Color.FromHex("#D29922"),
        ScottPlot.Color.FromHex("#F85149"),
        ScottPlot.Color.FromHex("#A371F7"),
        ScottPlot.Color.FromHex("#39C5CF"),
        ScottPlot.Color.FromHex("#DB6D28"),
        ScottPlot.Color.FromHex("#8B949E"),
    };

    public static ScottPlot.Color ForDay(int dayIndex) => Palette[dayIndex % Palette.Length];
}
