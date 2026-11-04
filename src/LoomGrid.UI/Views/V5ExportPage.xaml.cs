using System.Collections.ObjectModel;
using System.ComponentModel;
using System.IO;
using System.Runtime.CompilerServices;
using LoomGrid.Bridge;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.Storage.Pickers;

namespace LoomGrid.UI;

/// <summary>V5 清单里的一行：一个可导出的分组。</summary>
public sealed class ExportItem : INotifyPropertyChanged
{
    public required string SessionId { get; init; }
    public required int GroupIndex { get; init; }
    public required string SourceFile { get; init; }
    public required string Asset { get; init; }
    public required string Month { get; init; }
    public required int TimePoints { get; init; }

    private bool _isChecked = true;
    private string _multiplierText = "";

    public bool IsChecked
    {
        get => _isChecked;
        set { _isChecked = value; Raise(); }
    }

    /// <summary>空字符串 = 用全局倍率；填了数字 = 这一组的覆盖值。</summary>
    public string MultiplierText
    {
        get => _multiplierText;
        set { _multiplierText = value; Raise(); Raise(nameof(HasOverride)); }
    }

    public bool HasOverride => ParsedMultiplier is not null;

    public double? ParsedMultiplier =>
        double.TryParse(_multiplierText, out var v) && v > 0 ? v : null;

    public string TimePointsText => TimePoints.ToString();

    public event PropertyChangedEventHandler? PropertyChanged;
    private void Raise([CallerMemberName] string? n = null)
        => PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(n));
}

/// <summary>
/// V5 导出：按 session 分组列可导出项，逐行可改倍率（group_overrides），
/// 一次 /run 只处理一个 session，所以多文件时按 session 循环调用。
/// </summary>
public sealed partial class V5ExportPage : Page
{
    private const string DefaultTemplate = "{table_no}_{year_month}_负荷曲线";

    private readonly ObservableCollection<ExportItem> _items = new();
    private readonly List<string> _taskIds = new();
    private string _outputDir = "";
    private bool _loaded;

    public V5ExportPage()
    {
        InitializeComponent();
        Loaded += OnLoaded;

        TitleBox.Text = DefaultTemplate;
        FileBox.Text = DefaultTemplate;

        HighlightCombo.ItemsSource = new List<string> { "none", "weekend", "holiday", "grouped" };
        HighlightCombo.SelectedIndex = 0;

        _outputDir = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "LoomGrid导出");
        OutputDirText.Text = _outputDir;
    }

    private async void OnLoaded(object sender, RoutedEventArgs e)
    {
        if (_loaded) return;
        _loaded = true;
        AppLog.Write("V5 导出页已加载");

        BuildItems();
        ItemList.ItemsSource = _items;
        UpdateCounts();

        if (_items.Count > 0)
            await RefreshPreviewAsync();
    }

    private void BuildItems()
    {
        _items.Clear();
        foreach (var file in Workspace.Current.Files)
        {
            if (file.SessionId is null) continue;
            for (int i = 0; i < file.Groups.Count; i++)
            {
                var g = file.Groups[i];
                _items.Add(new ExportItem
                {
                    SessionId = file.SessionId,
                    GroupIndex = i,
                    SourceFile = file.FileName,
                    Asset = string.IsNullOrWhiteSpace(g.Asset) ? "未命名" : g.Asset,
                    Month = string.IsNullOrWhiteSpace(g.Month) ? "—" : g.Month,
                    TimePoints = g.TimePoints,
                });
            }
        }
        AppLog.Write($"V5：{_items.Count} 个可导出分组，来自 {_items.Select(i => i.SessionId).Distinct().Count()} 个 session");
    }

    // ── 选择 ───────────────────────────────────────────────

    private void OnSelectAll(object sender, RoutedEventArgs e) => SetAll(true);
    private void OnSelectNone(object sender, RoutedEventArgs e) => SetAll(false);

    private void OnInvert(object sender, RoutedEventArgs e)
    {
        foreach (var it in _items) it.IsChecked = !it.IsChecked;
        UpdateCounts();
    }

    private void SetAll(bool value)
    {
        foreach (var it in _items) it.IsChecked = value;
        UpdateCounts();
    }

    private void OnItemCheckedChanged(object sender, RoutedEventArgs e) => UpdateCounts();

    private void UpdateCounts()
    {
        int chosen = _items.Count(i => i.IsChecked);
        CountText.Text = $"{chosen} / {_items.Count}";
        int withOverride = _items.Count(i => i.IsChecked && i.HasOverride);
        SubtitleText.Text = withOverride > 0
            ? $"已选 {chosen} 个分组，其中 {withOverride} 个有自己的倍率"
            : "选要导出的分组，设好输出目录与命名模板";
        RunButton.IsEnabled = chosen > 0 && !string.IsNullOrWhiteSpace(_outputDir);
    }

    // ── 目录与模板 ─────────────────────────────────────────

    private async void OnPickOutputDir(object sender, RoutedEventArgs e)
    {
        var picker = new FolderPicker();
        if (App.MainWindowRef is { } window)
        {
            nint hwnd = WinRT.Interop.WindowNative.GetWindowHandle(window);
            WinRT.Interop.InitializeWithWindow.Initialize(picker, hwnd);
        }
        picker.FileTypeFilter.Add("*");
        picker.SuggestedStartLocation = PickerLocationId.DocumentsLibrary;

        var folder = await picker.PickSingleFolderAsync();
        if (folder is null) return;

        _outputDir = folder.Path;
        OutputDirText.Text = _outputDir;
        UpdateCounts();
        AppLog.Write($"V5：输出目录 = {_outputDir}");
    }

    private async void OnOpenOutputDir(object sender, RoutedEventArgs e)
    {
        try
        {
            Directory.CreateDirectory(_outputDir);
            await Windows.System.Launcher.LaunchFolderPathAsync(_outputDir);
        }
        catch (Exception ex)
        {
            StatusText.Text = $"打不开目录：{ex.Message}";
        }
    }

    private async void OnRefreshPreview(object sender, RoutedEventArgs e) => await RefreshPreviewAsync();

    private async Task RefreshPreviewAsync()
    {
        try
        {
            var services = await Boot.EnsureAsync();
            var api = new ExportApi(services.Api);

            var first = _items.FirstOrDefault(i => i.IsChecked);
            if (first is null)
            {
                PreviewText.Text = "（未选分组）";
                return;
            }

            var result = await api.PreviewAsync(
                first.SessionId,
                _items.Where(i => i.IsChecked && i.SessionId == first.SessionId)
                      .Select(i => i.GroupIndex).ToList(),
                TitleBox.Text, FileBox.Text);

            PreviewText.Text = result.Items.Count > 0
                ? $"示例：{result.Items[0].Filename}"
                : "（没有可预览的项）";
            AppLog.Write($"V5：预览 {result.Items.Count} 条，示例 {result.Items.FirstOrDefault()?.Filename}");
        }
        catch (Exception ex)
        {
            PreviewText.Text = $"预览失败：{ex.Message}";
            AppLog.Write($"V5：预览失败 {ex.GetType().Name} {ex.Message}");
        }
    }

    // ── 导出 ───────────────────────────────────────────────

    private async void OnRunClick(object sender, RoutedEventArgs e)
    {
        var chosen = _items.Where(i => i.IsChecked).ToList();
        if (chosen.Count == 0)
        {
            await ShowMessageAsync("没有选分组", "左侧至少勾一个分组再导出。");
            return;
        }

        try
        {
            Directory.CreateDirectory(_outputDir);
        }
        catch (Exception ex)
        {
            await ShowMessageAsync("输出目录不可用", $"{_outputDir}\n{ex.Message}");
            return;
        }

        RunButton.IsEnabled = false;
        ResultButton.IsEnabled = false;
        Progress.Visibility = Visibility.Visible;
        Progress.Value = 0;
        _taskIds.Clear();
        Workspace.Current.LastRuns.Clear();

        var services = await Boot.EnsureAsync();
        var api = new ExportApi(services.Api);

        double globalMult = double.TryParse(GlobalMultiplierBox.Text, out var gm) && gm > 0 ? gm : 1.0;
        string highlight = HighlightCombo.SelectedItem as string ?? "none";
        var options = new Dictionary<string, object?>
        {
            ["highlight_mode"] = highlight,
            ["dark_theme"] = DarkThemeCheck.IsChecked == true,
        };

        var bySession = chosen.GroupBy(i => i.SessionId).ToList();
        int doneSessions = 0, okGroups = 0, failGroups = 0;
        var failures = new List<string>();

        foreach (var group in bySession)
        {
            var overrides = group
                .Where(i => i.ParsedMultiplier is not null)
                .Select(i => new GroupOverride { Index = i.GroupIndex, Multiplier = i.ParsedMultiplier })
                .ToList();

            var req = new ExportRunRequest
            {
                SessionId = group.Key,
                OutputDir = _outputDir,
                Groups = group.Select(i => i.GroupIndex).ToList(),
                TitleTemplate = TitleBox.Text,
                FilenameTemplate = FileBox.Text,
                Multiplier = globalMult,
                PlotOptions = options,
                ExportMidwide = MidwideCheck.IsChecked == true,
                GroupOverrides = overrides.Count > 0 ? overrides : null,
            };

            StatusText.Text = $"正在提交：{group.First().SourceFile}（{group.Count()} 个分组）…";
            AppLog.Write($"V5：提交 {group.First().SourceFile} groups={string.Join(',', req.Groups)} overrides={overrides.Count} mult={globalMult}");

            try
            {
                var run = await api.RunAsync(req);
                if (run.TaskId is null) throw new ApiException("没有返回 task_id");
                _taskIds.Add(run.TaskId);

                var status = await WaitForAsync(api, run.TaskId);
                if (status.Status == "done")
                {
                    okGroups += status.Succeeded;
                    failGroups += status.Result.ValueKind == System.Text.Json.JsonValueKind.Object
                        && status.Result.TryGetProperty("failed", out var f)
                        && f.ValueKind == System.Text.Json.JsonValueKind.Array
                        ? f.GetArrayLength() : 0;

                    // 给 V6 的汇总：分组口径用 groups_succeeded，产物清单用 artifacts
                    var runSummary = new ExportRunSummary
                    {
                        TaskId = run.TaskId ?? "",
                        SourceFile = group.First().SourceFile,
                        OutputDir = _outputDir,
                        TotalGroups = group.Count(),
                        GroupsSucceeded = status.GroupsSucceeded,
                        GroupsFailed = status.GroupsFailed,
                        ReportName = status.ReportName,
                    };
                    foreach (var (name, kind) in status.Artifacts)
                        runSummary.Artifacts.Add(ExportArtifact.From(_outputDir, name, kind));
                    runSummary.FailedMessages.AddRange(status.FailedMessages);
                    Workspace.Current.LastRuns.Add(runSummary);
                }
                else
                {
                    failGroups += group.Count();
                    failures.Add($"{group.First().SourceFile}：{status.Error ?? status.Status}");
                }
            }
            catch (Exception ex)
            {
                failGroups += group.Count();
                failures.Add($"{group.First().SourceFile}：{ex.Message}");
                AppLog.Write($"V5：{group.First().SourceFile} 导出失败 {ex}");
            }

            doneSessions++;
            Progress.Value = doneSessions * 100.0 / bySession.Count;
            StatusText.Text = $"已完成 {doneSessions}/{bySession.Count} 个文件 · 成功 {okGroups} · 失败 {failGroups}";
        }

        Progress.Value = 100;
        ResultButton.IsEnabled = okGroups > 0;
        RunButton.IsEnabled = true;

        string summary = $"导出结束：成功 {okGroups} 个分组，失败 {failGroups} 个。\n输出目录：{_outputDir}";
        if (failures.Count > 0) summary += "\n\n失败明细：\n" + string.Join("\n", failures);
        StatusText.Text = summary.Split('\n')[0];
        AppLog.Write($"V5：{summary.Replace('\n', ' ')}");

        await ShowMessageAsync("导出完成", summary);
    }

    /// <summary>轮询到 done/error；超时返回最后状态。</summary>
    private static async Task<ExportStatusResult> WaitForAsync(
        ExportApi api, string taskId, int timeoutSeconds = 180)
    {
        var deadline = DateTime.UtcNow.AddSeconds(timeoutSeconds);
        ExportStatusResult last = new();
        while (DateTime.UtcNow < deadline)
        {
            last = await api.StatusAsync(taskId);
            if (last.IsDone) return last;
            await Task.Delay(350);
        }
        return last;
    }

    // ── 其它 ───────────────────────────────────────────────

    private void OnBackClick(object sender, RoutedEventArgs e)
    {
        if (Frame.CanGoBack) Frame.GoBack();
        else Frame.Navigate(typeof(V2MappingPage));
    }

    private void OnResultClick(object sender, RoutedEventArgs e)
    {
        if (Workspace.Current.LastRuns.Count == 0) return;
        AppLog.Write($"V5：进入 V6，{Workspace.Current.LastRuns.Count} 个任务结果");
        Frame.Navigate(typeof(V6ResultPage));
    }

    private void OnCapacityClick(object sender, RoutedEventArgs e)
    {
        AppLog.Write("V5：进入 V4 容量测算");
        Frame.Navigate(typeof(V4CapacityPage));
    }

    private async Task ShowMessageAsync(string title, string text)
    {
        var dialog = new ContentDialog
        {
            Title = title,
            Content = new TextBlock { Text = text, TextWrapping = TextWrapping.Wrap },
            CloseButtonText = "好",
            XamlRoot = XamlRoot,
        };
        await dialog.ShowAsync();
    }
}
