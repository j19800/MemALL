"""
Auto-Capture — 自动记忆捕获管道

自动从 MCP 工具调用、对话、代码变更中捕获关键信息，
无需手动调用 capture()。

工作流程:
1. 监听 MCP 工具调用 (post_tool_use hook)
2. 按 action 提取高价值内容 (decision / reflection / stance / discussion)
3. 智能分配 level/category/subject (intelligent_capture)
4. 存入记忆系统

提取策略
--------
不是"任何调用都存"——那会把检索日志淹没。只提取**带结论或判断**的
内容，且每个来源都有明确的去重键：

| 来源 | 提取字段 | 目标层级 |
|---|---|---|
| ask          | result.answer + question     | L4 决策推演 |
| reflect      | arguments.context            | L6 反思 |
| discussion   | arguments.arguments (立场)   | L4 讨论结论 |
| capture 家族  | 不捕获（已显式入库）          | — |
| 纯检索类      | 不捕获（无结论）              | — |
"""

import hashlib
import json
import logging
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

from memall.core.lifecycle import (
    HookRegistry, HookDef,
    HOOK_POST_TOOL_USE,
)

logger = logging.getLogger(__name__)

# ── Structured skip/hit reasons for observability (additive, non-breaking) ──────
_REASON_CAPTURED = "captured"
_REASON_SKIPPED_DISABLED = "skipped_disabled"
_REASON_SKIPPED_EMPTY = "skipped_empty"
_REASON_SKIPPED_MIN_LENGTH = "skipped_min_length"
_REASON_SKIPPED_ERROR_PAYLOAD = "skipped_error_payload"
_REASON_SKIPPED_DEDUP_IN_PROCESS = "skipped_dedup_in_process"
_REASON_SKIPPED_DEDUP_PERSISTENT = "skipped_dedup_persistent"
_REASON_SKIPPED_LOW_INFORMATION = "skipped_low_information"
_REASON_CAPTURE_ERROR = "capture_error"

# Last decision per (tool_name, action, target) key for test/trace observability.
# Bounded in-process cache; best-effort only.
_last_decisions: dict[tuple, dict] = {}
_last_decisions_lock = threading.Lock()
_LAST_DECISIONS_MAX = 512


def _record_decision(key: tuple, reason: str, **extra) -> dict:
    entry = {"reason": reason, "at": datetime.now(timezone.utc).isoformat(), **extra}
    with _last_decisions_lock:
        _last_decisions[key] = entry
        if len(_last_decisions) > _LAST_DECISIONS_MAX:
            for k in list(_last_decisions.keys())[: _LAST_DECISIONS_MAX // 4]:
                _last_decisions.pop(k, None)
    return entry


def get_last_auto_capture_decision(tool_name: str = "", action: str = "", target: str = "") -> Optional[dict]:
    """Return last recorded auto-capture decision for observability/tests."""
    key = (str(tool_name), str(action), str(target))
    with _last_decisions_lock:
        v = _last_decisions.get(key)
        return dict(v) if isinstance(v, dict) else None


def clear_auto_capture_decisions() -> None:
    with _last_decisions_lock:
        _last_decisions.clear()


# ── 内容长度下限 ───────────────────────────────────────────
# 短于此的内容不构成可复用知识（intelligent_capture 另有可配置门槛）。
_MIN_CONTENT_LEN = 20


def _auto_capture_min_len() -> int:
    """Config-aware minimum content length for auto-capture extraction."""
    try:
        from memall.config import get_config
        # Prefer explicit auto-capture knob, fall back to quality gate, then legacy default.
        v = get_config("capture.auto_capture_min_content_len", None)
        if v is None:
            v = get_config("quality_gate.lenient_min_len", _MIN_CONTENT_LEN)
        n = int(v)
    except Exception:
        n = _MIN_CONTENT_LEN
    # Keep a sane floor/ceiling to avoid disabling capture or over-filtering.
    return max(10, min(n, 500))


def _auto_capture_info_increment_min_ratio() -> float:
    try:
        from memall.config import get_config
        v = float(get_config("capture.auto_capture_info_increment_min_ratio", 0.15) or 0.15)
    except Exception:
        v = 0.15
    return max(0.0, min(v, 1.0))


def _auto_capture_info_increment_enabled() -> bool:
    try:
        from memall.config import get_config
        return bool(get_config("capture.auto_capture_info_increment_enabled", True))
    except Exception:
        return True


def _as_text(value) -> str:
    """Coerce an argument value to trimmed text (lists/dicts → JSON)."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    try:
        return json.dumps(value, ensure_ascii=False).strip()
    except Exception:
        return str(value).strip()


def _normalize_for_dedup(text: str) -> str:
    """Normalize text for fingerprinting/information comparison."""
    return re.sub(r"\s+", " ", str(text or "").strip()).lower()


def _compute_fingerprint(content: str) -> str:
    """Stable fingerprint for auto-capture dedup (normalized content hash)."""
    norm = _normalize_for_dedup(content)
    return hashlib.sha256(norm.encode("utf-8", errors="ignore")).hexdigest()


def _tokenize_for_increment(text: str) -> set[str]:
    """Cheap tokenization for information-increment heuristic (CJK + word tokens)."""
    t = _normalize_for_dedup(text)
    if not t:
        return set()
    return set(re.findall(r"[a-z0-9_]{2,}|[\u4e00-\u9fff]{1,2}", t))


def _information_increment_ratio(new_text: str, seen_texts: list[str]) -> float:
    """Return fraction of new tokens not present in seen texts. 1.0 = fully novel."""
    new_tokens = _tokenize_for_increment(new_text)
    if not new_tokens:
        return 0.0
    seen: set[str] = set()
    for s in seen_texts:
        seen |= _tokenize_for_increment(s)
    if not seen:
        return 1.0
    novel = new_tokens - seen
    return len(novel) / max(1, len(new_tokens))


def _looks_low_information(content: str) -> bool:
    """Heuristic gate to skip obvious low-signal auto-captures."""
    text = str(content or "").strip()
    if not text:
        return True
    low = text.lower()
    trivial_exact = {
        "ok", "okay", "yes", "no", "done", "thanks", "thank you",
        "好的", "可以", "收到", "知道了", "明白", "嗯", "哦", "行", "完成", "谢谢",
        "没问题", "完全没问题", "可以的", "好的呀", "好的呢",
    }
    compact = re.sub(r"[\s，。,.!！?？~～…·、:：;；\"'“”‘’()（）\[\]【】<>《》-]+", "", text)
    if low in trivial_exact or compact in trivial_exact:
        return True
    trivial_phrases = ("可以", "好的", "行", "没问题", "完全没问题", "可以的", "收到", "知道了", "明白", "ok", "okay", "yes", "no", "done", "thanks")
    if compact and all(any(tp in part for tp in trivial_phrases) for part in re.split(r"(可以|好的|行|没问题|完全没问题|可以的|收到|知道了|明白|ok|okay|yes|no|done|thanks|的|呀|啊|呢|哦)", compact, flags=re.IGNORECASE) if part):
        # composed only of trivial confirmation tokens/fillers
        if len(compact) <= 20:
            return True
    if re.fullmatch(r"(可以|好的|行|没问题|完全没问题|可以的|收到|知道了|明白|ok|okay|yes|no)(的|呀|啊|呢|哦)?", compact, flags=re.IGNORECASE):
        return True
    tokens = _tokenize_for_increment(text)
    if len(text) >= 20 and len(tokens) <= 3:
        return True
    alnum_cjk = len(re.findall(r"[A-Za-z0-9\u4e00-\u9fff]", text))
    if alnum_cjk < 8 and len(text) >= 20:
        return True
    return False


def _decision_target_key(tool_name: str, action: str, hints: Optional[dict]) -> tuple:
    meta = (hints or {}).get("metadata") or {}
    target = (meta.get("target_memory_id")
              or meta.get("discussion_id")
              or meta.get("subject_of")
              or "")
    return (str(tool_name), str(action), str(target))


def _extract_ask(arguments: dict, result: dict) -> tuple[str, dict]:
    """Digital-twin stance answer — a reasoned conclusion worth keeping."""
    question = _as_text(arguments.get("question"))
    answer = _as_text(result.get("answer"))
    if not question or not answer:
        return "", {}
    subject = _as_text(arguments.get("subject") or arguments.get("agent_name"))
    content = f"问：{question}\n答：{answer}"
    return content, {
        "subject": f"推演: {question[:40]}",
        "category": "decision",
        "level": "L4",
        "metadata": {"source": "ask", "subject_of": subject,
                     "mode": arguments.get("mode", "stance"),
                     "answer_excerpt": answer[:120]},
    }


def _extract_reflect(arguments: dict, result: dict) -> tuple[str, dict]:
    """A stance taken on an existing L6 reflection — feedback on a lesson."""
    memory_id = arguments.get("memory_id")
    context = _as_text(arguments.get("context"))
    verb = _as_text(arguments.get("action") or arguments.get("reflect_sub_action"))
    if not memory_id or not context:
        return "", {}
    verb_cn = {"agree": "认同", "disagree": "反对", "probe": "存疑"}.get(verb, verb or "反馈")
    content = f"对反思 #{memory_id} {verb_cn}：{context}"
    return content, {
        "subject": f"反思反馈: #{memory_id}",
        "category": "reflection",
        "level": "L6",
        "metadata": {"source": "reflect", "target_memory_id": memory_id,
                     "verb": verb},
    }


def _extract_discussion(arguments: dict, result: dict) -> tuple[str, dict]:
    """A stance recorded in a multi-agent discussion."""
    stance = _as_text(arguments.get("stance"))
    reasoning = _as_text(arguments.get("arguments"))
    discussion_id = arguments.get("discussion_id")
    agent = _as_text(arguments.get("agent_name"))
    if not stance or not reasoning:
        return "", {}
    stance_cn = {"agree": "支持", "disagree": "反对", "abstain": "弃权",
                 "pass": "弃权", "confirm": "确认", "reject": "否决"}.get(
                     stance.lower(), stance)
    content = f"[{stance_cn}] {reasoning}"
    return content, {
        "subject": f"讨论立场: #{discussion_id}",
        "category": "discussion",
        "level": "L4",
        "agent_name": agent or "",
        "metadata": {"source": "discussion", "discussion_id": discussion_id,
                     "stance": stance, "participant": agent},
    }


# action → extractor.  Anything not listed is deliberately ignored: pure
# reads (search/retrieve/timeline/...) carry no conclusion, and the capture
# family has already written its content explicitly.
_EXTRACTORS = {
    ("memall_persona", "ask"): _extract_ask,
    ("memall_system", "reflect"): _extract_reflect,
    ("memall_discussion", "respond"): _extract_discussion,
}

# TD-AUTO-SRC-01: the default whitelist. Config `capture.auto_capture_extractors`
# may further RESTRICT this set (deployment policy); extending behavior beyond
# the built-ins is deliberately explicit via register_auto_capture_extractor(),
# so every new source ships with its own extractor function and contract test.
_DEFAULT_EXTRACTOR_KEYS = (
    "memall_persona.ask",
    "memall_system.reflect",
    "memall_discussion.respond",
)


def _extractor_allowed(key: tuple) -> bool:
    """Whether (tool_name, action) passes the optional config restriction.

    Fail-open: config errors keep the built-in whitelist behavior.
    """
    try:
        from memall.config import get_config
        allowed = get_config("capture.auto_capture_extractors", None)
        if allowed is None:
            return True
        if not isinstance(allowed, (list, tuple)):
            return True
        allowed_set = {str(x).strip().lower() for x in allowed}
        return f"{key[0]}.{key[1]}".lower() in allowed_set
    except Exception:
        return True


def register_auto_capture_extractor(tool_name: str, action: str, extractor):
    """Register an additional auto-capture extractor (TD-AUTO-SRC-01).

    The built-in whitelist stays the default; this is the explicit, tested
    extension point so new sources are added deliberately (each new extractor
    should ship with its own contract test) rather than by silently widening
    the hook. Keys are (tool_name, action) exactly as matched in the hook.
    Returns the previous extractor (None if new).
    """
    key = (str(tool_name), str(action))
    prev = _EXTRACTORS.get(key)
    _EXTRACTORS[key] = extractor
    return prev


def unregister_auto_capture_extractor(tool_name: str, action: str) -> bool:
    """Remove a registered extractor. Returns True if one was removed."""
    key = (str(tool_name), str(action))
    return _EXTRACTORS.pop(key, None) is not None


def auto_capture_extractor_whitelist() -> list:
    """Current effective (tool_name, action) extractor whitelist (observability)."""
    return sorted(_EXTRACTORS.keys())


# ── Hook 入口 ─────────────────────────────────────────────


def _auto_capture_hook(tool_name: str, arguments: dict, result: str = "", **kwargs) -> None:
    """自动捕获: 从 MCP 工具调用中提取关键结论并存入记忆。"""
    action = (arguments or {}).get("action", "")
    # best-effort decision key before extraction (target refined later)
    pre_key = (str(tool_name), str(action), "")
    # fail-open config gate (default enabled for backward compatibility)
    try:
        from memall.config import get_config
        if not bool(get_config("capture.auto_capture_enabled", True)):
            _record_decision(pre_key, _REASON_SKIPPED_DISABLED)
            return
    except Exception:
        pass

    if (tool_name, action) not in _EXTRACTORS:
        # non-target actions are expected/noisy for hook matcher; do not overwrite unrelated decisions
        return
    if not _extractor_allowed((tool_name, action)):
        # deployment restriction via capture.auto_capture_extractors
        _record_decision(pre_key, _REASON_SKIPPED_DISABLED)
        return
    extractor = _EXTRACTORS.get((tool_name, action))

    # Only capture successful calls — an error payload has nothing to keep.
    try:
        parsed = json.loads(result) if result else {}
    except Exception:
        _record_decision(pre_key, _REASON_SKIPPED_ERROR_PAYLOAD)
        return
    if not isinstance(parsed, dict) or parsed.get("error"):
        _record_decision(pre_key, _REASON_SKIPPED_ERROR_PAYLOAD)
        return

    try:
        content, hints = extractor(arguments, parsed)
    except Exception as e:
        logger.debug("auto_capture: extractor failed for %s/%s: %s",
                     tool_name, action, e)
        _record_decision(pre_key, _REASON_CAPTURE_ERROR, error=str(e))
        return

    if not content:
        _record_decision(pre_key, _REASON_SKIPPED_EMPTY)
        return

    content = content.strip()
    dkey = _decision_target_key(tool_name, action, hints)
    if len(content) < _auto_capture_min_len():
        _record_decision(dkey, _REASON_SKIPPED_MIN_LENGTH, content_len=len(content), min_len=_auto_capture_min_len())
        return

    # Information-increment / low-signal gate (conservative): skip obvious boilerplate and
    # near-duplicate answers for repeated ask prompts before dedup accounting/capture.
    if _auto_capture_info_increment_enabled():
        if _looks_low_information(content):
            _record_decision(dkey, _REASON_SKIPPED_LOW_INFORMATION, gate="low_information")
            return
        if action == "ask":
            a_raw = str((hints.get("metadata") or {}).get("answer_excerpt") or "")
            if _looks_low_information(a_raw):
                _record_decision(dkey, _REASON_SKIPPED_LOW_INFORMATION, gate="ask_answer_low_information")
                return
            q = _normalize_for_dedup((arguments or {}).get("question", ""))
            a = _normalize_for_dedup(a_raw)
            # If answer adds little beyond question, treat as low increment.
            if q and a:
                inc = _information_increment_ratio(a, [q])
                min_inc = _auto_capture_info_increment_min_ratio()
                if inc < min_inc:
                    _record_decision(dkey, _REASON_SKIPPED_LOW_INFORMATION, gate="ask_increment", increment_ratio=round(inc, 4), min_ratio=min_inc)
                    return

    # Skip if this exact conclusion was already captured recently
    dedup_hit, dedup_info = _already_captured(tool_name, action, content, hints)
    if dedup_hit:
        reason = dedup_info.get("reason") or _REASON_SKIPPED_DEDUP_IN_PROCESS
        _record_decision(dkey, reason, dedup=dedup_info)
        return

    agent = hints.get("agent_name") or _as_text((arguments or {}).get("agent_name")) or "system"

    try:
        from memall.intelligent_capture import intelligent_capture

        mem_id = intelligent_capture(
            content,
            agent_name=agent,
            subject=hints.get("subject", ""),
            category=hints.get("category", "general"),
            level=hints.get("level", "P2"),
            metadata=json.dumps(hints.get("metadata", {}), ensure_ascii=False),
            owner="",
        )
        if mem_id:
            _record_decision(dkey, _REASON_CAPTURED, mem_id=int(mem_id), dedup=dedup_info)
            logger.debug("auto_capture: captured #%d from %s/%s",
                         mem_id, tool_name, action)
        else:
            _record_decision(dkey, _REASON_SKIPPED_EMPTY, mem_id=0, dedup=dedup_info)
    except Exception as e:
        _record_decision(dkey, _REASON_CAPTURE_ERROR, error=str(e), dedup=dedup_info)
        logger.debug("auto_capture: skipped (%s)", e)


# ── 去重 ──────────────────────────────────────────────────
# 进程内缓存，键 = (来源 action, 目标 ID, 内容前 64 字, agent_name)。capture() 本身有
# 精确 hash 去重，但 auto-capture 的内容每次都会带上时间戳或措辞差异，
# hash 去重拦不住，所以这里在写入前先用一个粗键挡掉明显的重复。
_recent_keys: dict[tuple, str] = {}
_RECENT_KEY_TTL = 3600  # seconds

_PERSISTENT_DEDUP_WINDOW_SECONDS = 300
_PERSISTENT_DEDUP_MIN_HITS = 2
# Hard caps for config-driven values (protection against absurd settings).
_PERSISTENT_WINDOW_MAX = 86_400      # 1 day
_PERSISTENT_MIN_HITS_MAX = 100
_PERSISTENT_RETENTION_DEFAULT_FACTOR = 12  # retention = window * factor, floor 3600s


def _persistent_dedup_settings() -> tuple:
    """Resolve (window_seconds, min_hits, retention_seconds) from config.

    Defaults preserve the existing contract exactly (300s window, 2 hits,
    retention = max(window*12, 3600)). Config keys (TD hardening):
      capture.auto_capture_persistent_window_seconds
      capture.auto_capture_persistent_min_hits
      capture.auto_capture_retention_seconds   (0/absent → derived from window)
    Fail-open to defaults on any config error; values clamped to sane bounds.
    """
    window = _PERSISTENT_DEDUP_WINDOW_SECONDS
    min_hits = _PERSISTENT_DEDUP_MIN_HITS
    retention = 0
    try:
        from memall.config import get_config
        v = get_config("capture.auto_capture_persistent_window_seconds", None)
        if v is not None:
            window = max(1, min(int(v), _PERSISTENT_WINDOW_MAX))
        v = get_config("capture.auto_capture_persistent_min_hits", None)
        if v is not None:
            min_hits = max(1, min(int(v), _PERSISTENT_MIN_HITS_MAX))
        v = get_config("capture.auto_capture_retention_seconds", None)
        if v is not None:
            retention = max(0, int(v))
    except Exception:
        window = _PERSISTENT_DEDUP_WINDOW_SECONDS
        min_hits = _PERSISTENT_DEDUP_MIN_HITS
        retention = 0
    if retention <= 0:
        retention = max(window * _PERSISTENT_RETENTION_DEFAULT_FACTOR, 3600)
    # Retention must never cut into the dedup window itself.
    retention = max(retention, window)
    return window, min_hits, retention


def _already_persistently_captured(conn, fingerprint: str, target: str, action: str, agent_name: str = "") -> tuple[bool, dict]:
    """Check/update persistent short-window dedup state.

    Returns (should_skip, info_dict). Best-effort and fail-open: on DB errors returns (False, {...}).
    Uses agent-scoped key when agent_name is provided to avoid cross-agent suppression while keeping
    compatibility with legacy empty-agent rows.
    """
    if not fingerprint:
        return False, {"reason": "no_fingerprint", "persistent_hit": False}
    window_seconds, min_hits, retention_seconds = _persistent_dedup_settings()
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    window_start = (now_dt - timedelta(seconds=window_seconds)).isoformat()
    meta = json.dumps({"target": str(target or ""), "action": str(action or ""), "agent_name": str(agent_name or "")}, ensure_ascii=False)
    agent_key = str(agent_name or "")
    try:
        # Opportunistic cleanup: keep table bounded (stale rows far outside dedup window).
        try:
            stale_before = (now_dt - timedelta(seconds=retention_seconds)).isoformat()
            conn.execute("DELETE FROM auto_capture_recent WHERE last_seen_at < ?", (stale_before,))
            # Row-count cap (TD hardening): under load the TTL alone may not
            # bound the table; trim oldest rows beyond the cap.
            try:
                from memall.config import get_config as _gc
                _max_rows = int(_gc("capture.auto_capture_max_rows", 10000) or 0)
            except Exception:
                _max_rows = 10000
            if _max_rows > 0:
                conn.execute(
                    "DELETE FROM auto_capture_recent WHERE rowid NOT IN "
                    "(SELECT rowid FROM auto_capture_recent ORDER BY last_seen_at DESC LIMIT ?)",
                    (_max_rows,),
                )
        except Exception:
            pass
        # Prefer agent-scoped row; also consider legacy "" row for backward compatibility.
        row = conn.execute(
            "SELECT fingerprint, hit_count, last_seen_at, agent_name FROM auto_capture_recent "
            "WHERE fingerprint = ? AND agent_name IN (?, '') AND last_seen_at >= ? "
            "ORDER BY CASE WHEN agent_name = ? THEN 0 ELSE 1 END, last_seen_at DESC LIMIT 1",
            (fingerprint, agent_key, window_start, agent_key),
        ).fetchone()
        if row:
            hit_count = int(row["hit_count"] or 1) + 1
            stored_agent = row["agent_name"] if "agent_name" in row.keys() else agent_key
            # Always update in the scoped namespace we queried for to avoid writing back to legacy "".
            conn.execute(
                "INSERT OR REPLACE INTO auto_capture_recent "
                "(fingerprint, source_key, agent_name, content_hash, created_at, last_seen_at, hit_count, metadata) "
                "VALUES (?, ?, ?, ?, COALESCE((SELECT created_at FROM auto_capture_recent WHERE fingerprint=? AND agent_name=?), ?), ?, ?, ?)",
                (fingerprint, str(action or ""), agent_key, fingerprint[:32], fingerprint, agent_key, now, now, hit_count, meta),
            )
            conn.commit()
            should_skip = hit_count >= min_hits
            return should_skip, {
                "reason": _REASON_SKIPPED_DEDUP_PERSISTENT if should_skip else "persistent_seen",
                "persistent_hit": should_skip,
                "hit_count": hit_count,
                "window_seconds": window_seconds,
                "threshold_hits": min_hits,
                "matched_agent_scope": stored_agent,
            }
        conn.execute(
            "INSERT OR REPLACE INTO auto_capture_recent "
            "(fingerprint, source_key, agent_name, content_hash, created_at, last_seen_at, hit_count, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (fingerprint, str(action or ""), agent_key, fingerprint[:32], now, now, 1, meta),
        )
        conn.commit()
        return False, {
            "reason": "persistent_first_seen",
            "persistent_hit": False,
            "hit_count": 1,
            "window_seconds": window_seconds,
            "threshold_hits": min_hits,
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return False, {"reason": "persistent_dedup_error", "persistent_hit": False, "fail_open": True}


def _already_captured(tool_name: str, action: str, content: str,
                      hints: dict) -> tuple[bool, dict]:
    meta = hints.get("metadata") or {}
    agent_name = str(hints.get("agent_name") or "")
    target = (meta.get("target_memory_id")
              or meta.get("discussion_id")
              or meta.get("subject_of")
              or "")
    key = (action, str(target), content[:64], agent_name)
    now = datetime.now(timezone.utc).isoformat()

    # Expire stale keys so the dict cannot grow without bound.
    if len(_recent_keys) > 500:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=_RECENT_KEY_TTL)
        for k, ts in list(_recent_keys.items()):
            try:
                if datetime.fromisoformat(ts) < cutoff:
                    _recent_keys.pop(k, None)
            except Exception:
                _recent_keys.pop(k, None)

    # Persistent short-window dedup across process restarts/calls (best-effort).
    try:
        from memall.core.db import get_conn
        fp = _compute_fingerprint(content)
        conn = get_conn()
        should_skip, pinfo = _already_persistently_captured(conn, fp, str(target), action, agent_name=agent_name)
        if should_skip:
            _recent_keys[key] = now
            return True, {**pinfo, "layer": "persistent"}
        # Keep first-seen info for observability but do not skip solely on first hit.
        persistent_info = pinfo
    except Exception:
        # Fail-open to in-process dedup only when DB unavailable.
        persistent_info = {"reason": "persistent_dedup_unavailable", "persistent_hit": False, "fail_open": True}

    if key in _recent_keys:
        return True, {"reason": _REASON_SKIPPED_DEDUP_IN_PROCESS, "persistent_hit": False, "layer": "in_process", **{k: v for k, v in persistent_info.items() if k in ("hit_count", "window_seconds", "threshold_hits")}}
    _recent_keys[key] = now
    return False, {**persistent_info, "layer": persistent_info.get("layer") or "none"}


# ── 注册自动捕获 hook ─────────────────────────────────────

HookRegistry.register(HookDef(
    hook_point=HOOK_POST_TOOL_USE,
    matcher="memall_*",
    handler=_auto_capture_hook,
    description="Auto-capture conclusions from ask/reflect/discussion tool calls",
))


# ── 批量导入工具 ────────────────────────────────────────

def import_from_mem0(jsonl_path: str, agent_name: str = "imported") -> dict:
    """从 Mem0 导出的 JSONL 文件导入记忆。

    Mem0 导出格式: {"role": "user"/"assistant", "content": "...", ...}
    """
    from pathlib import Path
    from memall.intelligent_capture import intelligent_capture

    path = Path(jsonl_path)
    if not path.is_file():
        return {"error": f"file not found: {jsonl_path}"}

    imported = 0
    errors = 0
    with open(path, "r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                content = rec.get("content") or rec.get("memory") or ""
                if not content or not str(content).strip():
                    continue
                intelligent_capture(str(content), agent_name=agent_name)
                imported += 1
            except Exception:
                errors += 1
                logger.warning("auto_capture: mem0 line %d import failed",
                               line_no, exc_info=True)

    return {"imported": imported, "errors": errors, "source": str(path)}


def import_from_jsonl(jsonl_path: str, agent_name: str = "imported") -> dict:
    """从 MemALL 自己的 JSONL 导出文件导入记忆。

    每行: {"content": "...", "level": "...", "category": "...", ...}
    """
    from pathlib import Path
    from memall.intelligent_capture import intelligent_capture

    path = Path(jsonl_path)
    if not path.is_file():
        return {"error": f"file not found: {jsonl_path}"}

    imported = 0
    errors = 0
    with open(path, "r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                content = rec.get("content") or ""
                if not content or not str(content).strip():
                    continue
                overrides = {k: rec[k] for k in
                             ("level", "category", "subject", "project", "owner")
                             if rec.get(k)}
                intelligent_capture(str(content), agent_name=agent_name, **overrides)
                imported += 1
            except Exception as e:
                errors += 1
                logger.warning("auto_capture: jsonl line %d import failed: %s",
                               line_no, e, exc_info=True)

    return {"imported": imported, "errors": errors, "source": str(path)}


def import_from_csv(csv_path: str, agent_name: str = "imported") -> dict:
    """从 CSV 导入记忆。需包含 content 列；可选 level/category/subject/project。"""
    import csv
    from pathlib import Path
    from memall.intelligent_capture import intelligent_capture

    path = Path(csv_path)
    if not path.is_file():
        return {"error": f"file not found: {csv_path}"}

    imported = 0
    errors = 0
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames or "content" not in reader.fieldnames:
            return {"error": "CSV must contain a 'content' column"}
        for row_no, row in enumerate(reader, 2):
            try:
                content = (row.get("content") or "").strip()
                if not content:
                    continue
                overrides = {k: (row.get(k) or "").strip() for k in
                             ("level", "category", "subject", "project", "owner")}
                intelligent_capture(content, agent_name=agent_name,
                                    **{k: v for k, v in overrides.items() if v})
                imported += 1
            except Exception as e:
                errors += 1
                logger.warning("auto_capture: csv row %d import failed: %s",
                               row_no, e, exc_info=True)

    return {"imported": imported, "errors": errors, "source": str(path)}