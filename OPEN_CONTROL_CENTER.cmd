@echo off
setlocal
cd /d "%~dp0"
set "SETUP_TRIED=0"

:find_python
where py >nul 2>nul
if %errorlevel% equ 0 (
  py -3 -c "import sys; raise SystemExit(sys.version_info ^< (3,10))" >nul 2>nul
  if not errorlevel 1 goto run_py
)
where python >nul 2>nul
if %errorlevel% equ 0 (
  python -c "import sys; raise SystemExit(sys.version_info ^< (3,10))" >nul 2>nul
  if not errorlevel 1 goto run_python
)

if "%SETUP_TRIED%"=="1" (
  echo Setup finished, but this window cannot see Python yet.
  echo Close it, open the Control Center again, and it should start.
  pause
  exit /b 1
)
set "SETUP_TRIED=1"
echo Python 3.10+ was not found. Opening the guided setup first...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
if errorlevel 1 (
  pause
  exit /b 1
)
goto find_python

:run_py
py -3 "%~dp0control_center\app.py" --root "%~dp0"
set "CONTROL_EXIT=%errorlevel%"
goto finished

:run_python
python "%~dp0control_center\app.py" --root "%~dp0"
set "CONTROL_EXIT=%errorlevel%"

:finished
if not "%CONTROL_EXIT%"=="0" (
  echo.
  echo The Control Center stopped with an error.
  pause
)
exit /b %CONTROL_EXIT%
