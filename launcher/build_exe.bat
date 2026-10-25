@echo off
rem Builds launcher\RecallAI.exe (a single file you can double-click or pin to the taskbar).
rem The exe still uses your installed Python to run RecallAI itself; it only replaces RecallAI.bat.
cd /d "%~dp0"
python -m pip install --upgrade pyinstaller || goto :fail
python -m PyInstaller --noconfirm --onefile --console --name RecallAI ^
  --distpath . --workpath build --specpath build ^
  --add-data "%~dp0panel.html;." launcher.py || goto :fail
echo.
echo Built %~dp0RecallAI.exe  (keep it in this launcher folder next to steps.json)
pause
exit /b 0
:fail
echo Build failed - see the messages above.
pause
exit /b 1
