using System.Text.Json;

namespace LoomGrid.Bridge;

/// <summary>
/// 测算接口封装。注意两个量纲：
///   daily_demand.value_kwh             kWh      「电池该装多大」
///   daily_throughput.value_kwh_per_day kWh/天   「一天能搬多少能量」
/// **不可互换、不可相除。**
/// </summary>
public sealed class CalcApi
{
    private static readonly JsonSerializerOptions Opts = new() { PropertyNameCaseInsensitive = true };

    private readonly ApiClient _api;

    public CalcApi(ApiClient api) => _api = api;

    public async Task<CapacityResult> CapacityAsync(CapacityRequest req, CancellationToken ct = default)
    {
        JsonElement data = await _api
            .PostDataAsync(ApiPaths.CalcCapacity, req, ct)
            .ConfigureAwait(false);

        return data.Deserialize<CapacityResult>(Opts)
               ?? throw new ApiException("容量测算返回了空数据");
    }

    /// <summary>逐日充放空间。date_labels 是真实日期（如 2026-01-01），不是索引。</summary>
    public async Task<SpacesResult> SpacesAsync(CapacityRequest req, CancellationToken ct = default)
    {
        JsonElement data = await _api
            .PostDataAsync(ApiPaths.CalcSpaces, req, ct)
            .ConfigureAwait(false);

        return data.Deserialize<SpacesResult>(Opts)
               ?? throw new ApiException("充放空间返回了空数据");
    }

    /// <summary>
    /// 跨分组按月份聚合的月度表。
    /// 同月份后者覆盖前者（与 Fusion 一致），<c>source_count &gt; 1</c> 时要在界面上标明。
    /// </summary>
    public async Task<MonthlySpacesResult> MonthlySpacesAsync(
        MonthlySpacesRequest req, CancellationToken ct = default)
    {
        JsonElement data = await _api
            .PostDataAsync(ApiPaths.CalcMonthlySpaces, req, ct)
            .ConfigureAwait(false);

        return data.Deserialize<MonthlySpacesResult>(Opts)
               ?? throw new ApiException("月度空间返回了空数据");
    }
}
