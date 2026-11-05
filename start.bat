@echo off
setlocal
title Haoge Papers
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0launcher.ps1" -Action Start %*
if errorlevel 1 (
  echo.
  echo Haoge Papers could not start. Please check the message above.
  pause
  exit /b 1
)
endlocal
