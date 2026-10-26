@echo off
cd /d "%~dp0"
echo [1/2] Checking packages (requests, websockets)...
python -m pip install --quiet --disable-pip-version-check requests websockets
if errorlevel 1 echo Package install failed. Check the message above.
echo [2/2] Starting chart server...
python realtime_server.py
pause
