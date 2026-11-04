using System.Text.Json;
using System.Text.Json.Serialization;

namespace LoomGrid.Bridge;

/// <summary>
/// 导出相关请求/响应模型。字段名与 api/routes_export.py 一一对应，
/// 这里不猜键名——契约在 Python 侧的 pydantic 模型里。
/// </summary>
public sealed record ExportPreviewItem
{
    [JsonPropertyName("group_index")] public int GroupIndex { get; init; }
    [JsonPropertyName("asset")] public string? Asset { get; init; }
    [JsonPropertyName("month")] public string? Month { get; init; }
    [JsonPropertyName("title")] public string? Title { get; init; }
    [JsonPropertyName("filename")] public string? Filename { get; init; }
}

public sealed record ExportPreviewResult
{
    [JsonPropertyName("items")] public List<ExportPreviewItem> Items { get; init; } = new();
    [JsonPropertyName("total")] public int Total { get; init; }
}

/// <summary>
/// 分组级参数覆盖。字段为 null 时用请求里的全局值。
/// 对应 api/routes_export.py 的 GroupOverride。
/// </summary>
public sealed record GroupOverride
{
    [JsonPropertyName("index")] public required int Index { get; init; }
    [JsonPropertyName("multiplier")] public double? Multiplier { get; init; }
    [JsonPropertyName("billing")] public Dictionary<string, object?>? Billing { get; init; }
    [JsonPropertyName("plot_options")] public Dictionary<string, object?>? PlotOptions { get; init; }
}

/// <summary>
/// POST /api/export/run 的请求体。
/// **注意**：multiplier / billing / plot_options 是「整批一个值」；
/// 逐行不同值靠 GroupOverrides（不传则完全等同整批行为）。
/// </summary>
public sealed record ExportRunRequest
{
    [JsonPropertyName("session_id")] public required string SessionId { get; init; }
    [JsonPropertyName("output_dir")] public required string OutputDir { get; init; }

    /// <summary>要导出的分组序号；null = 全部。</summary>
    [JsonPropertyName("groups")] public List<int>? Groups { get; init; }

    [JsonPropertyName("title_template")] public string TitleTemplate { get; init; } = DefaultTemplate;
    [JsonPropertyName("filename_template")] public string FilenameTemplate { get; init; } = DefaultTemplate;

    [JsonPropertyName("multiplier")] public double Multiplier { get; init; } = 1.0;
    [JsonPropertyName("plot_options")] public Dictionary<string, object?>? PlotOptions { get; init; }
    [JsonPropertyName("billing")] public Dictionary<string, object?>? Billing { get; init; }
    [JsonPropertyName("price_periods")] public List<object?>? PricePeriods { get; init; }
    [JsonPropertyName("export_midwide")] public bool ExportMidwide { get; init; }

    /// <summary>分组级覆盖（可选）。不传 = 完全等同原有整批行为。</summary>
    [JsonPropertyName("group_overrides")] public List<GroupOverride>? GroupOverrides { get; init; }

    /// <summary>与 services/export.py 的 DEFAULT_TITLE_TEMPLATE 保持一致。</summary>
    public const string DefaultTemplate = "{table_no}_{year_month}_负荷曲线";
}

public sealed record ExportRunResult
{
    [JsonPropertyName("task_id")] public string? TaskId { get; init; }
    [JsonPropertyName("accepted")] public bool Accepted { get; init; }
    [JsonPropertyName("total")] public int Total { get; init; }
    [JsonPropertyName("ws")] public string? WebSocket { get; init; }
    [JsonPropertyName("status_url")] public string? StatusUrl { get; init; }
}

public sealed record ExportStatusResult
{
    [JsonPropertyName("task_id")] public string? TaskId { get; init; }
    [JsonPropertyName("status")] public string? Status { get; init; }
    [JsonPropertyName("result")] public JsonElement Result { get; init; }
    [JsonPropertyName("error")] public string? Error { get; init; }
    [JsonPropertyName("events")] public int Events { get; init; }

    public bool IsDone => Status is "done" or "error";

    public int Succeeded => Result.ValueKind == JsonValueKind.Object
        && Result.TryGetProperty("succeeded", out var v) && v.ValueKind == JsonValueKind.Number
        ? v.GetInt32() : 0;

    public List<string> Files
    {
        get
        {
            if (Result.ValueKind != JsonValueKind.Object) return new();
            if (!Result.TryGetProperty("files", out var arr) || arr.ValueKind != JsonValueKind.Array)
                return new();
            return arr.EnumerateArray()
                .Select(x => x.GetString())
                .Where(s => !string.IsNullOrEmpty(s))
                .Select(s => s!)
                .ToList();
        }
    }

    public string? OutputDir => Result.ValueKind == JsonValueKind.Object
        && Result.TryGetProperty("output_dir", out var v) && v.ValueKind == JsonValueKind.String
        ? v.GetString() : null;
}
