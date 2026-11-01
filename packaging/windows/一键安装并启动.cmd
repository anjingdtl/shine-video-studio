@echo off
rem Chinese-named alias of SETUP.cmd so the entry point is obvious on a Chinese
rem desktop. Behaviour is identical; if an extractor mangles this file name,
rem SETUP.cmd is the exact same installer.
cd /d "%~dp0"
if not exist "%~dp0SETUP.cmd" (
  echo [ERROR] SETUP.cmd is missing. Re-extract the whole package.
  pause
  exit /b 2
)
call "%~dp0SETUP.cmd"
exit /b %ERRORLEVEL%
