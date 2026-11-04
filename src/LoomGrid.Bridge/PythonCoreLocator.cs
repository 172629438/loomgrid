namespace LoomGrid.Bridge;

/// <summary>
/// 定位 Python 代码根目录（含 core/ services/ plotting/ api/）。
/// 优先级：显式指定 → 输出目录下 python-core（打包）→ 从输出目录向上找仓库根（开发）。
/// </summary>
public static class PythonCoreLocator
{
    private static readonly string[] Markers =
    {
        Path.Combine("api", "app.py"),
        Path.Combine("core", "ingest.py"),
        Path.Combine("services", "config.py"),
        Path.Combine("plotting", "curves.py"),
    };

    public static string Resolve(string? explicitPath = null)
    {
        if (!string.IsNullOrWhiteSpace(explicitPath))
        {
            string full = Path.GetFullPath(explicitPath);
            if (IsCoreRoot(full)) return full;
            throw new PythonHostException($"指定的 Python 代码目录不完整：{full}");
        }

        string packaged = Path.Combine(AppContext.BaseDirectory, "python-core");
        if (IsCoreRoot(packaged)) return packaged;

        // 开发模式：从输出目录向上逐级找仓库根
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        for (int i = 0; i < 8 && dir is not null; i++, dir = dir.Parent)
        {
            if (IsCoreRoot(dir.FullName)) return dir.FullName;
        }

        string cwd = Directory.GetCurrentDirectory();
        if (IsCoreRoot(cwd)) return cwd;

        throw new PythonHostException(
            "找不到 Python 代码目录（需要同时存在 api/app.py、core/ingest.py、services/config.py、plotting/curves.py）。\n" +
            $"已检查：{packaged}；从 {AppContext.BaseDirectory} 向上的各级父目录；当前目录 {cwd}。");
    }

    public static bool IsCoreRoot(string dir)
    {
        try
        {
            return Markers.All(m => File.Exists(Path.Combine(dir, m)));
        }
        catch
        {
            return false;
        }
    }
}
