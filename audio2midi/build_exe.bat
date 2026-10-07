@echo off
setlocal
cd /d "%~dp0"
rem Builds a single Audio2MIDI.exe (no Python needed to run it) into the "dist" folder.
set PY=py -3
%PY% --version >nul 2>&1 || set PY=python
%PY% -m pip install --quiet -r requirements.txt pyinstaller || (pause & exit /b 1)
%PY% -m PyInstaller --noconfirm --onefile --windowed --name Audio2MIDI gui.py || (pause & exit /b 1)
echo.
echo Done! Your program is dist\Audio2MIDI.exe
pause
