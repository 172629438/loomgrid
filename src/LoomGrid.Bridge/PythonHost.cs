using System.Diagnostics;
using System.Text;
using System.Text.Json;

namespace LoomGrid.Bridge;

/// <summary>Python 后端启动参数。</summary>
public sealed record PythonHostOptions
{
    /// <summary>显式指定的 python.exe 路径；null 时按内置 runtime → LGM_PYTHON → PATH 顺序探测。</summary>
    public string? PythonExecutable { get; init; }

    /// <summary>工作目录：必须是包含 core/ services/ plotting/ api/ 的根目录。</summary>
    public required string WorkingDirectory { get; init; }

    /// <summary>uvicorn 应用入口。**是 api.app:app，不是 api.main:app**。</summary>
    public string AppModule { get; init; } = "api.app:app";

    /// <summary>首选端口。被占用时自动向后探测。</summary>
    public int PreferredPort { get; init; } = 8000;

    /// <summary>端口最多向后探测多少个。</summary>
    public int MaxPortProbe { get; init; } = 40;

    /// <summary>等待 /api/health 就绪的超时。</summary>
    public TimeSpan StartupTimeout { get; init; } = TimeSpan.FromSeconds(40);

    /// <summary>额外环境变量（例如 LGM_WEB_DIR）。</summary>
    public IReadOnlyDictionary<string, string>? Environment { get; init; }
}

/// <summary>Python 后端相关的可读错误。</summary>
public sealed class PythonHostException : Exception
{
    public PythonHostException(string message) : base(message) { }
    public PythonHostException(string message, Exception inner) : base(message, inner) { }
}

/// <summary>
/// 管理 Python 后端进程：端口扫描 → 启动 uvicorn → 轮询 /api/health → 绑定 Job Object。
/// 父进程退出（含被强杀）时，Job Object 保证子进程一并终止。
/// </summary>
public sealed class PythonHost : IAsyncDisposable
{
    private const int LogCapacity = 80;

    private readonly Process _process;
    private readonly JobObject _job;
    private readonly HttpClient _probe;
    private readonly Queue<string> _log = new();
    private readonly object _logLock = new();
    private bool _disposed;

    /// <summary>实际使用的端口。</summary>
    public int Port { get; }

    /// <summary>后端根地址，例如 http://127.0.0.1:8000/。</summary>
    public Uri BaseAddress { get; }

    /// <summary>启动时 /api/health 返回的原始 JSON。</summary>
    public string HealthJson { get; private set; } = "";

    public int ProcessId => _process.Id;

    public bool IsRunning => !_process.HasExited;

    /// <summary>Job Object 是否绑定成功（false 表示只靠 Dispose 时 Kill 兜底）。</summary>
    public bool JobBound => _job.IsActive;

    /// <summary>最近若干行 stdout/stderr，用于失败时定位。</summary>
    public string LogTail
    {
        get
        {
            lock (_logLock)
            {
                return _log.Count == 0 ? "(无输出)" : string.Join("\n", _log);
            }
        }
    }

    private PythonHost(Process process, JobObject job, int port)
    {
        _process = process;
        _job = job;
        Port = port;
        BaseAddress = new Uri($"http://127.0.0.1:{port}/");
        _probe = new HttpClient
        {
            BaseAddress = BaseAddress,
            Timeout = TimeSpan.FromSeconds(3),
        };
    }

    /// <summary>
    /// 启动后端并等待就绪。端口被抢或 uvicorn 启动失败时自动换一个端口重试一次。
    /// </summary>
    public static async Task<PythonHost> StartAsync(
        PythonHostOptions options, CancellationToken ct = default)
    {
        string exe;
        try
        {
            exe = PythonRuntimeLocator.Resolve(options.PythonExecutable);
        }
        catch (PythonHostException)
        {
            throw;
        }

        string workDir = Path.GetFullPath(options.WorkingDirectory);
        if (!Directory.Exists(workDir))
            throw new PythonHostException($"Python 后端工作目录不存在：{workDir}");

        PythonHostException? lastError = null;
        int startPort = options.PreferredPort;

        for (int attempt = 0; attempt < 2; attempt++)
        {
            int port = PortFinder.FindFree(startPort, options.MaxPortProbe);
            var host = Begin(exe, workDir, port, options);
            try
            {
                await host.WaitReadyAsync(options.StartupTimeout, ct).ConfigureAwait(false);
                return host;
            }
            catch (PythonHostException ex)
            {
                lastError = ex;
                await host.DisposeAsync().ConfigureAwait(false);
                startPort = port + 1;
            }
        }

        throw lastError ?? new PythonHostException("Python 后端启动失败（未知原因）。");
    }

    private static PythonHost Begin(string exe, string workDir, int port, PythonHostOptions options)
    {
        var psi = new ProcessStartInfo
        {
            FileName = exe,
            Arguments =
                $"-m uvicorn {options.AppModule} --host 127.0.0.1 --port {port} " +
                "--log-level warning --no-access-log",
            WorkingDirectory = workDir,
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
        };

        psi.Environment["PYTHONUNBUFFERED"] = "1";
        psi.Environment["PYTHONIOENCODING"] = "utf-8";
        string existingPath = psi.Environment.TryGetValue("PYTHONPATH", out var p) && !string.IsNullOrEmpty(p)
            ? p
            : "";
        psi.Environment["PYTHONPATH"] = string.IsNullOrEmpty(existingPath)
            ? workDir
            : workDir + Path.PathSeparator + existingPath;
        psi.Environment["LGM_LAUNCHED_BY"] = "winui";

        if (options.Environment is not null)
        {
            foreach (var kv in options.Environment) psi.Environment[kv.Key] = kv.Value;
        }

        var process = new Process { StartInfo = psi, EnableRaisingEvents = true };
        var job = new JobObject();
        var host = new PythonHost(process, job, port);

        process.OutputDataReceived += (_, e) => { if (e.Data is not null) host.AddLog("out", e.Data); };
        process.ErrorDataReceived += (_, e) => { if (e.Data is not null) host.AddLog("err", e.Data); };

        try
        {
            process.Start();
            process.BeginOutputReadLine();
            process.BeginErrorReadLine();
            job.Assign(process.Handle);
        }
        catch (Exception ex)
        {
            host.DisposeSyncQuiet();
            throw new PythonHostException(
                $"启动 Python 后端失败（{exe}）：{ex.Message}", ex);
        }

        return host;
    }

    private async Task WaitReadyAsync(TimeSpan timeout, CancellationToken ct)
    {
        var sw = Stopwatch.StartNew();
        while (sw.Elapsed < timeout)
        {
            ct.ThrowIfCancellationRequested();

            if (_process.HasExited)
            {
                throw new PythonHostException(
                    $"Python 后端启动后立即退出（exit={_process.ExitCode}，端口 {Port}）。\n{LogTail}");
            }

            try
            {
                using var resp = await _probe.GetAsync("api/health", ct).ConfigureAwait(false);
                if (resp.IsSuccessStatusCode)
                {
                    string json = await resp.Content.ReadAsStringAsync(ct).ConfigureAwait(false);
                    if (IsOk(json))
                    {
                        HealthJson = json;
                        AddLog("host", $"/api/health 就绪（{sw.ElapsedMilliseconds} ms，端口 {Port}）");
                        return;
                    }
                }
            }
            catch (OperationCanceledException) when (!ct.IsCancellationRequested)
            {
                // 单次探测超时，继续轮询
            }
            catch (HttpRequestException)
            {
                // 还没起监听，继续轮询
            }

            await Task.Delay(250, ct).ConfigureAwait(false);
        }

        throw new PythonHostException(
            $"Python 后端在 {timeout.TotalSeconds:0} 秒内未就绪（端口 {Port}）。\n{LogTail}");
    }

    private static bool IsOk(string json)
    {
        try
        {
            using var doc = JsonDocument.Parse(json);
            return doc.RootElement.TryGetProperty("ok", out var ok)
                   && ok.ValueKind == JsonValueKind.True;
        }
        catch (JsonException)
        {
            return false;
        }
    }

    private void AddLog(string kind, string line)
    {
        lock (_logLock)
        {
            _log.Enqueue($"[{kind}] {line}");
            while (_log.Count > LogCapacity) _log.Dequeue();
        }
    }

    /// <summary>优雅关闭：先杀进程树，再释放作业与句柄。可重复调用。</summary>
    public async ValueTask DisposeAsync()
    {
        if (_disposed) return;
        _disposed = true;

        try
        {
            if (!_process.HasExited)
            {
                try { _process.Kill(entireProcessTree: true); }
                catch (InvalidOperationException) { }

                using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
                try { await _process.WaitForExitAsync(cts.Token).ConfigureAwait(false); }
                catch (OperationCanceledException) { }
            }
        }
        catch (InvalidOperationException)
        {
            // 进程已退出
        }
        finally
        {
            _job.Dispose();
            _probe.Dispose();
            _process.Dispose();
        }
    }

    private void DisposeSyncQuiet()
    {
        try { if (!_process.HasExited) _process.Kill(entireProcessTree: true); } catch { }
        try { _job.Dispose(); } catch { }
        try { _probe.Dispose(); } catch { }
        try { _process.Dispose(); } catch { }
    }
}
