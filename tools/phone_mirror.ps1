# Phone mirror launcher (owner 18.11 23:32: "the phone-mirror icon does nothing when clicked").
# The old shortcut ran scrcpy hidden (scrcpy-noconsole.vbs): when the phone was not reachable over adb (Tailscale off on the
# phone, adb port dropped after a reboot) scrcpy quit silently. This launcher heals the adb link first (tools/adb_keeper.py heal),
# says in Hebrew what is missing when it cannot, starts scrcpy, and shows scrcpy's own error if it quits within a few seconds.
# Desktop shortcut: powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File <this file>
$ErrorActionPreference = 'Continue'
Add-Type -AssemblyName PresentationFramework
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
function Show-Msg($text, $icon = 'Warning') { [void][System.Windows.MessageBox]::Show($text, 'מראה לטלפון', 'OK', $icon) }

$scrcpy = (Get-Command scrcpy.exe -ErrorAction SilentlyContinue).Source
if (-not $scrcpy) {
    $scrcpy = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Recurse -Filter scrcpy.exe -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty FullName
}
if (-not $scrcpy) { Show-Msg 'scrcpy לא מותקן במחשב הזה (winget install Genymobile.scrcpy).' 'Error'; exit 1 }

if (Get-Process scrcpy -ErrorAction SilentlyContinue) {
    # already open: bring it to the front instead of starting a second copy
    (New-Object -ComObject WScript.Shell).AppActivate('POCO mirror') | Out-Null
    exit 0
}

# 1. heal adb (fixed port 5555 over Tailscale, or the wireless-debugging port after a phone reboot)
$res = $null
try {
    $out = & uv run -q --no-project python -I -X utf8 "$root\tools\adb_keeper.py" heal 2>$null
    $res = ($out -join "`n") | ConvertFrom-Json
} catch { }
$st = if ($res) { "$($res.state)$($res.status)" } else { '' }
if (-not $res) {
    # uv / python not available here: plain adb on the fixed Tailscale port
    $adb = Join-Path (Split-Path $scrcpy) 'adb.exe'
    & $adb connect 100.115.100.33:5555 2>&1 | Out-Null
    $st = if ((& $adb devices) -match '100\.115\.100\.33:5555\s+device') { 'ok' } else { 'unreachable' }
}
$ok = $st -match 'ok|healed|device'
if (-not $ok) {
    $why = switch -regex ($st) {
        'unreachable' { 'הטלפון לא נגיש: Tailscale כבוי בטלפון, או שהטלפון כבוי / בלי רשת. הדלק Tailscale בטלפון ונסה שוב.' }
        'unauthorized' { 'הטלפון לא אישר את המחשב הזה. בטלפון: אשר "לאפשר ניפוי באגים" שמופיע על המסך, ונסה שוב.' }
        'no_port' { 'ניפוי באגים אלחוטי כבוי בטלפון (קורה אחרי הפעלה מחדש). הגדרות ← אפשרויות מפתחים ← ניפוי באגים אלחוטי: הדלק, ונסה שוב.' }
        default { "לא הצלחתי לחבר את הטלפון ב-adb ($st)." }
    }
    Show-Msg $why
    exit 1
}

# 2. start scrcpy; if it quits within 6 seconds, show why
$log = Join-Path $env:TEMP 'phone_mirror.log'
$p = Start-Process -FilePath $scrcpy -WorkingDirectory (Split-Path $scrcpy) -NoNewWindow -PassThru `
    -ArgumentList '--window-title="POCO mirror" --stay-awake' `
    -RedirectStandardError $log
Start-Sleep -Seconds 6
if ($p.HasExited) {
    $err = (Get-Content $log -ErrorAction SilentlyContinue | Where-Object { $_ -match 'ERROR|error' } | Select-Object -Last 4) -join "`n"
    Show-Msg "המראה נסגרה מיד.`n$err" 'Error'
    exit 1
}
