namespace LoomGrid.UI;

/// <summary>
/// 六屏共享的工作区：V1 收集的文件、V2 产出的 session 与分组。
///
/// 为什么用共享对象而不是页面间传参：V1→V2→V3 沿途要带着同一批文件走，
/// 每屏都能改（移除、改映射、勾选），传值会变成「谁的副本才是真的」的问题。
/// 一个对象、一份状态，页面只是它的视图。
/// </summary>
public sealed class Workspace
{
    public static Workspace Current { get; } = new();

    /// <summary>V1 入列的文件（含 inspect 结果缓存）。</summary>
    public List<WorkFile> Files { get; } = new();

    /// <summary>V2 逐文件确认后写入。同一批文件共用一个 session（后端按路径扫描）。</summary>
    public string? SessionId { get; set; }

    /// <summary>V2 产出的分组清单（V3 左树的输入）。</summary>
    public List<LoomGrid.Bridge.GroupInfoDto> Groups { get; } = new();

    /// <summary>上次导出结果（V5 写完，V6 读）。</summary>
    public List<ExportRunSummary> LastRuns { get; } = new();

    /// <summary>已完成映射的文件数（V2 底部进度用）。</summary>
    public int MappedCount => Files.Count(f => f.SessionId is not null);

    public bool AllMapped => Files.Count > 0 && Files.All(f => f.SessionId is not null || f.State == FileState.Failed);

    public void Reset()
    {
        Files.Clear();
        SessionId = null;
        Groups.Clear();
    }
}
