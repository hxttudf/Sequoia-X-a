#!/bin/bash
set -e

if [ ! -f /app/data/sequoia_v2.db ]; then
    echo "[entrypoint] 首次运行：回填历史数据..."
    .venv/bin/python main.py --backfill
    echo "[entrypoint] 回填完成"
fi

echo "[entrypoint] 启动 cron 守护进程（每日 15:30 CST 自动执行）..."
touch /app/logs/daily.log
cron && tail -f /app/logs/daily.log
