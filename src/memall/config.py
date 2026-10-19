"""
MemALL Configuration Manager (Phase 16+ enhanced)
==================================================
Supports JSON config, YAML config (priority), deep merge, environment
variable override (MEMALL_ prefix), and dot-path key access.
"""

import json
import logging
import os
import copy
from pathlib import Path
from typing import Any, Dict, Optional, Union

logger = logging.getLogger(__name__)

# ── Default configuration ──────────────────────────────────────────────
_DEFAULT_CONFIG: Dict[str, Any] = {
    "db": {
        "path": str(Path.home() / ".memall" / "data.db"),
    },
    "gateway": {
        "host": "127.0.0.1",
        "port": 9919,
        "secret_key": "",  # auto-generated on first start if empty
    },
    "discovery": {
        "port": 9919,
    },
    "identity": {
        # The human who owns this MemALL deployment.  Invariant #1 (from design
        # memory #823): owner is ALWAYS a human, creator is the agent that
        # triggered the write.  When a capture omits owner, it falls back to the
        # human owner rather than silently becoming the agent that wrote it.
        "human_owner": "老陈",
    },
    "search": {
        "provider": "vec0",
        "rrf_k": 60,
        "reranker_model": "BAAI/bge-reranker-v2-m3",
        "rerank_top_k": 15,
        "rerank_enabled": True,
        "reranker_onnx_dir": "~/.memall/.rerank_model",
        "context_rerank": {
            "enabled": False,
            "weight": 0.15,
            "freshness_boost": 1.1,
            "affinity_boost": 1.2,
        },
        # ── Query enhancement layer (retrieval quality) ──
        # Applied by ``enhanced_search()`` (memall/search/enhancements.py) and
        # surfaced through ``unified_search`` / ``memall_read``.
        "query_expansion": {
            "enabled": False,
            # Term → synonyms.  Keys are matched case-insensitively after
            # whitespace tokenisation.  Deployments can extend this in
            # config.json / memall.yaml; a JSON file at
            # ``~/.memall/synonyms.json`` is also honoured.
            "synonyms": {},
            # Number of query-history expansions to blend in (recent hot
            # queries that share tokens with the current query).
            "history_expansion": 3,
        },
        "facets": {
            # Default facet fields when the caller passes facet_fields="auto".
            # Must be real ``memories`` columns (see _FACET_WHITELIST).
            "default_fields": ["level", "category", "project"],
            "max_fields": 8,
        },
        "explain": {
            "enabled": False,
        },
        "clustering": {
            "enabled": False,
            # Number of clusters (0 = auto, derived from result count).
            "k": 0,
            "min_cluster_size": 2,
        },
        "personalization": {
            "enabled": False,
            # Weight of the learned per-agent term→memory affinity in the
            # final score.  0 disables personalization even when enabled.
            "weight": 0.25,
            # Interaction window (days) used to build the affinity profile.
            "window_days": 30,
        },
        "context_boost": {
            "enabled": False,
            # Boost applied to memories that share the active project / category
            # inferred from recent interactions.
            "weight": 0.15,
            "window_days": 7,
        },
        # Per-session "already returned" tracking.  A repeat query within one
        # session gets the same top-k both times and teaches the agent nothing;
        # this surfaces novelty on the second call instead.
        # CRITICAL rule (learned from dsh-meow-memory): seen items are never
        # *excluded*, only demoted below the protected top slice.  Hard
        # exclusion makes the most relevant entries unreachable and search
        # drifts further off with every query.
        "seen_tracking": {
            "enabled": False,
            # Results in the first N positions are never reordered.
            "protect_top": 5,
            # Session seen-state TTL (in-process, not persisted).
            "session_ttl_minutes": 60,
        },
    },
    "dream": {
        "enabled": True,
        "threshold": 0.4,
        "scan_window": 50,
    },
    "persona": {
        "dynamic_window_days": 7,
        "static_half_life_days": 60,
    },
    "forget": {
        "ttl_days": 90,
        "low_value_days": 7,
    },
    "capture": {
        # Project inference is always-on (root-cause fix for empty-project defect).
        "project_inference_enabled": True,
        # Semantic de-duplication at the write entry (catches near-duplicates).
        # OFF by default: TF-IDF cosine can falsely merge legitimately distinct
        # memories that merely share wording (e.g. "content A" vs "content B").
        # Enable deliberately where the trade-off is acceptable; the existing
        # ``deduplicate()`` op remains the recommended remediation for historical
        # duplicates. Exact-content (hash) de-dup always runs regardless.
        "semantic_dedup_enabled": False,
        "semantic_dedup_threshold": 0.92,
        "semantic_dedup_window": 30,
        # Thread inference: link new memories to a thread root within N minutes.
        # 0 disables the feature (thread_id left null). Tune per deployment.
        "thread_inference_window_minutes": 60,
        # Thread inference semantic fallback: when temporal window yields no candidate,
        # use semantic similarity to find related memories (M2 enhancement).
        "thread_inference_semantic_window": 50,
        "thread_inference_semantic_threshold": 0.7,
        # Category hygiene (root-cause guard for the "field pollution" defect).
        # A distill.py slicing bug once turned two-char domain names into single
        # characters ("决策" -> "决"/"策", "[任务]" -> "["/"任"), producing 159
        # polluted rows that then got re-distilled 40+ times.  The fix is in the
        # code; this gate stops anything like it from ever landing again.
        "category_validation_enabled": True,
        # Minimum length for a structurally valid category.  2 allows legitimate
        # short tags ("AI", "QA", "决策") while rejecting single chars.
        "category_min_length": 2,
        # Optional enum.  Empty list = no enum restriction, only structural
        # checks (length / whitespace / punctuation / control chars) apply.
        # Populate with the categories this deployment actually uses to get
        # hard rejection of anything outside the set.
        "allowed_categories": [],
        # "reject" raises ValueError at capture(); "coerce" falls back to
        # "general" and logs a warning instead of failing the write.
        "category_invalid_action": "coerce",
        # Dual-stream split (A2): capture-time categories that are pure
        # operational bookkeeping (heartbeat/testing) are tagged 'ledger' instead
        # of 'knowledge'.  Deliberately excludes session/meeting/reflection —
        # those carry genuine knowledge and must stay distillable/retrievable.
        "ledger_categories": ["heartbeat", "testing"],
        # Contextual recall: after storing a memory, check for related high-level
        # memories in the same project.  Enable to surface related L4/L6/L9+ memories.
        "contextual_recall_enabled": True,
        # Auto-tagging: when a capture supplies no explicit tags, derive a small
        # set of intent keywords from the content (entities + identifiers + CJK
        # terms).  Tags are indexed in FTS5 as their own column, so a memory
        # becomes findable by what it is *about*, not only by words that happen
        # to appear in its text.  ON by default — it is deterministic, cheap,
        # and never overrides caller-supplied tags.
        "auto_tags_enabled": True,
        # Upper bound on auto-derived tags per memory.
        "auto_tags_max": 8,
    },
    # Heat-based level promotion configuration (M4)
    "promotion": {
        # Enable/disable heat-based level promotion
        "enabled": True,
        # Access count thresholds for promotion between levels
        # Format: "from_level": {"to_level": min_access_count, ...}
        "access_thresholds": {
            "P2": {"L1": 15},      # Promote P2→L1 when accessed 15+ times
            "L1": {"L2": 25},      # Promote L1→L2 when accessed 25+ times
            "L2": {"L3": 35},      # Promote L2→L3 when accessed 35+ times
            "L3": {"L4": 50},      # Promote L3→L4 when accessed 50+ times
            "L4": {"L5": 75},      # Promote L4→L5 when accessed 75+ times
            "L5": {"L6": 100},     # Promote L5→L6 when accessed 100+ times
            "L6": {"L7": 150},     # Promote L6→L7 when accessed 150+ times
            "L7": {"L8": 200},     # Promote L7→L8 when accessed 200+ times
            "L8": {"L9": 300},     # Promote L8→L9 when accessed 300+ times
            "L9": {"L10": 500},    # Promote L9→L10 when accessed 500+ times
            "L10": {"L11": 800},   # Promote L10→L11 when accessed 800+ times
        },
        # Minimum time (in days) a memory must exist before being eligible for promotion
        "min_age_days": 7,
        # Whether to also consider referenced memories (edges pointing to this memory)
        "consider_references": True,
        # Reference count threshold for promotion (in addition to access count)
        "reference_threshold": 5,
    },
    "security": {
        # Cross-agent ownership enforcement on memall_write/update: an agent may
        # only update memories it owns (agent_name match).  ON by default —
        # otherwise any agent can overwrite another agent's memory *and* reassign
        # agent_name to itself, i.e. silently take ownership of it.
        # Deployments that legitimately use a supervisor agent to curate others'
        # memories should list those agents in ``supervisor_agents`` rather than
        # turning the whole gate off.
        "enforce_agent_ownership": True,
        # Agents exempt from ownership enforcement (supervisor / orchestrator
        # roles that curate other agents' memories).  Names are normalized the
        # same way as memory owner names.
        "supervisor_agents": [],
        # Write deny-list: captures whose ``project`` (case-insensitive) or
        # ``agent_name`` (normalized) matches are rejected up front.  Motivation
        # (borrowed from dsh-meow-memory's disabledWorkspaces): one-off task
        # agents and test/audit identities otherwise pollute the long-term
        # store — MemALL's own audit runs left rows under agents like
        # "mcp_audit"/"probe*", indistinguishable from real history.
        # Reads are NOT blocked (hiding data is a different concern from
        # refusing to store it); background rounds skip disabled agents.
        "disabled_projects": [],
        "disabled_agents": [],
        # Gateway: unauthenticated (SPA) endpoints are only reachable from
        # loopback.  Requests from any other address must present a valid token.
        "open_api_loopback_only": True,
    },
    "distill": {
        # Upsert L9 distillations instead of appending a new row per cycle.
        # Root-cause fix for unbounded near-identical L9 accumulation (the old
        # INSERT-only path keyed de-dup on a content hash that embeds a
        # changing source count, so every cycle produced a new row).
        # Contract: exactly ONE L9 per (agent_name, category).
        "upsert_enabled": True,
    },
    "lifecycle": {
        "cluster_threshold": 0.85,
        "connected_component_threshold": 0.85,
    },
    "nlp": {
        "model_dir": os.path.expanduser("~/.memall/.vector_model/"),
        "embedding_cache_size": 10000,
        "sentence_transformers": False,
        "sentence_transformers_model": "paraphrase-multilingual-MiniLM-L12-v2",
    },
    "plugins": {
        "auto_load": True,
    },
    "quality": {
        # Quality thresholds per level (0-10 scale) - memories scoring below these thresholds
        # may be rejected by the quality gate depending on gate_policy
        "thresholds": {
            "P0": 0,      # No quality gate for highest priority
            "P1": 2,      # Very low threshold for P1
            "P2": 3,      # Low threshold for P2
            "L1": 3,      # Low threshold for L1
            "L2": 3,      # Low threshold for L2
            "L3": 4,      # Moderate threshold for L3
            "L4": 5,      # Standard threshold for L4
            "L5": 5,      # Standard threshold for L5 (decisions/tasks need reasoning)
            "L6": 5,      # Standard threshold for L6 (reflections need substance)
            "L7": 4,      # Slightly lower for L7
            "L8": 4,      # Slightly lower for L8
            "L9": 4,      # Lower threshold for distilled knowledge (L9+)
            "L10": 4,     # Lower threshold for distilled knowledge (L10+)
            "L11": 4,     # Lower threshold for distilled knowledge (L11+)
        },
        # Gate policy: "strict" (reject if ANY dimension below threshold) or 
        # "lenient" (reject only if AVERAGE score below threshold)
        "gate_policy": "lenient",
        # Minimum content length: memories shorter than this are rejected regardless of quality score
        "min_length": 15,
    },
    "scheduler": {
        "forget_interval": 86400,
        "audit_interval": 86400,
        "heartbeat_interval": 300,
        "pipeline_interval": 21600,
        "doctor_interval": 3600,
        "marvis_interval": 300,
        # agent_round cadence: how often the daemon scans active agents for
        # pending discussions / tasks and writes P2 reminder memories.
        # Agents have no runtime of their own, so this is what makes proactive
        # reminders reach them at all.  Without this key the interval was only
        # settable by editing code.
        "proxy_session_interval": 300,
        "missed_heartbeat_limit": 7,
        # Destructive maintenance passes owned by the single scheduler daemon
        # (see F-04).  Opt-in: OFF by default to avoid surprise data churn.
        "lifecycle_interval": 86400,
        "lifecycle_enabled": False,
        "dream_interval": 86400,
        "dream_enabled": False,
    },
    "strategy": {
        "default": "buffer",
        "buffer": {"buffer_size": 50},
        "summary": {"trigger_after": 10, "max_sources": 20},
        "entity": {"auto_extract": True, "extract_triples": False, "entity_boost": 1.5},
        "kg": {"auto_extract": True, "min_level": "L6", "max_triples": 20, "traverse_depth": 1},
    },
    "logging": {
        "level": "INFO",
    },
}

# Cached config, loaded lazily
_config: Optional[Dict[str, Any]] = None
_config_loaded: bool = False

# ── Hot reload support ───────────────────────────────────────────────
# (path, mtime_ns) for every file the loaders actually read.  get_config()
# re-stats these at most every _RELOAD_CHECK_INTERVAL seconds and
# transparently rebuilds the cache when any of them changed.  Motivation: the
# MCP server is a long-lived process, and without this a config.json edit only
# ever took effect after a restart — silently, because the process kept
# serving the values it read at startup.  Throttling keeps the hot path cheap:
# two stats every few seconds instead of one per get_config() call.
#
# NOTE: the paths tracked here must be exactly the paths load_json_config /
# load_yaml_config resolve (~/.memall/<name>), not merely plausible ones —
# tracking a file the loader never reads would re-trigger the change check on
# every pass and reload in a loop without ever converging.
_config_files: Dict[str, int] = {}
_last_mtime_check: float = 0.0
_RELOAD_CHECK_INTERVAL = 2.0  # seconds


def _tracked_config_paths() -> list:
    """The files the loaders actually resolve, in load order.

    Must mirror load_json_config / load_yaml_config exactly: relative names
    resolve under ~/.memall only.  Tracking a file the loaders never read
    would make _config_changed fire on every check and reload in a loop.
    """
    home = Path.home() / ".memall"
    return [home / "config.json", home / "memall.yaml"]


def _stat_config_file(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0  # sentinel for "absent" — appearance changes the value


def _config_fingerprint() -> Dict[str, int]:
    return {str(p): _stat_config_file(p) for p in _tracked_config_paths()}


def _config_changed() -> bool:
    """True when a tracked config file appeared, vanished, or changed."""
    return _config_fingerprint() != _config_files


def _maybe_reload() -> None:
    """Rebuild the cached config if a file layer changed (throttled)."""
    global _last_mtime_check
    import time as _time

    now = _time.monotonic()
    if now - _last_mtime_check < _RELOAD_CHECK_INTERVAL:
        return
    _last_mtime_check = now
    if _config_changed():
        logger.info("config: file change detected — reloading")
        reload_config()


def reload_config() -> Optional[Dict[str, Any]]:
    """Force a config rebuild now and return the fresh full config.

    Used by the hot-reload check and available for callers that just wrote a
    new config (e.g. a settings UI) and want it visible immediately.
    """
    global _config, _config_loaded, _last_mtime_check
    import time as _time

    _config = None
    _config_loaded = False
    cfg = get_config()
    _last_mtime_check = _time.monotonic()
    return cfg


# ── YAML support (optional dependency) ─────────────────────────────────

try:
    import yaml

    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


def load_yaml_config(path: Union[str, Path] = "memall.yaml") -> Optional[Dict[str, Any]]:
    """Load configuration from a YAML file.

    Args:
        path: Path to the YAML file. An absolute path is used as-is; a
            relative name resolves under ``~/.memall/`` only.

    Returns:
        Parsed config dict, or None if the file doesn't exist or YAML unavailable.
    """
    if not _HAS_YAML:
        return None

    p = Path(path)
    candidates = [p] if p.is_absolute() else [Path.home() / ".memall" / p.name]

    for cand in candidates:
        if cand.exists():
            try:
                with open(cand, "r", encoding="utf-8") as f:
                    return yaml.safe_load(f)
            except (OSError, yaml.YAMLError) as e:
                logger.warning("Config YAML load error (%s): %s", cand, e)

    return None


def load_json_config(path: Union[str, Path] = "config.json") -> Optional[Dict[str, Any]]:
    """Load configuration from a JSON file.

    Args:
        path: Path to the JSON file. An absolute path is used as-is; a
            relative name resolves under ``~/.memall/`` only.

    Returns:
        Parsed config dict, or None if the file doesn't exist.
    """
    p = Path(path)
    candidates = [p] if p.is_absolute() else [Path.home() / ".memall" / p.name]

    for cand in candidates:
        if cand.exists():
            try:
                with open(cand, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                logger.warning("Config JSON load error (%s): %s", cand, e)

    return None


def merge_config(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Deep merge two dicts. Values from `override` take precedence over `base`.

    Nested dicts are merged recursively. Non-dict values from override replace
    base values outright.

    Args:
        base: The base configuration dict.
        override: The overriding configuration dict.

    Returns:
        A new dict with merged values (base is not mutated).
    """
    result = copy.deepcopy(base)

    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_config(result[key], value)
        else:
            result[key] = copy.deepcopy(value)

    return result


def _has_dot_path(d: Dict[str, Any], key: str) -> bool:
    """Return True if ``key`` (dot-path) resolves to an existing node in ``d``."""
    parts = key.split(".")
    current = d
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return False
    return True


def _apply_env_overrides(config: Dict[str, Any]) -> Dict[str, Any]:
    """Apply environment variable overrides to config.

    Environment variables with the MEMALL_ prefix are mapped to config keys:
      MEMALL_DB_PATH        → db.path
      MEMALL_GATEWAY_PORT   → gateway.port
      MEMALL_FORGET_TTL_DAYS → forget.ttl_days

    Underscores in env var names become dots, lowercased. Values are
    type-coerced: int, float, bool, else string.

    Resolution rule (fix for nested keys that themselves contain underscores):
      1. Try the literal dotted path first (e.g. ``MEMALL_DB_PATH`` →
         ``db.path``). This preserves the long-standing contract.
      2. If that dotted path does *not* exist in the config, fall back to the
         *flattened* key: keep the first segment's nested dot but collapse the
         remaining dots into underscores (e.g. ``capture.thread.inference.window.minutes``
         → ``capture.thread_inference_window_minutes``), so config keys that carry
         underscores within a segment — e.g. ``capture.thread_inference_window_minutes``
         or ``nlp.sentence_transformers`` — can still be overridden via env vars
         like ``MEMALL_CAPTURE_THREAD_INFERENCE_WINDOW_MINUTES``.
      3. If neither exists, create the dotted path anyway (legacy behaviour for
         custom / unknown sections).
    """
    result = copy.deepcopy(config)

    for env_key, env_val in os.environ.items():
        if not env_key.startswith("MEMALL_"):
            continue

        # Convert MEMALL_DB_PATH → db.path
        dot_key = env_key[len("MEMALL_"):].lower().replace("_", ".")

        # Type coercion
        coerced: Any = env_val
        if env_val.lstrip("-").isdigit():
            coerced = int(env_val)
        elif env_val.lower() in ("true", "false"):
            coerced = env_val.lower() == "true"
        else:
            try:
                coerced = float(env_val)
                if coerced == int(coerced):
                    coerced = int(coerced)
            except ValueError:
                coerced = env_val

        # Resolve against the PRISTINE config (not the mutating ``result``). This
        # is critical: the legacy branch below may create unknown dotted paths
        # (e.g. ``capture.thread``) for one env var, which would otherwise pollute
        # the existence check for a later var and prevent the flattened-key
        # fallback from firing.
        if _has_dot_path(config, dot_key):
            _set_dot_path(result, dot_key, coerced)
        else:
            # Flattened key: keep the first segment's nested dot, but collapse the
            # REMAINING dots back into underscores. This recovers config keys that
            # carry underscores within a segment, e.g.
            #   capture.thread.inference.window.minutes
            #     -> capture.thread_inference_window_minutes  (real key)
            # while leaving genuinely dotted keys like ``db.path`` untouched
            # (``db`` + ``.`` + ``path``).
            _parts = dot_key.split(".")
            flat_key = _parts[0] + "." + "_".join(_parts[1:]) if len(_parts) > 1 else dot_key
            if _has_dot_path(config, flat_key):
                _set_dot_path(result, flat_key, coerced)
            else:
                # Legacy: create the dotted path even if unknown (custom sections).
                _set_dot_path(result, dot_key, coerced)

    return result


def _set_dot_path(d: Dict[str, Any], key: str, value: Any) -> None:
    """Set a value in a nested dict using dot-path notation.

    Example: _set_dot_path(d, 'db.path', '/tmp/test.db')
    Creates intermediate dicts if they don't exist.
    """
    parts = key.split(".")
    current = d
    for part in parts[:-1]:
        if part not in current or not isinstance(current[part], dict):
            current[part] = {}
        current = current[part]
    current[parts[-1]] = value


def _get_dot_path(d: Dict[str, Any], key: str, default: Any = None) -> Any:
    """Get a value from a nested dict using dot-path notation.

    Example: _get_dot_path(d, 'db.path') → '/home/user/.memall/data.db'
    """
    parts = key.split(".")
    current = d
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return default
    return current


def _load_config_layers() -> Dict[str, Any]:
    """Build the full config from all layers and record the files used.

    Load order (last wins): defaults → config.json → memall.yaml → env.
    Populates ``_config_files`` with the mtimes of exactly the paths the
    loaders resolve (see ``_tracked_config_paths``) so ``_config_changed``
    detects edits without ever chasing files the loaders never read.
    """
    global _config_files
    cfg = copy.deepcopy(_DEFAULT_CONFIG)

    json_cfg = load_json_config()
    if json_cfg:
        cfg = merge_config(cfg, json_cfg)

    yaml_cfg = load_yaml_config()
    if yaml_cfg:
        cfg = merge_config(cfg, yaml_cfg)

    cfg = _apply_env_overrides(cfg)
    _config_files = _config_fingerprint()
    return cfg


def get_config(key: Optional[str] = None, default: Any = None) -> Any:
    """Get the full configuration or a specific key via dot-path.

    Configuration is loaded lazily and cached. Load order (last wins):
      1. Default config (built-in)
      2. config.json (if exists)
      3. memall.yaml (if exists, overrides JSON)
      4. MEMALL_* environment variables (highest priority)

    Long-lived processes (the MCP server) hot-reload transparently: at most
    every 2s the cached config's source files are re-statted and any change
    rebuilds the cache before the value is returned.  See ``_maybe_reload``.

    Args:
        key: Dot-path key (e.g. 'db.path', 'gateway.port'). None for full config.
        default: Default value if key not found.

    Returns:
        The configuration value, or full config dict if key is None.
    """
    global _config, _config_loaded

    if _config_loaded:
        # Throttled hot-reload.  Must run OUTSIDE the load branch to avoid
        # re-entering get_config through reload_config() while loading.
        _maybe_reload()
    else:
        _config = _load_config_layers()
        _config_loaded = True

    if key is None:
        return copy.deepcopy(_config)

    return _get_dot_path(_config, key, default)


def reset_config() -> None:
    """Reset cached config (useful for testing)."""
    global _config, _config_loaded, _config_files, _last_mtime_check
    _config = None
    _config_loaded = False
    _config_files = {}
    _last_mtime_check = 0.0


def save_config(config: Optional[Dict[str, Any]] = None, path: str = "config.json") -> None:
    """Save configuration to a JSON file (atomic write: temp + rename).

    Args:
        config: The config dict to save. Uses current config if None.
        path: Output path (default: config.json in CWD).
    """
    cfg = config or get_config()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def config_dir() -> Path:
    """Return the MemALL configuration directory (~/.memall), creating if needed."""
    d = Path.home() / ".memall"
    d.mkdir(parents=True, exist_ok=True)
    return d