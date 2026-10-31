"""
Memory Strategy Package — Pluggable memory strategies for AI agents.

Inspired by LangChain's pluggable memory patterns and CrewAI's multi-agent
memory sharing. Strategies wrap the existing ``capture()`` / ``build_context()``
calls with pre/post-processing for entity extraction, summarization, KG triples,
and multi-agent sharing.

Strategy selection is per-agent via config::

    # config.yaml
    strategy:
        default: buffer
        researcher:
            type: entity
            auto_extract: true
        writer:
            type: summary
            trigger_after: 5
"""

from .base import MemoryStrategy
from .buffer import BufferStrategy
from .summary import SummaryStrategy
from .entity import EntityStrategy
from .kg import KGStrategy
from .registry import register, get_strategy, get_registered_strategies, clear_cache

__all__ = [
    "MemoryStrategy",
    "BufferStrategy",
    "SummaryStrategy",
    "EntityStrategy",
    "KGStrategy",
    "register",
    "get_strategy",
    "get_registered_strategies",
    "clear_cache",
]