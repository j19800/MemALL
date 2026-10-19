"""Prompt-label localisation (the injection surface).

Ported from dsh-meow-memory's ``src/prompts/<lang>/labels.md`` idea, scoped to
what an agent actually re-reads every session: the section labels and connective
copy of the session-injection block.  The full prompt bodies (dream, reflect,
tool descriptions) stay in their modules — those are edited rarely and read by
one agent per run, so localising them is a much larger, lower-value refactor.

Usage
-----
    from memall.i18n import L
    header = L("context_header")

The active language comes from ``prompt.lang`` (default ``zh``).  An unknown
key returns the key itself rather than raising — a missing translation degrades
to a visible placeholder instead of breaking the injection chain, which is the
same red line meow-memory draws for its global-marker lookup.

Adding a language = add a dict to ``_PACKS``.  Keys must stay in sync across
packs; :func:`missing_keys` reports gaps for tests.
"""

from __future__ import annotations

from typing import Dict

# zh is the source of truth for the key set; other packs are checked against it.
_PACKS: Dict[str, Dict[str, str]] = {
    "zh": {
        "context_header": "[CONTEXT] 会话上下文（首段先概括以下信息）：",
        "decisions_pending": "待决",
        "decisions_in_progress": "进行中",
        "distill_pending": "组待写摘要，调 memall_system action=distill 查看",
        "continuity_prefix": "距上次约",
        # narrative greeting
        "narrative_short_absence": "不久未见",
        "narrative_hours_since": "距离上次会话约 {h} 小时",
        "narrative_one_day": "隔了一天",
        "narrative_days_short": "隔了 {days} 天",
        "narrative_days_long": "距上次会话约 {days} 天",
        "narrative_new_session": "新会话开始",
        "narrative_new_memories": "期间新增 {n} 条记忆",
        "narrative_epoch": "当前阶段「{label}」",
        "narrative_todo_p0": "有 {n} 件待办（含 P0 紧急项）",
        "narrative_todo": "有 {n} 件待办未处理",
        "narrative_open_arcs": "{n} 条决策未闭合",
        "narrative_weekly": "本周有回顾总结",
        "narrative_pulse": "有长期未动的待办",
        "narrative_joiner": "，",
        "narrative_term": "。",
        # session.py L7 instruction blocks (mcp/tools/session.py)
        "l7_rules_header": "【行为准则（L7 经验教训）】",
        "l7_prefs_header": "【工作偏好（L7 偏好提取）】",
        "l7_reflect_header": "【历史反思（L6 反思摘要）】",
    },
    "en": {
        "context_header": "[CONTEXT] Session context (summarise these first):",
        "decisions_pending": "open",
        "decisions_in_progress": "in progress",
        "distill_pending": "groups await summaries — run memall_system action=distill",
        "continuity_prefix": "since last session ~",
        "narrative_short_absence": "back shortly",
        "narrative_hours_since": "about {h}h since the last session",
        "narrative_one_day": "back after a day",
        "narrative_days_short": "back after {days} days",
        "narrative_days_long": "about {days} days since the last session",
        "narrative_new_session": "New session started",
        "narrative_new_memories": "{n} new memories since",
        "narrative_epoch": "current phase “{label}”",
        "narrative_todo_p0": "{n} open todos (incl. P0 urgent)",
        "narrative_todo": "{n} open todos",
        "narrative_open_arcs": "{n} decision arcs unclosed",
        "narrative_weekly": "weekly review available",
        "narrative_pulse": "long-stale todos pending",
        "narrative_joiner": ", ",
        "narrative_term": ".",
        "l7_rules_header": "[BEHAVIOUR RULES (L7 lessons)]",
        "l7_prefs_header": "[WORK PREFERENCES (L7)]",
        "l7_reflect_header": "[PAST REFLECTIONS (L6)]",
    },
}

_DEFAULT_LANG = "zh"


def _active_lang() -> str:
    try:
        from memall.config import get_config

        lang = get_config("prompt.lang", _DEFAULT_LANG)
    except Exception:
        return _DEFAULT_LANG
    return lang if lang in _PACKS else _DEFAULT_LANG


def L(key: str, **fmt) -> str:
    """Translate *key* in the active language; fall back to zh, then the key.

    Keyword arguments are applied as ``str.format`` substitutions (e.g.
    ``L("narrative_days_since", days=3)``).
    """
    lang = _active_lang()
    text = _PACKS.get(lang, {}).get(key)
    if text is None:
        text = _PACKS[_DEFAULT_LANG].get(key)
    if text is None:
        # Missing translation must not break injection — return the key so the
        # gap is visible in the output instead of raising on the hot path.
        return key
    if fmt:
        try:
            return text.format(**fmt)
        except (KeyError, IndexError, ValueError):
            return text
    return text


def missing_keys() -> Dict[str, list]:
    """Keys present in zh but absent from each other pack (for tests)."""
    base = set(_PACKS[_DEFAULT_LANG])
    out: Dict[str, list] = {}
    for lang, pack in _PACKS.items():
        if lang == _DEFAULT_LANG:
            continue
        gap = sorted(base - set(pack))
        if gap:
            out[lang] = gap
    return out


def available_langs() -> list:
    return sorted(_PACKS)


__all__ = ["L", "missing_keys", "available_langs"]