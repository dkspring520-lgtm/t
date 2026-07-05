@echo off
setlocal
cd /d "%~dp0"

for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8765" ^| findstr "LISTENING"') do (
  taskkill /F /PID %%p >nul 2>nul
)

ping -n 3 127.0.0.1 >nul

if exist "%USERPROFILE%\Desktop\1.env" (
  for /f "usebackq tokens=1,* delims==" %%a in ("%USERPROFILE%\Desktop\1.env") do (
    if /I "%%a"=="HTTPS_PROXY" set GIT_PROXY=%%b
    if /I "%%a"=="HTTP_PROXY" set GIT_PROXY=%%b
  )
)

if defined GIT_PROXY (
  git -c http.proxy=%GIT_PROXY% -c https.proxy=%GIT_PROXY% pull --ff-only
) else (
  git pull --ff-only
)

if errorlevel 1 (
  echo Update failed.
  exit /b 1
)

start "" /min powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0start_dashboard_background.ps1"
echo Updated and restarted: http://127.0.0.1:8765/
