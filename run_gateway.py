import sys, time
from memall.gateway import MemAllGateway
gw = MemAllGateway(host="127.0.0.1", port=9920)
gw.start()
print(f"MemALL Gateway running on http://127.0.0.1:9920", flush=True)
print(f"  MCP: http://127.0.0.1:9920/mcp", flush=True)
sys.stdout.flush()
try:
    while True:
        time.sleep(60)
except KeyboardInterrupt:
    gw.stop()
