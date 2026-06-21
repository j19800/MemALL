"""Multi-bot Feishu/Lark API client.

Manages independent ``tenant_access_token`` per agent (bot identity),
with caching and auto-refresh (tokens expire after 2 h).
"""

import json
import logging
import time
import urllib.request
import urllib.error
from typing import Optional

from memall.lark.credentials import get

logger = logging.getLogger(__name__)

# Cache: {agent_name: {"token": str, "expires_at": float}}
_TOKEN_CACHE: dict[str, dict] = {}

_OPEN_API_BASE = "https://open.feishu.cn/open-apis"


def _tenant_access_token(app_id: str, app_secret: str) -> Optional[str]:
    """Obtain a ``tenant_access_token`` from the Feishu auth API.

    Returns ``None`` on failure (network / invalid credentials).
    """
    url = f"{_OPEN_API_BASE}/auth/v3/tenant_access_token/internal"
    body = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("code") != 0:
            logger.warning("tenant_access_token error: %s", data.get("msg", "unknown"))
            return None
        return data["tenant_access_token"]
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        logger.warning("tenant_access_token request failed: %s", e)
        return None


def get_access_token(agent_name: str) -> Optional[str]:
    """Get a cached (or fresh) ``tenant_access_token`` for *agent_name*.

    Returns ``None`` if the agent has no credentials or auth fails.
    """
    now = time.time()
    cached = _TOKEN_CACHE.get(agent_name)
    if cached and cached["expires_at"] > now + 60:
        return cached["token"]

    creds = get(agent_name)
    if not creds:
        logger.warning("no bot credentials for agent '%s'", agent_name)
        return None

    token = _tenant_access_token(creds["app_id"], creds["app_secret"])
    if token:
        _TOKEN_CACHE[agent_name] = {"token": token, "expires_at": now + 7100}
    return token


def send_message(
    agent_name: str,
    chat_id: str,
    msg_type: str,
    content: str,
    *,
    timeout: int = 15,
) -> dict:
    """Send a message as the bot of *agent_name* to *chat_id*.

    Args:
        agent_name: Which agent's bot to send as.
        chat_id: Target chat (``oc_xxx``) or user (``ou_xxx``).
        msg_type: ``"text"``, ``"post"``, ``"interactive"``, etc.
        content: JSON-encoded content body for the message type.
        timeout: HTTP request timeout in seconds.

    Returns:
        The Feishu API response dict (always a dict; ``{"ok": False}`` on
        failure so callers don't need to handle exceptions).
    """
    token = get_access_token(agent_name)
    if not token:
        return {"ok": False, "error": "no access token"}

    url = f"{_OPEN_API_BASE}/im/v1/messages?receive_id_type=chat_id"
    body = json.dumps({
        "receive_id": chat_id,
        "msg_type": msg_type,
        "content": content,
    }).encode("utf-8")

    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("code") != 0:
            return {"ok": False, "error": data.get("msg", str(data))}
        return {"ok": True, "data": data.get("data", {})}
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        logger.warning("send_message(%s) failed: %s", agent_name, e)
        return {"ok": False, "error": str(e)}


def get_bot_open_id(agent_name: str) -> Optional[str]:
    """Fetch the bot's own ``open_id`` by calling ``/v1/bot``.

    Returns ``None`` on failure.  The result is **not** cached here
    (callers should store it in bot_credentials.json as ``open_id``).
    """
    token = get_access_token(agent_name)
    if not token:
        return None
    url = f"{_OPEN_API_BASE}/bot/v3/info"
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("code") == 0:
            return data.get("data", {}).get("open_id")
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        logger.warning("get_bot_open_id(%s) failed: %s", agent_name, e)
    return None


def reply_message(
    agent_name: str,
    message_id: str,
    msg_type: str,
    content: str,
    *,
    timeout: int = 15,
) -> dict:
    """Reply to a specific message as the bot of *agent_name*.

    Args:
        agent_name: Which agent's bot to reply as.
        message_id: The ``message_id`` (``om_xxx``) to reply to.
        msg_type: ``"text"``, ``"post"``, ``"interactive"``, etc.
        content: JSON-encoded content body.
        timeout: HTTP request timeout.

    Returns:
        API response dict (``{"ok": False}`` on failure).
    """
    token = get_access_token(agent_name)
    if not token:
        return {"ok": False, "error": "no access token"}

    url = f"{_OPEN_API_BASE}/im/v1/messages/{message_id}/reply"
    body = json.dumps({
        "msg_type": msg_type,
        "content": content,
    }).encode("utf-8")

    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("code") != 0:
            return {"ok": False, "error": data.get("msg", str(data))}
        return {"ok": True, "data": data.get("data", {})}
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        logger.warning("reply_message(%s) failed: %s", agent_name, e)
        return {"ok": False, "error": str(e)}


def clear_token_cache(agent_name: Optional[str] = None) -> None:
    """Clear cached access tokens (all or for one agent)."""
    if agent_name:
        _TOKEN_CACHE.pop(agent_name, None)
    else:
        _TOKEN_CACHE.clear()