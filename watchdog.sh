#!/bin/bash
# watchdog.sh - 守护脚本：每60秒检查main.py是否运行，挂了自动重启
# 用法：nohup /usr/bin/python3 /opt/stock-news/watchdog.sh >> /opt/stock-news/logs/watchdog.log 2>&1 &

cd /opt/stock-news
LOG=logs/watchdog.log

while true; do
    if pgrep -f "python3 main.py" >/dev/null 2>&1; then
        # 进程正常，静默
        :
    else
        echo "$(date '+%Y-%m-%d %H:%M:%S') [WATCHDOG] main.py 已停止，正在重启..." >> $LOG
        nohup /usr/bin/python3 main.py >> logs/stdout.log 2>&1 &
        echo "$(date '+%Y-%m-%d %H:%M:%S') [WATCHDOG] 已重启，PID=$!" >> $LOG
    fi
    sleep 60
done
