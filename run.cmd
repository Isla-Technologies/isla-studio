@echo off
rem Start Isla Studio on Windows. The first start sets it up (needs the internet).
rem Options pass through, for example:  run.cmd --host 0.0.0.0
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Setting up Isla Studio for the first time. This takes a few minutes.
  where py >nul 2>nul
  if not errorlevel 1 (
    py -3 -m venv .venv
  ) else (
    python -m venv .venv
  )
  if not exist ".venv\Scripts\python.exe" (
    echo.
    echo Isla Studio needs Python 3.10 or newer. Install it from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH", then run this again.
    pause
    exit /b 1
  )
)

".venv\Scripts\python.exe" -c "import sys; sys.exit(sys.version_info < (3, 10))"
if errorlevel 1 (
  echo Isla Studio needs Python 3.10 or newer. Delete the .venv folder, install a newer Python and run this again.
  pause
  exit /b 1
)

rem Install, or update, the packages when requirements.txt has changed since the last start.
fc /b requirements.txt ".venv\requirements.installed" >nul 2>nul
if errorlevel 1 (
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
  if errorlevel 1 (
    echo.
    echo Installing Isla Studio's packages failed: the messages above say why.
    pause
    exit /b 1
  )
  copy /y requirements.txt ".venv\requirements.installed" >nul
)

".venv\Scripts\python.exe" -m isla_rl.server %*
if errorlevel 1 pause
