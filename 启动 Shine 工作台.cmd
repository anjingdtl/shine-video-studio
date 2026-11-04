@echo off
rem Portable development launcher. The .lnk next to it is a convenience; this
rem file works even when the shortcut target or the pinned pwsh path is stale.
setlocal
title Shine Video Studio - Dev Launcher
cd /d "%~dp0"
if not exist "%~dp0scripts\start_dev.ps1" (
  echo [ERROR] scripts\start_dev.ps1 is missing. Open the repository root.
  pause
  exit /b 2
)
where pwsh >nul 2>nul
if %ERRORLEVEL%==0 (
  pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_dev.ps1"
) else (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_dev.ps1"
)
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo Start failed with exit code %RC%. Read the messages above.
  pause
)
exit /b %RC%
