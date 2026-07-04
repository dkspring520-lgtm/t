#!/bin/bash
set -euo pipefail

cd /opt/stock-news

echo "===== $(date '+%Y-%m-%d %H:%M:%S') ====="

# 1) 检查进程是否运行
if pgrep -f "python3 main.py" >/dev/null 2>&1; then
    echo "[OK] main.py 进程运行中"
else
    echo "[FAIL] 未检测到 main.py 进程"
fi

# 2) 检查最近是否有日志更新
if [ -f logs/stock-news.log ]; then
    echo "[OK] 存在 stock-news.log"
    last_line=$(tail -n 5 logs/stock-news.log | tail -n 1 || true)
    echo "[日志] 最近记录: ${last_line:-空}"
else
    echo "[FAIL] 缺少 stock-news.log"
fi

# 3) 检查错误日志是否有内容
if [ -f logs/stock-news-error.log ]; then
    err_count=$(grep -c . logs/stock-news-error.log 2>/dev/null || echo 0)
    echo "[检查] 错误日志记录数: ${err_count}"
else
    echo "[检查] 无 stock-news-error.log"
fi
