@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8

cd /d "%~dp0"
echo ==========================================
echo    A股做T监控系统启动脚本
echo ==========================================
echo.
echo 正在检查Python环境...

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] 未找到Python，请确保Python已正确安装
    pause
    exit /b 1
)
echo [OK] Python环境检查通过

echo.
echo 启动监控系统... (启动后请不要关闭此窗口)
echo 打开浏览器访问: http://127.0.0.1:5000
echo ==========================================

:: 启动浏览器
timeout /t 2 >nul
start http://127.0.0.1:5000

:: 启动服务器
python launcher.py
pause
