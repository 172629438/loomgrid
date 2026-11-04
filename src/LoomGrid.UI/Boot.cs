using LoomGrid.Bridge;

namespace LoomGrid.UI;

/// <summary>
/// 后端启动的唯一入口。六屏共用一次启动，页面切换不重启后端。
/// </summary>
public static class Boot
{
    private static readonly object Gate = new();
    private static Task<AppServices>? _task;

    public static Task<AppServices> EnsureAsync()
    {
        lock (Gate)
        {
            return _task ??= AppServices.StartAsync();
        }
    }

    /// <summary>给状态栏读的当前后端信息；未就绪时为 null。</summary>
    public static PythonHost? Host => AppServices.Current?.Host;
}
