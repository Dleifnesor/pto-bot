@echo off
rem Starts the bot (dashboard + scheduler) and opens the dashboard once it's up. Close this window to stop it.
cd /d "%~dp0"
title PTO Bot
if not exist ".venv\Scripts\python.exe" (
  echo Not installed yet: run install.bat first.
  pause
  exit /b 1
)
start "" /min powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep 6; Start-Process 'http://127.0.0.1:8765/'"
".venv\Scripts\python.exe" -m pto serve
pause
