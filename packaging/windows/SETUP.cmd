@echo off
rem One-click entry for the Shine Windows test build.
rem All user-facing text lives in scripts\install_once.ps1, which is UTF-8 with
rem BOM and therefore renders Chinese correctly under Windows PowerShell 5.1.
setlocal
title Shine Video Studio - Install and Start
cd /d "%~dp0"
if not exist "%~dp0scripts\install_once.ps1" (
  echo [ERROR] scripts\install_once.ps1 is missing. Re-extract the whole package.
  pause
  exit /b 2
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_once.ps1"
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" (
  echo Installer exit code: %RC%
  echo Copy this whole window and send it to the developer. See START_HERE.md.
) else (
  echo Finished. The setup wizard opens in your browser.
)
echo.
pause
exit /b %RC%
