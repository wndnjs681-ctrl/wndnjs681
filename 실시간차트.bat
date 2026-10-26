@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 필요한 패키지를 확인합니다...
python -m pip install --quiet --disable-pip-version-check requests websockets
python realtime_server.py
pause
