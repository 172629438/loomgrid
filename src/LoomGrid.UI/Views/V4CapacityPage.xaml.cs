using LoomGrid.Bridge;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace LoomGrid.UI;

/// <summary>分组下拉项：把 (session, group, 资产, 月份) 打包成一个可显示对象。</summary>
public sealed class V4GroupItem
{
    public required string SessionId { get; init; }
    public required int GroupIndex { get; init; }
    public required string Asset { get; init; }
    public required string Month { get; init; }
    public required int TimePoints { get; init; }
    public string Display => $"{Asset} · {Month} · {TimePoints} 点";
}

/// <summary>
/// V4 容量测算。参数区的语义说明直接写在控件旁边（不进 tooltip）——
/// 口径标错时数字看着合理但全是错的，且不会报错。
/// </summary>
public sealed partial class V4CapacityPage : Page
{
    private readonly List<V4GroupItem> _groups = new();
    private DispatcherTimer? _debounce;
    private bool _loaded;
    private bool _suppressAuto;

    public V4CapacityPage()
    {
        InitializeComponent();
        Loaded += OnLoaded;
    }

    private void OnLoaded(object sender, RoutedEventArgs e)
    {
        if (_loaded) return;
        _loaded = true;
        AppLog.Write("V4 容量测算页已加载");

        BuildGroups();
        if (_groups.Count == 0)
        {
            SubtitleText.Text = "没有可测算的分组——请先在映射页完成至少一个文件";
            CalcButton.IsEnabled = false;
            return;
        }

        _suppressAuto = true;
        GroupCombo.ItemsSource = _groups;
        GroupCombo.DisplayMemberPath = nameof(V4GroupItem.Display);
        GroupCombo.SelectedIndex = 0;

        ModeCombo.ItemsSource = new List<string>
        {
            "A · 需量模式（吃容量线）",
            "B · 功率模式（吃负负荷）",
            "C · 功率模式（吃负负荷）",
        };
        ModeCombo.SelectedIndex = 0;

        BasisCombo.ItemsSource = new List<string>
        {
            "min_feasible · 最小可行容量",
            "full_load · 满载利用率",
        };
        BasisCombo.SelectedIndex = 0;
        _suppressAuto = false;

        UpdateModeHint();
        UpdateBasisHint();
        _ = CalcAsync();
    }

    private void BuildGroups()
    {
        _groups.Clear();
        foreach (var f in Workspace.Current.Files)
        {
            if (f.SessionId is null) continue;
            for (int i = 0; i < f.Groups.Count; i++)
            {
                var g = f.Groups[i];
                _groups.Add(new V4GroupItem
                {
                    SessionId = f.SessionId,
                    GroupIndex = i,
                    Asset = string.IsNullOrWhiteSpace(g.Asset) ? "未命名" : g.Asset,
                    Month = string.IsNullOrWhiteSpace(g.Month) ? "—" : g.Month,
                    TimePoints = g.TimePoints,
                });
            }
        }

        // 混合粒度提醒：seg_dt 按每组自己的采样点算，混算会让表里的数字不可比
        var distinct = _groups.Select(g => g.TimePoints).Distinct().ToList();
        if (distinct.Count > 1)
            AppLog.Write($"V4：警告 —— 分组粒度不一致 {string.Join("/", distinct)}，跨组对比不可直接比较");
    }

    // ── 口径说明（进界面，不进 tooltip）──────────────────────

    private string ModeKey => (ModeCombo.SelectedIndex) switch
    {
        1 => "B",
        2 => "C",
        _ => "A",
    };

    private string BasisKey => BasisCombo.SelectedIndex == 1 ? "full_load" : "min_feasible";

    private void UpdateModeHint()
    {
        ModeHint.Text = ModeKey == "A"
            ? "充电 = Σ max(容量线 − 负荷, 0) × dt。正负荷也能充，与负值无关；缺容量线则充电空间为 0。"
            : "充电 = Σ min(额定功率, max(−负荷, 0)) × dt × η。只吃负负荷；缺额定功率则充电空间为 0。";
    }

    private void UpdateBasisHint()
    {
        BasisHint.Text = BasisKey == "min_feasible"
            ? "取所有天里最紧的那一天（要满足全部日）——保守，适合按最差日定容量。"
            : "取各天充放空间的较大利用率——激进，适合按整体能力定容量。";
    }

    // ── 参数变化 → 防抖重算 ────────────────────────────────

    private void OnParamChanged(object sender, RoutedEventArgs e)
    {
        if (_suppressAuto || !_loaded) return;
        if (sender == ModeCombo) UpdateModeHint();
        if (sender == BasisCombo) UpdateBasisHint();
        ScheduleCalc();
    }

    private void ScheduleCalc()
    {
        _debounce ??= new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(350) };
        _debounce.Tick -= OnDebounceTick;
        _debounce.Tick += OnDebounceTick;
        _debounce.Stop();
        _debounce.Start();
    }

    private async void OnDebounceTick(object? sender, object e)
    {
        _debounce?.Stop();
        await CalcAsync();
    }

    // ── 测算 ───────────────────────────────────────────────

    private async void OnCalcClick(object sender, RoutedEventArgs e) => await CalcAsync();

    /// <summary>
    /// 入口 B：容量 → 天数。
    /// 与入口 A（target_days → 容量）完全独立：自己的输入框、自己的结果行。
    /// 默认值为空，不预填——两个入口都有数时用户不知道该看哪个。
    /// </summary>
    private async void OnSatisfiedDaysClick(object sender, RoutedEventArgs e)
    {
        if (GroupCombo.SelectedItem is not V4GroupItem g) return;

        if (!TryNum(InstalledCapacityBox.Text, out var cap) || cap <= 0)
        {
            SatisfiedDaysText.Text = "请输入安装容量（kWh，> 0）";
            return;
        }

        var segs = new List<CalcSegment>();
        if (TryNum(ChargeStartBox.Text, out var cs) && TryNum(ChargeEndBox.Text, out var ce) && ce > cs)
            segs.Add(new CalcSegment { Start = cs, End = ce, Type = "charge", Label = "充电段" });
        if (TryNum(DischargeStartBox.Text, out var ds) && TryNum(DischargeEndBox.Text, out var de) && de > ds)
            segs.Add(new CalcSegment { Start = ds, End = de, Type = "discharge", Label = "放电段" });
        if (segs.Count == 0)
        {
            SatisfiedDaysText.Text = "至少需要一个有效时段（起 < 止）";
            return;
        }

        double? rated = TryNum(RatedPowerBox.Text, out var rp) && rp > 0 ? rp : null;
        CalcBilling? billing = TryNum(CapacityLineBox.Text, out var cl) && cl > 0
            ? new CalcBilling { Value = cl }
            : null;
        double dod = TryNum(DodBox.Text, out var d) && d > 0 ? d : 95.0;

        try
        {
            SatisfiedDaysText.Text = "计算中…";
            var services = await Boot.EnsureAsync();
            var api = new CalcApi(services.Api);
            var r = await api.SatisfiedDaysAsync(new SatisfiedDaysRequest
            {
                SessionId = g.SessionId,
                Group = g.GroupIndex,
                Segments = segs,
                Billing = billing,
                Mode = ModeKey,
                RatedPower = rated,
                IgnoreNegative = IgnoreNegativeCheck.IsChecked == true,
                CapacityKwh = cap,
                DodPercent = dod,
            });

            // 只显示 satisfied_days（DOD 折算版，与 Fusion 界面同口径）。
            // satisfied_days_core 是 core 的另一个算法，两个数并排会让用户不知道信哪个。
            SatisfiedDaysText.Text =
                $"满足 {r.SatisfiedDays} / {r.TotalDays} 天" +
                $" · 等效 {r.EquivalentDays} 天/年" +
                $" · 可用 {r.UsableKwh:N1} kWh（DOD {r.DodPercent:0.#}%）";
            AppLog.Write($"V4 入口B：容量 {cap:N1} kWh → 满足 {r.SatisfiedDays}/{r.TotalDays} 天，" +
                         $"等效 {r.EquivalentDays}，可用 {r.UsableKwh:N1}");
        }
        catch (ApiException ex)
        {
            SatisfiedDaysText.Text = $"失败：{ex.Message}" + (ex.Code is null ? "" : $"（{ex.Code}）");
            AppLog.Write($"V4 入口B 失败：{ex.Code} {ex.Message}");
        }
        catch (Exception ex)
        {
            SatisfiedDaysText.Text = $"出错：{ex.GetType().Name}";
            AppLog.Write($"V4 入口B 异常：{ex}");
        }
    }

    private async Task CalcAsync()
    {
        if (GroupCombo.SelectedItem is not V4GroupItem g) return;

        var segs = new List<CalcSegment>();
        if (TryNum(ChargeStartBox.Text, out var cs) && TryNum(ChargeEndBox.Text, out var ce) && ce > cs)
            segs.Add(new CalcSegment { Start = cs, End = ce, Type = "charge", Label = "充电段" });
        if (TryNum(DischargeStartBox.Text, out var ds) && TryNum(DischargeEndBox.Text, out var de) && de > ds)
            segs.Add(new CalcSegment { Start = ds, End = de, Type = "discharge", Label = "放电段" });

        if (segs.Count == 0)
        {
            StatusText.Text = "至少需要一个有效时段（起 < 止）";
            return;
        }

        double? rated = TryNum(RatedPowerBox.Text, out var rp) && rp > 0 ? rp : null;
        CalcBilling? billing = TryNum(CapacityLineBox.Text, out var cl) && cl > 0
            ? new CalcBilling { Value = cl }
            : null;
        double dod = TryNum(DodBox.Text, out var d) && d > 0 ? d : 95.0;
        int? targetDays = TryNum(TargetDaysBox.Text, out var td) && td > 0 ? (int)td : null;

        var req = new CapacityRequest
        {
            SessionId = g.SessionId,
            Group = g.GroupIndex,
            Segments = segs,
            Billing = billing,
            Mode = ModeKey,
            RatedPower = rated,
            IgnoreNegative = IgnoreNegativeCheck.IsChecked == true,
            Basis = BasisKey,
            TargetDays = targetDays,
            DodPercent = dod,
        };

        try
        {
            var services = await Boot.EnsureAsync();
            var api = new CalcApi(services.Api);
            var r = await api.CapacityAsync(req);
            Render(r, segs);

            // 每日表另拉一次 /spaces：它返回逐日数组与真实日期标签
            try
            {
                var sp = await api.SpacesAsync(req);
                RenderDaily(sp);
            }
            catch (ApiException ex)
            {
                AppLog.Write($"V4：每日表获取失败 {ex.Code} {ex.Message}");
                DailySubText.Text = $"每日明细不可用：{ex.Message}";
            }

            // 月度表：跨所有已加载 session 按月份聚合（同月份后者覆盖前者）
            try
            {
                var sids = Workspace.Current.Files
                    .Where(f => f.SessionId is not null)
                    .Select(f => f.SessionId!)
                    .Distinct()
                    .ToList();
                if (sids.Count > 0)
                {
                    var mr = await api.MonthlySpacesAsync(new MonthlySpacesRequest
                    {
                        SessionIds = sids,
                        Segments = segs,
                        Billing = billing,
                        Mode = ModeKey,
                        RatedPower = rated,
                        IgnoreNegative = IgnoreNegativeCheck.IsChecked == true,
                    });
                    RenderMonthly(mr);
                }
            }
            catch (ApiException ex)
            {
                AppLog.Write($"V4：月度表获取失败 {ex.Code} {ex.Message}");
                MonthlySubText.Text = $"月度表不可用：{ex.Message}";
            }
        }
        catch (ApiException ex)
        {
            StatusText.Text = $"测算失败：{ex.Message}" + (ex.Code is null ? "" : $"（{ex.Code}）");
            AppLog.Write($"V4：测算失败 {ex.Code} {ex.Message}");
        }
        catch (Exception ex)
        {
            StatusText.Text = $"测算出错：{ex.GetType().Name}";
            AppLog.Write($"V4：测算异常 {ex}");
        }
    }

    private void Render(CapacityResult r, List<CalcSegment> segs)
    {
        DemandText.Text = r.DailyDemand.ValueKwh.ToString("N1");
        DemandSubText.Text = $"kWh · {BasisLabel(r.DailyDemand.Basis)}";

        ThroughputText.Text = r.DailyThroughput.ValueKwhPerDay.ToString("N1");
        ThroughputSubText.Text = $"kWh/天 · {r.DailyThroughput.Days} 天 · 瓶颈={SideLabel(r.DailyThroughput.MinSide)}";

        InstalledText.Text = r.CapacitySuggestionInstalledKwh is { } inst ? inst.ToString("N1") : "—";

        var rows = r.DailyDemand.BySegment
            .Select(s => $"{s.Label}   充 {s.ChargeKwh:N1} / 放 {s.DischargeKwh:N1} kWh")
            .ToList();
        SegmentList.ItemsSource = rows;

        HintsText.Text = r.Hints.Count > 0
            ? string.Join("\n", r.Hints)
            : "";

        StatusText.Text =
            $"{segs.Count} 个时段 · mode {ModeKey} · {BasisKey} · " +
            $"容量线 {(r.DailyDemand.ValueKwh > 0 ? "已用" : "为 0")}";

        AppLog.Write($"V4：{ModeKey}/{BasisKey} → 日需求 {r.DailyDemand.ValueKwh:N1} kWh，" +
                     $"日吞吐 {r.DailyThroughput.ValueKwhPerDay:N1} kWh/天，" +
                     $"建议装机 {r.CapacitySuggestionInstalledKwh?.ToString("N1") ?? "—"} kWh");
    }

    // ── 每日明细 ───────────────────────────────────────────

    private void RenderMonthly(MonthlySpacesResult r)
    {
        MonthlyGrid.Children.Clear();
        MonthlyGrid.ColumnDefinitions.Clear();
        MonthlyGrid.RowDefinitions.Clear();

        if (r.Months.Count == 0)
        {
            MonthlySubText.Text = "没有月度数据（先完成列映射）";
            return;
        }

        int nSeg = r.Columns.Count;
        int totalCols = 1 + nSeg + 1;      // 月份 + 各段 + 最小段空间

        for (int c = 0; c < totalCols; c++)
        {
            bool fixedWidth = c == 0 || c == totalCols - 1;
            MonthlyGrid.ColumnDefinitions.Add(new ColumnDefinition
            {
                Width = fixedWidth
                    ? new GridLength(c == 0 ? 210 : 130)
                    : new GridLength(1, GridUnitType.Star),
            });
        }
        MonthlyGrid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        for (int i = 0; i < r.Months.Count; i++)
            MonthlyGrid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });

        AddMonthlyCell("月份", 0, 0, mono: false, header: true);
        for (int i = 0; i < nSeg; i++)
            AddMonthlyCell(r.Columns[i], 1 + i, 0, mono: false, header: true);
        AddMonthlyCell(r.MinSegmentLabel, totalCols - 1, 0, mono: true, header: true);

        var monoFont = (Microsoft.UI.Xaml.Media.FontFamily)Application.Current.Resources["MonoFamily"];
        var secondary = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["TextFillColorSecondaryBrush"];

        for (int m = 0; m < r.Months.Count; m++)
        {
            var mo = r.Months[m];
            int row = m + 1;

            // 月份列：主行是月份，副标说明点数和「同月多源」——
            // 措辞写「取后加载的那个」而不是「合并」，后者会让用户以为数据相加。
            var sp = new StackPanel { Spacing = 0, Padding = new Thickness(4, 3, 4, 3) };
            sp.Children.Add(new TextBlock
            {
                Text = mo.Month,
                FontSize = 12.5,
                FontFamily = monoFont,
            });
            sp.Children.Add(new TextBlock
            {
                Text = mo.SourceCount > 1
                    ? $"{mo.SourceCount} 个文件同月，本行取后加载的那个"
                    : $"{mo.Resolution} 点",
                FontSize = 10.5,
                Foreground = secondary,
                TextWrapping = TextWrapping.Wrap,
            });
            Grid.SetColumn(sp, 0);
            Grid.SetRow(sp, row);
            MonthlyGrid.Children.Add(sp);

            for (int i = 0; i < nSeg; i++)
            {
                string text = i < mo.Segments.Count ? mo.Segments[i].Value.ToString("N1") : "—";
                AddMonthlyCell(text, 1 + i, row, mono: true, header: false);
            }
            AddMonthlyCell(mo.MinSegment.ToString("N1"), totalCols - 1, row, mono: true, header: false);
        }

        MonthlySubText.Text = r.HiddenZeroSegments > 0
            ? $"最小段空间 = 本月各段中最小的那一段（充+放）；已隐藏 {r.HiddenZeroSegments} 个全零段列"
            : "最小段空间 = 本月各段中最小的那一段（充+放）";
    }

    private void AddMonthlyCell(string text, int column, int row, bool mono, bool header)
    {
        var tb = new TextBlock
        {
            Text = text,
            FontSize = 12,
            Padding = new Thickness(4, 3, 4, 3),
            TextAlignment = mono ? TextAlignment.Right : TextAlignment.Left,
            VerticalAlignment = VerticalAlignment.Center,
        };
        if (mono)
        {
            tb.FontFamily = (Microsoft.UI.Xaml.Media.FontFamily)
                Application.Current.Resources["MonoFamily"];
        }
        if (header || !mono)
        {
            tb.Foreground = (Microsoft.UI.Xaml.Media.Brush)
                Application.Current.Resources["TextFillColorSecondaryBrush"];
        }
        if (header)
        {
            tb.FontWeight = Microsoft.UI.Text.FontWeights.SemiBold;
        }

        Grid.SetColumn(tb, column);
        Grid.SetRow(tb, row);
        MonthlyGrid.Children.Add(tb);
    }

    private void RenderDaily(SpacesResult sp)
    {
        DailyGrid.Children.Clear();
        DailyGrid.ColumnDefinitions.Clear();
        DailyGrid.RowDefinitions.Clear();

        if (sp.DateLabels.Count == 0)
        {
            DailySubText.Text = "没有逐日数据";
            return;
        }

        for (int c = 0; c < 4; c++)
        {
            DailyGrid.ColumnDefinitions.Add(new ColumnDefinition
            {
                Width = c == 0 ? new GridLength(112) : new GridLength(1, GridUnitType.Star),
            });
        }
        for (int r = 0; r < sp.DateLabels.Count; r++)
            DailyGrid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });

        for (int d = 0; d < sp.DateLabels.Count; d++)
        {
            double charge = 0, discharge = 0;
            double minSeg = double.MaxValue;
            foreach (var seg in sp.Segments)
            {
                double cv = d < seg.Charge.Count ? seg.Charge[d] : 0;
                double dv = d < seg.Discharge.Count ? seg.Discharge[d] : 0;
                charge += cv;
                discharge += dv;
                // 与月度表同口径：min(各段的 充+放)
                minSeg = Math.Min(minSeg, cv + dv);
            }
            if (minSeg == double.MaxValue) minSeg = 0;

            AddDailyCell(sp.DateLabels[d], 0, d, false);
            AddDailyCell(charge.ToString("N1"), 1, d, true);
            AddDailyCell(discharge.ToString("N1"), 2, d, true);
            AddDailyCell(minSeg.ToString("N1"), 3, d, true);
        }

        // 判据：日期列必须是真实日期（2026-01-01 这种），不是数组索引
        string first = sp.DateLabels.FirstOrDefault() ?? "";
        DailySubText.Text = $"{sp.DateLabels.Count} 天 · 首行 {first} · 最小段口径同月度表（min 各段充+放）";
        AppLog.Write($"V4：每日表 {sp.DateLabels.Count} 行，首行日期 = {first}");
    }

    private void AddDailyCell(string text, int column, int row, bool mono)
    {
        var tb = new TextBlock
        {
            Text = text,
            FontSize = 12,
            Padding = new Thickness(4, 3, 4, 3),
            TextAlignment = mono ? TextAlignment.Right : TextAlignment.Left,
            VerticalAlignment = VerticalAlignment.Center,
        };
        // FontFamily 不能赋 null（WinUI 会抛异常）——非数值列不设，保留默认字体
        if (mono)
        {
            tb.FontFamily = (Microsoft.UI.Xaml.Media.FontFamily)
                Application.Current.Resources["MonoFamily"];
        }
        else
        {
            tb.Foreground = (Microsoft.UI.Xaml.Media.Brush)
                Application.Current.Resources["TextFillColorSecondaryBrush"];
        }

        Grid.SetColumn(tb, column);
        Grid.SetRow(tb, row);
        DailyGrid.Children.Add(tb);
    }

    private static string BasisLabel(string basis) => basis switch
    {
        "full_load" => "满载利用率",
        "min_feasible" => "最小可行容量",
        _ => basis,
    };

    private static string SideLabel(string side) => side switch
    {
        "charge" => "充电侧",
        "discharge" => "放电侧",
        "balanced" => "两侧均衡",
        _ => "无",
    };

    private static bool TryNum(string? text, out double value)
    {
        value = 0;
        if (string.IsNullOrWhiteSpace(text)) return false;
        return double.TryParse(text.Trim(), out value);
    }

    private void OnBackClick(object sender, RoutedEventArgs e)
    {
        if (Frame.CanGoBack) Frame.GoBack();
        else Frame.Navigate(typeof(V5ExportPage));
    }
}
