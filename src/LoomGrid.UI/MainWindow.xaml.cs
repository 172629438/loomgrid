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
