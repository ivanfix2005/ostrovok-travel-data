@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto ready
echo Run INSTALL.cmd first to set up this project.
pause
exit /b 1

:ready
".venv\Scripts\python.exe" "scripts\launch.py" %*
exit /b %errorlevel%
