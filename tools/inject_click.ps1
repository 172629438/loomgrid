# Inject a REAL left click at screen (X, Y): park cursor, emit real relative
# moves so WinUI's internal pointer position catches up, then press/release.
#
# Why not just SetCursorPos + mouse_event(LEFTDOWN):
#   SetCursorPos moves the OS cursor but does NOT post WM_MOUSEMOVE, so WinUI's
#   cached pointer position stays stale. A subsequent LEFTDOWN then fires
#   PointerPressed at the OLD coordinates - which is exactly the symptom we hit
#   (two clicks at different screen points both reporting the same PlotView
#   point). The wobble below forces WM_MOUSEMOVE so the cache refreshes first.
#
# ASCII-only on purpose: PowerShell 5.1 reads UTF-8-without-BOM as ANSI.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\inject_click.ps1 -X 1115 -Y 894

param(
    [int]$X = 1500,
    [int]$Y = 700,
    [int]$Wobble = 5
)

$ErrorActionPreference = "Stop"

if (-not ("LGMouseClick" -as [type])) {
Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Threading;

public class LGMouseClick {
    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool SetCursorPos(int X, int Y);

    [DllImport("user32.dll", SetLastError = true)]
    public static extern void mouse_event(uint dwFlags, int dx, int dy, uint dwData, IntPtr dwExtraInfo);

    public const uint MOVE      = 0x0001;
    public const uint LEFTDOWN  = 0x0002;
    public const uint LEFTUP    = 0x0004;

    public static void MoveRel(int dx, int dy) {
        mouse_event(MOVE, dx, dy, 0, IntPtr.Zero);
    }

    public static void Click(int wobble) {
        // Force WM_MOUSEMOVE so the WinUI pointer cache matches the real cursor.
        for (int i = 0; i < wobble; i++) {
            MoveRel(2, 0); Thread.Sleep(40);
            MoveRel(-2, 0); Thread.Sleep(40);
        }
        Thread.Sleep(120);
        mouse_event(LEFTDOWN, 0, 0, 0, IntPtr.Zero);
        Thread.Sleep(60);
        mouse_event(LEFTUP, 0, 0, 0, IntPtr.Zero);
    }
}
"@
}

[void][LGMouseClick]::SetCursorPos($X, $Y)
Start-Sleep -Milliseconds 150
[LGMouseClick]::Click($Wobble)
Start-Sleep -Milliseconds 250

Write-Host "clicked ($X,$Y) after $($Wobble*2) wobble moves"
