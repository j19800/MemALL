#!/bin/bash
# MemALL scheduler 保活（Linux NAS，2026-11-08 技术债务巡检 P0-1）
# 与 gateway_keepalive.sh 同构：单实例、崩溃 5 秒自动重启、日志落盘。
# 覆盖的周期任务：heartbeat / pipeline / doctor / forget / security /
# agent_round（含 idle_session_sweep）/ narrative / backup。
MEMALL_DIR="/vol1/@appshare/fn-deepseek-harness/MemALL"
PY="$MEMALL_DIR/.venv/bin/python"
LOG="$MEMALL_DIR/scheduler.log"
PID_FILE="$HOME/.memall/scheduler_keepalive.pid"

mkdir -p "$(dirname "$PID_FILE")"

# 只允许一个保活实例（先检查旧 pid，再写自己的）
if [ -f "$PID_FILE" ]; then
    OLD=$(cat "$PID_FILE" 2>/dev/null)
    if [ -n "$OLD" ] && [ "$OLD" != "$$" ] && kill -0 "$OLD" 2>/dev/null; then
        echo "[$(date '+%F %T')] 已有保活实例 pid=$OLD，退出" >> "$LOG"
        exit 0
    fi
fi
echo $$ > "$PID_FILE"

cleanup() { rm -f "$PID_FILE"; exit 0; }
trap cleanup SIGTERM SIGINT

cd "$MEMALL_DIR" || exit 1

while true; do
    echo "[$(date '+%F %T')] 启动 MemALL scheduler daemon..." >> "$LOG"
    "$PY" -u -c "
import time
from memall.scheduler.scheduler import run_daemon
run_daemon()
" >> "$LOG" 2>&1
    echo "[$(date '+%F %T')] scheduler 退出(exit=$?)，5 秒后重启..." >> "$LOG"
    sleep 5
done
