#!/usr/bin/env bash
ssh -o StrictHostKeyChecking=no -o ConnectTimeout=15 root@45.153.131.171 'ls -la /opt/stock-news/ && echo "===ENV===" && cat /opt/stock-news/.env 2>/dev/null; echo "===PYFILES===" && find /opt/stock-news -name "*.py" 2>/dev/null | head -50'
