@echo off
setlocal
cd /d "%~dp0"
rem Find Python: the "py" launcher first, then plain "python".
set PY=py -3
%PY% --version >nul 2>&1 || set PY=python
%PY% --version >nul 2>&1 || (
  echo Python was not found. Install it from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" in the installer, then try again.
  pause
  exit /b 1
)
echo Getting things ready (first time only, needs internet)...
%PY% -m pip install --quiet -r requirements.txt || (
  echo Could not install the helper libraries. Check your internet and try again.
  pause
  exit /b 1
)
%PY% gui.py
if errorlevel 1 pause
