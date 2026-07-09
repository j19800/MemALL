"""
MemoryStrategy — Abstract base class for all memory strategies.

Each strategy wraps ``capture()`` and ``build_context()`` / ``retrieve()``
with additional processing logic (entity extraction, summarization, KG triples).

The strategy layer sits ON TOP of the existing capture/retrieve pipeline — it
does not modify the core. This means:
- All strategies share the same quality gates, dedup, and identity checks.
- Strategies can be mixed per agent via config.
- The existing capture() call remains the single write entry point.
"""

from abc import ABC, abstractmethod
from typing import Any, Optional

from memall.core.models import MemoryInput


class MemoryStrategy(ABC):
    """Abstract base class for all memory strategies.

    Usage::

        class MyStrategy(MemoryStrategy):
            def store(self, data, **overrides) -> int:
                mem_id = capture(data, **overrides)
                # ... additional processing ...
                return mem_id

            def retrieve(self, query="", top_k=10, **kwargs):
                return retrieve(query, agent_name=self.agent_name, limit=top_k)

    Args:
        agent_name: The agent this strategy instance serves.
        config: Optional strategy-specific configuration dict.
    """

    def __init__(self, agent_name: str, config: dict = None):
        self.agent_name = agent_name
        self.config = config or {}

    @abstractmethod
    def store(self, data: MemoryInput | dict | str, **overrides) -> int:
        """Store a memory with strategy-specific processing.

        The default implementation calls ``capture()`` directly.  Subclasses
        may add pre/post processing (entity extraction, summarization, etc.).

        Args:
            data: Memory input (MemoryInput, dict, or raw string).
            **overrides: Passed through to ``capture()``.

        Returns:
            The new or existing memory ID.
        """
        ...

    @abstractmethod
    def retrieve(self, query: str = "", top_k: int = 10, **kwargs) -> list | dict:
        """Retrieve memories with strategy-specific augmentation.

        Args:
            query: Search query.
            top_k: Maximum number of results.
            **kwargs: Additional retrieval parameters.

        Returns:
            List of memory dicts, or a dict with ``results`` key.
        """
        ...

    def summarize(self, memory_ids: list[int] = None) -> Optional[str]:
        """Generate a summary of selected memories.

        Returns summary text, or None if not implemented by this strategy.
        """
        return None

    def clear(self, older_than_days: int = 30) -> int:
        """Clear strategy-specific transient state.

        Args:
            older_than_days: Age threshold for clearing.

        Returns:
            Count of items cleared.
        """
        return 0