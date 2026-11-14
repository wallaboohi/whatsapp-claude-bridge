# Installs the latest WhatsAppDash release for the current user: %LOCALAPPDATA%\WhatsAppDash + Desktop and Start-menu shortcuts.
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$latest = Get-Content "$root\releases\latest.json" -Raw -Encoding UTF8 | ConvertFrom-Json
$dir = Join-Path $env:LOCALAPPDATA "WhatsAppDash"
New-Item -ItemType Directory -Force $dir | Out-Null
Copy-Item -Force "$root\releases\$($latest.file)" "$dir\WhatsAppDash.exe"
# tell the app where the tool lives (it reads the tool's databases from there)
@{ tool_root = $root } | ConvertTo-Json | Set-Content -Encoding UTF8 "$dir\settings.json"
$sh = New-Object -ComObject WScript.Shell
foreach ($p in @([Environment]::GetFolderPath("Desktop"), (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"))) {
    $l = $sh.CreateShortcut((Join-Path $p "ווצאפ Claude - לוח בקרה.lnk"))
    $l.TargetPath = "$dir\WhatsAppDash.exe"; $l.WorkingDirectory = $dir; $l.Description = "לוח הבקרה של YB גשר AI לווצאפ"; $l.Save()
}
Write-Output "installed $($latest.version) -> $dir"
