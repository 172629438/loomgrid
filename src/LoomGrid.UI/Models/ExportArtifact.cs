using System.ComponentModel;
using System.IO;
using System.Runtime.CompilerServices;
using Microsoft.UI.Xaml.Media.Imaging;

namespace LoomGrid.UI;

/// <summary>V6 清单里的一条产物。kind 由后端标注（chart / midwide），不靠扩展名猜。</summary>
public sealed class ExportArtifact : INotifyPropertyChanged
{
    public string Name { get; init; } = "";
    public string Kind { get; init; } = "chart";
    public string FullPath { get; init; } = "";
    public long SizeBytes { get; init; }

    public bool IsChart => Kind == "chart";

    private BitmapImage? _thumb;

    /// <summary>
    /// 缩略图。**不能用 file:// URI 直接绑 Source** —— unpackaged 应用里 Image 会把
    /// file:// 当网络请求，报 E_NETWORK_ERROR。必须用文件流加载后再赋值。
    /// </summary>
    public BitmapImage? Thumb
    {
        get => _thumb;
        set { _thumb = value; Raise(); }
    }

    public event PropertyChangedEventHandler? PropertyChanged;
    private void Raise([CallerMemberName] string? n = null)
        => PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(n));

    public string SizeText => SizeBytes switch
    {
        <= 0 => "—",
        < 1024 => $"{SizeBytes} B",
        < 1024 * 1024 => $"{SizeBytes / 1024.0:F0} KB",
        _ => $"{SizeBytes / 1024.0 / 1024.0:F1} MB",
    };

    public static ExportArtifact From(string outputDir, string name, string kind)
    {
        string full = Path.Combine(outputDir, name);
        long size = 0;
        try { size = new FileInfo(full).Length; } catch { }
        return new ExportArtifact { Name = name, Kind = kind, FullPath = full, SizeBytes = size };
    }
}

/// <summary>
/// 一次导出的汇总（V6 的输入）。由 V5 在导出完成后填充，或由 adopt 从目录构造。
/// </summary>
public sealed class ExportRunSummary
{
    public string TaskId { get; init; } = "";
    public string SourceFile { get; init; } = "";
    public string OutputDir { get; init; } = "";

    /// <summary>分组数。**不是产物数** —— 带宽表时产物数是它的两倍。</summary>
    public int TotalGroups { get; init; }
    public int GroupsSucceeded { get; init; }
    public int GroupsFailed { get; init; }

    /// <summary>产物总数（PNG + 宽表）。</summary>
    public int ArtifactCount => Artifacts.Count;

    public List<ExportArtifact> Artifacts { get; } = new();

    /// <summary>失败明细原文（未聚合）。</summary>
    public List<string> FailedMessages { get; } = new();

    public string? ReportName { get; init; }

    /// <summary>true = 从已有目录读出（adopt），不是本次导出的结果。</summary>
    public bool Adopted { get; init; }

    public IEnumerable<ExportArtifact> Charts => Artifacts.Where(a => a.IsChart);
    public IEnumerable<ExportArtifact> Midwides => Artifacts.Where(a => !a.IsChart);
}

/// <summary>失败归因的一组：同一「错误骨架」下的若干条。</summary>
public sealed class FailureGroup
{
    public string Skeleton { get; init; } = "";
    public List<string> Items { get; } = new();
    public int Count => Items.Count;
    public string Header => Count > 1 ? $"{Skeleton}（{Count} 项）" : Skeleton;
    public string Detail => string.Join("\n", Items);
}
