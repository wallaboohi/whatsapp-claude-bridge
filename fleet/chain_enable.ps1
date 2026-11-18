# Owner 18.11.2026: make this PC a live link of the 4-PC backup chain (desktop -> bedroom -> living room -> laptop).
# Run on the backup PC itself (from ssh is fine). Idempotent. Steps:
#   1. the bridge gate is switched on BEFORE any bridge start (store/lease_mode): this PC never links a WhatsApp device
#      of its own and never connects without holding the shared identity;
#   2. the chain fields are merged into agent/config.json (tools/chain_config.py, backup copy first);
#   3. the "WhatsApp bridge" / "WhatsApp agent" logon tasks are registered (install-tasks.ps1) and started.
# The bridge exe must already contain the gate (whatsapp-bridge/lease.go, built by agent._rebuild_bridge).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$store = Join-Path $root "whatsapp-bridge\store"
New-Item -ItemType Directory -Force $store | Out-Null
$mode = Join-Path $store "lease_mode"
if (-not (Test-Path $mode)) {
    Set-Content -Path $mode -Value "owner 18.11: one WhatsApp identity moves between the PCs; the bridge connects only with store/lease_grant" -Encoding UTF8
}
Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $store "lease_grant")
$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) { $uv = Join-Path $env:USERPROFILE ".local\bin\uv.exe" }
& $uv run --no-project python -X utf8 (Join-Path $root "tools\chain_config.py")
if ($LASTEXITCODE) { throw "chain_config failed" }
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root "install-tasks.ps1")
foreach ($t in "WhatsApp bridge", "WhatsApp agent") {
    $st = (Get-ScheduledTask -TaskName $t).State
    if ($st -ne "Running") { Start-ScheduledTask -TaskName $t }
    "{0}: {1}" -f $t, (Get-ScheduledTask -TaskName $t).State
}
