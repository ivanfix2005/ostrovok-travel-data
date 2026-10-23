@echo off
setlocal
cd /d "%~dp0"
call code .
if errorlevel 1 goto failed
exit /b 0

:failed
echo Open this project folder in Visual Studio Code.
pause
exit /b 1
