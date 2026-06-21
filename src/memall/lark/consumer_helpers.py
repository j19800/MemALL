"""Shared helpers for Feishu bot operations."""

import json
import pathlib

_PROFILES_DIR = pathlib.Path.home() / ".memall" / "lark-cli-profiles"


def ensure_profile(agent: str, app_id: str, app_secret: str) -> str:
    """Create ``~/.memall/lark-cli-profiles/<agent>/.lark-cli/hermes/config.json``
    containing a single-app config.

    Returns the Windows path for ``USERPROFILE``.
    """
    profile_dir = _PROFILES_DIR / agent
    lark_dir = profile_dir / ".lark-cli" / "hermes"
    lark_dir.mkdir(parents=True, exist_ok=True)

    cfg = {
        "apps": [
            {
                "appId": app_id,
                "appSecret": app_secret,
                "brand": "feishu",
                "defaultAs": "bot",
                "strictMode": "bot",
                "users": [],
            }
        ]
    }
    (lark_dir / "config.json").write_text(
        json.dumps(cfg, ensure_ascii=False), encoding="utf-8"
    )
    return str(profile_dir.resolve())