"""Foreground MemALL gateway with DEV auto-reload enabled.

Wraps gateway_start.py with MEMALL_AUTORELOAD=1 so the in-process watcher
(src/memall/gateway_autoreload.py) execv-restarts the gateway when edited
code settles and compiles. Same PID, fresh interpreter — no stale half-loaded
modules.

DEV ONLY: a restart can cut an in-flight request. Do not use this entrypoint
for the shared/production unit (memall-gateway.service / gateway_keepalive.sh
stay reload-free).

Usage:  PYTHONPATH=src python gateway_start_dev_reload.py
"""
import os

os.environ.setdefault("MEMALL_AUTORELOAD", "1")

if __name__ == "__main__":
    import runpy
    runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "gateway_start.py"),
                   run_name="__main__")
