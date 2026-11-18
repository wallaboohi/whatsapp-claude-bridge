$p='HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*','HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*','HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'
Get-ItemProperty $p -ErrorAction SilentlyContinue | Where-Object {$_.DisplayName -and -not $_.SystemComponent} | Select-Object -ExpandProperty DisplayName | Sort-Object -Unique
