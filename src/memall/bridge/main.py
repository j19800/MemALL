"""Bridge daemon entry point.

Run: python -m memall.bridge.main [agent_name]

Without agent_name, starts bridges for all configured bots.
"""

import logging
import sys
import time
import threading
from typing import Optional

from memall.bridge.config import BridgeConfig
from memall.bridge.lark_client import LarkClient
from memall.bridge.watchdog import FileWatcher

logger = logging.getLogger("memall.bridge")


class AgentBridge:
    """Bridge daemon for one agent.

    Watches:
      - Inbox: agent writes tasks here -> bridge sends to Feishu
      - Outbox: Feishu replies arrive here -> bridge writes responses
      - Feishu: polls/consumes events -> routes @mentions to agent
    """

    def __init__(self, config: BridgeConfig):
        self.config = config
        config.resolve_paths()
        self.lark = LarkClient(
            agent_name=config.agent_name,
            app_id=config.feishu_app_id,
            app_secret=config.feishu_app_secret,
            chat_id=config.chat_id,
            open_id=config.feishu_open_id,
        )
        self._inbox_watcher = FileWatcher(
            watch_dir=config.inbox_dir,
            callback=self._on_agent_message,
            interval=config.poll_interval,
            agent_name=config.agent_name,
        )
        self._outbox_watcher = FileWatcher(
            watch_dir=config.outbox_dir,
            callback=self._on_agent_response,
            interval=config.poll_interval,
            agent_name=config.agent_name,
        )
        self._pending_requests: dict[str, dict] = {}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        logger.info("bridge(%s) starting", self.config.agent_name)
        self._inbox_watcher.start()
        self._outbox_watcher.start()
        if self.config.use_event_listener:
            self._start_event_listener()
        else:
            self._start_poll_lark()
        logger.info("bridge(%s) started", self.config.agent_name)

    def stop(self) -> None:
        self._stop.set()
        try:
            self._inbox_watcher.stop()
        finally:
            self._outbox_watcher.stop()
        logger.info("bridge(%s) stopped", self.config.agent_name)

    def _start_event_listener(self) -> None:
        t = threading.Thread(
            target=self.lark.start_event_consumer,
            args=(self._on_lark_event, self._stop),
            daemon=True,
        )
        t.start()

    def _start_poll_lark(self) -> None:
        t = threading.Thread(target=self._poll_lark_loop, daemon=True)
        t.start()

    def _poll_lark_loop(self) -> None:
        while not self._stop.is_set():
            try:
                msgs = self.lark.fetch_recent_messages(page_size=10)
                for msg in reversed(msgs):
                    self._on_lark_event(msg)
            except Exception as e:
                logger.warning("bridge(%s) lark poll error: %s",
                               self.config.agent_name, e)
            time.sleep(self.config.poll_interval)

    def _on_lark_event(self, event: dict) -> None:
        """Process an incoming Feishu message / event."""
        import json as _j2

        msg = event if isinstance(event, dict) else {}
        msg_id = msg.get("message_id", "")
        if not msg_id:
            inner = event.get("event", {}) if isinstance(event, dict) else {}
            msg = inner.get("message", inner.get("Message", {}))
            msg_id = msg.get("message_id", "")
        if not msg_id:
            return

        msg_type = msg.get("msg_type", "")
        if msg_type not in ("text", "post", ""):
            return

        sender = msg.get("sender", {})
        sender_type = sender.get("sender_type", "") if isinstance(sender, dict) else ""
        if sender_type == "app":
            return  # skip own messages

        # Check if this is a reply to a pending request (reply routing)
        parent_id = msg.get("parent_id", msg.get("thread_id", ""))
        if parent_id and hasattr(self, '_pending_requests') and parent_id in self._pending_requests:
            original = self._pending_requests[parent_id]
            raw_text = msg.get("content", "")
            if isinstance(raw_text, str) and raw_text.startswith("{"):
                try:
                    p = _j2.loads(raw_text)
                    if isinstance(p, dict):
                        raw_text = p.get("text", raw_text)
                except (_j2.JSONDecodeError, TypeError):
                    logger.warning("bridge(%s) failed to parse reply content: %s", self.config.agent_name, exc_info=True)
            reply_msg = {
                "id": f"reply_{msg_id[:12]}",
                "type": "response",
                "from": original.get("to", "unknown"),
                "to": original.get("from", self.config.agent_name),
                "task": raw_text,
                "reply_to": original.get("id", ""),
                "feishu_msg_id": msg_id,
                "created_at": msg.get("created_at", ""),
            }
            outbox_path = self.config.outbox_dir / f"{reply_msg['id']}.json"
            outbox_path.write_text(_j2.dumps(reply_msg, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info("bridge(%s) feishu reply -> outbox: from=%s",
                         self.config.agent_name, reply_msg["from"])
            return

        mentions = msg.get("mentions", [])
        if not isinstance(mentions, list):
            mentions = []
        bot_mentioned = any(
            m.get("id") == self.config.feishu_open_id
            for m in mentions
        )
        if not bot_mentioned:
            return

        text = msg.get("content", "")
        if isinstance(text, str):
            try:
                p = _j2.loads(text) if text.startswith("{") else {}
                if isinstance(p, dict):
                    text = p.get("text", text)
            except (_j2.JSONDecodeError, TypeError):
                    logger.warning("bridge(%s) failed to parse mention content: %s", self.config.agent_name, exc_info=True)

        for m in mentions:
            if isinstance(m, dict):
                key = m.get("key", "")
                if key:
                    text = text.replace(key, "", 1).strip()
        text = text.strip()
        if not text:
            return

        msg_file = {
            "id": f"feishu_{msg_id[:12]}",
            "type": "feishu_mention",
            "from": sender.get("id", "unknown"),
            "to": self.config.agent_name,
            "task": text,
            "feishu_msg_id": msg_id,
            "created_at": msg.get("created_at", ""),
        }
        inbox_path = self.config.inbox_dir / f"{msg_file['id']}.json"
        inbox_path.write_text(_j2.dumps(msg_file, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("bridge(%s) feishu -> inbox: %s",
                     self.config.agent_name, text[:60])

        # Also capture to MemALL so the agent can retrieve it
        try:
            import urllib.request
            cap = _j2.dumps({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "capture", "arguments": {
                    "content": f"[Bridge] Feishu @mention from {msg_file.get('from','?')}: {text}",
                    "level": "P2", "category": "inter_agent_communication",
                    "agent_name": self.config.agent_name,
                    "subject": f"Feishu @{self.config.agent_name}: {text[:60]}",
                    "summary": f"?????? @{self.config.agent_name} ??",
                }}
            }, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                self.config.memall_mcp_url,
                data=cap,
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception as e:
            logger.warning("bridge(%s) capture to memall failed: %s", self.config.agent_name, e)

    def _on_agent_message(self, data: dict, filename: str) -> None:
        """Process a message file written by the local agent into inbox."""
        msg_type = data.get("type", "request")
        to_agent = data.get("to", "")
        task = data.get("task", "")
        feishu_msg_id = data.get("reply_to", "")
        logger.info("bridge(%s) inbox file: %s type=%s to=%s task=%s",
                     self.config.agent_name, filename, msg_type, to_agent, task[:60])

        text = f"@{to_agent} {task}" if to_agent else task
        r = self.lark.send_text(text, reply_to_msg_id=feishu_msg_id or None)
        if isinstance(r, dict) and r.get("ok"):
            feishu_sent_id = r.get("data", {}).get("message_id", "")
            if to_agent and msg_type in ("request",) and feishu_sent_id:
                self._pending_requests[feishu_sent_id] = {
                    "id": data.get("id", filename),
                    "from": self.config.agent_name,
                    "to": to_agent,
                    "task": task,
                }
            if feishu_msg_id and to_agent:
                self._pending_requests[feishu_msg_id] = {
                    "id": data.get("id", filename),
                    "from": self.config.agent_name,
                    "to": to_agent,
                    "task": task,
                }
            logger.info("bridge(%s) inbox -> feishu: %s",
                         self.config.agent_name, text[:60])
        else:
            logger.warning("bridge(%s) feishu send fail: %s",
                           self.config.agent_name, r.get("error", "?") if isinstance(r, dict) else "?")

    def _on_agent_response(self, data: dict, filename: str) -> None:
        """Process a message file written by the local agent into outbox."""
        logger.info("bridge(%s) outbox file: %s type=%s to=%s",
                     self.config.agent_name, filename,
                     data.get("type", "?"), data.get("to", "?"))

    def wait(self) -> None:
        try:
            while not self._stop.is_set():
                time.sleep(2)
        except KeyboardInterrupt:
            self.stop()

    def is_alive(self) -> bool:
        return not self._stop.is_set()


def main() -> None:
    from memall.core.log_setup import configure as configure_logging; configure_logging()

    agent_name = sys.argv[1] if len(sys.argv) > 1 else None

    if agent_name:
        config = BridgeConfig.from_credentials(agent_name)
        if not config:
            logger.error("agent %r not found in credentials", agent_name)
            sys.exit(1)
        bridges = [AgentBridge(config)]
    else:
        from memall.lark.credentials import load_all
        all_creds = load_all()
        bridges = []
        for name in all_creds:
            config = BridgeConfig.from_credentials(name)
            if config:
                bridges.append(AgentBridge(config))
                logger.info("queued bridge for %s", name)

    for b in bridges:
        b.start()

    if not bridges:
        logger.warning("no bridges started")
        return

    try:
        while any(b.is_alive() for b in bridges):
            time.sleep(2)
    except KeyboardInterrupt:
        logger.info("shutdown requested")
        for b in bridges:
            b.stop()


if __name__ == "__main__":
    main()
