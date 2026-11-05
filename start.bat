@echo off
setlocal
title PaperDesk
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0launcher.ps1" -Action Start %*
if errorlevel 1 (
  echo.
  echo PaperDesk could not start. Please check the message above.
  pause
  exit /b 1
)
endlocal
