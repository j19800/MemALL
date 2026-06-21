import sys; sys.path.insert(0, "E:\\MemALL\\src")
import logging, time
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s", stream=sys.stdout, force=True)
from memall.bridge.main import BridgeConfig, AgentBridge
cfg = BridgeConfig.from_credentials("codex")
cfg.poll_interval = 2.0
cfg.resolve_paths()
b = AgentBridge(cfg)
b.start()
print("Bridge RUNNING - send @codex in Feishu group now")
time.sleep(300)
b.stop()
