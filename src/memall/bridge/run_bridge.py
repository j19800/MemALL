import sys; sys.path.insert(0, "E:\\MemALL\\src")
import logging, time
from memall.core.log_setup import configure as configure_logging; configure_logging()
from memall.bridge.main import BridgeConfig, AgentBridge
cfg = BridgeConfig.from_credentials("codex")
cfg.poll_interval = 2.0
cfg.resolve_paths()
b = AgentBridge(cfg)
b.start()
print("Bridge RUNNING - send @codex in Feishu group now")
time.sleep(300)
b.stop()
