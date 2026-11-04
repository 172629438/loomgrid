using System.IO;

namespace LoomGrid.UI;

/// <summary>
/// 中文字体初始化。
///
/// 为什么必须有这一层：ScottPlot 解析系统字体名（"Microsoft YaHei" 之类）会
/// 静默 fallback 成 Segoe UI，中文随之渲染成方块 —— 而 FontName 设置后读回是
/// 正确的，控件也不报错。唯一可靠路径是两步：
///   ① Fonts.AddFontFile(注册名, 字体文件路径)   ← 必须给文件，不能给字体名
///   ② plot.Font.Set(注册名)                    ← 一次接管标题/轴标签/刻度/图例
/// 只做第①步，图表区仍是 Segoe UI；只设 Label.FontName 会被 fallback 掉。
///
/// 这里做一次，六屏共用，不让每个页面各自踩一遍。
/// </summary>
internal static class PlotFontBootstrap
{
    private const string FamilyName = "LoomCjk";

    private static bool _done;

    /// <summary>实际加载的字体文件；null 表示本机没有可用中文字体。</summary>
    public static string? FontFile { get; private set; }

    public static string Family => FamilyName;

    public static bool IsReady => FontFile is not null;

    /// <summary>启动时调用一次即可，重复调用无副作用。</summary>
    public static void Ensure()
    {
        if (_done) return;
        _done = true;

        string fontsDir = Environment.GetFolderPath(Environment.SpecialFolder.Fonts);
        // 顺序即回退链：雅黑（最好看）→ 黑体 → 宋体
        foreach (var name in new[] { "msyh.ttc", "msyh.ttf", "simhei.ttf", "simsun.ttc" })
        {
            string path = Path.Combine(fontsDir, name);
            if (!File.Exists(path)) continue;

            try
            {
                ScottPlot.Fonts.AddFontFile(FamilyName, path);
                // 注册后必须验证能取到 typeface，否则等于没注册
                if (ScottPlot.Fonts.GetTypeface(FamilyName, false, false) is null) continue;

                FontFile = path;
                AppLog.Write($"中文字体：{name}（注册名 {FamilyName}）");
                return;
            }
            catch (Exception ex)
            {
                AppLog.Write($"中文字体：加载 {name} 失败（{ex.GetType().Name}），继续尝试下一个");
            }
        }

        AppLog.Write("中文字体：未找到可用字体，图表中文可能渲染为方块");
    }

    /// <summary>给一个 Plot 应用中文字体。在每次创建 Plot 后调用。</summary>
    public static void Apply(ScottPlot.Plot plot)
    {
        Ensure();
        if (FontFile is null) return;
        plot.Font.Set(FamilyName);
    }
}
