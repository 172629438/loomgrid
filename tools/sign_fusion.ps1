<#
.SYNOPSIS
    给 LoomGrid Fusion 的 exe 做自签名，消除本机上的「无法验证发布者」提示。

.DESCRIPTION
    分三步：
      ① 生成一张自签名代码签名证书（存到 CurrentUser\My）
      ② 用它签 exe（带时间戳）
      ③ 把证书装进 CurrentUser\Root（受信任根）

    装进受信任根之后，本机再运行这个 exe 就不会弹「无法验证发布者」了。

.PARAMETER ExePath
    要签名的 exe。/自动在 dist / build_release 下找最新的 LoomGrid-Fusion。

.NOTES
    边界说明（很重要，别抱错期望）:
      · 自签名只在本机有效。别人下载你签过的 exe，仍然会看到警告——
        因为他们机器上没有你的证书。
      · 这不解决 SmartScreen 的「Windows 已保护你的电脑」。
        SmartScreen 看的是证书类型与下载声誉：OV 证书要攒声誉，
        EV 证书才立即通过。自签名对它无效。
      · 想彻底解决需要买证书：OV 约 1000-2000 元/年，EV 约 3000-5000 元/年。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File sign_fusion.ps1
    powershell -ExecutionPolicy Bypass -File sign_fusion.ps1 -ExePath "D:\xx\LoomGrid-Fusion-v2.3.20.exe"
    powershell -ExecutionPolicy Bypass -File sign_fusion.ps1 -Uninstall
#>

param(
    [string]$ExePath = "",
    [string]$Subject = "CN=LoomGrid Fusion, O=LoomGrid, C=CN",
    [int]$ValidYears = 5,
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$CertFriendly = "LoomGrid Fusion Self-Signed"

function Write-Step($msg) { Write-Host ">>> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Warn2($msg){ Write-Host "  [!!] $msg" -ForegroundColor Yellow }

# ── 卸载 ──────────────────────────────────────────────────────────
if ($Uninstall) {
    Write-Step "卸载自签名证书"
    $found = Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert -ErrorAction SilentlyContinue |
             Where-Object { $_.FriendlyName -eq $CertFriendly }
    if (-not $found) { Write-Warn2 "没找到证书，无需卸载"; exit 0 }
    foreach ($c in $found) {
        # 先从 Root 移除同名证书
        $rootCerts = Get-ChildItem Cert:\CurrentUser\Root -ErrorAction SilentlyContinue |
                     Where-Object { $_.Thumbprint -eq $c.Thumbprint }
        foreach ($rc in $rootCerts) {
            Remove-Item -Path "Cert:\CurrentUser\Root\$($rc.Thumbprint)" -Force
            Write-Ok "已从受信任根移除 $($rc.Thumbprint.Substring(0,16))…"
        }
        Remove-Item -Path "Cert:\CurrentUser\My\$($c.Thumbprint)" -Force
        Write-Ok "已从个人存储移除 $($c.Thumbprint.Substring(0,16))…"
    }
    Write-Host ""
    Write-Host "卸载完成。已签过名的 exe 签名仍在，但本机将重新出现「未知发布者」。" -ForegroundColor Yellow
    exit 0
}

# ── 找 exe ────────────────────────────────────────────────────────
if (-not $ExePath) {
    Write-Step "自动查找最新的 LoomGrid-Fusion exe"
    # $PSScriptRoot 是 tools\，仓库根是上一层
    $repo = Split-Path $PSScriptRoot -Parent
    $roots = @((Join-Path $repo "dist"), (Join-Path $repo "build_release"))
    $cands = @()
    foreach ($r in $roots) {
        if (Test-Path $r) {
            $cands += Get-ChildItem -Path $r -Recurse -Filter "LoomGrid-Fusion-v*.exe" `
                      -ErrorAction SilentlyContinue |
                      Where-Object { $_.Length -gt 10MB }
        }
    }
    if (-not $cands) {
        Write-Warn2 "没找到 exe。用 -ExePath 指定路径。"
        Write-Host "  找过的目录：" -ForegroundColor Gray
        foreach ($r in $roots) { Write-Host "    $r" -ForegroundColor Gray }
        exit 1
    }
    $ExePath = ($cands | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
}

if (-not (Test-Path $ExePath)) {
    Write-Warn2 "exe 不存在: $ExePath"
    exit 1
}

Write-Host ""
Write-Host "目标: $ExePath" -ForegroundColor White
Write-Host "大小: $([math]::Round((Get-Item $ExePath).Length/1MB,1)) MB" -ForegroundColor White
Write-Host ""

# ── ① 生成证书 ────────────────────────────────────────────────────
Write-Step "① 生成自签名代码签名证书"
$existing = Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert -ErrorAction SilentlyContinue |
            Where-Object { $_.FriendlyName -eq $CertFriendly } |
            Sort-Object NotAfter -Descending | Select-Object -First 1

if ($existing) {
    Write-Ok "复用已有证书 $($existing.Thumbprint.Substring(0,16))…  到期 $($existing.NotAfter.ToString('yyyy-MM-dd'))"
    $cert = $existing
} else {
    $cert = New-SelfSignedCertificate `
        -Type CodeSigningCert `
        -Subject $Subject `
        -KeyUsage DigitalSignature `
        -FriendlyName $CertFriendly `
        -CertStoreLocation "Cert:\CurrentUser\My" `
        -NotAfter (Get-Date).AddYears($ValidYears) `
        -KeyExportPolicy Exportable
    Write-Ok "已生成 $($cert.Thumbprint.Substring(0,16))…  到期 $($cert.NotAfter.ToString('yyyy-MM-dd'))"
}

# ── ② 签名 ────────────────────────────────────────────────────────
Write-Step "② 签名 exe"
try {
    $sig = Set-AuthenticodeSignature -FilePath $ExePath -Certificate $cert `
           -TimestampServer "http://timestamp.digicert.com" -HashAlgorithm SHA256
    if ($sig.Status -eq "Valid" -or $sig.Status -eq "UnknownError") {
        Write-Ok "签名完成，状态: $($sig.Status)"
        if ($sig.Status -eq "UnknownError") {
            Write-Warn2 "状态 UnknownError 通常是因为证书还没装进受信任根——下一步会装"
        }
    } else {
        Write-Warn2 "签名状态: $($sig.Status) — $($sig.StatusMessage)"
    }
} catch {
    Write-Warn2 "签名失败: $_"
    Write-Host "  （时间戳服务器可能不可达；去掉 -TimestampServer 可离线签，但签名不过期需要时间戳）" -ForegroundColor Gray
    exit 1
}

# ── ③ 装进受信任根 ────────────────────────────────────────────────
Write-Step "③ 把证书装进 CurrentUser\Root（受信任根）"
$inRoot = Get-ChildItem Cert:\CurrentUser\Root -ErrorAction SilentlyContinue |
          Where-Object { $_.Thumbprint -eq $cert.Thumbprint }
if ($inRoot) {
    Write-Ok "已在受信任根，无需重复安装"
} else {
    try {
        $cerPath = Join-Path $env:TEMP "LoomGridFusion-signing.cer"
        Export-Certificate -Cert $cert -FilePath $cerPath -Type CERT | Out-Null
        Import-Certificate -FilePath $cerPath -CertStoreLocation "Cert:\CurrentUser\Root" | Out-Null
        Remove-Item $cerPath -Force -ErrorAction SilentlyContinue
        Write-Ok "已装入受信任根"
    } catch {
        Write-Warn2 "装根证书失败: $_"
        Write-Host "  可能被系统弹窗拦截，请手动确认，或用管理员重跑本脚本。" -ForegroundColor Gray
    }
}

# ── 复核 ──────────────────────────────────────────────────────────
Write-Host ""
Write-Step "复核签名状态"
$v = Get-AuthenticodeSignature -FilePath $ExePath
Write-Host "  Status : $($v.Status)"
Write-Host "  Signer : $($v.SignerCertificate.Subject)"
Write-Host "  有效期 : $($v.SignerCertificate.NotBefore.ToString('yyyy-MM-dd')) ~ $($v.SignerCertificate.NotAfter.ToString('yyyy-MM-dd'))"
Write-Host ""

if ($v.Status -eq "Valid") {
    Write-Host "完成。本机再运行这个 exe 不会弹「无法验证发布者」。" -ForegroundColor Green
} else {
    Write-Host "签名状态不是 Valid，检查上面输出。" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "边界提醒：" -ForegroundColor Yellow
Write-Host "  · 别人下载你签过的 exe 仍会看到警告——他们机器上没有你的证书" -ForegroundColor Gray
Write-Host "  · SmartScreen 的「Windows 已保护你的电脑」不解决：它看证书类型与声誉，" -ForegroundColor Gray
Write-Host "    自签名对它无效。要彻底解决需要 OV（约 1000-2000/年）或 EV（约 3000-5000/年）证书" -ForegroundColor Gray
Write-Host "  · 卸载：powershell -ExecutionPolicy Bypass -File sign_fusion.ps1 -Uninstall" -ForegroundColor Gray
