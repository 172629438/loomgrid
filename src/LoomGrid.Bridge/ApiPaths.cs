namespace LoomGrid.Bridge;

/// <summary>
/// 后端端点集中定义。路径只写一次 —— 这是唯一来源，
/// 界面层不允许再手写字符串（避免「改了一个地方、另一个地方还是旧路径」）。
/// </summary>
public static class ApiPaths
{
    public const string Health = "api/health";

    public const string IngestInspect = "api/ingest/inspect";
    public const string IngestAllow = "api/ingest/allow";
    public const string IngestMapping = "api/ingest/mapping";
    public const string Preview = "api/preview";
    public const string CalcSpaces = "api/calc/spaces";
    public const string CalcCapacity = "api/calc/capacity";
    public const string CalcMonthlySpaces = "api/calc/monthly-spaces";
    public const string CalcSatisfiedDays = "api/calc/satisfied-days";
    public const string SimulateTemplates = "api/simulate/templates";
    public const string SimulateLoad = "api/simulate/load";
    public const string SimulateApply = "api/simulate/apply";
    public const string ExportRun = "api/export/run";
    public const string ExportStatus = "api/export/status";
    public const string ExportReport = "api/export/report";
    public const string ProgressSocket = "ws/progress";
}
