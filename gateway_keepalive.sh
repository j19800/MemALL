#!/bin/bash
# MemALL 统一网关保活: 绑定0.0.0.0(局域网可接入) + 固定token + 崩溃自动重启
# 单进程同时提供: /mcp (MCP Streamable HTTP) + /bus/* (实时消息总线, 已合并)
MEMALL_DIR="/vol1/@appshare/fn-deepseek-harness/MemALL"
SECRET=$(cat "$MEMALL_DIR/.gateway-token")
LOG="$MEMALL_DIR/gateway.log"
PY="$MEMALL_DIR/.venv/bin/python"
export MEMALL_GATEWAY_SECRET="$SECRET"
# MCP 强制身份声明：不再设服务端兜底。每个客户端必须在 initialize 里
# 自己声明 agent_name（params / clientInfo / X-MemAll-Agent header），
# 解析不到会被网关拒绝。DSH 客户端的声明见 cordis.patch.yml 的
# X-MemAll-Agent header，本进程重启后生效。

while true; do
    echo "[$(date '+%F %T')] 启动 MemALL Gateway (0.0.0.0 局域网可接入)..." >> "$LOG"
    "$PY" -u -c "
import os, time
from memall.gateway import MemAllGateway
from memall.gateway_peers import start_discovery, stop_discovery
secret = os.environ.get('MEMALL_GATEWAY_SECRET','')
gw = MemAllGateway(host='0.0.0.0', port=9920, secret_key=secret)
gw.start()
# LAN auto-discovery: broadcast a UDP beacon every 5s so clients can find
# this gateway without hardcoding its IP.  TCP/9920 and UDP/9920 coexist.
start_discovery(port=9920)
print('Gateway running on http://0.0.0.0:9920 (LAN accessible, token auth)', flush=True)
print('  MCP:      http://127.0.0.1:9920/mcp', flush=True)
print('  MCP(LAN): http://192.168.1.140:9920/mcp', flush=True)
print('  Bus:      http://127.0.0.1:9920/bus/health (same port+token as MCP)', flush=True)
print('  Discovery: UDP broadcast 255.255.255.255:9920 (every 5s)', flush=True)
try:
    while True: time.sleep(60)
except KeyboardInterrupt:
    stop_discovery()
    gw.stop()
" >> "$LOG" 2>&1
    echo "[$(date '+%F %T')] 网关退出(exit=$?), 5秒后重启..." >> "$LOG"
    sleep 5
done
