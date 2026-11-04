using System.Text.Json;
using LoomGrid.Bridge;

namespace LoomGrid.UI;

/// <summary>
/// 开发期自动流程：不点任何按钮，直接把「导入 → 模拟 → 切换 → 测算」跑一遍。
///
/// **为什么需要它**：本机的鼠标与键盘事件注入都到不了窗口（实测五种坐标、两种键盘、
/// SetForegroundWindow 全不通），因此「应用模拟后数字是否真的变了」这一眼，
/// 没法靠点按钮验证。数据链路本身全是 HTTP，不依赖 GUI —— 把这条链跑通，
/// 就能证明「切换生效」，而按钮那层由代码审查 + 人工按一次确认。
///
/// **只在 `LGM_AUTO_E2E=1` 时执行**；生产环境不设这个变量，本文件不产生任何副作用。
/// 它不修改任何页面、不写配置文件、不碰 core/。
///
/// 判据（日志里逐条可核）：
///   ① 原始：分组来源=文件，日需求 = X
///   ② 应用模拟，session 切换
///   ③ 模拟：分组来源=模拟，日需求 = Y     （Y ≠ X 才算切换真的生效）
///   ④ 恢复原始，日需求 = X               （回到原值才算切换可逆）
/// </summary>
internal static class AutoE2E
{
    private const string FileName = @"baseline\_inputs\W1\W1_宽表96点.xlsx";

    public static void RunIfRequested()
    {
        if (Environment.GetEnvironmentVariable("LGM_AUTO_E2E") != "1") return;
        _ = Task.Run(RunAsync);
    }

    private static async Task RunAsync()
    {
        AppLog.Write("E2E：开始（LGM_AUTO_E2E=1）");

        AppServices? services = null;
        for (int i = 0; i < 60 && services is null; i++)
        {
            services = AppServices.Current;
            if (services is null) await Task.Delay(500).ConfigureAwait(false);
        }
        if (services is null)
        {
            AppLog.Write("E2E：FAIL —— 后端 30 秒内未就绪");
            return;
        }

        try
        {
            string root = FindRepoRoot();
            string path = Path.Combine(root, FileName);
            if (!File.Exists(path))
            {
                AppLog.Write($"E2E：FAIL —— 找不到测试文件 {path}");
                return;
            }

            var ingest = new IngestApi(services.Api);
            var calc = new CalcApi(services.Api);
            var sim = new SimulateApi(services.Api);

            // ① 导入（allow 是桌面壳在 inspect 前的授权步骤，等价于用户拖入文件）
            await ingest.AllowPathsAsync(new[] { path }).ConfigureAwait(false);
            InspectResult insp = await ingest.InspectAsync(path).ConfigureAwait(false);
            SheetInfo sheet = insp.Sheets.FirstOrDefault()
                              ?? throw new InvalidOperationException("inspect 没有返回工作表");
            MappingResult map = await ingest.ApplyMappingAsync(path, sheet.Name, sheet.Suggest)
                .ConfigureAwait(false);
            string original = map.SessionId
                              ?? throw new InvalidOperationException("mapping 没有返回 session_id");

            var ws = Workspace.Current;
            ws.Files.Clear();
            ws.Groups.Clear();
            foreach (var g in map.Groups) ws.Groups.Add(g);
            ws.SessionId = original;
            AppLog.Write($"E2E：① 原始 session={original}，分组 {map.Groups.Count} 个");

            var req = new CapacityRequest
            {
                SessionId = original,
                Group = 0,
                Billing = new CalcBilling { Value = 800 },
                Mode = "A",
                Basis = "min_feasible",
                Segments = DefaultSegments(),
            };
            CapacityResult c0 = await calc.CapacityAsync(req).ConfigureAwait(false);
            double demandOriginal = c0.DailyDemand.ValueKwh;
            AppLog.Write($"E2E：① 原始 · 分组来源=文件 · 日需求={demandOriginal:F1} kWh");

            // ② 生成模拟曲线（电解铝平线，功率放大到 5000 kW，保证与原始差异明显）
            SimLoadResult simData = await sim.GenerateAsync(new SimLoadRequest
            {
                Industry = "电解铝",
                AvgPowerKw = 5000,
                NoiseStd = 0.0,
                NDays = 31,
                NPoints = 96,
                Seed = 424242,
            }).ConfigureAwait(false);
            AppLog.Write($"E2E：② 生成模拟 {simData.Industry} · {simData.Shape[0]} 天 × " +
                         $"{simData.Shape[1]} 点 · seed #{simData.AppliedSeed}");

            SimApplyResult applied = await sim.ApplyAsync(new SimApplyRequest
            {
                OriginalSessionId = original,
                LoadArray = simData.LoadArray,
                TimeLabels = simData.TimeLabels,
                DateLabels = simData.DateLabels,
                Industry = simData.Industry,
                Seed = simData.AppliedSeed,
                Asset = simData.Industry,
            }).ConfigureAwait(false);

            ws.OriginalSessionId = original;
            ws.SimulatedSessionId = applied.SessionId;
            ws.SimOriginIndustry = simData.Industry;
            ws.SimOriginSeed = simData.AppliedSeed;
            ws.SimAsset = applied.Asset;
            ws.SimMonth = applied.MonthStr;
            ws.SimDays = simData.DateLabels.Count;
            ws.SimPoints = simData.Shape.Count > 1 ? simData.Shape[1] : simData.TimeLabels.Count;
            ws.SessionId = applied.SessionId;
            AppLog.Write($"E2E：② 应用模拟，session 切换 {original} → {applied.SessionId}" +
                         $"（清掉旧模拟 {applied.DroppedPrevious} 个）");

            // ③ 用模拟 session 算
            var simReq = req with { SessionId = applied.SessionId };
            CapacityResult c1 = await calc.CapacityAsync(simReq).ConfigureAwait(false);
            double demandSim = c1.DailyDemand.ValueKwh;
            AppLog.Write($"E2E：③ 模拟 · 分组来源={(ws.IsSimulated ? "模拟" : "文件(异常!)")} · " +
                         $"日需求={demandSim:F1} kWh");

            // ④ 切回原始再算
            ws.RevertToOriginal();
            var backReq = req with { SessionId = ws.SessionId! };
            CapacityResult c2 = await calc.CapacityAsync(backReq).ConfigureAwait(false);
            double demandBack = c2.DailyDemand.ValueKwh;
            AppLog.Write($"E2E：④ 恢复原始 · 分组来源={(ws.IsSimulated ? "模拟(异常!)" : "文件")} · " +
                         $"日需求={demandBack:F1} kWh");

            // ⑤ 判定
            bool switched = Math.Abs(demandSim - demandOriginal) > 1.0;
            bool reverted = Math.Abs(demandBack - demandOriginal) < 0.05;
            bool flagOk = ws.SimulatedSessionId is not null;

            AppLog.Write($"E2E：结论 —— 原始 {demandOriginal:F1} / 模拟 {demandSim:F1} / " +
                         $"恢复 {demandBack:F1} kWh");

            if (switched && reverted && flagOk)
            {
                AppLog.Write($"E2E：PASS（切换生效 {demandOriginal:F1}→{demandSim:F1}，" +
                             $"恢复可逆 →{demandBack:F1}）");
            }
            else
            {
                AppLog.Write($"E2E：FAIL —— 切换生效={switched} 恢复可逆={reverted} " +
                             $"模拟标记={flagOk}");
            }
        }
        catch (Exception ex)
        {
            AppLog.Write($"E2E：FAIL —— 异常 {ex.GetType().Name}: {ex.Message}");
        }
    }

    /// <summary>与 V4 页面默认一致的时段：0–8 充电，18–22 放电。</summary>
    private static List<CalcSegment> DefaultSegments() => new()
    {
        new CalcSegment { Start = 0, End = 8, Type = "charge", Label = "充电段" },
        new CalcSegment { Start = 18, End = 22, Type = "discharge", Label = "放电段" },
    };

    /// <summary>
    /// 从可执行文件目录往上找含 <c>baseline</c> 的祖先目录（仓库根）。
    /// 不写死路径：Debug 输出在 src\LoomGrid.UI\bin\...\win-x64\ 下，层数会随目标框架变。
    /// </summary>
    private static string FindRepoRoot()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null)
        {
            if (Directory.Exists(Path.Combine(dir.FullName, "baseline"))) return dir.FullName;
            dir = dir.Parent;
        }
        return AppContext.BaseDirectory;
    }
}
