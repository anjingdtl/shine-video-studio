@echo off
rem Read-only environment report: host, disks and the pinned toolchain.
rem Writes nothing outside the package.
setlocal
title Shine Video Studio - Environment check
cd /d "%~dp0"
if not exist "%~dp0scripts\install_once.ps1" (
  echo [ERROR] scripts\install_once.ps1 is missing. Re-extract the whole package.
  pause
  exit /b 2
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_once.ps1" -CheckOnly
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
