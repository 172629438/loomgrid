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

/// <summary>逐日充放空间（/api/calc/spaces 的返回）。</summary>
public sealed record SpacesSegmentDto
{
    [JsonPropertyName("label")] public string Label { get; init; } = "";
    [JsonPropertyName("type")] public string Type { get; init; } = "";
    [JsonPropertyName("charge")] public List<double> Charge { get; init; } = new();
    [JsonPropertyName("discharge")] public List<double> Discharge { get; init; } = new();
}

public sealed record SpacesGroupDto
{
    [JsonPropertyName("asset")] public string Asset { get; init; } = "";
    [JsonPropertyName("month")] public string Month { get; init; } = "";
}

public sealed record SpacesResult
{
    [JsonPropertyName("group")] public SpacesGroupDto Group { get; init; } = new();
    [JsonPropertyName("date_labels")] public List<string> DateLabels { get; init; } = new();
    [JsonPropertyName("segments")] public List<SpacesSegmentDto> Segments { get; init; } = new();
}

public sealed record CapacityResult
{
    [JsonPropertyName("daily_demand")] public DailyDemandDto DailyDemand { get; init; } = new();
    [JsonPropertyName("daily_throughput")] public DailyThroughputDto DailyThroughput { get; init; } = new();
    [JsonPropertyName("capacity_suggestion_installed_kwh")] public double? CapacitySuggestionInstalledKwh { get; init; }
    [JsonPropertyName("hints")] public List<string> Hints { get; init; } = new();
}

// ───────────────────────── 月度表（跨分组按月份聚合） ─────────────────────────

/// <summary>月度表的请求。session_ids 是全部已加载的 session，不是单个。</summary>
public sealed record MonthlySpacesRequest
{
    [JsonPropertyName("session_ids")] public required List<string> SessionIds { get; init; }
    [JsonPropertyName("segments")] public List<CalcSegment>? Segments { get; init; }
    [JsonPropertyName("billing")] public CalcBilling? Billing { get; init; }
    [JsonPropertyName("mode")] public string Mode { get; init; } = "A";
    [JsonPropertyName("rated_power")] public double? RatedPower { get; init; }
    [JsonPropertyName("ignore_negative")] public bool IgnoreNegative { get; init; }
}

public sealed record MonthlySegmentValueDto
{
    [JsonPropertyName("label")] public string Label { get; init; } = "";
    [JsonPropertyName("value")] public double Value { get; init; }
}

/// <summary>
/// 月度表的一行。
/// <para><c>source_count &gt; 1</c> 表示该月有多个文件，**本行取后加载的那个**（前者被覆盖）。
/// 副标写「N 个文件同月，本行取后加载的那个」——不写「合并」，用户会以为数据相加。</para>
/// <para><c>resolution</c> 是该月原始点数，用来回答「这月和那月可比吗」。</para>
/// </summary>
public sealed record MonthlyMonthDto
{
    [JsonPropertyName("month")] public string Month { get; init; } = "";
    [JsonPropertyName("resolution")] public int Resolution { get; init; }
    [JsonPropertyName("asset")] public string? Asset { get; init; }
    [JsonPropertyName("sources")] public List<string> Sources { get; init; } = new();
    [JsonPropertyName("source_count")] public int SourceCount { get; init; }
    [JsonPropertyName("segments")] public List<MonthlySegmentValueDto> Segments { get; init; } = new();
    [JsonPropertyName("min_segment")] public double MinSegment { get; init; }
}

public sealed record MonthlySpacesResult
{
    [JsonPropertyName("months")] public List<MonthlyMonthDto> Months { get; init; } = new();
    [JsonPropertyName("columns")] public List<string> Columns { get; init; } = new();
    [JsonPropertyName("hidden_zero_segments")] public int HiddenZeroSegments { get; init; }
    [JsonPropertyName("min_segment_label")] public string MinSegmentLabel { get; init; } = "最小段空间";
    [JsonPropertyName("note")] public string Note { get; init; } = "";
}

// ───────────────────── 反向测算：容量 → 天数 ─────────────────────

/// <summary>
/// 入口 B：已知装了 X kWh，算能供几天。
/// **与入口 A（target_days → 容量）是两套独立输入，不是同一控件的两种显示。**
/// </summary>
public sealed record SatisfiedDaysRequest
{
    [JsonPropertyName("session_id")] public required string SessionId { get; init; }
    [JsonPropertyName("group")] public int Group { get; init; }
    [JsonPropertyName("segments")] public List<CalcSegment>? Segments { get; init; }
    [JsonPropertyName("billing")] public CalcBilling? Billing { get; init; }
    [JsonPropertyName("mode")] public string Mode { get; init; } = "A";
    [JsonPropertyName("rated_power")] public double? RatedPower { get; init; }
    [JsonPropertyName("ignore_negative")] public bool IgnoreNegative { get; init; }
    [JsonPropertyName("capacity_kwh")] public required double CapacityKwh { get; init; }
    [JsonPropertyName("dod_percent")] public double DodPercent { get; init; } = 95.0;
}

public sealed record MonthSatisfactionDto
{
    [JsonPropertyName("month")] public string Month { get; init; } = "";
    [JsonPropertyName("satisfied")] public int Satisfied { get; init; }
    [JsonPropertyName("total")] public int Total { get; init; }
}

/// <summary>
/// <para><c>satisfied_days</c>：按 DOD 折算 + 逐对判定（**与 Fusion 界面同口径，界面就显示这个**）。</para>
/// <para><c>satisfied_days_core</c>：不折算、各段相加后再比（与 core 一致）。两个数语义不同，不并排展示。</para>
/// </summary>
public sealed record SatisfiedDaysResult
{
    [JsonPropertyName("capacity_kwh")] public double CapacityKwh { get; init; }
    [JsonPropertyName("usable_kwh")] public double UsableKwh { get; init; }
    [JsonPropertyName("dod_percent")] public double DodPercent { get; init; }
    [JsonPropertyName("satisfied_days")] public int SatisfiedDays { get; init; }
    [JsonPropertyName("satisfied_days_core")] public int SatisfiedDaysCore { get; init; }
    [JsonPropertyName("total_days")] public int TotalDays { get; init; }
    [JsonPropertyName("equivalent_days")] public int EquivalentDays { get; init; }
    [JsonPropertyName("by_month")] public List<MonthSatisfactionDto> ByMonth { get; init; } = new();
    [JsonPropertyName("note")] public string Note { get; init; } = "";
}
