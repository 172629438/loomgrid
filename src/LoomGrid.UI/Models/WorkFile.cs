using System.ComponentModel;
using System.IO;
using System.Runtime.CompilerServices;
using LoomGrid.Bridge;
using Microsoft.UI;
using Microsoft.UI.Xaml.Media;
using Windows.UI;

namespace LoomGrid.UI;

/// <summary>文件在 V1 列表里的状态。顺序即处理流程的先后。</summary>
public enum FileState
{
    Pending,        // 刚加入，还没识别
    Inspecting,     // inspect 请求进行中
    Ready,          // 可继续
    NeedsConfirm,   // 能继续，但要人看一眼
    Failed,         // 无法继续
}

/// <summary>
/// 状态判定的唯一实现。
///
/// 为什么单独写在这里：配色不能凭感觉。哪个文件该绿、哪个该黄，
/// 判据必须能指到具体字段——否则同一个文件在两台机器上会显示成两种颜色。
///
/// 判据（与协作方确认）：
///   Ready         confidence.score >= 0.9 且 format_type 明确
///   NeedsConfirm  confidence.score < 0.9，或 format_type 缺失，或 hints 里 guessed_year
///   Failed        请求失败（含文件读不出、路径不允许）
/// </summary>
public static class FileStateJudge
{
    public const double ReadyThreshold = 0.9;

    private static readonly string[] KnownFormats =
        { "wide", "wide_transposed", "long", "accumulated" };

    public static (FileState State, string Detail) Judge(InspectResult result)
    {
        if (result.Sheets.Count == 0)
            return (FileState.Failed, "这个文件里没有可读的工作表。");

        var usable = result.Sheets.FirstOrDefault(s => s.Suggest.HasLoadCol)
                     ?? result.Sheets[0];

        var reasons = new List<string>();

        bool hasYearGuess = result.Hints is not null
                            && result.Hints.TryGetValue("guessed_year", out var gy)
                            && gy.Count > 0;
        if (hasYearGuess)
        {
            result.Hints!.TryGetValue("guessed_year", out var hint);
            reasons.Add(hint?.Message ?? "有日期缺年份，已按今年处理。");
        }

        string? fmt = usable.Suggest.FormatType;
        if (fmt is null || !KnownFormats.Contains(fmt))
        {
            reasons.Add("表格结构没能确定（宽表 / 长表 / 转置 / 累积 都没把握）。");
        }

        if (usable.Confidence.Score < ReadyThreshold)
        {
            string why = usable.Confidence.Reasons.Count > 0
                ? string.Join("；", usable.Confidence.Reasons)
                : "识别把握不足。";
            reasons.Add($"识别可信度 {usable.Confidence.Score:P0}：{why}");
        }

        if (reasons.Count > 0)
            return (FileState.NeedsConfirm, string.Join("\n", reasons));

        string unit = usable.Suggest.LoadCol.ValueKind == System.Text.Json.JsonValueKind.Array
            ? $"{usable.Suggest.LoadCol.GetArrayLength()} 个负荷列"
            : "1 个负荷列";
        return (FileState.Ready, $"工作表「{usable.Name}」，{unit}，可信度 {usable.Confidence.Score:P0}。");
    }
}

/// <summary>V1 列表里的一行：一个待处理的源文件。</summary>
public sealed class WorkFile : INotifyPropertyChanged
{
    private static readonly SolidColorBrush ReadyBrush = new(Color.FromArgb(255, 0x2E, 0xA0, 0x43));
    private static readonly SolidColorBrush WarnBrush = new(Color.FromArgb(255, 0xC9, 0xA2, 0x27));
    private static readonly SolidColorBrush FailBrush = new(Color.FromArgb(255, 0xD1, 0x34, 0x38));
    private static readonly SolidColorBrush IdleBrush = new(Colors.Gray);

    public string FullPath { get; }
    public string FileName { get; }
    public long SizeBytes { get; }
    public string SizeText => SizeBytes switch
    {
        < 1024 => $"{SizeBytes} B",
        < 1024 * 1024 => $"{SizeBytes / 1024.0:F0} KB",
        _ => $"{SizeBytes / 1024.0 / 1024.0:F1} MB",
    };

    private FileState _state = FileState.Pending;
    private string _detail = "";
    private bool _isSelected;

    // mapping 之后才有的列
    private string _asset = "—";
    private string _month = "—";
    private string _timePoints = "—";
    private string _minLoad = "—";
    private string _maxLoad = "—";

    public WorkFile(string fullPath)
    {
        FullPath = fullPath;
        FileName = Path.GetFileName(fullPath);
        SizeBytes = TryGetSize(fullPath);
    }

    private static long TryGetSize(string path)
    {
        try { return new FileInfo(path).Length; } catch { return 0; }
    }

    public FileState State
    {
        get => _state;
        set
        {
            if (_state == value) return;
            _state = value;
            Raise();
            Raise(nameof(StatusText));
            Raise(nameof(StatusBrush));
            Raise(nameof(CanContinue));
        }
    }

    public string StatusText => State switch
    {
        FileState.Pending => "待识别",
        FileState.Inspecting => "识别中",
        FileState.Ready => "就绪",
        FileState.NeedsConfirm => "需确认",
        FileState.Failed => "失败",
        _ => "—",
    };

    public SolidColorBrush StatusBrush => State switch
    {
        FileState.Ready => ReadyBrush,
        FileState.NeedsConfirm => WarnBrush,
        FileState.Failed => FailBrush,
        _ => IdleBrush,
    };

    /// <summary>失败与需确认的文件仍然可以继续（需确认）或不能（失败）。</summary>
    public bool CanContinue => State is FileState.Ready or FileState.NeedsConfirm;

    public string Detail
    {
        get => _detail;
        set { _detail = value; Raise(); Raise(nameof(HasDetail)); }
    }

    public bool HasDetail => !string.IsNullOrWhiteSpace(_detail);

    public string Asset { get => _asset; set { _asset = value; Raise(); } }
    public string Month { get => _month; set { _month = value; Raise(); } }
    public string TimePoints { get => _timePoints; set { _timePoints = value; Raise(); } }
    public string MinLoad { get => _minLoad; set { _minLoad = value; Raise(); } }
    public string MaxLoad { get => _maxLoad; set { _maxLoad = value; Raise(); } }

    public bool IsSelected
    {
        get => _isSelected;
        set { _isSelected = value; Raise(); }
    }

    // inspect 结果缓存（进入 V2 时不用重新请求）
    public InspectResult? Inspect { get; set; }
    public SheetInfo? ChosenSheet { get; set; }

    private string? _sessionId;

    /// <summary>V2 确认映射后写入。null 表示尚未映射。</summary>
    public string? SessionId
    {
        get => _sessionId;
        set
        {
            _sessionId = value;
            Raise();
            Raise(nameof(MappingText));
        }
    }

    /// <summary>映射状态（V2 列表显示用）。</summary>
    public string MappingText => SessionId is null ? "待映射" : "已确认";

    /// <summary>V2 产出：该文件扫描出的分组。</summary>
    public List<GroupInfoDto> Groups { get; } = new();

    public event PropertyChangedEventHandler? PropertyChanged;

    private void Raise([CallerMemberName] string? name = null)
        => PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
}
