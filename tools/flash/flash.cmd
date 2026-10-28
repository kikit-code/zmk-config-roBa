@echo off
rem roBa: download firmware from GitHub Actions and flash it to the XIAO
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONUTF8=1
py -3 flash.py %*
set RC=%ERRORLEVEL%
rem keep the window open when started by double-click
if "%~1"=="" pause
exit /b %RC%
