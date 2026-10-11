# Capture a window (by process name) to PNG using PrintWindow (works even if partly covered).
param([string]$ProcName, [string]$Out)
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System; using System.Runtime.InteropServices;
public class W {
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint f);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  public struct RECT { public int L, T, R, B; }
}
"@
$p = Get-Process | Where-Object { $_.ProcessName -like $ProcName -and $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if (-not $p) { "no window for $ProcName"; exit 1 }
$h = $p.MainWindowHandle
if ([W]::IsIconic($h)) { [W]::ShowWindow($h, 9) | Out-Null; Start-Sleep -Milliseconds 600 }
$r = New-Object W+RECT; [W]::GetWindowRect($h, [ref]$r) | Out-Null
$w = $r.R - $r.L; $hh = $r.B - $r.T
$bmp = New-Object System.Drawing.Bitmap $w, $hh
[W]::SetForegroundWindow($h) | Out-Null; Start-Sleep -Milliseconds 700
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($r.L, $r.T, 0, 0, $bmp.Size)   # layered (DuiLib) windows don't support PrintWindow
$g.Dispose(); $bmp.Save($Out); $bmp.Dispose()
"saved $Out ${w}x${hh}"
