"""
Strategy Registry — Maps strategy names to implementations.

Resolution order for ``get_strategy()``:
1. ``strategy_name`` parameter (explicit override)
2. ``config`` parameter (dict from caller)
3. ``get_config(f"strategy.{agent_name}.type")`` (per-agent config)
4. ``get_config("strategy.default")`` (global default)
5. Falls back to ``BufferStrategy``

Instances are cached per agent to maintain in-memory state (counters, etc.).
Call ``clear_cache()`` to force re-creation.
"""

import logging
from typing import Optional

from .base import MemoryStrategy
from .buffer import BufferStrategy
from .summary import SummaryStrategy
from .entity import EntityStrategy
from .kg import KGStrategy

logger = logging.getLogger(__name__)

# ── Registry ──────────────────────────────────────────────────

_registry: dict[str, type[MemoryStrategy]] = {
    "buffer": BufferStrategy,
    "summary": SummaryStrategy,
    "entity": EntityStrategy,
    "kg": KGStrategy,
}

# Per-agent cache: key = "agent_name:strategy_name"
_agent_strategies: dict[str, MemoryStrategy] = {}


def register(name: str, strategy_cls: type[MemoryStrategy]):
    """Register a custom strategy type."""
    _registry[name] = strategy_cls
    logger.info("Strategy registered: %s → %s", name, strategy_cls.__name__)


def get_strategy(
    agent_name: str,
    strategy_name: str = None,
    config: dict = None,
) -> MemoryStrategy:
    """Get or create a strategy instance for an agent.

    Args:
        agent_name: The agent this strategy serves.
        strategy_name: Explicit strategy name override.
        config: Additional config dict (merged with global config).

    Returns:
        A MemoryStrategy instance (cached per agent).
    """
    from memall.config import get_config as memall_config

    # Resolve strategy name
    resolved = strategy_name
    if not resolved:
        resolved = memall_config(f"strategy.{agent_name}.type", None)
    if not resolved:
        resolved = memall_config("strategy.default", "buffer")

    # Check cache
    cache_key = f"{agent_name}:{resolved}"
    cached = _agent_strategies.get(cache_key)
    if cached is not None:
        return cached

    # Look up class
    cls = _registry.get(resolved)
    if cls is None:
        logger.warning(
            "Unknown strategy '%s' for agent '%s', falling back to 'buffer'",
            resolved, agent_name,
        )
        cls = BufferStrategy
        resolved = "buffer"
        cache_key = f"{agent_name}:buffer"

    # Build merged config
    merged_config = dict(config or {})

    # Global defaults for this strategy type
    global_cfg = memall_config(f"strategy.{resolved}", {})
    if isinstance(global_cfg, dict):
        for k, v in global_cfg.items():
            merged_config.setdefault(k, v)

    # Per-agent config override
    agent_cfg = memall_config(f"strategy.{agent_name}", {})
    if isinstance(agent_cfg, dict):
        for k, v in agent_cfg.items():
            if k != "type":
                merged_config.setdefault(k, v)

    instance = cls(agent_name=agent_name, config=merged_config)
    _agent_strategies[cache_key] = instance
    logger.debug(
        "Strategy '%s' created for agent '%s' (config: %s)",
        resolved, agent_name, merged_config,
    )
    return instance


def get_registered_strategies() -> list[str]:
    """Return list of registered strategy names."""
    return list(_registry.keys())


def clear_cache():
    """Clear all cached strategy instances (for testing / hot-reload)."""
    _agent_strategies.clear()


def remove_agent_cache(agent_name: str):
    """Remove cached strategy for a specific agent."""
    keys = [k for k in _agent_strategies if k.startswith(f"{agent_name}:")]
    for k in keys:
        del _agent_strategies[k]