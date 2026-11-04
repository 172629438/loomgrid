using System.Text;

namespace LoomGrid.UI;

/// <summary>
/// 启动日志：写到 %LOCALAPPDATA%\LoomGrid\ui-startup.log。
/// 存在的理由：界面状态没法被外部工具读取，
/// 「进程活着」不能证明「后端真的就绪」——日志是唯一可外部核对的证据。
/// </summary>
internal static class AppLog
{
    private static readonly object Gate = new();

    public static string FilePath { get; } = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "LoomGrid", "ui-startup.log");

    public static void Reset()
    {
        lock (Gate)
        {
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(FilePath)!);
                File.WriteAllText(FilePath, "", Encoding.UTF8);
            }
            catch { }
        }
    }

    public static void Write(string line)
    {
        lock (Gate)
        {
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(FilePath)!);
                File.AppendAllText(FilePath, $"{DateTime.Now:HH:mm:ss.fff} {line}\n", Encoding.UTF8);
            }
            catch { }
        }
    }
}
