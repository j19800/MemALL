"""Feishu IM bridge — polling mode (no event subscription needed).

Polls the group chat periodically via lark-cli ``+chat-messages-list``,
detects new @mentions, calls LLM with conversation context, and replies.

No WebSocket, no event subscription, no app publishing needed.
"""

import json
import logging
import os
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from memall.lark.credentials import load_all

logger = logging.getLogger("memall.lark.consumer")

_LARK_CLI = str(Path.home() / "AppData/Roaming/npm/lark-cli.cmd")
_PROFILES_DIR = Path.home() / ".memall" / "lark-cli-profiles"
_CHAT_ID = os.environ.get("MEMALL_CHAT_ID", "")
_SESSION_DB = Path.home() / ".memall" / "feishu_sessions.db"
_POLL_INTERVAL = 3  # seconds


# ── lark-cli helpers ─────────────────────────────────────────


def _run_lark(agent: str, args: list[str], timeout: int = 10) -> dict:
    """Run a ``lark-cli`` command for *agent* and return parsed JSON."""
    profile = _PROFILES_DIR / agent
    env = {**os.environ, "USERPROFILE": str(profile)}
    try:
        r = subprocess.run(
            [str(_LARK_CLI)] + args, capture_output=True, timeout=timeout,
            encoding="utf-8", errors="replace",
            env=env,
        )
        out = r.stdout.strip()
        if not out:
            return {"ok": False, "error": r.stderr[:200]}
        data = json.loads(out)
        return data if isinstance(data, dict) else {"ok": False, "error": "not json"}
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"json: {e}"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _fetch_latest_messages(agent: str, page_size: int = 10) -> list[dict]:
    """Fetch the latest messages from the group chat."""
    r = _run_lark(agent, [
        "im", "+chat-messages-list",
        "--chat-id", _CHAT_ID,
        "--as", "bot",
        "--page-size", str(page_size),
        "--order", "desc",
    ])
    return r.get("data", {}).get("messages", [])


# ── Session store ──────────────────────────────────────────────


class SessionStore:
    def __init__(self, db_path: Path = _SESSION_DB):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), timeout=10, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS sessions ("
            "session_key TEXT PRIMARY KEY, history TEXT, "
            "created_at TEXT, updated_at TEXT)"
        )
        self._conn.commit()
        self._lock = threading.Lock()

    def _key(self, agent: str, chat_id: str, user_id: str) -> str:
        return f"{agent}:{chat_id}:{user_id}"

    def get_history(self, agent: str, chat_id: str, user_id: str) -> list:
        k = self._key(agent, chat_id, user_id)
        with self._lock:
            row = self._conn.execute(
                "SELECT history FROM sessions WHERE session_key=?", (k,)
            ).fetchone()
        return json.loads(row[0]) if row else []

    def append(self, agent: str, chat_id: str, user_id: str,
               user_msg: str, assistant_msg: str) -> None:
        k = self._key(agent, chat_id, user_id)
        now = datetime.now(timezone.utc).isoformat()
        history = self.get_history(agent, chat_id, user_id)
        history.append({"role": "user", "content": user_msg})
        history.append({"role": "assistant", "content": assistant_msg})
        if len(history) > 80:
            history = history[-80:]
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO sessions VALUES (?,?,COALESCE((SELECT created_at FROM sessions WHERE session_key=?),?),?)",
                (k, json.dumps(history, ensure_ascii=False), k, now, now),
            )
            self._conn.commit()


# ── Memory-based responder ──────────────────────────────────────


def _search_memories(query: str, limit: int = 5) -> list[dict]:
    """Search MemALL's memory store for context relevant to *query*."""
    try:
        from memall.core.db import get_conn
        conn = get_conn()
        try:
            from memall.search.registry import get_search_provider
            provider = get_search_provider()
            results = provider.search(query, top_k=limit)
            if results:
                conn.close()
                return results
        except Exception:
            logger.warning("consumer.py: silent error", exc_info=True)
        from memall.search.faiss_provider import SearchProvider as FaissSearchProvider
        try:
            faiss = FaissSearchProvider()
            results = faiss.search(query, top_k=limit)
            if results:
                conn.close()
                return results
        except Exception:
            logger.warning("consumer.py: silent error", exc_info=True)
        rows = conn.execute(
            "SELECT id, content, summary, level, category, subject, "
            "       datetime(created_at, 'localtime') as created "
            "FROM memories WHERE content LIKE ? OR summary LIKE ? OR subject LIKE ? "
            "ORDER BY CASE WHEN level='L1' AND category='identity' THEN 0 "
            "WHEN category='architecture' THEN 1 "
            "WHEN level='P1' OR level='P2' THEN 2 ELSE 3 END, "
            "created_at DESC LIMIT ?",
            (f"%{query}%", f"%{query}%", f"%{query}%", limit),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning("memory search failed: %s", e)
        return []


def _get_agent_persona(agent: str) -> str:
    """Get agent's identity/persona from MemALL (L1 traits)."""
    try:
        from memall.core.db import get_conn
        conn = get_conn()
        row = conn.execute(
            "SELECT content, summary FROM memories WHERE level='L1' AND category='identity' "
            "AND agent_name=? ORDER BY created_at DESC LIMIT 1",
            (agent,),
        ).fetchone()
        conn.close()
        if row:
            return row["summary"] or row["content"]
        return ""
    except Exception as e:
        logger.warning("persona lookup failed: %s", e)
        return ""


def _format_memory_entry(m: dict) -> str:
    """Format a single memory as a readable line."""
    summary = m.get("summary", "") or ""
    content = m.get("content", "") or ""
    text = summary[:150] if summary else content[:150]
    level = m.get("level", "")
    created = m.get("created", "")
    tag = f"[{level}]" if level else ""
    return f"  {tag} {text} ({created})" if created else f"  {tag} {text}"


def _answer_from_memory(agent: str, user_text: str, history: list) -> str:
    """Answer a user's @mention by looking up relevant memories.

    Searches with combined context (current query + last user message),
    prioritizes vector search, falls back to FTS with relevance ordering.
    """
    # Build query: use history context instead of short follow-ups
    # Short/generic messages (< 4 chars) → search from history
    if len(user_text.strip()) < 4 and history:
        context_parts = []
        for entry in reversed(history):
            if entry.get("role") == "user":
                ctx = entry.get("content", "")
                if ctx.strip() and ctx.strip() != user_text.strip():
                    context_parts.append(ctx[:200])
                    break
        query = " ".join(context_parts) if context_parts else user_text
    else:
        query = user_text

    lines = []

    # Agent identity
    persona = _get_agent_persona(agent)
    if persona:
        lines.append(f"[{agent}] {persona[:300]}")

    # Memory search
    memories = _search_memories(query)
    if memories:
        seen_ids = set()
        filtered = []
        for m in memories:
            mid = m.get("id")
            if mid and mid not in seen_ids:
                seen_ids.add(mid)
                filtered.append(m)
        if filtered:
            lines.append("相关记忆:")
            for m in filtered[:5]:
                lines.append(_format_memory_entry(m))
    else:
        lines.append("(未找到直接相关记忆)")

    return "\n".join(lines)


# ── Bot poller ────────────────────────────────────────────────


class BotPoller:
    """Poll group chat for @mentions and reply from MemALL memory."""

    def __init__(self, agent: str, creds: dict):
        self.agent = agent
        self.bot_open_id = creds.get("open_id", "")
        self.sessions = SessionStore()
        self._seen: set[str] = set()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._recv_app_id = creds["app_id"]
        self._recv_app_secret = creds["app_secret"]
        from memall.lark.consumer_helpers import ensure_profile
        ensure_profile(agent, self._recv_app_id, self._recv_app_secret)
        self._seed_seen()

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name=f"poll-{self.agent}")
        self._thread.start()
        logger.info("bot(%s) polling started (interval=%ds)", self.agent, _POLL_INTERVAL)

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        while self._running:
            try:
                self._poll_once()
            except Exception as e:
                logger.exception("bot(%s) poll error: %s", self.agent, e)
            time.sleep(_POLL_INTERVAL)

    def _seed_seen(self) -> None:
        """Pre-fill seen set with existing messages to avoid re-processing on startup."""
        msgs = _fetch_latest_messages(self.agent, page_size=20)
        if msgs:
            for m in msgs:
                mid = m.get("message_id", "")
                if mid:
                    self._seen.add(mid)
            logger.info("bot(%s) pre-seeded %d seen message IDs", self.agent, len(msgs))

    def _poll_once(self) -> None:
        msgs = _fetch_latest_messages(self.agent)
        if not msgs:
            return
        for msg in reversed(msgs):
            self._process_message(msg)

    def _process_message(self, msg: dict) -> None:
        if not isinstance(msg, dict):
            return
        msg_id = msg.get("message_id", "")
        if not msg_id or msg_id in self._seen:
            return
        self._seen.add(msg_id)
        # Keep seen set bounded
        if len(self._seen) > 2000:
            self._seen = set(list(self._seen)[-1000:])

        msg_type = msg.get("msg_type", "")
        if msg_type not in ("text", "post"):
            return
        sender = msg.get("sender", {})
        sender_id = sender.get("id", "") if isinstance(sender, dict) else ""
        sender_type = sender.get("sender_type", "") if isinstance(sender, dict) else ""

        # Skip bot's own messages to avoid echo
        if sender_type == "app":
            return

        # Group: check if bot is @mentioned via structured mentions field
        if not self.bot_open_id:
            return
        mentions = msg.get("mentions", [])
        mentioned = any(
            m.get("id") == self.bot_open_id
            for m in (mentions if isinstance(mentions, list) else [])
        )
        if not mentioned:
            return

        chat_id = msg.get("chat_id", _CHAT_ID)
        content = msg.get("content", "")
        text = content
        if isinstance(content, str):
            try:
                p = json.loads(content)
                if isinstance(p, dict):
                    text = p.get("text", content)
            except (json.JSONDecodeError, TypeError):
                logger.warning("consumer.py: silent error", exc_info=True)

        # Strip @mentions to get user text
        user_text = text
        for m in (mentions if isinstance(mentions, list) else []):
            key = m.get("key", "")
            if key:
                user_text = user_text.replace(key, "", 1).strip()
        user_text = user_text.strip()

        if not user_text:
            return

        history = self.sessions.get_history(self.agent, chat_id, sender_id)

        logger.info("bot(%s) @from %s history=%d: %s",
                     self.agent, sender_id[:20], len(history) // 2, user_text[:60])

        reply_text = _answer_from_memory(self.agent, user_text, history)
        if not reply_text:
            logger.warning("bot(%s) no memory response", self.agent)
            reply_text = f"[{self.agent}] 没有找到相关信息"

        self.sessions.append(self.agent, chat_id, sender_id, user_text, reply_text)

        # Reply via lark-cli
        r = _run_lark(self.agent, [
            "im", "+messages-reply",
            "--message-id", msg_id,
            "--as", "bot",
            "--text", reply_text[:1500],
        ])
        if r.get("ok"):
            logger.info("bot(%s) replied to %s", self.agent, msg_id[:30])
        else:
            logger.warning("bot(%s) reply fail: %s", self.agent, r.get("error", "?"))


# ── Entry point ────────────────────────────────────────────────


def main() -> None:
    logger.info("starting polling bridge")
    # Ensure profiles exist for all configured bots
    from memall.lark.consumer_helpers import ensure_profile
    all_creds = load_all()
    for agent, creds in all_creds.items():
        if creds.get("app_id") and creds.get("app_secret"):
            ensure_profile(agent, creds["app_id"], creds["app_secret"])

    bots = []
    for agent, creds in all_creds.items():
        if not creds.get("app_id") or not creds.get("app_secret"):
            continue
        if not creds.get("open_id"):
            logger.info("skip %s: no open_id", agent)
            continue
        bot = BotPoller(agent, creds)
        bot.start()
        bots.append(bot)
        logger.info("bot(%s) started (polling)", agent)

    if not bots:
        logger.warning("no bots started")
        return

    try:
        while any(b._thread and b._thread.is_alive() for b in bots):
            time.sleep(2)
    except KeyboardInterrupt:
        logger.info("shutting down")


if __name__ == "__main__":
    from memall.core.log_setup import configure as configure_logging; configure_logging()
    main()