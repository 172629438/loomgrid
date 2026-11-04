using System.Diagnostics;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace LoomGrid.UI;

/// <summary>
/// 启动自检页：验证「UI 能拉起 Python 后端并拿到 /api/health 的 JSON」。
/// 这一页跑通之前不写任何业务界面 —— 地基不对，上面盖什么都会塌。
/// </summary>
public sealed partial class MainPage : Page
{
    private static readonly JsonSerializerOptions PrettyOpts = new()
    {
        WriteIndented = true,
        Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
    };

    private readonly StringBuilder _log = new();
    private bool _started;

    public MainPage()
    {
        InitializeComponent();
        Loaded += OnLoaded;
    }

    private void OnLoaded(object sender, RoutedEventArgs e)
    {
        if (_started) return;
        _started = true;
        AppLog.Reset();
        AppLog.Write("窗口已加载，开始自检");
        _ = StartBackendAsync();
    }

    private async void OnRetryClick(object sender, RoutedEventArgs e)
    {
        if (AppServices.Current is { } old)
        {
            try { await old.DisposeAsync(); } catch { }
        }

        _log.Clear();
        LogText.Text = "";
        HealthText.Text = "";
        await StartBackendAsync();
    }

    private async Task StartBackendAsync()
    {
        SetBusy(true);
        RetryButton.Visibility = Visibility.Collapsed;
        StatusText.Text = "正在启动 Python 后端…";
        PortText.Text = "—";
        PidText.Text = "—";
        JobText.Text = "—";
        ReadyText.Text = "—";

        var sw = Stopwatch.StartNew();
        try
        {
            var services = await AppServices.StartAsync(AppendLog);
            sw.Stop();

            StatusText.Text = "后端就绪";
            PortText.Text = services.Host.Port.ToString();
            PidText.Text = services.Host.ProcessId.ToString();
            JobText.Text = services.Host.JobBound
                ? "已绑定（父进程退出即终止）"
                : "未绑定（关闭时 Kill 兜底）";
            ReadyText.Text = $"{sw.ElapsedMilliseconds} ms";
            HealthText.Text = Pretty(services.Host.HealthJson);
            AppLog.Write($"界面已渲染健康 JSON（{sw.ElapsedMilliseconds} ms）");
            SetBusy(false);
        }
        catch (Exception ex)
        {
            sw.Stop();
            StatusText.Text = "后端启动失败";
            HealthText.Text = ex.Message;
            AppendLog(ex.ToString());
            AppLog.Write($"界面失败：{ex.GetType().Name} {ex.Message}");
            RetryButton.Visibility = Visibility.Visible;
            SetBusy(false);
        }
    }

    private void SetBusy(bool busy)
    {
        Busy.IsActive = busy;
        Busy.Visibility = busy ? Visibility.Visible : Visibility.Collapsed;
    }

    private void AppendLog(string line)
    {
        var dq = DispatcherQueue;
        if (dq is null) return;
        dq.TryEnqueue(() =>
        {
            _log.AppendLine(line);
            LogText.Text = _log.ToString();
            DetailScroller.ChangeView(null, DetailScroller.ScrollableHeight, null);
        });
    }

    private static string Pretty(string json)
    {
        try
        {
            using var doc = JsonDocument.Parse(json);
            return JsonSerializer.Serialize(doc.RootElement, PrettyOpts);
        }
        catch (JsonException)
        {
            return json;
        }
    }
}
