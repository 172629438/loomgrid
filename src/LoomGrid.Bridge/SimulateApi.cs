using System.Text.Json;

namespace LoomGrid.Bridge;

/// <summary>
/// 负荷模拟接口：25 个行业模板 + 参数化曲线生成。
/// <para><b>名字陷阱</b>：本接口用的是 <c>services/loadsim.py</c> 的 25 个模板；
/// <c>algorithms/loadgen.py</c> 里另有 10 个行业的一套引擎，曲线形状不同，不能混用。</para>
/// <para><b>seed 契约</b>：预览与「应用」必须传同一个 seed，否则两条曲线不一致。
/// 不传时端点自己生成一个并回显在 <c>applied_seed</c>，用户凭它可复现同一条。</para>
/// </summary>
public sealed class SimulateApi
{
    private static readonly JsonSerializerOptions Opts = new() { PropertyNameCaseInsensitive = true };

    private readonly ApiClient _api;

    public SimulateApi(ApiClient api) => _api = api;

    /// <summary>25 个模板的展示用参数（来源标注在 description，界面上要显示出来）。</summary>
    public async Task<List<SimTemplate>> TemplatesAsync(CancellationToken ct = default)
    {
        JsonElement data = await _api.GetDataAsync(ApiPaths.SimulateTemplates, ct)
            .ConfigureAwait(false);

        var wrapper = data.Deserialize<TemplateListWrapper>(Opts);
        return wrapper?.Templates ?? new List<SimTemplate>();
    }

    /// <summary>按模板生成曲线。</summary>
    public async Task<SimLoadResult> GenerateAsync(SimLoadRequest req, CancellationToken ct = default)
    {
        JsonElement data = await _api.PostDataAsync(ApiPaths.SimulateLoad, req, ct)
            .ConfigureAwait(false);

        return data.Deserialize<SimLoadResult>(Opts)
               ?? throw new ApiException("负荷模拟返回了空数据");
    }
}

public sealed class TemplateListWrapper
{
    public int Count { get; set; }
    public List<SimTemplate> Templates { get; set; } = new();
}

public sealed class SimTemplate
{
    [System.Text.Json.Serialization.JsonPropertyName("key")]
    public string Key { get; set; } = "";

    [System.Text.Json.Serialization.JsonPropertyName("name")]
    public string Name { get; set; } = "";

    [System.Text.Json.Serialization.JsonPropertyName("daily_load_factor")]
    public double DailyLoadFactor { get; set; }

    /// <summary>峰谷差率。</summary>
    [System.Text.Json.Serialization.JsonPropertyName("peak_valley_ratio")]
    public double PeakValleyRatio { get; set; }

    [System.Text.Json.Serialization.JsonPropertyName("weekend_factor")]
    public double WeekendFactor { get; set; }

    /// <summary>one_shift / two_shift / three_shift / commercial。</summary>
    [System.Text.Json.Serialization.JsonPropertyName("schedule")]
    public string Schedule { get; set; } = "";

    [System.Text.Json.Serialization.JsonPropertyName("morning_peak")]
    public List<double> MorningPeak { get; set; } = new();

    [System.Text.Json.Serialization.JsonPropertyName("evening_peak")]
    public List<double> EveningPeak { get; set; } = new();

    [System.Text.Json.Serialization.JsonPropertyName("noon_valley")]
    public List<double> NoonValley { get; set; } = new();

    /// <summary>数据来源标注（如「贵州电网(2020)·电解铝连续生产」）—— 界面上必须显示。</summary>
    [System.Text.Json.Serialization.JsonPropertyName("description")]
    public string Description { get; set; } = "";

    /// <summary>班制的中文说法，用于界面展示。</summary>
    public string ScheduleText => Schedule switch
    {
        "one_shift" => "单班",
        "two_shift" => "双班",
        "three_shift" => "三班连续",
        "commercial" => "商业时段",
        _ => Schedule,
    };

    public override string ToString() => Name;
}

public sealed class SimLoadRequest
{
    [System.Text.Json.Serialization.JsonPropertyName("industry")]
    public string Industry { get; set; } = "电子制造";

    [System.Text.Json.Serialization.JsonPropertyName("avg_power_kw")]
    public double AvgPowerKw { get; set; } = 500;

    /// <summary>null = 用模板自己的值。不传这三个就等于「完全按模板」。⚠️ 不能填 0 冒充 null。</summary>
    [System.Text.Json.Serialization.JsonPropertyName("daily_load_factor")]
    public double? DailyLoadFactor { get; set; }

    [System.Text.Json.Serialization.JsonPropertyName("peak_valley_ratio")]
    public double? PeakValleyRatio { get; set; }

    [System.Text.Json.Serialization.JsonPropertyName("weekend_factor")]
    public double? WeekendFactor { get; set; }

    [System.Text.Json.Serialization.JsonPropertyName("noise_std")]
    public double NoiseStd { get; set; } = 0.05;

    [System.Text.Json.Serialization.JsonPropertyName("pv_kw")]
    public double PvKw { get; set; }

    [System.Text.Json.Serialization.JsonPropertyName("n_days")]
    public int NDays { get; set; } = 365;

    [System.Text.Json.Serialization.JsonPropertyName("n_points")]
    public int NPoints { get; set; } = 96;

    /// <summary>预览与应用必须同 seed。null 时服务端生成并回显。</summary>
    [System.Text.Json.Serialization.JsonPropertyName("seed")]
    public int? Seed { get; set; }
}

public sealed class SimLoadResult
{
    [System.Text.Json.Serialization.JsonPropertyName("industry")]
    public string Industry { get; set; } = "";

    [System.Text.Json.Serialization.JsonPropertyName("requested_industry")]
    public string RequestedIndustry { get; set; } = "";

    [System.Text.Json.Serialization.JsonPropertyName("load_array")]
    public List<List<double>> LoadArray { get; set; } = new();

    [System.Text.Json.Serialization.JsonPropertyName("time_labels")]
    public List<string> TimeLabels { get; set; } = new();

    [System.Text.Json.Serialization.JsonPropertyName("date_labels")]
    public List<string> DateLabels { get; set; } = new();

    [System.Text.Json.Serialization.JsonPropertyName("shape")]
    public List<int> Shape { get; set; } = new();

    /// <summary>本次实际使用的随机种子。想复现同一条曲线，下次传它。</summary>
    [System.Text.Json.Serialization.JsonPropertyName("applied_seed")]
    public int AppliedSeed { get; set; }

    [System.Text.Json.Serialization.JsonPropertyName("stats")]
    public SimStats Stats { get; set; } = new();
}

public sealed class SimStats
{
    public double Min { get; set; }
    public double Max { get; set; }
    public double Mean { get; set; }
}
