$pat='Claude|ChatGPT|Copilot|Ollama|LM Studio|^Jan|Cursor|Windsurf|Perplexity|Trae|Zed|Antigravity|Gemini|Grok|DeepSeek|Msty|AnythingLLM|Cherry'
$p='HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*','HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*','HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'
$a=@(Get-ItemProperty $p -EA 0 | ?{$_.DisplayName -match $pat} | %{ 'W:'+$_.DisplayName })
$a+=@(Get-AppxPackage -EA 0 | ?{$_.Name -match 'Claude|ChatGPT|OpenAI|Copilot|Perplexity|Gemini'} | %{ 'X:'+$_.Name })
$a | Sort-Object -Unique
