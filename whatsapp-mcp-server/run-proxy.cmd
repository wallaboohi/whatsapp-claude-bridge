@echo off
rem Start the MCP proxy used by the bridge (runs on port 8799)
cd /d "%~dp0"
call ".venv\Scripts\activate.bat"
uvicorn main:app --host 127.0.0.1 --port 8799