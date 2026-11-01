@echo off
rem Stop the local API, worker and web processes recorded by the launcher.
setlocal
title Shine Video Studio - Stop
cd /d "%~dp0"
if not exist "%~dp0scripts\stop_shine.ps1" (
  echo [ERROR] scripts\stop_shine.ps1 is missing. Re-extract the whole package.
  pause
  exit /b 2
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop_shine.ps1"
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
