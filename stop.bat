@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0launcher.ps1" -Action Stop %*
if errorlevel 1 (
  pause
  exit /b 1
)
endlocal
