@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
"%~dp0.venv\Scripts\python.exe" "%~dp0scripts\run_consumer_lifesim.py" --config "%~dp0config\consumer_lifesim.local.json" --interactive
pause
