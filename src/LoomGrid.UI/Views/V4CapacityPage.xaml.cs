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
