"""Feishu IM message send/receive for bridge daemon.

Wraps lark-cli commands for sending messages to group chat,
replying to threads, and (optionally) consuming event streams.
"""

import json
import logging
import subprocess
import os
import time
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_LARK_CLI_CANDIDATES = [
    str(Path.home() / "AppData/Roaming/npm/lark-cli.cmd"),
    str(Path.home() / "AppData/Roaming/npm/lark-cli"),
    "lark-cli.cmd",
    "lark-cli",
]
_LARK_CLI = None
import os as _os2
for _c in _LARK_CLI_CANDIDATES:
    if _os2.path.isfile(_c) or _os2.path.exists(_c):
        _LARK_CLI = _c
        break
if not _LARK_CLI:
    _LARK_CLI = "lark-cli.cmd" 
_PROFILES_DIR = Path.home() / ".memall" / "lark-cli-profiles"


class LarkClient:
    """Lark IM client wrapping lark-cli for one bot identity."""

    def __init__(self, agent_name: str, app_id: str, app_secret: str,
                 chat_id: str, open_id: str):
        self.agent_name = agent_name
        self.app_id = app_id
        self.app_secret = app_secret
        self.chat_id = chat_id
        self.open_id = open_id
        self._lark_path = _LARK_CLI
        self._ensure_profile()

    def _ensure_profile(self) -> None:
        """Ensure the lark-cli profile for this bot exists."""
        from memall.lark.consumer_helpers import ensure_profile
        ensure_profile(self.agent_name, self.app_id, self.app_secret)

    def _run(self, args: list[str], timeout: int = 15, _retry: int = 2) -> dict:
        profile = _PROFILES_DIR / self.agent_name
        env = {**os.environ, "USERPROFILE": str(profile)}
        for attempt in range(1 + _retry):
            try:
                r = subprocess.run(
                [self._lark_path] + args, capture_output=True, timeout=timeout,
                encoding="utf-8", errors="replace",
                env=env,
            )
                stdout = r.stdout.strip()
                if not stdout or r.returncode != 0:
                    if attempt < _retry:
                        time.sleep(0.5 * (attempt + 1))
                        continue
                    return {"ok": False, "error": r.stderr[:200]}
                data = json.loads(stdout)
                if isinstance(data, dict) and data.get("code") in (99991663, 99991668):
                    if attempt < _retry:
                        time.sleep(1.0 * (attempt + 1))
                        continue
                return data if isinstance(data, dict) else {"ok": False, "error": "not dict"}
            except json.JSONDecodeError as e:
                if attempt < _retry:
                    time.sleep(0.5)
                    continue
                return {"ok": False, "error": f"json: {e}"}
            except subprocess.TimeoutExpired:
                if attempt < _retry:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                return {"ok": False, "error": "timeout"}
            except Exception as e:
                if attempt < _retry:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                return {"ok": False, "error": str(e)}
        return {"ok": False, "error": "max retries exceeded"}

    def send_text(self, text: str, reply_to_msg_id: Optional[str] = None) -> dict:
        """Send a text message to the group chat (or reply in thread)."""
        if reply_to_msg_id:
            return self._run([
                "im", "+messages-reply",
                "--message-id", reply_to_msg_id,
                "--as", "bot",
                "--text", text[:1500],
            ])
        return self._run([
            "im", "+messages-send",
            "--chat-id", self.chat_id,
            "--as", "bot",
            "--text", text[:1500],
        ])

    def send_markdown(self, text: str, reply_to_msg_id: Optional[str] = None) -> dict:
        """Send markdown-formatted message via post content."""
        content = {
            "zh_cn": {
                "title": f"?? {self.agent_name} ???",
                "content": [[{"tag": "text", "text": text[:1500]}]],
            }
        }
        if reply_to_msg_id:
            return self._run([
                "im", "+messages-reply",
                "--message-id", reply_to_msg_id,
                "--as", "bot",
                "--json", json.dumps(content, ensure_ascii=False),
            ])
        return self._run([
            "im", "+messages-send",
            "--chat-id", self.chat_id,
            "--as", "bot",
            "--json", json.dumps(content, ensure_ascii=False),
        ])

    def fetch_recent_messages(self, page_size: int = 10) -> list[dict]:
        """Fetch latest messages from the group chat (for fallback polling)."""
        r = self._run([
            "im", "+chat-messages-list",
            "--chat-id", self.chat_id,
            "--as", "bot",
            "--page-size", str(page_size),
            "--order", "desc",
        ])
        return r.get("data", {}).get("messages", []) if isinstance(r, dict) else []

    def start_event_consumer(self, handler, stop_event: threading.Event):
        """Start lark-cli event consume in a thread.

        `handler` is called with parsed event dicts.
        This is a blocking long-lived process; run in a thread.
        """
        profile = _PROFILES_DIR / self.agent_name
        env = {**os.environ, "USERPROFILE": str(profile)}
        proc = subprocess.Popen(
            [self._lark_path, "event", "consume", "im.message.receive_v1"],
            env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding="utf-8", errors="replace",
        )
        logger.info("lark(%s) event consumer started (pid=%d)",
                     self.agent_name, proc.pid)
        for line in proc.stdout:
            if stop_event.is_set():
                proc.terminate()
                break
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                handler(event)
            except json.JSONDecodeError:
                logger.warning("lark(%s) non-JSON event line: %s",
                               self.agent_name, line[:80])
        proc.wait()
        logger.info("lark(%s) event consumer stopped", self.agent_name)