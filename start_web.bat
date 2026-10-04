@echo off
rem XAVI web app (Flask) - double-click to start. Keep this window open while you use it.
cd /d "%~dp0"
echo Address: http://127.0.0.1:5000   (close this window to stop the app)
start "" /min cmd /c "timeout /t 4 /nobreak >nul & explorer http://127.0.0.1:5000"
py -3.13 web\app.py
pause
