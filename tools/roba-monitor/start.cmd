@echo off
rem Start the tray icon in the background (no console window)
setlocal
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" -m roba_monitor %*
