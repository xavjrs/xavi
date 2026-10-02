@echo off
rem XAVI - double-click to start. Keep this window open while you use it.
cd /d "%~dp0"
echo Starting ISA Terminal... your browser will open in a few seconds.
echo Address: http://127.0.0.1:8501   (close this window to stop the app)
start "" /min cmd /c "timeout /t 6 /nobreak >nul & explorer http://127.0.0.1:8501"
py -3.12 -m streamlit run app.py --server.headless true --server.address 127.0.0.1 --server.port 8501
pause

