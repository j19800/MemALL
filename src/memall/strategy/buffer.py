"""
BufferStrategy — Sliding window of recent memories.

This is the simplest strategy: it keeps the most recent N memories for the agent.
No extra storage or processing — wraps capture()/retrieve() with an added limit.

Config:
    buffer_size (int): Maximum memories to track (default 50).
"""

from memall.core.thin_waist import capture as _capture, retrieve as _retrieve
from memall.core.models import MemoryInput
from .base import MemoryStrategy


class BufferStrategy(MemoryStrategy):
    """Keeps a sliding window of recent N memories."""

    def __init__(self, agent_name: str, config: dict = None):
        super().__init__(agent_name, config)
        self.buffer_size = int(self.config.get("buffer_size", 50))

    def store(self, data: MemoryInput | dict | str, **overrides) -> int:
        """Store via capture() — no extra processing."""
        return _capture(data, **overrides)

    def retrieve(self, query: str = "", top_k: int = 10, **kwargs) -> list | dict:
        """Retrieve with limit capped to buffer_size."""
        actual_limit = min(top_k, self.buffer_size)
        return _retrieve(
            query,
            agent_name=self.agent_name,
            limit=actual_limit,
            **kwargs,
        )

    def clear(self, older_than_days: int = 30) -> int:
        """No-op — individual memories manage their own lifecycle via the forget pipeline."""
        return 0