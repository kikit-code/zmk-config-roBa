@echo off
rem Register roba-monitor to start automatically at sign-in
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo [ERROR] Run setup.cmd first.
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$lnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'roBa Monitor.lnk';" ^
  "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk);" ^
  "$s.TargetPath = (Resolve-Path '.venv\Scripts\pythonw.exe').Path;" ^
  "$s.Arguments = '-m roba_monitor';" ^
  "$s.WorkingDirectory = (Get-Location).Path;" ^
  "$s.Description = 'roBa battery / connection monitor';" ^
  "$s.Save(); Write-Host ('Created ' + $lnk)"
