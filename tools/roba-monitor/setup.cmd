@echo off
rem roba-monitor: create .venv and install dependencies
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python launcher "py" not found.
  echo         Install Python 3.12 or 3.13, e.g.:  winget install Python.Python.3.13
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  py -3 -m venv .venv || exit /b 1
)

".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt || exit /b 1

echo.
echo Done. Try:  check.cmd   (prints the current status once)
echo       or:  start.cmd   (starts the tray icon)
