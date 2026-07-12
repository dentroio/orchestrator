@echo off
setlocal
set SCRIPT_DIR=%~dp0
echo Launching Clarion Windows Runner installer...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%install_windows_runner.ps1"
set EXIT_CODE=%ERRORLEVEL%
if not "%EXIT_CODE%"=="0" (
  echo Installer exited with code %EXIT_CODE%.
  pause
)
exit /b %EXIT_CODE%
