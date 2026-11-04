using System.Text.Json;
using System.Text.Json.Serialization;

namespace LoomGrid.Bridge;

/// <summary>
/// GET /api/preview 的返回（format=json）。
/// <para><c>date_labels</c> 是真实日期（2026-01-15），<c>time_labels</c> 是时刻（00:00）。</para>
/// <para><c>load_array</c> 形状是 (天 × 点)。行数可能小于 date_labels 的长度，按下标对齐。</para>
/// </summary>
public sealed record PreviewResult
{
    [JsonPropertyName("date_labels")] public List<string> DateLabels { get; init; } = new();
    [JsonPropertyName("time_labels")] public List<string> TimeLabels { get; init; } = new();
    [JsonPropertyName("load_array")] public List<List<double>> LoadArray { get; init; } = new();
    /// <summary>后端返回的是数组 [天, 点]，不是字符串 —— 写成 string 会直接反序列化失败。</summary>
    [JsonPropertyName("shape")] public List<int> Shape { get; init; } = new();

    public string ShapeText => Shape.Count >= 2 ? $"{Shape[0]}×{Shape[1]}" : "";
}

public sealed class PreviewApi
{
    private static readonly JsonSerializerOptions Opts = new() { PropertyNameCaseInsensitive = true };

    private readonly ApiClient _api;

    public PreviewApi(ApiClient api) => _api = api;

    /// <summary>
    /// 取某个分组的宽表数据。
    /// <para>
    /// 用 <c>format=json</c> 而不是 <c>bin</c>：bin 更快（省一次序列化），
    /// 但要手动解析响应头 + 二进制布局；json 在当前数据量（约 3 千点）下
    /// 传输约 60 KB，够用。**bin 是性能优化项，不是正确性项**——数据量上到十万点时再切。
    /// </para>
    /// </summary>
    public async Task<PreviewResult> GetAsync(string sessionId, int group, CancellationToken ct = default)
    {
        string path = $"{ApiPaths.Preview}?session_id={Uri.EscapeDataString(sessionId)}" +
                      $"&group={group}&format=json";
        JsonElement data = await _api.GetDataAsync(path, ct).ConfigureAwait(false);
        return data.Deserialize<PreviewResult>(Opts)
               ?? throw new ApiException("预览数据为空");
    }
}
