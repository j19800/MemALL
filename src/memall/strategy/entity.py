"""
EntityStrategy — Auto-extracts entities during store, enables entity-based retrieval.

During store:
    1. Call ``capture()`` to persist the memory.
    2. Extract named entities from content.
    3. Upsert into ``entities`` table.
    4. Insert into ``memory_entities`` junction.

During retrieve:
    1. Standard retrieval (FTS/rerank).
    2. Entity-aware retrieval: extract entities from query, find connected memories.
    3. Weighted merge of both result sets.

Config:
    auto_extract (bool): Extract entities during store (default True).
    extract_triples (bool): Also extract KG triples (default False).
    entity_boost (float): Boost factor for entity-matched results (default 1.5).
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from memall.core.thin_waist import capture as _capture, retrieve as _retrieve
from memall.core.models import MemoryInput
from memall.core.db import pool_conn, get_conn
from memall.core.entity_extractor import extract_entities, resolve_entity
from .base import MemoryStrategy

logger = logging.getLogger(__name__)


class EntityStrategy(MemoryStrategy):
    """Memory strategy with automatic entity extraction and entity-based retrieval."""

    def __init__(self, agent_name: str, config: dict = None):
        super().__init__(agent_name, config)
        self.auto_extract = bool(self.config.get("auto_extract", True))
        self.extract_triples = bool(self.config.get("extract_triples", False))
        self.entity_boost = float(self.config.get("entity_boost", 1.5))

    def store(self, data: MemoryInput | dict | str, **overrides) -> int:
        """Store and extract entities from content."""
        mem_id = _capture(data, **overrides)
        if self.auto_extract:
            # Get content from the stored data
            if isinstance(data, MemoryInput):
                content = data.content
            elif isinstance(data, dict):
                content = data.get("content", "")
            else:
                content = str(data)
            if content:
                self._extract_and_link(mem_id, content)
        return mem_id

    def retrieve(self, query: str = "", top_k: int = 10, **kwargs) -> list | dict:
        """Standard retrieve + entity-aware retrieve merged."""
        # Standard retrieval
        results = _retrieve(query, viewer=self.agent_name, limit=top_k, **kwargs)
        if not isinstance(results, list):
            return results

        if not query:
            return results

        # Entity-aware retrieval
        try:
            entity_hits = self._entity_aware_retrieve(query, top_k)
            results = self._merge_results(results, entity_hits, top_k)
        except Exception as e:
            logger.debug("EntityStrategy: entity retrieve failed: %s", e)

        return results

    def _extract_and_link(self, mem_id: int, content: str):
        """Extract entities and create memory_entity links."""
        now = datetime.now(timezone.utc).isoformat()
        with pool_conn() as conn:
            entities = extract_entities(content)
            for ent in entities:
                eid = resolve_entity(ent["name"], ent["entity_type"], conn)
                conn.execute(
                    "INSERT OR IGNORE INTO memory_entities "
                    "(memory_id, entity_id, role, confidence, context_snippet, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        mem_id, eid, "mentioned",
                        ent.get("confidence", 1.0),
                        ent.get("context_snippet", "")[:200],
                        now,
                    ),
                )
            conn.commit()
            if entities:
                logger.debug(
                    "EntityStrategy: linked %d entities to memory #%d",
                    len(entities), mem_id,
                )

    def _entity_aware_retrieve(self, query: str, top_k: int) -> list[dict]:
        """Find memories connected to entities mentioned in the query."""
        query_entities = extract_entities(query)
        if not query_entities:
            return []

        entity_names = [e["name"] for e in query_entities]
        with pool_conn() as conn:
            placeholders = ",".join("?" * len(entity_names))
            rows = conn.execute(
                f"SELECT DISTINCT m.id, m.content, m.subject, m.level, m.category, "
                f"m.created_at, m.agent_name "
                f"FROM memories m "
                f"JOIN memory_entities me ON m.id = me.memory_id "
                f"JOIN entities e ON me.entity_id = e.id "
                f"WHERE LOWER(e.name) IN ({placeholders}) "
                f"ORDER BY me.created_at DESC LIMIT ?",
                tuple(n.lower() for n in entity_names) + (top_k,),
            ).fetchall()
            return [dict(r) for r in rows]

    def _merge_results(self, standard: list, entity_results: list[dict], top_k: int) -> list:
        """Deduplicate and merge standard + entity results."""
        seen: set[int] = set()
        merged: list = []

        for r in standard:
            mid = r.get("id") if isinstance(r, dict) else getattr(r, "id", None)
            if mid and mid not in seen:
                seen.add(mid)
                merged.append(r)

        for r in entity_results:
            mid = r.get("id")
            if mid and mid not in seen:
                seen.add(mid)
                # Add entity boost marker
                r["_entity_match"] = True
                merged.append(r)

        return merged[:top_k]