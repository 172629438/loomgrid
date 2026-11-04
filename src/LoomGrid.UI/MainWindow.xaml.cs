using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Input;
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

    /// <summary>
    /// F11 坐标自检。本机 175% 缩放下，UI 自动化工具报的坐标与物理坐标差一个 scale，
    /// 导致「看得见按钮但点不中」。这里一次打全四个值，**不靠推算**：
    ///   物理原点（屏幕像素）、逻辑尺寸（DIP）、scale、内容区中心点的物理坐标。
    /// **边界**：它只报数字，不修正任何东西；也不参数化任何业务逻辑。
    /// </summary>
    private void OnCoordProbe(KeyboardAccelerator sender, KeyboardAcceleratorInvokedEventArgs args)
    {
        var pos = AppWindow.Position;
        double scale = RootGrid.XamlRoot?.RasterizationScale ?? 1.0;
        double w = RootGrid.ActualWidth;
        double h = RootGrid.ActualHeight;

        // 客户区左上角在屏幕上的物理坐标：物理原点 + 标题栏高度（逻辑 × scale）
        double titleH = AppTitleBar.ActualHeight;
        double cx = pos.X + w * scale / 2;
        double cy = pos.Y + titleH * scale + (h * scale) / 2;

        AppLog.Write($"坐标自检：窗口物理原点=({pos.X},{pos.Y}) 逻辑尺寸=({w:0}×{h:0}) " +
                     $"scale={scale:0.00} 标题栏逻辑高={titleH:0} " +
                     $"内容中心物理=({cx:0},{cy:0})");
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
