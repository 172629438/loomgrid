using Microsoft.UI.Xaml;
using Windows.Graphics;

namespace LoomGrid.UI;

/// <summary>
/// 主窗口：Mica 背景 + 自定义标题栏 + 页面容器。
/// 关闭时释放后端（Job Object 另有兜底，但优雅关闭能保证端口立即释放）。
/// </summary>
public sealed partial class MainWindow : Window
{
    public MainWindow()
    {
        InitializeComponent();

        ExtendsContentIntoTitleBar = true;
        SetTitleBar(AppTitleBar);
        AppWindow.SetIcon("Assets/AppIcon.ico");

        // 仅开发期自测浅色主题用，不是产品功能：
        //   生产环境不设此变量，界面跟随系统主题（WinUI 原生行为）。
        //   日志同时打 RequestedTheme（我要的）与 ActualTheme（实际生效的）——
        //   只打前者会被“设了但被上级覆盖”骗过去，和 FontName 那次同族。
        string? forced = Environment.GetEnvironmentVariable("LGM_UI_THEME");
        if (forced == "light" || forced == "dark")
        {
            RootGrid.RequestedTheme = forced == "light" ? ElementTheme.Light : ElementTheme.Dark;
        }
        AppLog.Write($"主题：RequestedTheme={RootGrid.RequestedTheme} " +
                     $"ActualTheme={RootGrid.ActualTheme} " +
                     $"env={forced ?? "(未设)"}");

        AppWindow.Resize(new SizeInt32(1440, 920));

        RootFrame.Navigate(typeof(V1WorkbenchPage));

        Closed += OnClosed;
    }

    private async void OnClosed(object sender, WindowEventArgs args)
    {
        if (AppServices.Current is { } services)
        {
            try { await services.DisposeAsync(); }
            catch { }
        }
    }
}
