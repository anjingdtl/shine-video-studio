@echo off
rem One entry, three steps: check this checkout, stop the running stack only
rem when the code really moved, then start. A zip or packaged copy has no .git,
rem so the check is skipped and this behaves exactly as before.
rem The .lnk next to it is a machine-local convenience; this file works even
rem when the shortcut target or the pinned pwsh path is stale.
setlocal
title Shine Video Studio - Dev Launcher
cd /d "%~dp0"
if not exist "%~dp0scripts\start_dev.ps1" (
  echo [ERROR] scripts\start_dev.ps1 is missing. Open the repository root.
  pause
  exit /b 2
)
rem Pass -Update only when the update step really exists, so an older or
rem partially copied tree still starts instead of failing on parameter binding.
set "UPDATE_ARG="
if exist "%~dp0scripts\update_checkout.ps1" set "UPDATE_ARG=-Update"
where pwsh >nul 2>nul
if %ERRORLEVEL%==0 (
  pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_dev.ps1" %UPDATE_ARG%
) else (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_dev.ps1" %UPDATE_ARG%
)
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo Start failed with exit code %RC%. Read the messages above.
  pause
)
exit /b %RC%
