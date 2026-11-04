using System.Text.Json;
using System.Text.Json.Serialization;

namespace LoomGrid.Bridge;

/// <summary>inspect 返回的建议映射。字段名与 api/schemas.py 的 MappingSuggest 一一对应。</summary>
public sealed record SheetSuggest
{
    [JsonPropertyName("date_col")] public string? DateCol { get; init; }
    [JsonPropertyName("time_col")] public string? TimeCol { get; init; }

    /// <summary>可能是字符串，也可能是数组（多列负荷）——保持原始 JSON 由调用方判断。</summary>
    [JsonPropertyName("load_col")] public JsonElement LoadCol { get; init; }

    [JsonPropertyName("asset_col")] public string? AssetCol { get; init; }
    [JsonPropertyName("format_type")] public string? FormatType { get; init; }
    [JsonPropertyName("header_row")] public int HeaderRow { get; init; }
    [JsonPropertyName("transpose")] public bool Transpose { get; init; }

    public bool HasLoadCol => LoadCol.ValueKind switch
    {
        JsonValueKind.String => !string.IsNullOrWhiteSpace(LoadCol.GetString()),
        JsonValueKind.Array => LoadCol.GetArrayLength() > 0,
        _ => false,
    };
}

public sealed record SheetConfidence
{
    [JsonPropertyName("score")] public double Score { get; init; }
    [JsonPropertyName("reasons")] public List<string> Reasons { get; init; } = new();
}

public sealed record SheetInfo
{
    [JsonPropertyName("name")] public string Name { get; init; } = "";
    [JsonPropertyName("columns")] public List<string> Columns { get; init; } = new();
    [JsonPropertyName("rows_sampled")] public int RowsSampled { get; init; }
    [JsonPropertyName("suggest")] public SheetSuggest Suggest { get; init; } = new();
    [JsonPropertyName("confidence")] public SheetConfidence Confidence { get; init; } = new();
    [JsonPropertyName("yearless_samples")] public List<string> YearlessSamples { get; init; } = new();
}

public sealed record GuessedYear
{
    [JsonPropertyName("value")] public int Value { get; init; }
    [JsonPropertyName("count")] public int Count { get; init; }
    [JsonPropertyName("samples")] public List<string> Samples { get; init; } = new();
    [JsonPropertyName("message")] public string Message { get; init; } = "";
}

public sealed record InspectResult
{
    [JsonPropertyName("path")] public string Path { get; init; } = "";
    [JsonPropertyName("sheets")] public List<SheetInfo> Sheets { get; init; } = new();
    [JsonPropertyName("hints")] public Dictionary<string, GuessedYear>? Hints { get; init; }
}

public sealed record GroupInfoDto
{
    [JsonPropertyName("asset")] public string Asset { get; init; } = "";
    [JsonPropertyName("month")] public string Month { get; init; } = "";
    [JsonPropertyName("date_count")] public int DateCount { get; init; }
    [JsonPropertyName("time_points")] public int TimePoints { get; init; }
    [JsonPropertyName("min_val")] public double? MinVal { get; init; }
    [JsonPropertyName("max_val")] public double? MaxVal { get; init; }
}

public sealed record MappingResult
{
    [JsonPropertyName("groups")] public List<GroupInfoDto> Groups { get; init; } = new();
    [JsonPropertyName("session_id")] public string? SessionId { get; init; }
    [JsonPropertyName("hints")] public Dictionary<string, GuessedYear>? Hints { get; init; }
}
