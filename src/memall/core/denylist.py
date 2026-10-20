"""Write deny-list matching for ``security.disabled_projects`` / ``disabled_agents``.

Glob patterns, not exact strings
--------------------------------
The first implementation compared names with ``==``, which made the config
awkward for the case it exists to solve: test and one-off identities arrive
with per-run suffixes (``autocapdimtsxcg``, ``autocapbowdsatj``, ...) so an
exact list can never keep up — you would have to add every variant after the
fact, i.e. after the pollution is already stored.  Patterns let the deployment
ban the *family* up front: ``autocap*``, ``probe*``, ``*-test``.

Semantics
---------
* Patterns use ``fnmatch`` case-insensitively (``*``, ``?``, ``[seq]``).
* A pattern with no glob metacharacters behaves exactly like the old exact
  match, so existing configs keep working unchanged.
* Project patterns match the whole project string; agent patterns match the
  *normalized* agent name (the same form stored in the DB), so a pattern is
  written against what a query would see.

Reads are deliberately NOT gated — this decides whether to *store*, not what
may be inspected.  See ``capture._check_disabled`` and
``scheduler.agent_round.get_active_agents`` for the two enforcement points.
"""

from __future__ import annotations

import fnmatch
import logging
from typing import Any, Iterable, List

logger = logging.getLogger(__name__)

# Set once we have warned about a malformed pattern, so a bad entry cannot
# spam the log on every capture.
_BAD_PATTERN_WARNED: set = set()


def _patterns(raw: Any) -> List[str]:
    """Normalise a configured list into lowercase patterns."""
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [p for p in raw.replace(",", " ").split() if p.strip()]
    if not isinstance(raw, (list, tuple, set, frozenset)):
        return []
    out: List[str] = []
    for p in raw:
        s = str(p).strip()
        if s:
            out.append(s.lower())
    return out


def matches_any(value: str, patterns: Iterable[str]) -> bool:
    """True when *value* matches any pattern (case-insensitive)."""
    if not value:
        return False
    v = str(value).strip().lower()
    if not v:
        return False
    for pat in patterns:
        try:
            if fnmatch.fnmatchcase(v, pat):
                return True
        except Exception:
            if pat not in _BAD_PATTERN_WARNED and len(_BAD_PATTERN_WARNED) < 50:
                _BAD_PATTERN_WARNED.add(pat)
                logger.warning("denylist: unusable pattern %r ignored", pat)
    return False


def disabled_project_patterns() -> List[str]:
    from memall.config import get_config

    return _patterns(get_config("security.disabled_projects", []))


def disabled_agent_patterns() -> List[str]:
    from memall.config import get_config

    return _patterns(get_config("security.disabled_agents", []))


def is_disabled_project(project: str) -> bool:
    return matches_any(project, disabled_project_patterns())


def is_disabled_agent(agent_name: str) -> bool:
    """Check against normalized agent names (what the DB stores)."""
    pats = disabled_agent_patterns()
    if not pats:
        return False
    try:
        from memall.core.thin_waist import normalize_agent_name

        normalized = normalize_agent_name(agent_name)
    except Exception:
        normalized = (agent_name or "").strip().lower()
    return matches_any(normalized, pats) or matches_any(agent_name, pats)


__all__ = [
    "matches_any",
    "is_disabled_project",
    "is_disabled_agent",
    "disabled_project_patterns",
    "disabled_agent_patterns",
]