using Microsoft.UI.Xaml;

namespace LoomGrid.UI;

/// <summary>
/// 应用入口。窗口之外不做事；后端生命周期由 AppServices 管理，MainWindow 关闭时释放。
/// </summary>
public partial class App : Application
{
    private Window? _window;

    /// <summary>供 Page 反向访问主窗口（读 SystemBackdrop / 主题）。</summary>
    public static Window? MainWindowRef { get; private set; }

    public App()
    {
        InitializeComponent();

        // 一级异常日志：WinUI 的 fail-fast（0xc000027b）往往不经过 UnhandledException，
        // 但会先冒一次 first-chance。只记「将来会致命」的类型，不刷屏。
        AppDomain.CurrentDomain.FirstChanceException += (_, e) =>
        {
            var ex = e.Exception;
            if (ex is HttpRequestException or TaskCanceledException or OperationCanceledException)
                return;
            try { AppLog.Write($"[first-chance] {ex.GetType().FullName}: {ex.Message}"); }
            catch { }
        };

        UnhandledException += (_, e) =>
        {
            // 未处理异常不留空白窗口：写进日志后让进程正常结束
            try
            {
                string path = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    "LoomGrid", "ui-crash.log");
                Directory.CreateDirectory(Path.GetDirectoryName(path)!);
                File.AppendAllText(path,
                    $"{DateTime.Now:yyyy-MM-dd HH:mm:ss} {e.Exception}\n\n");
            }
            catch { }
        };
    }

    protected override void OnLaunched(Microsoft.UI.Xaml.LaunchActivatedEventArgs args)
    {
        _window = new MainWindow();
        MainWindowRef = _window;

        // 仅开发期自测浅色主题用，不是产品功能：
        //   生产环境不设此变量，界面跟随系统主题（WinUI 原生行为）。
        //   设 LGM_UI_THEME=light 可强制浅色，便于在不动桌面设置的情况下截图对比。
        string? forced = Environment.GetEnvironmentVariable("LGM_UI_THEME");
        if (_window.Content is FrameworkElement root &&
            (forced == "light" || forced == "dark"))
        {
            root.RequestedTheme = forced == "light" ? ElementTheme.Light : ElementTheme.Dark;
            AppLog.Write($"界面主题：强制 {forced}（仅开发自测，生产跟随系统）");
        }

        _window.Activate();
    }
}
