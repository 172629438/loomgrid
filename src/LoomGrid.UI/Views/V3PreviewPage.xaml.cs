using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
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

    // 不写这个，ListView 会直接显示类型全名「LoomGrid.UI.V3GroupItem」。
    public override string ToString() => Display;
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
    private PreviewResult? _data;
    private int _selectedDay = -1;   // -1 = 画全部天

    // 双点对比：A → B → 重置（第三下把 A 换成新落点）。
    // 为什么不是「点空白重置」：曲线区域里几乎没有真正的空白（曲线/网格/刻度/图例都在），
    // 用户猜不到「点哪里才算空白」。三下循环语义弱，但可预测。
    private (int Day, int Idx)? _pointA;
    private (int Day, int Idx)? _pointB;
    // 对比标记的 plottable 引用：按引用删比按类型猜可靠（LinePattern 不支持 == 比较）
    private readonly List<ScottPlot.IPlottable> _comparePlottables = new();

    public V3PreviewPage()
    {
        InitializeComponent();
        Loaded += OnLoaded;
        KeyDown += OnKeyDown;
        PlotView.PointerWheelChanged += OnPointerWheelChanged;
        Probe.RenderTransform = new Microsoft.UI.Xaml.Media.TranslateTransform();
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
            _data = data;
            _selectedDay = -1;

            Draw(g, data);
            ShapeText.Text = data.ShapeText;
            StatusText.Text =
                $"{g.Display} · {data.DateLabels.Count} 天 × {data.TimeLabels.Count} 点" +
                $"（{data.ShapeText}）";
            BuildDatePanel(data);
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

    /// <summary>
    /// 日期面板：每格一天。点一天只看那天，再点同一格回到全部。
    /// 天数多的月份自动换行（3 列）。
    /// </summary>
    private void BuildDatePanel(PreviewResult data)
    {
        DateGrid.Children.Clear();
        DateGrid.ColumnDefinitions.Clear();
        DateGrid.RowDefinitions.Clear();

        const int Cols = 3;
        for (int c = 0; c < Cols; c++)
            DateGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });

        int n = data.DateLabels.Count;
        int rows = (n + Cols - 1) / Cols;
        for (int r = 0; r < rows; r++)
            DateGrid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });

        for (int d = 0; d < n; d++)
        {
            string label = data.DateLabels[d];
            var btn = new Button
            {
                Content = label.Length >= 5 ? label.Substring(5) : label,   // 只显示 MM-DD，省宽度
                FontSize = 11.5,
                Padding = new Thickness(4, 3, 4, 3),
                Margin = new Thickness(0, 0, 4, 4),
                HorizontalAlignment = Microsoft.UI.Xaml.HorizontalAlignment.Stretch,
                HorizontalContentAlignment = Microsoft.UI.Xaml.HorizontalAlignment.Center,
                Tag = d,
            };
            btn.Click += OnDateClick;
            if (d == _selectedDay)
                btn.Style = (Style)Application.Current.Resources["AccentButtonStyle"];
            Grid.SetColumn(btn, d % Cols);
            Grid.SetRow(btn, d / Cols);
            DateGrid.Children.Add(btn);
        }
    }

    private void OnDateClick(object sender, RoutedEventArgs e)
    {
        if (sender is not Button b || b.Tag is not int d || _data is null) return;
        if (_index < 0 || _index >= _groups.Count) return;

        _selectedDay = _selectedDay == d ? -1 : d;
        Draw(_groups[_index], _data);
        BuildDatePanel(_data);
        StatusText.Text = _selectedDay >= 0
            ? $"只看 {_data.DateLabels[_selectedDay]}（再点同一格回全部）"
            : $"{_groups[_index].Display} · 全部 {_data.DateLabels.Count} 天";
    }

    /// <summary>
    /// 探针：鼠标在图上移动 → 读数该时刻的负荷。
    /// 用 XAML 浮层而不是 plot.Add.Text：浮层只改 Canvas 位置，ScottPlot 不重绘；
    /// 画在图上则是每次移动重绘整张图，会卡。IsHitTestVisible=False 让浮层不吃事件。
    /// 读数取「当前显示的那一天」：单日模式看那天，否则看最后一天。
    /// 注：Fusion 曲线上原本没有鼠标交互（主程序无 mouseMoveEvent），探针是新增能力。
    /// </summary>
    private void OnPointerMoved(object sender, Microsoft.UI.Xaml.Input.PointerRoutedEventArgs e)
    {
        if (_data is null) return;

        var px = e.GetCurrentPoint(PlotView).Position;
        if (px.X < 0 || px.Y < 0) return;
        // 诊断：先确认事件到不到。之前两次换绑定层级都无效，但没区分
        // 「事件未触发」与「触发了但浮层未显示」——盲改。
        AppLog.Write($"V3 探针：PointerMoved ({px.X:0},{px.Y:0}) data={(_data is null ? "null" : "ok")}");

        int nPts = _data.TimeLabels.Count;
        if (nPts == 0) return;

        ScottPlot.Coordinates c;
        try
        {
            c = PlotView.Plot.GetCoordinates(new Pixel(px.X, px.Y));
        }
        catch { return; }

        int idx = (int)Math.Round(c.X);
        if (idx < 0 || idx >= nPts) { Probe.Visibility = Visibility.Collapsed; return; }

        int day = _selectedDay >= 0 ? _selectedDay : _data.LoadArray.Count - 1;
        if (day < 0 || day >= _data.LoadArray.Count) return;
        var row = _data.LoadArray[day];
        if (idx >= row.Count) return;

        string dateLabel = day < _data.DateLabels.Count ? _data.DateLabels[day] : $"第 {day + 1} 天";
        ProbeTime.Text = $"{dateLabel} {_data.TimeLabels[idx]}";
        ProbeValue.Text = $"{row[idx]:N1} kW";

        // 贴鼠标右下，靠边时翻到另一侧——避免浮层被窗口边界裁掉。
        // 注意：Probe 的父容器是 Grid，Canvas.SetLeft/Top 在这里**不生效**
        // （那是 Canvas 的附加属性），必须用 TranslateTransform。
        double left = px.X + 14;
        double top = px.Y - 44;
        if (left + 170 > PlotView.ActualWidth) left = px.X - 170;
        if (top < 4) top = px.Y + 18;
        if (Probe.RenderTransform is Microsoft.UI.Xaml.Media.TranslateTransform tt)
        {
            tt.X = left;
            tt.Y = top;
        }
        Probe.Visibility = Visibility.Visible;
        // 诊断：事件到达与浮层显示是两环。前面已证实 PointerMoved 会触发（日志有 x/y），
        // 但截图里看不到浮层——到底是没设 Visibility，还是截图时鼠标已移开（PointerExited），
        // 这行日志把它分开。
        AppLog.Write($"V3 探针浮层：pos=({left:0},{top:0}) vis={Probe.Visibility} " +
                     $"text=\"{ProbeTime.Text}\" / \"{ProbeValue.Text}\"");
    }

    private void OnPointerExited(object sender, Microsoft.UI.Xaml.Input.PointerRoutedEventArgs e)
        => Probe.Visibility = Visibility.Collapsed;

    /// <summary>
    /// 双点对比：点一下落 A，再点落 B 并算差值，第三下重置（A 变新落点，B 清空）。
    /// 差值口径：绝对值必给；百分比只在 A≠0 且 A、B 同号时给——
    /// 曲线里有负值（光伏倒送），A=-50 B=100 时百分比算出来是 -300%，数学对但用户会看懵。
    /// </summary>
    private void OnPointerPressed(object sender, Microsoft.UI.Xaml.Input.PointerRoutedEventArgs e)
    {
        if (_data is null || _index < 0 || _index >= _groups.Count) return;

        var px = e.GetCurrentPoint(PlotView).Position;
        ScottPlot.Coordinates c;
        try { c = PlotView.Plot.GetCoordinates(new Pixel(px.X, px.Y)); }
        catch { return; }

        int nPts = _data.TimeLabels.Count;
        int idx = (int)Math.Round(c.X);
        if (idx < 0 || idx >= nPts) return;

        int day = _selectedDay >= 0 ? _selectedDay : _data.LoadArray.Count - 1;
        if (day < 0 || day >= _data.LoadArray.Count) return;
        if (idx >= _data.LoadArray[day].Count) return;

        // 三下循环
        if (_pointA is null) { _pointA = (day, idx); }
        else if (_pointB is null) { _pointB = (day, idx); }
        else { _pointA = (day, idx); _pointB = null; }

        RedrawCompare();
        e.Handled = true;
    }

    private void ClearCompare()
    {
        _pointA = null;
        _pointB = null;
        RedrawCompare();
    }

    /// <summary>对比标记重绘。先清掉上次的 plottable，再按当前状态重画。</summary>
    private void RedrawCompare()
    {
        if (_data is null || _index < 0 || _index >= _groups.Count) return;
        var plot = PlotView.Plot;

        // 清旧标记（按引用删，不动曲线本体）
        foreach (var p in _comparePlottables)
            plot.PlottableList.Remove(p);
        _comparePlottables.Clear();

        double? vA = _pointA is null ? null : _data.LoadArray[_pointA.Value.Day][_pointA.Value.Idx];
        double? vB = _pointB is null ? null : _data.LoadArray[_pointB.Value.Day][_pointB.Value.Idx];

        var cA = ScottPlot.Color.FromHex("#4EA8DE");   // A 青蓝
        var cB = ScottPlot.Color.FromHex("#D29922");   // B 琥珀

        if (_pointA is not null && vA is not null)
        {
            var m = plot.Add.Marker(_pointA.Value.Idx, vA.Value);
            m.Color = cA; m.Size = 9; m.Shape = ScottPlot.MarkerShape.FilledCircle;
            _comparePlottables.Add(m);
            var t = plot.Add.Text("A", _pointA.Value.Idx, vA.Value);
            t.Color = cA; t.OffsetY = -14; t.FontSize = 12;
            _comparePlottables.Add(t);
        }
        if (_pointB is not null && vB is not null)
        {
            var m = plot.Add.Marker(_pointB.Value.Idx, vB.Value);
            m.Color = cB; m.Size = 9; m.Shape = ScottPlot.MarkerShape.FilledCircle;
            _comparePlottables.Add(m);
            var t = plot.Add.Text("B", _pointB.Value.Idx, vB.Value);
            t.Color = cB; t.OffsetY = -14; t.FontSize = 12;
            _comparePlottables.Add(t);

            if (_pointA is not null && vA is not null)
            {
                // 虚线连接：两点对比的核心是「与之间隔的关系」
                var ln = plot.Add.Line(_pointA.Value.Idx, vA.Value, _pointB.Value.Idx, vB.Value);
                ln.Color = ScottPlot.Color.FromHex("#8B949E");
                ln.LinePattern = ScottPlot.LinePattern.Dashed;
                ln.LineWidth = 1.2f;
                _comparePlottables.Add(ln);
            }
        }

        // 文案
        string lineA = _pointA is null ? "" :
            $"A: {_data.DateLabels[_pointA.Value.Day]} {_data.TimeLabels[_pointA.Value.Idx]} · {vA:N1} kW";
        string lineB = _pointB is null ? "" :
            $"B: {_data.DateLabels[_pointB.Value.Day]} {_data.TimeLabels[_pointB.Value.Idx]} · {vB:N1} kW";

        string diff = "";
        if (vA is not null && vB is not null)
        {
            double d = vB.Value - vA.Value;
            diff = $"Δ: {d:+0.0;-0.0;0.0} kW";
            // 百分比只在 A≠0 且同号时给（异号时百分比数学对但会误导）
            if (Math.Abs(vA.Value) > 1e-9 && vA.Value * vB.Value > 0)
                diff += $"（较 A {(d / Math.Abs(vA.Value) * 100):+0.0;-0.0;0.0}%）";
            else if (Math.Abs(vA.Value) <= 1e-9)
                diff += "（A 为 0，不计百分比）";
            else
                diff += "（A、B 异号，不计百分比）";

            int di = _pointB.Value.Idx - _pointA.Value.Idx;
            int minutes = di * (24 * 60 / Math.Max(1, _data.TimeLabels.Count));
            diff += $"  Δt: {Math.Abs(minutes) / 60}h{Math.Abs(minutes) % 60:00}m";
        }

        // 状态区 + 右下角淡字提示（11px / 40% 透明）
        if (_pointA is null)
        {
            CompareText.Text = "";
            CompareHint.Text = "点图落 A 点";
        }
        else if (_pointB is null)
        {
            CompareText.Text = lineA;
            CompareHint.Text = "再点一次落 B";
        }
        else
        {
            CompareText.Text = $"{lineA}　{lineB}　{diff}";
            CompareHint.Text = "再点一次重新开始 · Esc 清除";
        }

        PlotFontBootstrap.Apply(plot);
        PlotView.Refresh();
    }

    /// <summary>
    /// F12 自检：取 X 轴中心位置，走一遍「坐标 → 索引 → 数值」并打日志。
    /// <para><b>边界</b>：它只验证换算链（GetCoordinates / 索引取整 / 数组取值）。</para>
    /// <para>它**不验证** PointerMoved 是否触发——那条要靠真实鼠标移动。</para>
    /// <para>两者都过，探针才算可用；只有 F12 过只说明公式对。</para>
    /// </summary>
    private void OnDebugProbe(KeyboardAccelerator sender, KeyboardAcceleratorInvokedEventArgs args)
    {
        args.Handled = true;
        if (_data is null) { AppLog.Write("V3 探针自检：数据为空"); return; }

        var plot = PlotView.Plot;
        // 位置可由环境变量指定（开发自测用）；默认轴中点。
        // 为什么需要：轴中点恰好是 .5 边界（如 47.50），用它测舍入规则是碰运气——
        // 必须能指定 47.49 / 47.50 / 47.51 才能覆盖边界两侧。
        double cx;
        if (double.TryParse(Environment.GetEnvironmentVariable("LGM_PROBE_X"), out var forcedX))
            cx = forcedX;
        else
        {
            var range = plot.Axes.Bottom.Range;
            cx = range.Min + (range.Max - range.Min) / 2;
        }
        int idx = (int)Math.Round(cx);
        int nPts = _data.TimeLabels.Count;
        if (idx < 0 || idx >= nPts)
        {
            AppLog.Write($"V3 探针自检：x={cx:F2} 超出范围 (0..{nPts - 1})");
            return;
        }

        int day = _selectedDay >= 0 ? _selectedDay : _data.LoadArray.Count - 1;
        if (day < 0 || day >= _data.LoadArray.Count) { AppLog.Write("V3 探针自检：无可用行"); return; }
        var row = _data.LoadArray[day];
        if (idx >= row.Count) { AppLog.Write($"V3 探针自检：idx={idx} 超出该行长度 {row.Count}"); return; }

        string date = day < _data.DateLabels.Count ? _data.DateLabels[day] : $"第{day + 1}天";
        AppLog.Write($"V3 探针自检：x={cx:F2} idx={idx} time={_data.TimeLabels[idx]} " +
                     $"date={date} load={row[idx]:F1}（仅验换算链，不验事件）");
    }

    /// <summary>Shift+滚轮 或 滚轮 = 缩放 X 轴；这是 V3 的 P1 交互之一。</summary>
    private void OnPointerWheelChanged(object sender, Microsoft.UI.Xaml.Input.PointerRoutedEventArgs e)
    {
        if (_data is null) return;
        var plot = PlotView.Plot;
        var lim = plot.Axes.Bottom.Range;
        double center = (lim.Min + lim.Max) / 2;
        double half = (lim.Max - lim.Min) / 2;

        int delta = e.GetCurrentPoint(PlotView).Properties.MouseWheelDelta;
        double factor = delta > 0 ? 0.8 : 1.25;

        double minHalf = Math.Max(2, _data.TimeLabels.Count / 48.0);
        double maxHalf = _data.TimeLabels.Count * 1.2 / 2;
        half = Math.Clamp(half * factor, minHalf, maxHalf);

        // ScottPlot 5：IXAxis 没有 SetLimits，用 Axes.SetLimitsX
        plot.Axes.SetLimitsX(center - half, center + half);
        PlotView.Refresh();
        e.Handled = true;
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
            if (_selectedDay >= 0 && d != _selectedDay) continue;   // 单日模式
            var row = data.LoadArray[d];
            if (row.Count < nPts) continue;
            double[] ys = row.Take(nPts).ToArray();
            var sc = plot.Add.Scatter(xs, ys);
            sc.LegendText = d < data.DateLabels.Count ? data.DateLabels[d] : $"第 {d + 1} 天";
            sc.LineWidth = _selectedDay >= 0 ? 2.2f : 1.4f;   // 单日时线加粗，看得清拐点
            sc.MarkerSize = 0;
            sc.Color = CurveColors.ForDay(d);
        }

        // 刻度：最多 8 个。12 个时 96 点的标签会挤成一团（旋转 45° 也不够）。
        int step = Math.Max(1, nPts / 8);
        var ticks = new List<Tick>();
        for (int i = 0; i < nPts; i += step)
        {
            // 只保留小时边界（00:00 / 02:00 …）：非整点标签更长，挤在一起更难认。
            string label = data.TimeLabels[i];
            if (!label.EndsWith(":00")) continue;
            ticks.Add(new Tick(i, label));
        }
        if (ticks.Count == 0)
        {
            for (int i = 0; i < nPts; i += step)
                ticks.Add(new Tick(i, data.TimeLabels[i]));
        }
        plot.Axes.Bottom.TickGenerator = new ScottPlot.TickGenerators.NumericManual(ticks.ToArray());
        plot.Axes.Bottom.TickLabelStyle.Rotation = 45;
        plot.Axes.Bottom.TickLabelStyle.FontSize = 9;

        // ⚠️ 必须显式 AutoScale：ScottPlot 5 的 Add.Scatter **不会**自动调整轴范围。
        // 不调这一行的症状：图例正常、Figure 正常，但轴范围停在默认 -10~10，
        // 数据线全在视野之外 —— 看起来像「没数据」，实际是没缩放。
        plot.Axes.AutoScale();

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
        else if (e.Key == Windows.System.VirtualKey.Escape) { ClearCompare(); e.Handled = true; }
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
