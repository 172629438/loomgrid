using System.Text.Json;
using LoomGrid.Bridge;
using Microsoft.UI;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Windows.UI;

namespace LoomGrid.UI;

/// <summary>
/// V2 列映射：逐文件确认「哪个是日期、哪个是资产、哪些是负荷」，
/// 确认后调 /api/ingest/mapping 产出 session 与分组。
///
/// 预览显示的必须是**真实样本数据**（后端 inspect 采样返回的 sample_rows），
/// 不能造演示数据——用户看到和自己文件对不上的数字，会以为工具读错了。
/// </summary>
public sealed partial class V2MappingPage : Page
{
    private static SolidColorBrush HeaderBg => Res("TableHeaderBgBrush", Microsoft.UI.Colors.Black);
    private static SolidColorBrush CellBorder => Res("TableCellBorderBrush", Microsoft.UI.Colors.Gray);

    private static SolidColorBrush Res(string key, Windows.UI.Color fallback)
    {
        if (Application.Current?.Resources is { } res
            && res.TryGetValue(key, out var v)
            && v is SolidColorBrush brush)
            return brush;
        return new SolidColorBrush(fallback);
    }

    private WorkFile? _currentFile;
    private SheetInfo? _currentSheet;
    private bool _loaded;

    public V2MappingPage()
    {
        InitializeComponent();
        Loaded += OnLoaded;
    }

    private void OnLoaded(object sender, RoutedEventArgs e)
    {
        if (_loaded) return;
        _loaded = true;
        AppLog.Write("V2 映射页已加载");

        var usable = Workspace.Current.Files.Where(f => f.CanContinue).ToList();
        if (usable.Count == 0)
        {
            _ = ShowMessageAsync("没有可映射的文件", "回工作台添加文件，并确认它们识别成功。");
            return;
        }

        FileList.ItemsSource = usable;

        // 自动选中第一个尚未映射的文件
        var first = usable.FirstOrDefault(f => f.SessionId is null) ?? usable[0];
        FileList.SelectedItem = first;
        UpdateProgress();
    }

    // ── 文件 / 工作表选择 ──────────────────────────────────

    private void OnFileSelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (FileList.SelectedItem is not WorkFile file) return;

        _currentFile = file;
        _currentSheet = null;

        var sheets = file.Inspect?.Sheets ?? new List<SheetInfo>();
        SheetList.ItemsSource = sheets.Select(s =>
            $"{s.Name}   （{s.Columns.Count} 列，{s.RowsSampled} 行采样）").ToList();

        if (sheets.Count > 0)
        {
            // 优先选有负荷列建议的那个
            int idx = sheets.FindIndex(s => s.Suggest.HasLoadCol);
            SheetList.SelectedIndex = idx >= 0 ? idx : 0;
        }
        else
        {
            ClearPreview("这个文件没有可用的工作表");
        }

        UpdateProgress();
    }

    private void OnSheetSelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_currentFile is null) return;
        var sheets = _currentFile.Inspect?.Sheets;
        if (sheets is null || SheetList.SelectedIndex < 0 || SheetList.SelectedIndex >= sheets.Count)
            return;

        _currentSheet = sheets[SheetList.SelectedIndex];
        RenderMappingControls(_currentSheet);
        RenderPreview(_currentSheet);
        ConfirmButton.IsEnabled = true;
    }

    // ── 映射控件 ───────────────────────────────────────────

    private void RenderMappingControls(SheetInfo sheet)
    {
        var columns = sheet.SampleColumns.Count > 0 ? sheet.SampleColumns : sheet.Columns;

        DateCombo.ItemsSource = columns;
        DateCombo.SelectedItem = sheet.Suggest.DateCol is { } d && columns.Contains(d)
            ? d
            : columns.FirstOrDefault();

        var assetOptions = new List<string> { "(无)" };
        assetOptions.AddRange(columns);
        AssetCombo.ItemsSource = assetOptions;
        AssetCombo.SelectedItem = sheet.Suggest.AssetCol is { } a && columns.Contains(a) ? a : "(无)";

        LoadInfoText.Text = DescribeLoadColumns(sheet);
    }

    private static string DescribeLoadColumns(SheetInfo sheet)
    {
        var lc = sheet.Suggest.LoadCol;
        if (lc.ValueKind == JsonValueKind.Array)
        {
            int n = lc.GetArrayLength();
            if (n == 0) return "没识别到负荷列，请检查表头";
            var head = lc.EnumerateArray().Take(3).Select(x => x.GetString()).Where(s => s is not null);
            string sample = string.Join("、", head);
            return n > 3 ? $"{n} 列（{sample} …）" : $"{n} 列（{sample}）";
        }
        if (lc.ValueKind == JsonValueKind.String)
            return lc.GetString() ?? "—";
        return "没识别到负荷列，请检查表头";
    }

    // ── 预览 ───────────────────────────────────────────────

    private void ClearPreview(string caption)
    {
        PreviewCaption.Text = caption;
        PreviewGrid.Children.Clear();
        PreviewGrid.ColumnDefinitions.Clear();
        PreviewGrid.RowDefinitions.Clear();
    }

    private void RenderPreview(SheetInfo sheet)
    {
        PreviewGrid.Children.Clear();
        PreviewGrid.ColumnDefinitions.Clear();
        PreviewGrid.RowDefinitions.Clear();

        var columns = sheet.SampleColumns.Count > 0 ? sheet.SampleColumns : sheet.Columns;
        var rows = sheet.SampleRows;

        if (columns.Count == 0 || rows.Count == 0)
        {
            PreviewCaption.Text = "这个工作表没有可预览的数据";
            return;
        }

        const int maxCols = 12;
        int showCols = Math.Min(columns.Count, maxCols);
        int showRows = Math.Min(rows.Count, 20);

        PreviewCaption.Text = columns.Count > maxCols
            ? $"真实样本 · 显示前 {showCols} 列（共 {columns.Count} 列）× {showRows} 行"
            : $"真实样本 · {showCols} 列 × {showRows} 行";

        // 列：行号 + 数据列
        PreviewGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(46) });
        for (int c = 0; c < showCols; c++)
            PreviewGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(112) });

        // 行：表头 + 数据
        for (int r = 0; r <= showRows; r++)
            PreviewGrid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });

        // 表头
        for (int c = 0; c < showCols; c++)
        {
            var cell = MakeCell(columns[c], isHeader: true, column: c + 1, row: 0);
            PreviewGrid.Children.Add(cell);
        }

        // 数据
        for (int r = 0; r < showRows; r++)
        {
            var row = rows[r];

            var idx = MakeCell((r + 1).ToString(), isHeader: false, column: 0, row: r + 1);
            if (idx is Border { Child: TextBlock it })
            {
                it.Foreground = (Brush)Application.Current.Resources["TextFillColorSecondaryBrush"];
                it.HorizontalTextAlignment = TextAlignment.Right;
            }
            PreviewGrid.Children.Add(idx);

            for (int c = 0; c < showCols && c < row.Count; c++)
            {
                var cell = MakeCell(FormatCell(row[c]), isHeader: false, column: c + 1, row: r + 1);
                PreviewGrid.Children.Add(cell);
            }
        }
    }

    private static FrameworkElement MakeCell(string text, bool isHeader, int column, int row)
    {
        var tb = new TextBlock
        {
            Text = text,
            FontSize = 12,
            FontFamily = new FontFamily("Consolas"),
            Padding = new Thickness(8, 5, 8, 5),
            TextWrapping = TextWrapping.NoWrap,
            VerticalAlignment = VerticalAlignment.Center,
        };
        if (isHeader)
            tb.Foreground = (Brush)Application.Current.Resources["TextFillColorSecondaryBrush"];

        var border = new Border
        {
            Child = tb,
            BorderBrush = CellBorder,
            BorderThickness = new Thickness(0, 0, 1, 1),
        };
        if (isHeader) border.Background = HeaderBg;

        Grid.SetColumn(border, column);
        Grid.SetRow(border, row);
        return border;
    }

    private static string FormatCell(JsonElement e) => e.ValueKind switch
    {
        JsonValueKind.Number => e.ToString(),
        JsonValueKind.String => e.GetString() ?? "",
        JsonValueKind.Null or JsonValueKind.Undefined => "",
        JsonValueKind.True => "是",
        JsonValueKind.False => "否",
        _ => e.ToString(),
    };

    // ── 确认映射 ───────────────────────────────────────────

    private async void OnConfirmClick(object sender, RoutedEventArgs e)
    {
        if (_currentFile is null || _currentSheet is null) return;

        string? dateCol = DateCombo.SelectedItem as string;
        if (string.IsNullOrWhiteSpace(dateCol))
        {
            await ShowMessageAsync("缺少日期列", "日期列是必填的——系统靠它把数据分到每一天。");
            return;
        }

        string? assetCol = AssetCombo.SelectedItem as string;
        if (assetCol == "(无)") assetCol = null;

        var mapping = _currentSheet.Suggest with { DateCol = dateCol, AssetCol = assetCol };

        ConfirmButton.IsEnabled = false;
        StatusText.Text = $"正在扫描「{_currentFile.FileName}」…";

        try
        {
            var services = await Boot.EnsureAsync();
            var api = new IngestApi(services.Api);
            var result = await api.ApplyMappingAsync(_currentFile.FullPath, _currentSheet.Name, mapping);

            _currentFile.SessionId = result.SessionId;
            _currentFile.Groups.Clear();
            _currentFile.Groups.AddRange(result.Groups);

            Workspace.Current.SessionId = result.SessionId;
            Workspace.Current.Groups.Clear();
            foreach (var f in Workspace.Current.Files)
                Workspace.Current.Groups.AddRange(f.Groups);

            AppLog.Write($"V2：{_currentFile.FileName} 映射完成，{result.Groups.Count} 个分组，session={result.SessionId}");

            // 回填 V1 表格里的三列真实值（取第一个分组作代表）
            var g = result.Groups.FirstOrDefault();
            if (g is not null)
            {
                _currentFile.Asset = g.Asset;
                _currentFile.Month = g.Month;
                _currentFile.TimePoints = g.TimePoints.ToString();
                _currentFile.MinLoad = g.MinVal?.ToString("N1") ?? "—";
                _currentFile.MaxLoad = g.MaxVal?.ToString("N1") ?? "—";
            }
            if (result.Hints is not null && result.Hints.TryGetValue("guessed_year", out var yh))
                AppLog.Write($"V2：{_currentFile.FileName} 年份提示 —— {yh.Message}");

            StatusText.Text = $"「{_currentFile.FileName}」完成：{result.Groups.Count} 个分组";
            RefreshFileList();
            UpdateProgress();

            // 自动切到下一个未映射的文件
            var next = Workspace.Current.Files.FirstOrDefault(
                f => f.CanContinue && f.SessionId is null && !ReferenceEquals(f, _currentFile));
            if (next is not null)
            {
                FileList.SelectedItem = null;
                FileList.SelectedItem = next;
            }
        }
        catch (ApiException ex)
        {
            StatusText.Text = $"映射失败：{ex.Message}";
            AppLog.Write($"V2：{_currentFile.FileName} 映射失败 {ex.Code} {ex.Message}");
            await ShowMessageAsync("映射没成功", ex.Message);
        }
        catch (Exception ex)
        {
            StatusText.Text = $"映射出错：{ex.GetType().Name}";
            AppLog.Write($"V2：{_currentFile.FileName} 映射异常 {ex}");
            await ShowMessageAsync("映射出错", $"{ex.GetType().Name}：{ex.Message}");
        }
        finally
        {
            ConfirmButton.IsEnabled = _currentSheet is not null;
        }
    }

    // ── 进度与导航 ─────────────────────────────────────────

    private void RefreshFileList()
    {
        var usable = Workspace.Current.Files.Where(f => f.CanContinue).ToList();
        FileList.ItemsSource = usable;
    }

    private void UpdateProgress()
    {
        var usable = Workspace.Current.Files.Where(f => f.CanContinue).ToList();
        int done = usable.Count(f => f.SessionId is not null);
        ProgressText.Text = $"{done} / {usable.Count}";
        NextButton.IsEnabled = usable.Count > 0 && done == usable.Count;
        SubtitleText.Text = done == usable.Count && usable.Count > 0
            ? "全部确认完毕，可以进预览"
            : "逐文件确认：哪个是日期、哪个是资产、哪些是负荷";
    }

    private void OnBackClick(object sender, RoutedEventArgs e)
    {
        if (Frame.CanGoBack) Frame.GoBack();
        else Frame.Navigate(typeof(V1WorkbenchPage));
    }

    private async void OnNextClick(object sender, RoutedEventArgs e)
    {
        var usable = Workspace.Current.Files.Where(f => f.CanContinue).ToList();
        var pending = usable.Where(f => f.SessionId is null).ToList();
        if (pending.Count > 0)
        {
            await ShowMessageAsync("还有文件没确认",
                $"还有 {pending.Count} 个文件没做列映射：{string.Join("、", pending.Select(f => f.FileName))}");
            return;
        }

        int groups = Workspace.Current.Groups.Count;
        AppLog.Write($"V2：全部映射完成，共 {groups} 个分组，准备进 V3");
        await ShowMessageAsync("准备进入预览",
            $"共 {groups} 个分组，可以开始看曲线了。\n\nV3 预览页还没接上——下一步是分组树 + 曲线 + 策略参数。");
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
