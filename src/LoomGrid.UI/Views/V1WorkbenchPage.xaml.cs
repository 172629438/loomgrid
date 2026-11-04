using System.IO;
using LoomGrid.Bridge;
using Microsoft.UI;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Windows.ApplicationModel.DataTransfer;
using Windows.Storage;
using Windows.Storage.Pickers;
using Windows.UI;

namespace LoomGrid.UI;

/// <summary>
/// V1 工作台：拖入文件 → 识别 → 列表上显示状态 → 进 V2。
///
/// 状态判定的判据统一在 <see cref="FileStateJudge"/>，这里不写配色逻辑。
/// 拖放的每一次事件都写进启动日志，便于外部核对「到底是没触发还是被拦了」。
/// </summary>
public sealed partial class V1WorkbenchPage : Page
{
    private static readonly string[] SupportedExtensions =
        { ".xlsx", ".xlsm", ".xls", ".xlsb", ".csv" };

    private static readonly SolidColorBrush NormalBorder = new(Microsoft.UI.Colors.Transparent);
    private static SolidColorBrush HoverBorder => Res("SemanticDataBrush", Microsoft.UI.Colors.DodgerBlue);
    private static SolidColorBrush RejectBorder => Res("SemanticErrorBrush", Microsoft.UI.Colors.Red);

    /// <summary>颜色一律从令牌字典取。之前这里写死了 #4EA8DE（与数据青同值）与 #D13438，
    /// 同一个颜色两处定义，改主题时必漏。</summary>
    private static SolidColorBrush Res(string key, Windows.UI.Color fallback)
    {
        if (Application.Current?.Resources is { } res
            && res.TryGetValue(key, out var v)
            && v is SolidColorBrush brush)
            return brush;
        return new SolidColorBrush(fallback);
    }

    private readonly List<WorkFile> Files = Workspace.Current.Files;
    private readonly SemaphoreSlim _inspectGate = new(3);
    private bool _dragAccepted;
    private bool _loaded;
    private SortKey _sortKey = SortKey.FileName;
    private bool _sortAscending = true;

    private enum SortKey { FileName, Asset, TimePoints, Min, Max, State }

    public V1WorkbenchPage()
    {
        InitializeComponent();
        Loaded += OnLoaded;
    }

    // ── 启动 ────────────────────────────────────────────────

    private async void OnLoaded(object sender, RoutedEventArgs e)
    {
        if (_loaded) return;
        _loaded = true;
        AppLog.Write("V1 工作台已加载");

        // 从 V2 返回时页面会重建（Frame 默认不缓存页面），
        // 而文件列表存在 Workspace 里 —— 必须主动重建列表，
        // 否则用户回到工作台会看到「文件 0」，以为刚才的文件丢了。
        RefreshList();
        UpdateStatus();

        try
        {
            var services = await Boot.EnsureAsync();
            BackendText.Text = $"后端 {services.Host.Port} · PID {services.Host.ProcessId}";
            AppLog.Write("V1：后端就绪，可以拖放文件");
        }
        catch (Exception ex)
        {
            BackendText.Text = "后端未就绪";
            AppLog.Write($"V1：后端启动失败 {ex.GetType().Name} {ex.Message}");
            NoticeText.Text = "后端没起来，拖文件也无法识别";
        }
    }

    // ── 拖放 ────────────────────────────────────────────────

    private void OnDragEnter(object sender, DragEventArgs e)
    {
        bool ok = e.DataView.Contains(StandardDataFormats.StorageItems);
        AppLog.Write($"拖放：DragEnter StorageItems={ok}");
        ApplyDropVisual(ok);
    }

    private void OnDragOver(object sender, DragEventArgs e)
    {
        bool ok = e.DataView.Contains(StandardDataFormats.StorageItems);
        e.AcceptedOperation = ok ? DataPackageOperation.Copy : DataPackageOperation.None;
        if (ok)
        {
            e.DragUIOverride.Caption = "松开以添加";
            e.DragUIOverride.IsCaptionVisible = true;
        }

        // DragOver 高频触发，只在状态翻转时记一条
        if (_dragAccepted != ok)
        {
            _dragAccepted = ok;
            ApplyDropVisual(ok);
            AppLog.Write($"拖放：DragOver accepted={ok}");
        }
    }

    private void OnDragLeave(object sender, DragEventArgs e)
    {
        _dragAccepted = false;
        ApplyDropVisual(false);
        AppLog.Write("拖放：DragLeave");
    }

    private async void OnDrop(object sender, DragEventArgs e)
    {
        AppLog.Write("拖放：Drop 事件触发");
        _dragAccepted = false;
        ApplyDropVisual(false);

        if (!e.DataView.Contains(StandardDataFormats.StorageItems))
        {
            // 典型原因：源进程权限更高（任务管理器、提权后的资源管理器），UIPI 把数据拦掉了。
            AppLog.Write("拖放：Drop 里没有 StorageItems —— 大概率是提权进程拖来的（UIPI 拦截）");
            ShowNotice("这个文件来自权限更高的程序，系统不允许直接拖入。请先把它复制到普通文件夹，再从资源管理器拖进来。");
            return;
        }

        IReadOnlyList<IStorageItem> items;
        try
        {
            items = await e.DataView.GetStorageItemsAsync();
        }
        catch (Exception ex)
        {
            AppLog.Write($"拖放：GetStorageItemsAsync 异常 {ex.GetType().Name} {ex.Message}");
            ShowNotice("没能读到拖入的内容，请改用「添加文件」。");
            return;
        }

        var paths = new List<string>();
        int skipped = 0;
        foreach (var item in items)
        {
            if (item is StorageFile sf && IsSupported(sf.Path)) paths.Add(sf.Path);
            else skipped++;
        }

        AppLog.Write($"拖放：拿到 {items.Count} 项，可用 {paths.Count}，跳过 {skipped}");
        if (skipped > 0)
            ShowNotice($"{skipped} 个文件类型不支持（只收 xlsx / xlsm / xls / xlsb / csv）");

        if (paths.Count > 0) await AddFilesAsync(paths);
    }

    private void ApplyDropVisual(bool hover)
    {
        DropZone.BorderBrush = hover ? HoverBorder : NormalBorder;
        DropZone.BorderThickness = new Thickness(hover ? 2 : 1);
        DropHintText.Text = hover ? "松开以添加" : "把文件拖到这里";
    }

    // ── 添加文件 ────────────────────────────────────────────

    private async void OnAddClick(object sender, RoutedEventArgs e)
    {
        var picker = new FileOpenPicker();
        if (App.MainWindowRef is { } window)
        {
            nint hwnd = WinRT.Interop.WindowNative.GetWindowHandle(window);
            WinRT.Interop.InitializeWithWindow.Initialize(picker, hwnd);
        }
        foreach (var ext in SupportedExtensions) picker.FileTypeFilter.Add(ext);
        picker.SuggestedStartLocation = PickerLocationId.DocumentsLibrary;

        var picked = await picker.PickMultipleFilesAsync();
        if (picked is null || picked.Count == 0) return;

        await AddFilesAsync(picked.Select(f => f.Path).ToList());
    }

    private async Task AddFilesAsync(IReadOnlyList<string> paths)
    {
        var fresh = new List<WorkFile>();
        foreach (var path in paths)
        {
            if (Files.Any(f => string.Equals(f.FullPath, path, StringComparison.OrdinalIgnoreCase)))
                continue;
            var file = new WorkFile(path);
            Files.Add(file);
            fresh.Add(file);
        }

        if (fresh.Count == 0)
        {
            ShowNotice("这些文件已经在列表里了");
            return;
        }

        AppLog.Write($"V1：新增 {fresh.Count} 个文件");
        RefreshList();
        UpdateStatus();

        // 先登记授权：桌面程序里「用户拖入/选中」就是授权动作，
        // 但后端只认它自己白名单里的路径——不登记就会被 INGEST_PATH_FORBIDDEN 拦下。
        try
        {
            var services = await Boot.EnsureAsync();
            var api = new IngestApi(services.Api);
            var allow = await api.AllowPathsAsync(fresh.Select(f => f.FullPath).ToList());
            AppLog.Write($"授权文件：新增 {allow.Added.Count}，已存在 {allow.Existing.Count}，拒绝 {allow.Rejected.Count}");
            if (allow.Rejected.Count > 0)
            {
                // 把后端给的原因原样转达：用户需要知道是「类型不对」还是「读不了」
                foreach (var path in allow.Rejected)
                {
                    string why = allow.Reasons.TryGetValue(path, out var r) ? r : "不被允许";
                    AppLog.Write($"授权拒绝：{Path.GetFileName(path)} —— {why}");
                }
                string first = allow.Reasons.TryGetValue(allow.Rejected[0], out var f0) ? f0 : "不被允许";
                ShowNotice($"{allow.Rejected.Count} 个文件不能读取：{first}");
            }
        }
        catch (Exception ex)
        {
            AppLog.Write($"授权失败（将导致识别全部失败）：{ex.GetType().Name} {ex.Message}");
        }

        // 逐个识别；并发上限 3（后端 inspect 会读整表，开太多反而慢）
        await Task.WhenAll(fresh.Select(InspectOneAsync));

        UpdateStatus();
        AppLog.Write($"V1：识别完成，就绪 {Count(FileState.Ready)}，需确认 {Count(FileState.NeedsConfirm)}，失败 {Count(FileState.Failed)}");
    }

    private async Task InspectOneAsync(WorkFile file)
    {
        await _inspectGate.WaitAsync();
        try
        {
            file.State = FileState.Inspecting;
            var services = await Boot.EnsureAsync();
            var api = new IngestApi(services.Api);

            var result = await api.InspectAsync(file.FullPath);
            file.Inspect = result;
            file.ChosenSheet = result.Sheets.FirstOrDefault(s => s.Suggest.HasLoadCol)
                              ?? result.Sheets.FirstOrDefault();

            var (state, detail) = FileStateJudge.Judge(result);
            file.State = state;
            file.Detail = detail;

            _ = DispatcherQueue.TryEnqueue(() =>
            {
                // 时刻点数要等 mapping 之后才有真值；inspect 阶段只标出「待映射」，
                // 不能拿 format_type（wide/long）冒充点数——两回事。
                if (file.ChosenSheet is not null)
                {
                    file.Asset = "(待映射)";
                    file.TimePoints = "—";
                }
                RefreshList();
            });

            AppLog.Write($"inspect {file.FileName} → {state}；{detail.Replace('\n', ' ')}");
        }
        catch (ApiException ex)
        {
            file.State = FileState.Failed;
            file.Detail = $"{ex.Message}" + (ex.Code is null ? "" : $"（{ex.Code}）");
            AppLog.Write($"inspect {file.FileName} 失败：{ex.Code} {ex.Message}");
        }
        catch (Exception ex)
        {
            file.State = FileState.Failed;
            file.Detail = $"识别过程出错：{ex.GetType().Name} {ex.Message}";
            AppLog.Write($"inspect {file.FileName} 异常：{ex}");
        }
        finally
        {
            _inspectGate.Release();
            _ = DispatcherQueue.TryEnqueue(UpdateStatus);
        }
    }

    // ── 移除 / 清空 ─────────────────────────────────────────

    private void OnRemoveClick(object sender, RoutedEventArgs e)
    {
        var selected = FileList.SelectedItems.Cast<WorkFile>().ToList();
        if (selected.Count == 0) return;

        foreach (var file in selected) Files.Remove(file);
        AppLog.Write($"V1：移除 {selected.Count} 个文件，剩 {Files.Count}");
        FileList.SelectedItems.Clear();
        RefreshList();
        UpdateStatus();
    }

    private void OnClearClick(object sender, RoutedEventArgs e)
    {
        if (Files.Count == 0) return;
        int n = Files.Count;
        Files.Clear();
        AppLog.Write($"V1：清空 {n} 个文件");
        FileList.ItemsSource = null;
        RefreshList();
        UpdateStatus();
    }

    // ── 列表与状态 ──────────────────────────────────────────

    private void RefreshList()
    {
        bool hasFiles = Files.Count > 0;
        DropZone.Visibility = hasFiles ? Visibility.Collapsed : Visibility.Visible;
        ListArea.Visibility = hasFiles ? Visibility.Visible : Visibility.Collapsed;

        if (!hasFiles) return;

        var sorted = _sortAscending
            ? Files.OrderBy(KeyOf).ThenBy(f => f.FileName, StringComparer.OrdinalIgnoreCase)
            : Files.OrderByDescending(KeyOf).ThenBy(f => f.FileName, StringComparer.OrdinalIgnoreCase);

        FileList.ItemsSource = sorted.ToList();
    }

    private IComparable KeyOf(WorkFile f) => _sortKey switch
    {
        SortKey.Asset => f.Asset,
        SortKey.TimePoints => f.TimePoints,
        SortKey.Min => double.TryParse(f.MinLoad, out var mn) ? mn : double.MinValue,
        SortKey.Max => double.TryParse(f.MaxLoad, out var mx) ? mx : double.MinValue,
        SortKey.State => (int)f.State,
        _ => f.FileName,
    };

    private void ToggleSort(SortKey key)
    {
        if (_sortKey == key) _sortAscending = !_sortAscending;
        else { _sortKey = key; _sortAscending = true; }
        RefreshList();
    }

    private void OnSortFileName(object sender, RoutedEventArgs e) => ToggleSort(SortKey.FileName);
    private void OnSortAsset(object sender, RoutedEventArgs e) => ToggleSort(SortKey.Asset);
    private void OnSortTimePoints(object sender, RoutedEventArgs e) => ToggleSort(SortKey.TimePoints);
    private void OnSortMin(object sender, RoutedEventArgs e) => ToggleSort(SortKey.Min);
    private void OnSortMax(object sender, RoutedEventArgs e) => ToggleSort(SortKey.Max);
    private void OnSortState(object sender, RoutedEventArgs e) => ToggleSort(SortKey.State);

    private void OnSelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        RemoveButton.IsEnabled = FileList.SelectedItems.Count > 0;
    }

    private int Count(FileState state) => Files.Count(f => f.State == state);

    private void UpdateStatus()
    {
        int ready = Count(FileState.Ready);
        int warn = Count(FileState.NeedsConfirm);
        int failed = Count(FileState.Failed);
        int busy = Count(FileState.Inspecting) + Count(FileState.Pending);
        long totalBytes = Files.Sum(f => f.SizeBytes);

        string busyText = busy > 0 ? $" · 处理中 {busy}" : "";
        string sizeText = totalBytes > 0 ? $" · 总体积 {FormatSize(totalBytes)}" : "";
        StatusLeftText.Text = $"文件 {Files.Count} · 就绪 {ready} · 需确认 {warn} · 失败 {failed}{busyText}{sizeText}";

        ClearButton.IsEnabled = Files.Count > 0;
        NextButton.IsEnabled = busy == 0 && ready + warn > 0;
        SubtitleText.Text = Files.Count == 0
            ? "拖入 Excel 或 CSV，或点「添加文件」"
            : $"共 {Files.Count} 个文件，点「下一步：识别列」继续";
    }

    private static string FormatSize(long bytes) => bytes switch
    {
        < 1024 => $"{bytes} B",
        < 1024 * 1024 => $"{bytes / 1024.0:F0} KB",
        _ => $"{bytes / 1024.0 / 1024.0:F1} MB",
    };

    /// <summary>
    /// 关于：版本自述 + 未实现清单。
    /// 每一项都写了「在界面上哪里能找到那个灰按钮」——用户能自己核对，
    /// 而不是只能看到一句「部分功能未实现」。
    /// </summary>
    private void OnAboutClick(object sender, RoutedEventArgs e)
    {
        const string body =
            "基于 LoomGrid Fusion v2.3.9 重构，算法层零改动（基线 703 项零差异）。\n\n" +
            "功能等价：约 82%\n" +
            "　六屏全部可用；V3 的部分高级功能与 V4 的模拟功能待实现。\n\n" +
            "未实现的功能（括号内是界面上能找到它的位置）：\n" +
            "　V3 · 时段编辑　　（V3 左下栏「时段编辑」灰按钮）\n" +
            "　V3 · 配色自定义　（V3 左下栏「配色」灰按钮）\n" +
            "　V3 · 模板管理　　（V3 左下栏「模板」灰按钮）\n" +
            "　V3 · 策略管理　　（V3 左下栏「策略管理」灰按钮）\n" +
            "　V3 · 省份策略　　（属时段编辑的一部分，同上）\n" +
            "　V4 · 负荷模拟　　（V4 左侧「负荷模拟…」灰按钮）\n" +
            "　V4 · 光伏返送详情（V4 左侧「光伏返送详情…」灰按钮）\n" +
            "　V4 · 恢复原始　　（V4 左侧「恢复原始数据」灰按钮）\n" +
            "　V3 · 曲线探针　　（鼠标悬停在曲线上应出现读数，待验）\n" +
            "　V3 · 双点对比　　（新增能力，原 Fusion 也没有）\n\n" +
            "上述按钮在界面上均为禁用状态（灰），hover 有说明。\n" +
            "它们不是 bug，是本版未实现。";

        var dlg = new ContentDialog
        {
            Title = "LoomGrid Pro v1.0.0",
            Content = new ScrollViewer
            {
                MaxHeight = 420,
                Content = new TextBlock
                {
                    Text = body,
                    TextWrapping = TextWrapping.Wrap,
                    FontSize = 12.5,
                    LineHeight = 22,
                },
            },
            CloseButtonText = "知道了",
            XamlRoot = XamlRoot,
        };
        _ = dlg.ShowAsync();
    }

    private void ShowNotice(string text)
    {
        NoticeText.Text = text;
        var timer = DispatcherQueue.CreateTimer();
        timer.Interval = TimeSpan.FromSeconds(8);
        timer.IsRepeating = false;
        timer.Tick += (_, _) =>
        {
            if (NoticeText.Text == text) NoticeText.Text = "";
            timer.Stop();
        };
        timer.Start();
    }

    // ── 行详情 / 下一步 ─────────────────────────────────────

    private async void OnRowDoubleTapped(object sender, Microsoft.UI.Xaml.Input.DoubleTappedRoutedEventArgs e)
    {
        if (FileList.SelectedItem is not WorkFile file) return;
        await ShowDetailAsync(file);
    }

    private async Task ShowDetailAsync(WorkFile file)
    {
        var body = new StackPanel { Spacing = 10 };
        body.Children.Add(new TextBlock { Text = file.FullPath, FontSize = 12, TextWrapping = TextWrapping.Wrap });
        body.Children.Add(new TextBlock
        {
            Text = file.StatusText,
            FontSize = 13,
            Foreground = file.StatusBrush,
        });
        if (file.HasDetail)
        {
            body.Children.Add(new TextBlock
            {
                Text = file.Detail,
                FontSize = 12.5,
                TextWrapping = TextWrapping.Wrap,
                Foreground = (Brush)Application.Current.Resources["TextFillColorSecondaryBrush"],
            });
        }

        var dialog = new ContentDialog
        {
            Title = file.FileName,
            Content = body,
            CloseButtonText = "知道了",
            XamlRoot = XamlRoot,
        };
        await dialog.ShowAsync();
    }

    private async void OnNextClick(object sender, RoutedEventArgs e)
    {
        var usable = Files.Where(f => f.CanContinue).ToList();
        if (usable.Count == 0)
        {
            await ShowMessageAsync("没有可继续的文件",
                "所有文件都识别失败了。双击行看原因，或先把文件修好再拖进来。");
            return;
        }

        int failed = Files.Count(f => f.State == FileState.Failed);
        AppLog.Write($"V1：进入 V2，可继续 {usable.Count} 个，失败 {failed} 个");
        Frame.Navigate(typeof(V2MappingPage));
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

    private static bool IsSupported(string path)
    {
        string ext = Path.GetExtension(path).ToLowerInvariant();
        return SupportedExtensions.Contains(ext);
    }
}
