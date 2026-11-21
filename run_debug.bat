@echo off
cd /d "%~dp0"
set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not exist "%PY%" set "PY=python"
echo Running BatteryDisplay with console (for diagnostics)...
echo.
"%PY%" src\prototype\app.py
echo.
echo Exit code: %ERRORLEVEL%
pause
