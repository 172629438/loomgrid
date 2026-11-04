using System.IO;
using System.Text.RegularExpressions;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;

namespace LoomGrid.UI;

/// <summary>
/// V6 结果页：读 Workspace.LastRuns（V5 导出后填充，或 adopt 从目录构造）。
///
/// 三条设计约束（来自协作方）：
///   · 图表用真实 PNG 缩略；宽表**不进缩略图网格**——xlsx 没有缩略图概念，
///     一排 Excel 图标占地方又不提供信息，改用列表行（名字 + 大小）。
///   · 失败按「错误骨架」聚合，不平铺 N 条。骨架提取用可核规则（路径/数字/引号内容），
///     不凭感觉合并。
///   · 0 失败时「仅重试失败项」禁用，主按钮指向下一步。
/// </summary>
public sealed partial class V6ResultPage : Page
{
    private bool _loaded;

    public V6ResultPage()
    {
        InitializeComponent();
        Loaded += OnLoaded;
    }

    private void OnLoaded(object sender, RoutedEventArgs e)
    {
        if (_loaded) return;
        _loaded = true;
        AppLog.Write("V6 结果页已加载");
        Render();
    }

    private void Render()
    {
        var runs = Workspace.Current.LastRuns;
        if (runs.Count == 0)
        {
            SummaryText.Text = "没有可显示的导出结果。请先在导出页执行一次导出。";
            ChartLabel.Text = "";
            MidwideLabel.Text = "";
            FailLabel.Text = "";
            return;
        }

        // 合并多次 run（多 session）
        var artifacts = runs.SelectMany(r => r.Artifacts).ToList();
        var charts = artifacts.Where(a => a.IsChart).ToList();
        var midwides = artifacts.Where(a => !a.IsChart).ToList();
        int totalGroups = runs.Sum(r => r.TotalGroups);
        int okGroups = runs.Sum(r => r.GroupsSucceeded);
        int failGroups = runs.Sum(r => r.GroupsFailed);
        var failures = runs.SelectMany(r => r.FailedMessages).ToList();
        string outputDir = runs.Select(r => r.OutputDir).FirstOrDefault(d => !string.IsNullOrEmpty(d)) ?? "";
        bool adopted = runs.Any(r => r.Adopted);

        SummaryText.Text =
            $"{totalGroups} 个分组：{okGroups} 成功 / {failGroups} 失败  ·  " +
            $"产物 {artifacts.Count} 件（图表 {charts.Count} / 宽表 {midwides.Count}）" +
            (adopted ? "  ·  从目录读取，非本次导出" : "");

        ChartLabel.Text = charts.Count > 0 ? $"图表（{charts.Count}）" : "图表（无）";
        ChartGrid.ItemsSource = charts;

        MidwideLabel.Text = midwides.Count > 0 ? $"中间宽表（{midwides.Count}）" : "中间宽表（无）";
        MidwideList.ItemsSource = midwides;

        var groups = GroupFailures(failures);
        FailLabel.Text = failures.Count > 0 ? $"失败（{failures.Count} 条，归为 {groups.Count} 类）" : "失败（无）";
        FailList.ItemsSource = groups;
        FailHint.Visibility = failures.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
        FailHint.Text = failures.Count == 0 ? "全部成功，没有失败项。" : "";

        // 0 失败时禁用重试——别让用户点一个不存在的操作
        RetryButton.IsEnabled = failures.Count > 0;

        FooterText.Text = outputDir;

        AppLog.Write($"V6：{totalGroups} 组 / {okGroups} 成功 / {failGroups} 失败，" +
                     $"图表 {charts.Count} 宽表 {midwides.Count}，失败归为 {groups.Count} 类");

        _ = LoadThumbnailsAsync(charts);
    }

    /// <summary>
    /// 用文件流加载缩略图。不能直接绑 file:// URI——unpackaged 应用里 Image
    /// 会把 file:// 当网络请求，报 E_NETWORK_ERROR。
    /// </summary>
    private async Task LoadThumbnailsAsync(List<ExportArtifact> charts)
    {
        foreach (var a in charts)
        {
            if (string.IsNullOrEmpty(a.FullPath)) continue;
            try
            {
                var bmp = new Microsoft.UI.Xaml.Media.Imaging.BitmapImage();
                using var stream = await Windows.Storage.Streams.FileRandomAccessStream
                    .OpenAsync(a.FullPath, Windows.Storage.FileAccessMode.Read);
                await bmp.SetSourceAsync(stream);
                a.Thumb = bmp;
            }
            catch (Exception ex)
            {
                AppLog.Write($"V6：缩略图加载失败 {a.Name} {ex.GetType().Name} {ex.Message}");
            }
        }
    }

    // ── 失败归因：按错误骨架聚合 ──────────────────────────

    /// <summary>
    /// 骨架提取规则（可核，不凭感觉）：
    ///   ① Windows 路径 → &lt;路径&gt;
    ///   ② 引号/书名号里的名字 → &lt;名&gt;
    ///   ③ 任何数字（含小数）→ &lt;n&gt;
    /// 这样「读 C:\a\X失败」与「读 D:\b\Y失败」会落到同一骨架，
    /// 而「读失败」与「写失败」不会。
    /// </summary>
    internal static string Skeleton(string raw)
    {
        if (string.IsNullOrWhiteSpace(raw)) return "（无错误信息）";
        string s = raw;
        s = Regex.Replace(s, @"[A-Za-z]:\\[^\s，。；,;]*", "<路径>");
        s = Regex.Replace(s, @"[「『""'][^」』""']*[」』""']", "<名>");
        s = Regex.Replace(s, @"\d+(?:\.\d+)?", "<n>");
        s = Regex.Replace(s, @"\s+", " ").Trim();
        return s.Length > 120 ? s[..120] + "…" : s;
    }

    private static List<FailureGroup> GroupFailures(List<string> messages)
    {
        var map = new Dictionary<string, FailureGroup>(StringComparer.Ordinal);
        foreach (var raw in messages)
        {
            string key = Skeleton(raw);
            if (!map.TryGetValue(key, out var g))
            {
                g = new FailureGroup { Skeleton = key };
                map[key] = g;
            }
            g.Items.Add(raw);
        }
        // 数量多的排前面
        return map.Values.OrderByDescending(g => g.Count).ToList();
    }

    // ── 缩略图降级 ─────────────────────────────────────────

    private void OnThumbFailed(object sender, ExceptionRoutedEventArgs e)
    {
        // 破图标会让用户以为文件坏了；这里换成「预览不可用」+ 文件名仍然可见。
        if (sender is not Image img) return;
        img.Visibility = Visibility.Collapsed;

        if (img.Parent is Panel panel && !panel.Children.Any(c => c is TextBlock { Tag: "thumb-fallback" }))
        {
            var fallback = new TextBlock
            {
                Text = "预览不可用",
                Tag = "thumb-fallback",
                FontSize = 11,
                Height = 132,
                VerticalAlignment = VerticalAlignment.Center,
                HorizontalAlignment = HorizontalAlignment.Center,
                Foreground = (Brush)Application.Current.Resources["TextFillColorSecondaryBrush"],
            };
            panel.Children.Insert(0, fallback);
        }
        AppLog.Write($"V6：缩略图加载失败 {e.ErrorMessage}");
    }

    // ── 操作 ───────────────────────────────────────────────

    private async void OnOpenDirClick(object sender, RoutedEventArgs e)
    {
        var dir = FooterText.Text;
        if (string.IsNullOrWhiteSpace(dir)) return;
        try
        {
            Directory.CreateDirectory(dir);
            await Windows.System.Launcher.LaunchFolderPathAsync(dir);
        }
        catch (Exception ex)
        {
            AppLog.Write($"V6：打开目录失败 {ex.Message}");
        }
    }

    private async void OnRetryClick(object sender, RoutedEventArgs e)
    {
        int n = Workspace.Current.LastRuns.Sum(r => r.FailedMessages.Count);
        await ShowMessageAsync("重试失败项",
            $"有 {n} 条失败记录。\n\n重试需要回到导出页重新提交——" +
            "当前版本还没有「只重跑失败项」的接口，这一项记为待做。");
    }

    private void OnBackClick(object sender, RoutedEventArgs e)
    {
        while (Frame.CanGoBack) Frame.GoBack();
        if (Frame.Content is not V1WorkbenchPage)
            Frame.Navigate(typeof(V1WorkbenchPage));
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
