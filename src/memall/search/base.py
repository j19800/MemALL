from abc import ABC, abstractmethod
from typing import Optional


class SearchProvider(ABC):
    """Abstract search provider for MemALL knowledge base.

    All vector/dense search backends (TF-IDF+SVD, sqlite-vec, FAISS)
    implement this interface for seamless switching.
    """

    @abstractmethod
    def build_index(self, force: bool = False) -> dict:
        """Build or refresh the search index from all stored memories."""

    @abstractmethod
    def search(self, query: str, top_k: int = 10) -> dict:
        """Search top_k most similar memories.

        Returns::
            {
                "query": str,
                "mode": str,
                "results": [
                    {
                        "memory_id": int,
                        "content": str,
                        "score": float,
                        "source": str,
                    },
                ],
                "total": int,
            }
        """

    @abstractmethod
    def index_status(self) -> dict:
        """Return current index coverage and model info."""

    @abstractmethod
    def add_item(self, memory_id: int, content: str) -> None:
        """Incrementally add a single item to the index (best-effort)."""

    @abstractmethod
    def remove_item(self, memory_id: int) -> None:
        """Remove a single item from the index."""

    @abstractmethod
    def save(self) -> None:
        """Persist index state to disk."""

    @classmethod
    @abstractmethod
    def load(cls) -> Optional["SearchProvider"]:
        """Load persisted index state; returns None if no saved state."""
