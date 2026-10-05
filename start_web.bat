@echo off
rem XAVI web app - double-click to start. Keep this window open while you use it.
cd /d "%~dp0"
set "PY="
for %%V in (3.13 3.12 3.11 3.10) do (
  if not defined PY (py -%%V -c "import sys" >nul 2>nul && set "PY=py -%%V")
)
if not defined PY (python -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul && set "PY=python")
if not defined PY (
  echo Python 3.10 or newer is needed. Install it from https://www.python.org/downloads/
  echo ^(tick "Add python.exe to PATH" in the installer^), then double-click this file again.
  pause
  exit /b 1
)
%PY% run.py web
pause
