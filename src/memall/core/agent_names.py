"""Agent-name validation — one rule set, shared by every entry point.

Why this module exists (audit R3-12)
------------------------------------
Agent identity used to be normalised in two places with two different
contracts:

  * ``core.thin_waist.normalize_agent_name`` — strips, lowercases, validates,
    and *redirects* anything suspicious to ``"system"``;
  * ``mcp.session_identity.canonical_agent`` — strips and lowercases only,
    validating nothing.

The read paths only ever ran the second one.  Names the write path rejects as
artefacts — embedded timestamps (``dsh_20261031``), single characters,
template leaks — could therefore still handshake and become a *viewer*
identity, diverging from the identity their rows were stored under.  A
viewer whose name does not match the store is the same failure mode as the
original F3 bug: every agent-scoped query silently returns nothing.

The two behaviours are deliberate and now explicit:

  * :func:`validate_agent_name` — returns the name, or ``""`` when it is not
    a usable identity.  Callers that can refuse should refuse.
  * :func:`normalize_agent_name` — the write-path contract: it must always
    return *some* identity, so an unusable one falls back to ``"system"``
    (with a warning, never silently).

Both read their rules from here so the two can no longer drift.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Lowercase letters/digits, underscore, @, dot, hyphen, CJK.
_VALID_AGENT_RE = re.compile(r"^[a-z0-9_@.\u4e00-\u9fff-]+$")

# Generic words that are never an identity: they are classifier labels, UI
# section names, or the parse fallback.  A row attributed to one of these is
# unattributable.
AGENT_BLACKLIST = frozenset({
    "architecture",
    "brainstorm",
    "unknown",
    "session_active",
    "general",
})

# Embedded dates and long digit runs mark a per-run test/probe identity
# (``autocapdimtsxcg`` style suffixes, ``dsh_20261031``).  The digit run is
# 6+, not 10+: ``20261031`` (YYYYMMDD) is 8 digits and is exactly the shape a
# timestamp-suffixed test identity arrives in, so a 10-digit threshold let it
# through.  Hyphenated dates stay covered for ``dsh-2026-10-31``.
AGENT_TAG_RE = re.compile(r"(\d{4}-\d{2}-\d{2}|\d{6,})")

# Distinct inputs already warned about, bounded so a spammy producer cannot
# grow it without limit.
_WARNED: set = set()


def _warn_redirect(original: str, why: str) -> None:
    key = (original or "").strip()[:80]
    if key in _WARNED or len(_WARNED) > 200:
        return
    _WARNED.add(key)
    logger.warning(
        "agent_name %r redirected to 'system': %s. An unusable identity means "
        "the memory is attributed to the fallback and agent-scoped queries "
        "will miss it; fix the caller that supplied it.",
        key, why,
    )


def _invalid_reason(name: str) -> str:
    """Why *name* is not a usable identity, or "" when it is fine."""
    if not name:
        return "empty"
    if not _VALID_AGENT_RE.match(name):
        return "invalid characters"
    if name in AGENT_BLACKLIST:
        return "reserved/generic name"
    if AGENT_TAG_RE.search(name):
        return "embedded timestamp or digit run"
    if len(name) < 2:
        return "single character"
    if name.endswith(".agent_name"):
        return "template variable leak"
    if "{" in name or "}" in name:
        return "template delimiter"
    cjk = sum(1 for c in name if "\u4e00" <= c <= "\u9fff" or "\u3400" <= c <= "\u4dbf")
    if cjk and len(name) == cjk and cjk < 2:
        return "single CJK character"
    return ""


def validate_agent_name(name) -> str:
    """Normalised identity, or ``""`` when *name* is not usable.

    Use this where the caller can sensibly refuse — a handshake, a permission
    grant, a publish attribution.  Refusing is better than redirecting: the
    caller gets an error instead of acting under an identity that does not
    match the stored rows.
    """
    raw = str(name or "").strip().lower()
    if not raw:
        return ""
    return "" if _invalid_reason(raw) else raw


def normalize_agent_name(name) -> str:
    """Write-path contract: always returns an identity, falling back to ``"system"``.

    Kept for callers that must not fail a write outright (capture, import).
    The fallback is logged, never silent — a silent rename was what made an
    earlier timestamp-suffixed test agent impossible to trace.
    """
    raw = str(name or "").strip().lower()
    if not raw:
        return "system"
    reason = _invalid_reason(raw)
    if reason:
        _warn_redirect(name, reason)
        return "system"
    return raw
