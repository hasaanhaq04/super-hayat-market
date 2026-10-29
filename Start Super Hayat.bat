@echo off
cd /d "%~dp0"
set PYTHONUNBUFFERED=1
python "%~dp0app\server.py"
if errorlevel 1 pause
