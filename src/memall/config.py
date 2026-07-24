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
        "port": 9920,
    },
    "search": {
        "provider": "tfidf",
        "rrf_k": 60,
        "reranker_model": "BAAI/bge-reranker-v2-m3",
        "rerank_top_k": 30,
        "rerank_enabled": False,
        "context_rerank": {
            "enabled": False,
            "weight": 0.15,
            "freshness_boost": 1.1,
            "affinity_boost": 1.2,
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
    "scheduler": {
        "forget_interval": 86400,
        "audit_interval": 86400,
        "heartbeat_interval": 300,
        "pipeline_interval": 21600,
        "doctor_interval": 3600,
        "marvis_interval": 300,
        "missed_heartbeat_limit": 7,
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


# ── YAML support (optional dependency) ─────────────────────────────────

try:
    import yaml

    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


def load_yaml_config(path: Union[str, Path] = "memall.yaml") -> Optional[Dict[str, Any]]:
    """Load configuration from a YAML file.

    Args:
        path: Path to the YAML file. Resolves relative to CWD, then ~/.memall/.

    Returns:
        Parsed config dict, or None if the file doesn't exist or YAML unavailable.
    """
    if not _HAS_YAML:
        return None

    candidates = [
        Path(path),
        Path.home() / ".memall" / Path(path).name,
    ]

    for p in candidates:
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return yaml.safe_load(f)
            except (OSError, yaml.YAMLError) as e:
                logger.warning("Config YAML load error (%s): %s", p, e)

    return None


def load_json_config(path: Union[str, Path] = "config.json") -> Optional[Dict[str, Any]]:
    """Load configuration from a JSON file.

    Args:
        path: Path to the JSON file. Resolves relative to CWD, then ~/.memall/.

    Returns:
        Parsed config dict, or None if the file doesn't exist.
    """
    candidates = [
        Path(path),
        Path.home() / ".memall" / Path(path).name,
    ]

    for p in candidates:
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                logger.warning("Config JSON load error (%s): %s", p, e)

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


def _apply_env_overrides(config: Dict[str, Any]) -> Dict[str, Any]:
    """Apply environment variable overrides to config.

    Environment variables with the MEMALL_ prefix are mapped to config keys:
      MEMALL_DB_PATH        → db.path
      MEMALL_GATEWAY_PORT   → gateway.port
      MEMALL_FORGET_TTL_DAYS → forget.ttl_days

    Underscores in env var names become dots, lowercased. Values are
    type-coerced: int, float, bool, else string.
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

        # Set using dot-path
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


def get_config(key: Optional[str] = None, default: Any = None) -> Any:
    """Get the full configuration or a specific key via dot-path.

    Configuration is loaded lazily and cached. Load order (last wins):
      1. Default config (built-in)
      2. config.json (if exists)
      3. memall.yaml (if exists, overrides JSON)
      4. MEMALL_* environment variables (highest priority)

    Args:
        key: Dot-path key (e.g. 'db.path', 'gateway.port'). None for full config.
        default: Default value if key not found.

    Returns:
        The configuration value, or full config dict if key is None.
    """
    global _config, _config_loaded

    if not _config_loaded:
        cfg = copy.deepcopy(_DEFAULT_CONFIG)

        # Layer 2: JSON
        json_cfg = load_json_config()
        if json_cfg:
            cfg = merge_config(cfg, json_cfg)

        # Layer 3: YAML (higher priority than JSON)
        yaml_cfg = load_yaml_config()
        if yaml_cfg:
            cfg = merge_config(cfg, yaml_cfg)

        # Layer 4: Environment overrides
        cfg = _apply_env_overrides(cfg)

        _config = cfg
        _config_loaded = True

    if key is None:
        return copy.deepcopy(_config)

    return _get_dot_path(_config, key, default)


def reset_config() -> None:
    """Reset cached config (useful for testing)."""
    global _config, _config_loaded
    _config = None
    _config_loaded = False


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