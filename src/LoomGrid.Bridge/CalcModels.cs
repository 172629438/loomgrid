using System.Text.Json;
using System.Text.Json.Serialization;

namespace LoomGrid.Bridge;

/// <summary>时段。start / end 是 **float 小时**（0.0 / 8.0），不是 "00:00" 字符串。</summary>
public sealed record CalcSegment
{
    [JsonPropertyName("start")] public required double Start { get; init; }
    [JsonPropertyName("end")] public required double End { get; init; }
    [JsonPropertyName("type")] public required string Type { get; init; }   // charge | discharge
    [JsonPropertyName("label")] public string? Label { get; init; }
}

public sealed record CalcBilling
{
    [JsonPropertyName("value")] public double Value { get; init; }
}

/// <summary>
/// /api/calc/capacity 的请求。
/// **mode A 要 billing.value、B/C 要 rated_power** —— 缺参数时该方向空间为 0 而不报错。
/// </summary>
public sealed record CapacityRequest
{
    [JsonPropertyName("session_id")] public required string SessionId { get; init; }
    [JsonPropertyName("group")] public int Group { get; init; }
    [JsonPropertyName("segments")] public List<CalcSegment>? Segments { get; init; }
    [JsonPropertyName("billing")] public CalcBilling? Billing { get; init; }
    [JsonPropertyName("mode")] public string Mode { get; init; } = "A";
    [JsonPropertyName("rated_power")] public double? RatedPower { get; init; }
    [JsonPropertyName("ignore_negative")] public bool IgnoreNegative { get; init; }
    [JsonPropertyName("basis")] public string Basis { get; init; } = "min_feasible";
    [JsonPropertyName("target_days")] public int? TargetDays { get; init; }
    [JsonPropertyName("dod_percent")] public double DodPercent { get; init; } = 95.0;
}

public sealed record SegmentDemandDto
{
    [JsonPropertyName("label")] public string Label { get; init; } = "";
    [JsonPropertyName("charge_kwh")] public double ChargeKwh { get; init; }
    [JsonPropertyName("discharge_kwh")] public double DischargeKwh { get; init; }
}

public sealed record DailyDemandDto
{
    [JsonPropertyName("basis")] public string Basis { get; init; } = "";
    [JsonPropertyName("value_kwh")] public double ValueKwh { get; init; }
    [JsonPropertyName("by_segment")] public List<SegmentDemandDto> BySegment { get; init; } = new();
}

public sealed record DailyThroughputDto
{
    [JsonPropertyName("total_charge_kwh")] public double TotalChargeKwh { get; init; }
    [JsonPropertyName("total_discharge_kwh")] public double TotalDischargeKwh { get; init; }
    [JsonPropertyName("days")] public int Days { get; init; }
    [JsonPropertyName("value_kwh_per_day")] public double ValueKwhPerDay { get; init; }
    [JsonPropertyName("min_side")] public string MinSide { get; init; } = "none";
    [JsonPropertyName("per_day")] public List<double> PerDay { get; init; } = new();
}

public sealed record CapacityResult
{
    [JsonPropertyName("daily_demand")] public DailyDemandDto DailyDemand { get; init; } = new();
    [JsonPropertyName("daily_throughput")] public DailyThroughputDto DailyThroughput { get; init; } = new();
    [JsonPropertyName("capacity_suggestion_installed_kwh")] public double? CapacitySuggestionInstalledKwh { get; init; }
    [JsonPropertyName("hints")] public List<string> Hints { get; init; } = new();
}
