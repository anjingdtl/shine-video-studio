@echo off
rem Start the already-installed stack and reopen the setup wizard.
setlocal
title Shine Video Studio - Start
cd /d "%~dp0"
if not exist "%~dp0.env" (
  echo This copy has not been installed yet.
  echo Run the one-click installer first: SETUP.cmd ^(or the Chinese-named entry next to it^)
  pause
  exit /b 2
)
rem Prefer the pinned PowerShell 7 the installer put on PATH; fall back to the
rem bundled Windows PowerShell so this entry never depends on a missing tool.
where pwsh >nul 2>nul
if %ERRORLEVEL%==0 (
  pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_shine.ps1" -OpenBrowser -AutoPorts
) else (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_shine.ps1" -OpenBrowser -AutoPorts
)
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" (
  echo Start failed with exit code %RC%.
  echo If a previous stack is still running, run STOP_APP.cmd first.
) else (
  echo Started. The workbench opened in your browser.
)
echo.
pause
exit /b %RC%
