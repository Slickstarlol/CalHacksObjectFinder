@echo off
setlocal
title RecallAI launcher
rem Double-click to open the RecallAI control panel in your browser.
rem Keep this window open while RecallAI runs; closing it stops everything.
cd /d "%~dp0"

set "PY="
for %%P in (python py) do (
  if not defined PY (
    where %%P >nul 2>nul && (
      %%P -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PY=%%P"
    )
  )
)
if not defined PY (
  echo Python 3.10 or newer was not found.
  echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
  pause
  exit /b 1
)

"%PY%" "%~dp0launcher.py" %*
if errorlevel 1 pause
