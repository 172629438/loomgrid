using System.Text.Json;

namespace LoomGrid.Bridge;

public sealed record AllowPathsResult
{
    public List<string> Added { get; init; } = new();
    public List<string> Existing { get; init; } = new();
    public List<string> Rejected { get; init; } = new();
}

/// <summary>
/// 摄入相关接口的类型化封装。原始 JSON 解析一律交给 ApiClient，
/// 这里只做「字段 + 反序列化 + 错误语义」。
/// </summary>
public sealed class IngestApi
{
    private static readonly JsonSerializerOptions Opts = new()
    {
        PropertyNameCaseInsensitive = true,
    };

    private readonly ApiClient _api;

    public IngestApi(ApiClient api) => _api = api;

    /// <summary>
    /// 把用户显式选择/拖入的文件登记进后端白名单。
    /// 必须在 InspectAsync 之前调用 —— 否则后端会以 INGEST_PATH_FORBIDDEN 拒绝。
    /// </summary>
    public async Task<AllowPathsResult> AllowPathsAsync(
        IReadOnlyList<string> paths, CancellationToken ct = default)
    {
        JsonElement data = await _api
            .PostDataAsync(ApiPaths.IngestAllow, new { paths }, ct)
            .ConfigureAwait(false);

        return new AllowPathsResult
        {
            Added = ReadList(data, "added"),
            Existing = ReadList(data, "existing"),
            Rejected = ReadList(data, "rejected"),
        };
    }

    public async Task<InspectResult> InspectAsync(string path, CancellationToken ct = default)
    {
        JsonElement data = await _api
            .PostDataAsync(ApiPaths.IngestInspect, new { path }, ct)
            .ConfigureAwait(false);

        return data.Deserialize<InspectResult>(Opts)
               ?? throw new ApiException("inspect 返回了空数据");
    }

    /// <summary>
    /// 应用映射并扫描分组。mapping 直接传 SheetInfo.Suggest（它的形状就是 MappingSuggest）。
    /// </summary>
    public async Task<MappingResult> ApplyMappingAsync(
        string path, string sheet, SheetSuggest mapping, CancellationToken ct = default)
    {
        JsonElement data = await _api
            .PostDataAsync(ApiPaths.IngestMapping,
                new { path, sheet, mapping }, ct)
            .ConfigureAwait(false);

        return data.Deserialize<MappingResult>(Opts)
               ?? throw new ApiException("mapping 返回了空数据");
    }

    private static List<string> ReadList(JsonElement data, string name)
    {
        if (data.ValueKind != JsonValueKind.Object) return new List<string>();
        if (!data.TryGetProperty(name, out var arr) || arr.ValueKind != JsonValueKind.Array)
            return new List<string>();
        return arr.EnumerateArray()
            .Select(x => x.GetString())
            .Where(s => !string.IsNullOrEmpty(s))
            .Select(s => s!)
            .ToList();
    }
}
