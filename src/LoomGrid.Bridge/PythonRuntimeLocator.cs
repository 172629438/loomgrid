using System.Diagnostics;

namespace LoomGrid.Bridge;

/// <summary>
/// 定位可用的 Python 解释器。优先级：
/// ① 调用方显式指定 ② 打包内置 runtime\python.exe ③ 环境变量 LGM_PYTHON ④ PATH 中的 python。
/// 全部失败时抛出带中文说明的异常，不静默降级。
/// </summary>
public static class PythonRuntimeLocator
{
    public static string Resolve(string? explicitPath = null)
    {
        var candidates = new List<string>();

        if (!string.IsNullOrWhiteSpace(explicitPath))
            candidates.Add(explicitPath);

        var bundled = Path.Combine(AppContext.BaseDirectory, "runtime", "python.exe");
        candidates.Add(bundled);

        var env = Environment.GetEnvironmentVariable("LGM_PYTHON");
        if (!string.IsNullOrWhiteSpace(env))
            candidates.Add(env);

        foreach (var c in candidates)
        {
            if (File.Exists(c)) return c;
        }

        // 最后回落到 PATH：交给 Process 自己解析，但先验证能跑
        foreach (var name in new[] { "python.exe", "python" })
        {
            if (CanRun(name)) return name;
        }

        throw new PythonHostException(
            "找不到可用的 Python 解释器。请确认已安装 Python 3.12，或设置 LGM_PYTHON 环境变量指向 python.exe。\n" +
            $"已检查：{string.Join("；", candidates.Where(c => !string.IsNullOrWhiteSpace(c)))}；以及 PATH。");
    }

    private static bool CanRun(string exe)
    {
        try
        {
            using var p = Process.Start(new ProcessStartInfo
            {
                FileName = exe,
                Arguments = "-c \"import sys; print(sys.version)\"",
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true,
            });
            if (p is null) return false;
            if (!p.WaitForExit(5000)) { try { p.Kill(true); } catch { } return false; }
            return p.ExitCode == 0;
        }
        catch
        {
            return false;
        }
    }
}
