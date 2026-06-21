"""Multi-bot credential management.

Each AI agent (claude, zcode, opencode) can have its own Feishu bot
with independent app credentials.  Stored as JSON under
``~/.memall/bot_credentials.json``.

Schema (per agent)::

    {
      "app_id": "cli_xxx",
      "app_secret": "***",
      "open_id": "ou_xxx",        // bot's own open_id (for @mention detection)
      "chat_id": "oc_xxx",        // default notification chat
    }
"""

import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_CREDENTIALS_PATH = Path.home() / ".memall" / "bot_credentials.json"


def _ensure_file():
    _CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not _CREDENTIALS_PATH.exists():
        _CREDENTIALS_PATH.write_text("{}", encoding="utf-8")


def load_all() -> dict[str, dict]:
    """Load all bot credentials.  Returns ``{agent_name: creds_dict}``."""
    _ensure_file()
    try:
        return json.loads(_CREDENTIALS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("failed to load bot_credentials.json; returning {}")
        return {}


def save_all(creds: dict[str, dict]) -> None:
    """Overwrite the full credential file."""
    _ensure_file()
    # Mask secret in the in-memory copy before writing? No — write the real
    # secret; the file needs it to authenticate.  Just ensure proper perms.
    tmp = f"{_CREDENTIALS_PATH}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(creds, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _CREDENTIALS_PATH)


def get(agent_name: str) -> Optional[dict]:
    """Return a single agent's bot credentials, or ``None``."""
    return load_all().get(agent_name)


def set_credential(
    agent_name: str,
    app_id: str,
    app_secret: str,
    open_id: str = "",
    chat_id: str = "",
) -> None:
    """Set (create or update) one agent's bot credentials."""
    all_creds = load_all()
    all_creds[agent_name] = {
        "app_id": app_id,
        "app_secret": app_secret,
        "open_id": open_id,
        "chat_id": chat_id,
    }
    save_all(all_creds)


def remove(agent_name: str) -> bool:
    """Remove one agent's credentials.  Returns ``True`` if existed."""
    all_creds = load_all()
    ok = all_creds.pop(agent_name, None) is not None
    if ok:
        save_all(all_creds)
    return ok


def list_agents() -> list[str]:
    """Return agent names that have bot credentials configured."""
    return list(load_all().keys())