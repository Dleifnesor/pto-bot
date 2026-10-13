@echo off
rem One-click install / repair for PTO. Double-click it. Extra options: --yes --skip-downloads --no-shortcut
setlocal EnableExtensions
cd /d "%~dp0"
title PTO installer
echo.
echo   PTO installer: sets up everything the bot needs. Safe to run again (it skips what's already there).
echo.

if exist ".venv\Scripts\python.exe" goto have_venv
set "PY="
call :find_python
if defined PY goto make_venv
echo Python 3.10 or newer was not found.
where winget >nul 2>nul || goto no_python
echo Installing Python 3.12 with winget...
winget install -e --id Python.Python.3.12 --scope user
call :find_python
if not defined PY goto no_python

:make_venv
echo Creating the Python environment (.venv)...
%PY% -m venv .venv || goto failed

:have_venv
echo Installing Python packages (first time takes a few minutes)...
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check --upgrade pip
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt || goto failed
".venv\Scripts\python.exe" scripts\install.py %*
set "RC=%ERRORLEVEL%"
echo.
if not defined PTO_NO_PAUSE pause
exit /b %RC%

:find_python
for %%C in ("py -3.12" "py -3.11" "py -3.10" "py -3" "python") do (
  if not defined PY (
    %%~C -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PY=%%~C"
  )
)
rem winget installs here, and this window's PATH doesn't know about it yet
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY="%LOCALAPPDATA%\Programs\Python\Python312\python.exe""
exit /b 0

:no_python
echo Install Python 3.12 from https://www.python.org/downloads/ (tick "Add python.exe to PATH"), then run this again.
if not defined PTO_NO_PAUSE pause
exit /b 1

:failed
echo.
echo Setup failed (see the messages above). Fix the problem and run install.bat again.
if not defined PTO_NO_PAUSE pause
exit /b 1
