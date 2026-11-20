@echo off
rem Start the MCP proxy (if not already running)
start "" "%~dp0whatsapp-mcp-server\run-proxy.cmd"

rem Keeps the WhatsApp bridge running: restarts it 10 s after any exit. Started at logon by Task Scheduler.
cd /d "%~dp0whatsapp-bridge"
:loop
rem rotate the log before each start once it passes ~10 MB (keeps one previous file)
for %%F in (bridge.log) do if %%~zF GTR 10000000 move /y bridge.log bridge.log.1 >nul
echo [%date% %time%] starting bridge >> bridge.log
"%~dp0whatsapp-bridge\\whatsapp-bridge.exe" >> bridge.log 2>&1
timeout /t 10 /nobreak >nul
goto loop
