@echo off
cd /d "%~dp0"
title NBA news scanner
set PYTHONUNBUFFERED=1
:again
python scanner.py
if %errorlevel%==2 exit /b 2
echo Scanner stopped. Restarting in 15 seconds. Close this window to stop.
timeout /t 15 /nobreak >nul
goto again
