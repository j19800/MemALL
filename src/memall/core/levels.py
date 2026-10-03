"""Canonical memory ``level`` enum — the single source of truth (ADR-0001).

The storage layer only accepts ``P0/P1/P2`` + ``L1..L11``: that exact set is
enforced by the ``CHECK`` constraint on ``memories.level`` (see ``db.py``
``SCHEMA_SQL`` and migration 028).  Every application entry point must normalize
to this set *before* writing, otherwise SQLite raises ``IntegrityError`` at
INSERT time and the write is lost.

Historically the enum was duplicated in four places that disagreed
(``agent_memory.add`` accepted ``P3/P4``, ``thin_waist._sanitize_level`` emitted
``"medium"/"high"``), so callers could pass values the database rejects.  Import
``normalize_level`` / ``VALID_LEVELS`` from here instead of re-declaring them.
"""

import logging
import re

logger = logging.getLogger(__name__)

# Priority band (how important) and lifecycle band (what kind of memory).
PRIORITY_LEVELS = ("P0", "P1", "P2")
LIFECYCLE_LEVELS = (
    "L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8", "L9", "L10", "L11",
)

# Canonical order — used for DDL generation and display.
LEVEL_ORDER = PRIORITY_LEVELS + LIFECYCLE_LEVELS
VALID_LEVELS = frozenset(LEVEL_ORDER)

DEFAULT_LEVEL = "P2"

# Ready-to-interpolate SQL list, e.g. "'P0', 'P1', 'P2', 'L1', ...".
LEVEL_SQL_LIST = ", ".join("'%s'" % lv for lv in LEVEL_ORDER)

# Free-form producer vocabulary → priority band.  Producers that speak in
# words (TradingAgents et al.) must not be able to smuggle a value the storage
# layer rejects; collapse them into the band instead.
_ALIASES = {
    "critical": "P0",
    "urgent": "P0",
    "high": "P1",
    "important": "P1",
    "medium": "P2",
    "normal": "P2",
    "low": "P2",
}

_LEVEL_RE = re.compile(r"^([PL])(\d+)$", re.IGNORECASE)


def normalize_level(level: object, default: str = DEFAULT_LEVEL) -> str:
    """Coerce *level* into the canonical enum, never returning an invalid value.

    Accepts the canonical forms case-insensitively plus the word aliases above.
    Out-of-range numeric forms (``P3``, ``L99``) and unknown strings are clamped
    to *default* with a warning, so a bad producer degrades to a safe level
    instead of aborting the write at the database boundary.
    """
    if not isinstance(level, str) or not level.strip():
        return default
    lvl = level.strip()
    if lvl in VALID_LEVELS:
        return lvl
    upper = lvl.upper()
    if upper in VALID_LEVELS:
        return upper
    m = _LEVEL_RE.match(upper)
    if m:
        kind, num = m.group(1), int(m.group(2))
        if kind == "L" and 1 <= num <= 11:
            return upper
        logger.warning(
            "normalize_level: %r is outside the canonical enum; clamped to %s",
            level, default,
        )
        return default
    alias = _ALIASES.get(lvl.lower())
    if alias is not None:
        logger.warning("normalize_level: alias %r mapped to %s", level, alias)
        return alias
    logger.warning(
        "normalize_level: unknown level %r; falling back to %s", level, default
    )
    return default