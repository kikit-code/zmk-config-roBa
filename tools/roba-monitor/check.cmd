@echo off
rem Print roBa status once (add --diagnose for full GATT details)
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONUTF8=1
".venv\Scripts\python.exe" -m roba_monitor --once %*
