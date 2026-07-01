"""Shared utility functions used across core, pipeline, and other modules.

Consolidates duplicate ``_now``, ``_unwrap``, ``_parse_ts`` definitions that
were previously copy-pasted across multiple files.
"""

from datetime import datetime, timezone
from typing import Optional


def now_iso() -> str:
    """Return current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def unwrap(value):
    """Unwrap {value: X, _meta: {...}} → bare X.

    cleanup.py's _migrate_value() wraps metadata fields in a versioned
    envelope during the nightly sweep.  All reads of discussion metadata
    must unwrap to get the actual data.
    """
    if isinstance(value, dict) and "_meta" in value and "value" in value:
        return value["value"]
    return value


def parse_ts(ts_str: str) -> Optional[datetime]:
    """Parse ISO timestamp string to timezone-aware datetime.

    Supports:
      - ``%Y-%m-%dT%H:%M:%S.%f``
      - ``%Y-%m-%dT%H:%M:%S``
      - ``%Y-%m-%d``
      - Python's ``fromisoformat`` (as fallback for timezone-offset strings)
    """
    if not ts_str:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(ts_str[:26], fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None
