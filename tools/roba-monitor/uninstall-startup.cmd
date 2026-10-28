@echo off
rem Remove the sign-in shortcut created by install-startup.cmd
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$lnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'roBa Monitor.lnk';" ^
  "if (Test-Path $lnk) { Remove-Item $lnk; Write-Host ('Removed ' + $lnk) } else { Write-Host 'Not registered.' }"
