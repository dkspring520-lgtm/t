@echo off
chcp 65001 >nul
echo.
echo ============================================
echo   A股分时数据服務啟動器
echo ============================================
echo.

REM 检查Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [錯誤] 未找到Python，請先安裝Python
    pause
    exit /b 1
)

REM 进入目錄
cd /d "C:\Users\dkspr\Documents\A股做t\server"

REM 安裝依赖
echo [步骤1/3] 正在安裝依赖...
python -m pip install flask flask-cors requests -q
if errorlevel 1 (
    echo [錯誤] 依赖安裝失敗，請手動運行: pip install flask flask-cors requests
    pause
    exit /b 1
)
echo [✓] 依赖安裝完成
echo.

REM 啟動服務
echo [步骤2/3] 正在啟動服務...
echo.
echo ============================================
echo   服務啟動成功！
echo ============================================
echo.
echo 訪問地址: http://localhost:5001
echo.
echo API端點:
echo   - 實時行情: http://localhost:5001/api/quote/601899
echo   - 今日分時: http://localhost:5001/api/intraday/601899
echo   - 歷史分時: http://localhost:5001/api/history/601899/20240618
echo   - 熱門股票: http://localhost:5001/api/hot_stocks
echo.
echo 按 Ctrl+C 可以停止服務
echo ============================================
echo.

REM 啟動Python服務
python app.py

pause
