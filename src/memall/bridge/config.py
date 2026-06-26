"""Bridge daemon configuration."""

import logging
import os
from pathlib import Path
from typing import Optional
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_DEFAULT_CHAT_ID = os.environ.get("MEMALL_CHAT_ID", "")
_DEFAULT_MCP_URL = "http://127.0.0.1:9876/mcp"
_BRIDGE_DIR = Path(__file__).parent.resolve()


@dataclass
class BridgeConfig:
    agent_name: str
    chat_id: str = _DEFAULT_CHAT_ID
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_open_id: str = ""
    inbox_dir: Optional[Path] = None
    outbox_dir: Optional[Path] = None
    memall_mcp_url: str = _DEFAULT_MCP_URL
    poll_interval: float = 3.0
    use_event_listener: bool = False

    def resolve_paths(self) -> None:
        ib = _BRIDGE_DIR / "inboxes" / self.agent_name
        ob = _BRIDGE_DIR / "outboxes" / self.agent_name
        if self.inbox_dir is None:
            self.inbox_dir = ib
        if self.outbox_dir is None:
            self.outbox_dir = ob
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        self.outbox_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_credentials(cls, agent_name: str) -> Optional["BridgeConfig"]:
        from memall.lark.credentials import get
        creds = get(agent_name)
        if not creds:
            return None
        return cls(
            agent_name=agent_name,
            chat_id=creds.get("chat_id", _DEFAULT_CHAT_ID),
            feishu_app_id=creds.get("app_id", ""),
            feishu_app_secret=creds.get("app_secret", ""),
            feishu_open_id=creds.get("open_id", ""),
        )

    def to_message(self, msg_type: str, to_agent: str, task: str,
                   context: Optional[dict] = None,
                   reply_to: Optional[str] = None) -> dict:
        import uuid
        return {
            "id": f"msg_{uuid.uuid4().hex[:12]}",
            "type": msg_type,
            "from": self.agent_name,
            "to": to_agent,
            "task": task,
            "context": context or {},
            "reply_to": reply_to or "",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
