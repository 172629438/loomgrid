namespace LoomGrid.Bridge;

/// <summary>
/// 光伏返送（负荷为负 = 倒送）详情。
/// <para><b>没有「可存电量」字段</b>：Fusion 的 PVFeedbackDialog 也只有两样——
/// 逐日返送电量、最大返送功率。不要自己造一个。</para>
/// <para><b>时间权重是 PV 专用的整格差分</b>，与 spaces 的 seg_dt 不同（单点分配不同，
/// 总量对齐）。所以这个数不能拿去和充电空间互相验证。</para>
/// </summary>
public sealed record PvFeedbackRequest
{
    [System.Text.Json.Serialization.JsonPropertyName("session_id")]
    public required string SessionId { get; init; }

    [System.Text.Json.Serialization.JsonPropertyName("group")]
    public int Group { get; init; }

    /// <summary>倍率：先乘负荷再统计。用户调倍率时返送量要跟着变，漏乘会让数字停在旧值。</summary>
    [System.Text.Json.Serialization.JsonPropertyName("multiplier")]
    public double Multiplier { get; init; } = 1.0;
}

public sealed record PvFeedbackResult
{
    [System.Text.Json.Serialization.JsonPropertyName("total_energy_kwh")]
    public double TotalEnergyKwh { get; init; }

    [System.Text.Json.Serialization.JsonPropertyName("max_power_kw")]
    public double MaxPowerKw { get; init; }

    /// <summary>负值点数。为 0 时界面直说「本月无返送」，不画空表。</summary>
    [System.Text.Json.Serialization.JsonPropertyName("negative_points")]
    public int NegativePoints { get; init; }

    [System.Text.Json.Serialization.JsonPropertyName("has_negative")]
    public bool HasNegative { get; init; }

    [System.Text.Json.Serialization.JsonPropertyName("daily")]
    public List<PvDailyDto> Daily { get; init; } = new();

    [System.Text.Json.Serialization.JsonPropertyName("multiplier")]
    public double Multiplier { get; init; }
}

public sealed record PvDailyDto
{
    [System.Text.Json.Serialization.JsonPropertyName("date")]
    public string Date { get; init; } = "";

    [System.Text.Json.Serialization.JsonPropertyName("energy_kwh")]
    public double EnergyKwh { get; init; }

    [System.Text.Json.Serialization.JsonPropertyName("max_power_kw")]
    public double MaxPowerKw { get; init; }
}
