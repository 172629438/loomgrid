# Inject REAL mouse move events (WM_MOUSEMOVE), unlike SetCursorPos.
#
# Why: SetCursorPos only repositions the cursor; it does NOT post WM_MOUSEMOVE.
# WinUI's PointerMoved depends on the WM_POINTER* family, which a bare cursor
# reposition never produces. mouse_event() injects at the input-queue level,
# so the OS synthesizes the same messages a physical mouse would.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\inject_hover.ps1 -X 1500 -Y 700
#   ... -Steps 6   (extra relative wobbles so at least one lands as a move)
#
# ASCII-only on purpose: PowerShell 5.1 reads UTF-8-without-BOM as ANSI and
# mangles CJK, which breaks parsing (learned the hard way in publish_ui.ps1).

param(
    [int]$X = 1500,
    [int]$Y = 700,
    [int]$Steps = 6,
    [int]$StepPx = 3
)

$ErrorActionPreference = "Stop"

Add-Type @"
using System;
using System.Runtime.InteropServices;

public class LGMouse {
    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool SetCursorPos(int X, int Y);

    [DllImport("user32.dll", SetLastError = true)]
    public static extern void mouse_event(uint dwFlags, int dx, int dy, uint dwData, IntPtr dwExtraInfo);

    public const uint MOUSEEVENTF_MOVE = 0x0001;

    public static void MoveRel(int dx, int dy) {
        mouse_event(MOUSEEVENTF_MOVE, dx, dy, 0, IntPtr.Zero);
    }
}
"@

# 1) park the cursor at the target
[void][LGMouse]::SetCursorPos($X, $Y)
Start-Sleep -Milliseconds 120

# 2) now emit REAL relative moves; each one should become a WM_MOUSEMOVE
for ($i = 0; $i -lt $Steps; $i++) {
    [LGMouse]::MoveRel($StepPx, 0)
    Start-Sleep -Milliseconds 90
    [LGMouse]::MoveRel(-$StepPx, 0)
    Start-Sleep -Milliseconds 90
}

Write-Host "injected $($Steps * 2) relative moves around ($X,$Y)"
