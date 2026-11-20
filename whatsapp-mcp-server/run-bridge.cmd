@echo off
rem -------------------------------------------------
rem  WhatsApp‑MCP Bridge – One‑Click start script
rem -------------------------------------------------
cd /d "C:\Users\walla\whatsapp-mcp\whatsapp-mcp-server"

rem Upgrade pip and ensure required packages are installed (user scope)
python -m pip install --upgrade pip
python -m pip install --user requests click uvicorn annotated-types pydantic

rem Start the bridge
python -m uvicorn main:app --host 127.0.0.1 --port 8799

pause