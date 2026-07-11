@echo off
cd /d %~dp0frontend
python -m http.server 8080
pause
