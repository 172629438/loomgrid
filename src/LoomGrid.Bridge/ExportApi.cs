using System.Text.Json;

namespace LoomGrid.Bridge;

/// <summary>
/// 导出接口封装。
///
/// 语义要点（读契约得来，不是猜的）：
///   · POST /run 是**异步受理**：返回 task_id 不代表导出成功，
///     必须轮询 /status?task_id= 或订阅 WS，看 status 变 done 且 result.succeeded。
///   · /status 与 /report 的参数是 **task_id**，不是 session_id。
///   · 一次 /run 只处理 **一个 session**；多文件（多 session）要遍历调用。
/// </summary>
public sealed class ExportApi
{
    private static readonly JsonSerializerOptions Opts = new() { PropertyNameCaseInsensitive = true };

    private readonly ApiClient _api;

    public ExportApi(ApiClient api) => _api = api;

    /// <summary>预览每个分组将生成的标题与文件名（清洗由后端做，前端不重写）。</summary>
    public async Task<ExportPreviewResult> PreviewAsync(
        string sessionId, IReadOnlyList<int>? groups,
        string titleTemplate, string filenameTemplate, CancellationToken ct = default)
    {
        JsonElement data = await _api.PostDataAsync(
            "api/export/preview",
            new
            {
                session_id = sessionId,
                groups,
                title_template = titleTemplate,
                filename_template = filenameTemplate,
            },
            ct).ConfigureAwait(false);

        return data.Deserialize<ExportPreviewResult>(Opts)
               ?? throw new ApiException("导出预览返回了空数据");
    }

    /// <summary>提交一个 session 的导出任务。返回 task_id 仅代表受理。</summary>
    public async Task<ExportRunResult> RunAsync(ExportRunRequest request, CancellationToken ct = default)
    {
        JsonElement data = await _api
            .PostDataAsync("api/export/run", request, ct)
            .ConfigureAwait(false);

        return data.Deserialize<ExportRunResult>(Opts)
               ?? throw new ApiException("导出任务提交后没有返回 task_id");
    }

    /// <summary>轮询任务状态。task_id 不是 session_id。</summary>
    public async Task<ExportStatusResult> StatusAsync(string taskId, CancellationToken ct = default)
    {
        JsonElement data = await _api
            .GetDataAsync($"api/export/status?task_id={Uri.EscapeDataString(taskId)}", ct)
            .ConfigureAwait(false);

        return data.Deserialize<ExportStatusResult>(Opts)
               ?? throw new ApiException("导出状态返回了空数据");
    }

    /// <summary>HTML 报告的相对地址（由宿主决定怎么打开）。</summary>
    public static string ReportPath(string taskId)
        => $"api/export/report?task_id={Uri.EscapeDataString(taskId)}";
}
