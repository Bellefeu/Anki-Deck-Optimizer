@echo off
setlocal
cd /d "%~dp0"
set "SETUP_TRIED=0"

:find_python
set "PRISM_PY="

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

rem  Nothing on PATH. That is not the same as nothing installed: Windows leaves
rem  "Add python.exe to PATH" unticked by default and winget's silent install
rem  does not tick it either, so a perfectly good interpreter often sits in
rem  %LOCALAPPDATA%\Programs\Python where no PATH entry points. Asking only the
rem  PATH sends people who already have Python round the setup loop below
rem  forever, because setup cannot install a copy winget can see is there.
for /f "usebackq delims=" %%p in (`powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0control_center\install\find-python.ps1" 2^>nul`) do set "PRISM_PY=%%p"
if defined PRISM_PY goto run_found

if "%SETUP_TRIED%"=="1" (
  echo Setup finished, but this window cannot see Python yet.
  echo Close it, open PRISM again, and it should start.
  pause
  exit /b 1
)
set "SETUP_TRIED=1"
echo Python 3.10+ was not found. Opening the guided setup first...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0control_center\install\setup.ps1"
if errorlevel 1 (
  pause
  exit /b 1
)
goto find_python

rem  %~dp0 always ends in a backslash, and a backslash in front of the closing
rem  quote escapes that quote on the way into the program, so "%~dp0" arrives
rem  as a path with a stray " stuck on the end. The trailing dot keeps the
rem  folder we mean and leaves the quote alone.
:run_py
py -3 "%~dp0control_center\app.py" --root "%~dp0."
set "CONTROL_EXIT=%errorlevel%"
goto finished

:run_python
python "%~dp0control_center\app.py" --root "%~dp0."
set "CONTROL_EXIT=%errorlevel%"
goto finished

:run_found
"%PRISM_PY%" "%~dp0control_center\app.py" --root "%~dp0."
set "CONTROL_EXIT=%errorlevel%"

:finished
if not "%CONTROL_EXIT%"=="0" (
  echo.
  echo PRISM stopped with an error.
  pause
)
exit /b %CONTROL_EXIT%
