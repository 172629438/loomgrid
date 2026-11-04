using LoomGrid.Bridge;

namespace LoomGrid.UI;

/// <summary>
/// 应用级服务容器：后端进程 + HTTP 客户端。
/// 启动一次，全窗口共享；关闭时释放。
/// </summary>
public sealed class AppServices : IAsyncDisposable
{
    public PythonHost Host { get; }
    public ApiClient Api { get; }

    public static AppServices? Current { get; private set; }

    private AppServices(PythonHost host, ApiClient api)
    {
        Host = host;
        Api = api;
    }

    /// <summary>
    /// 启动后端。进度同时写启动日志与界面（两条通道都要有：
    /// 界面给人看，日志给验证工具看）。
    /// </summary>
    public static async Task<AppServices> StartAsync(
        Action<string>? log = null, CancellationToken ct = default)
    {
        void Sink(string line)
        {
            AppLog.Write(line);
            log?.Invoke(line);
        }

        Sink("定位 Python 代码目录…");
        string coreRoot = PythonCoreLocator.Resolve();
        Sink($"代码目录：{coreRoot}");

        Sink("定位 Python 解释器并启动后端…");
        var options = new PythonHostOptions
        {
            WorkingDirectory = coreRoot,
        };

        var host = await PythonHost.StartAsync(options, ct).ConfigureAwait(false);
        Sink($"后端就绪：端口 {host.Port} · PID {host.ProcessId} · Job 绑定 {host.JobBound}");
        Sink($"HEALTH={host.HealthJson}");

        var services = new AppServices(host, new ApiClient(host.BaseAddress));
        Current = services;
        return services;
    }

    public async ValueTask DisposeAsync()
    {
        AppLog.Write("开始关闭后端…");
        Api.Dispose();
        await Host.DisposeAsync().ConfigureAwait(false);
        AppLog.Write("后端已关闭");
        if (ReferenceEquals(Current, this)) Current = null;
    }
}
