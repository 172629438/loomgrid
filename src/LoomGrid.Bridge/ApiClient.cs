using System.Net.Http;
using System.Text;
using System.Text.Json;

namespace LoomGrid.Bridge;

/// <summary>后端返回结构化错误或 HTTP 失败时抛出。</summary>
public sealed class ApiException : Exception
{
    public string? Code { get; }
    public int? StatusCode { get; }

    public ApiException(string message, string? code = null, int? statusCode = null, Exception? inner = null)
        : base(message, inner)
    {
        Code = code;
        StatusCode = statusCode;
    }
}

/// <summary>
/// 后端 HTTP 客户端。统一解析 { ok, data, error } 包络：
/// ok=true 返回 data；ok=false 抛 ApiException（带 code 与中文 message）。
/// 超时给足（导出/测算可能跑几十秒），不在这里做业务重试。
/// </summary>
public sealed class ApiClient : IDisposable
{
    private static readonly JsonSerializerOptions JsonOpts = new(JsonSerializerDefaults.Web);

    private readonly HttpClient _http;

    public Uri BaseAddress { get; }

    public ApiClient(Uri baseAddress, TimeSpan? timeout = null)
    {
        BaseAddress = baseAddress;
        _http = new HttpClient
        {
            BaseAddress = baseAddress,
            Timeout = timeout ?? TimeSpan.FromMinutes(10),
        };
    }

    public async Task<JsonElement> GetDataAsync(string path, CancellationToken ct = default)
    {
        using var req = new HttpRequestMessage(HttpMethod.Get, path);
        return await SendAsync(req, ct).ConfigureAwait(false);
    }

    public async Task<JsonElement> PostDataAsync<T>(string path, T payload, CancellationToken ct = default)
    {
        string body = JsonSerializer.Serialize(payload, JsonOpts);
        using var req = new HttpRequestMessage(HttpMethod.Post, path)
        {
            Content = new StringContent(body, Encoding.UTF8, "application/json"),
        };
        return await SendAsync(req, ct).ConfigureAwait(false);
    }

    /// <summary>拿原始响应文本（诊断用，不经包络校验）。</summary>
    public async Task<string> GetRawAsync(string path, CancellationToken ct = default)
    {
        using var resp = await _http.GetAsync(path, ct).ConfigureAwait(false);
        return await resp.Content.ReadAsStringAsync(ct).ConfigureAwait(false);
    }

    private async Task<JsonElement> SendAsync(HttpRequestMessage req, CancellationToken ct)
    {
        HttpResponseMessage resp;
        try
        {
            resp = await _http.SendAsync(req, ct).ConfigureAwait(false);
        }
        catch (TaskCanceledException ex) when (!ct.IsCancellationRequested)
        {
            throw new ApiException($"请求超时：{req.RequestUri}", inner: ex);
        }
        catch (HttpRequestException ex)
        {
            throw new ApiException($"连不上后端：{ex.Message}", inner: ex);
        }

        using (resp)
        {
            string text = await resp.Content.ReadAsStringAsync(ct).ConfigureAwait(false);
            int status = (int)resp.StatusCode;

            JsonDocument doc;
            try
            {
                doc = JsonDocument.Parse(text);
            }
            catch (JsonException)
            {
                throw new ApiException(
                    $"后端返回了非 JSON 内容（HTTP {status}）：{Truncate(text, 300)}",
                    statusCode: status);
            }

            using (doc)
            {
                var root = doc.RootElement;
                if (root.ValueKind == JsonValueKind.Object
                    && root.TryGetProperty("ok", out var okEl)
                    && okEl.ValueKind is JsonValueKind.True or JsonValueKind.False)
                {
                    if (okEl.ValueKind == JsonValueKind.True)
                    {
                        return root.TryGetProperty("data", out var data)
                            ? data.Clone()
                            : default;
                    }

                    string? code = null;
                    string message = $"后端返回失败（HTTP {status}）";
                    if (root.TryGetProperty("error", out var err) && err.ValueKind == JsonValueKind.Object)
                    {
                        if (err.TryGetProperty("code", out var c) && c.ValueKind == JsonValueKind.String)
                            code = c.GetString();
                        if (err.TryGetProperty("message", out var m) && m.ValueKind == JsonValueKind.String)
                            message = m.GetString() ?? message;
                    }
                    throw new ApiException(message, code, status);
                }

                if (!resp.IsSuccessStatusCode)
                {
                    throw new ApiException(
                        $"HTTP {status}：{Truncate(text, 300)}", statusCode: status);
                }

                return root.Clone();
            }
        }
    }

    private static string Truncate(string s, int n)
        => s.Length <= n ? s : s[..n] + "…";

    public void Dispose() => _http.Dispose();
}
