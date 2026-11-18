# Owner 18.11: every PC gets every AI-provider desktop app that any of the 4 has. Runs in the user's session (scheduled task /it).
$ids = 'Anthropic.Claude','OpenAI.ChatGPT','OpenAI.Codex','Microsoft.Copilot','GitHub.CopilotApp','Google.Antigravity','xAI.GrokBot',
       'Anysphere.Cursor','Codeium.Windsurf','ElementLabs.LMStudio','Jan.Jan','Ollama.Ollama'
$log = "$env:USERPROFILE\ai_apps_sync.log"; "start $(Get-Date -f s) $env:COMPUTERNAME" | Out-File $log -Encoding utf8
$have = (winget list --accept-source-agreements --disable-interactivity 2>$null | Out-String)
foreach ($i in $ids) {
  if ($have -match [regex]::Escape($i)) { "$i :: already" | Out-File $log -Append -Encoding utf8; continue }
  $o = winget install --id $i -e --silent --accept-package-agreements --accept-source-agreements --disable-interactivity 2>&1 | Out-String
  if ($LASTEXITCODE -ne 0 -and $o -match 'No package found') { $o = winget install --id $i -e --source msstore --silent --accept-package-agreements --accept-source-agreements --disable-interactivity 2>&1 | Out-String }
  "$i :: exit=$LASTEXITCODE :: " + (($o.Trim() -split "`n")[-1]).Trim() | Out-File $log -Append -Encoding utf8
}
"DONE $(Get-Date -f s)" | Out-File $log -Append -Encoding utf8
