"""Foreground MemALL gateway entrypoint for systemd (Restart=on-failure)."""
import os
import time
from memall.gateway import MemAllGateway
from memall.gateway_peers import start_discovery, stop_discovery

secret = os.environ.get("MEMALL_GATEWAY_SECRET", "").strip()
gw = MemAllGateway(host="0.0.0.0", port=9920, secret_key=secret)
gw.start()
start_discovery(port=9920)
print("MemALL gateway on 0.0.0.0:9920 (systemd-managed, token auth)", flush=True)
try:
    while True:
        time.sleep(60)
except KeyboardInterrupt:
    stop_discovery()
    gw.stop()
