# Publish LoomGrid.UI as a distributable folder.
#
# Why a script instead of a command line: 5 -p parameters, easy to mistype;
# and re-running after code changes gives an identical artifact.
#
# Notes on parameters:
#   -p:PublishSingleFile=false           Do NOT single-file: large and slow to start
#                                        (extracts to temp); python-core/ sits beside it anyway.
#   -p:PublishTrimmed=false              csproj sets PublishTrimmed=true for Release, but trimming
#                                        is only supported for self-contained apps (NETSDK1102 otherwise).
#   -p:WindowsAppSDKSelfContained=true   Windows App Runtime self-contained (7 runtime DLLs).
#   -p:SelfContained=false               .NET runtime still from system (set true for full isolation).
#
# NOTE: kept ASCII-only on purpose. PowerShell 5.1 reads UTF-8-without-BOM as ANSI,
#       which mangles CJK and breaks parsing. Comments in English, docs in the commit message.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\publish_ui.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$out = "dist\LoomGrid.UI"
if (Test-Path $out) {
    Remove-Item -Recurse -Force $out
}

Write-Host "== dotnet publish =="
dotnet publish src\LoomGrid.UI\LoomGrid.UI.csproj `
    -c Release `
    -r win-x64 `
    -p:PublishSingleFile=false `
    -p:PublishTrimmed=false `
    -p:WindowsAppSDKSelfContained=true `
    -p:SelfContained=false `
    -o $out

if ($LASTEXITCODE -ne 0) {
    Write-Host "publish failed, exit=$LASTEXITCODE"
    exit $LASTEXITCODE
}

# Python payload: source lives at repo root only; this is a distribution copy.
Write-Host "== copy python-core =="
$dst = Join-Path $out "python-core"
New-Item -ItemType Directory -Force -Path $dst | Out-Null
foreach ($d in @("api", "core", "services", "plotting")) {
    Copy-Item -Recurse -Force $d (Join-Path $dst $d)
}

# Assets (icon / splash / tiles): publish does not copy them (they are Content, not Runtime assets).
# Missing Assets does not stop the app from starting, but the window falls back to a default icon.
Write-Host "== copy Assets =="
$assetsSrc = "src\LoomGrid.UI\Assets"
if (Test-Path $assetsSrc) {
    Copy-Item -Recurse -Force $assetsSrc (Join-Path $out "Assets")
}

# Layout check: exe / python-core / Assets must all sit at the artifact root.
Write-Host "== layout check =="
$exe = Join-Path $out "LoomGrid.UI.exe"
Write-Host ("exe        : " + (Test-Path $exe))
Write-Host ("python-core: " + (Test-Path (Join-Path $out "python-core")))
Write-Host ("Assets     : " + (Test-Path (Join-Path $out "Assets")))

if (Test-Path $exe) {
    $len = (Get-Item $exe).Length
    Write-Host ("exe bytes  : {0:N0}" -f $len)
}

$dlls = Get-ChildItem $out -Filter *.dll
Write-Host ("dll count  : " + $dlls.Count)

# Self-contained boundary: WindowsAppRuntime should be here, VC++ usually is not (system dep).
$war = $dlls | Where-Object { $_.Name -like "Microsoft.WindowsAppRuntime*" }
Write-Host ("WinAppRuntime dll: " + $war.Count)
$vc = $dlls | Where-Object { $_.Name -match 'vcruntime|msvcp' }
Write-Host ("VCRuntime dll    : " + $vc.Count + "  (0 = depends on system VC++ 2015-2022)")

$pyFiles = Get-ChildItem $dst -Recurse -Filter *.py
Write-Host ("python .py files : " + $pyFiles.Count)

$total = (Get-ChildItem $out -Recurse -File | Measure-Object -Property Length -Sum).Sum
Write-Host ("total size : {0:N1} MB" -f ($total / 1MB))
Write-Host ""
Write-Host "artifact: $out"
