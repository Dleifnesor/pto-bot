@echo off
rem Same as pto.ps1, for cmd and for PCs whose PowerShell blocks scripts:  pto doctor | generate --count 2 | ...
"%~dp0.venv\Scripts\python.exe" -m pto %*
exit /b %ERRORLEVEL%
