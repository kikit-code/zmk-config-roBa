@echo off
rem Dump everything Windows can see about roBa into data\diagnose.txt
setlocal
cd /d "%~dp0"
if not exist data mkdir data
set PYTHONUTF8=1
".venv\Scripts\python.exe" -m roba_monitor --diagnose > data\diagnose.txt 2>&1
echo Saved to %~dp0data\diagnose.txt
