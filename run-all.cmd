@echo off
rem =============================================================
rem  Setup and launch WhatsApp‑MCP proxy and bridge
rem  This script grants the necessary file‑system permissions, activates
rem  the virtual environment, starts the proxy (uvicorn) and then runs the
rem  bridge with automatic restarts.
rem =============================================================

rem Change to the directory where this script resides
cd /d "%~dp0"

rem -------------------------------------------------------------
rem  1️⃣ Grant full control to the current user for the entire project
rem  (required for the sandbox and for manual runs). Adjust the account
rem  name if you use a different Windows user.
rem -------------------------------------------------------------
icacls "." /grant "%USERNAME%:(OI)(CI)F" /T

rem -------------------------------------------------------------
rem  2️⃣ Activate the virtual‑env that contains fastmcp and uvicorn
rem -------------------------------------------------------------
call "whatsapp-mcp-server\.venv\Scripts\activate.bat"

rem -------------------------------------------------------------
rem  3️⃣ Start the MCP proxy (uvicorn) in a separate window
rem -------------------------------------------------------------
cd /d "%~dp0whatsapp-mcp-server"
start "WhatsApp‑MCP Proxy" "%~dp0whatsapp-mcp-server\.venv\Scripts\python.exe" -m uvicorn main:app --host 127.0.0.1 --port 8799

rem Optional: wait a moment for the proxy to become reachable
timeout /t 5 /nobreak >nul

rem -------------------------------------------------------------
rem  4️⃣ Launch the bridge – it will keep restarting if it exits
rem -------------------------------------------------------------
cd /d "%~dp0"
call run-bridge.cmd

rem -------------------------------------------------------------
rem  End of script – the bridge loop runs forever (or until you close
rem  the window). To stop everything, close this CMD window.
rem -------------------------------------------------------------
