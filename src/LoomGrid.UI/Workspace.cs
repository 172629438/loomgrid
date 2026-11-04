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

    // ── 负荷模拟（V4）──────────────────────────────────────
    // 语义是「切换」不是「替换」：原真实 session 一直不动，只多存一个模拟 session 的 id。
    // 所以「恢复原始」= 把 SessionId 改回 OriginalSessionId，**不能删模拟 session** ——
    // 删了用户就再回不到那条模拟曲线了（Fusion 也是这么做的）。

    /// <summary>进入模拟前的真实 session。第一次应用模拟时记录，之后不再改。</summary>
    public string? OriginalSessionId { get; set; }

    /// <summary>当前生效的模拟 session。为 null 表示没用模拟。</summary>
    public string? SimulatedSessionId { get; set; }

    public string? SimOriginIndustry { get; set; }

    /// <summary>生成时的 applied_seed。界面上以 #424242 形式常态显示，不藏 tooltip。</summary>
    public int? SimOriginSeed { get; set; }

    // 模拟分组的元信息：V4 的分组列表原来只从 Files 构建，模拟 session 不在 Files 里，
    // 所以这几个字段是 V4 能把「模拟」当成一个分组显示出来的唯一依据。
    public string? SimAsset { get; set; }
    public string? SimMonth { get; set; }
    public int SimDays { get; set; }
    public int SimPoints { get; set; }

    /// <summary>当前是否用的是模拟数据。</summary>
    public bool IsSimulated =>
        SimulatedSessionId is not null && SessionId == SimulatedSessionId;

    /// <summary>切回原始数据（模拟 session 保留，可再切回来）。</summary>
    public bool RevertToOriginal()
    {
        if (OriginalSessionId is null) return false;
        SessionId = OriginalSessionId;
        return true;
    }

    /// <summary>再切回模拟数据（「恢复原始」之后想回头时用）。</summary>
    public bool ReuseSimulation()
    {
        if (SimulatedSessionId is null) return false;
        SessionId = SimulatedSessionId;
        return true;
    }

    /// <summary>已完成映射的文件数（V2 底部进度用）。</summary>
    public int MappedCount => Files.Count(f => f.SessionId is not null);

    public bool AllMapped => Files.Count > 0 && Files.All(f => f.SessionId is not null || f.State == FileState.Failed);

    public void Reset()
    {
        Files.Clear();
        SessionId = null;
        Groups.Clear();
        OriginalSessionId = null;
        SimulatedSessionId = null;
        SimOriginIndustry = null;
        SimOriginSeed = null;
        SimAsset = null;
        SimMonth = null;
        SimDays = 0;
        SimPoints = 0;
    }
}
