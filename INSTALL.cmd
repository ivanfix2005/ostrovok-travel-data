@echo off
setlocal
cd /d "%~dp0"
echo Installing the project. Python 3.11+ and internet are required.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1"
if errorlevel 1 goto failed
echo.
echo Ready. Open START.cmd to launch the project.
pause
exit /b 0

:failed
echo.
echo Installation failed. Keep the error message shown above.
pause
exit /b 1
