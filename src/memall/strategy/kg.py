"""
KGStrategy — Triple-based knowledge graph strategy.

Extracts subject–predicate–object triples from L6+ memories during store.
Enables graph-traversal queries that return related memories via entity connections.

Only extracts triples from L6+ memories (lower levels are too raw for reliable
triple extraction).

Config:
    auto_extract (bool): Extract triples during store (default True).
    min_level (str): Minimum level for triple extraction (default "L6").
    max_triples (int): Max triples per memory (default 20).
    traverse_depth (int): KG traversal depth during retrieve (default 1).
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from memall.core.thin_waist import capture as _capture, retrieve as _retrieve
from memall.core.models import MemoryInput
from memall.core.db import pool_conn, get_conn
from memall.core.entity_extractor import extract_triples, extract_entities, resolve_entity
from .base import MemoryStrategy

logger = logging.getLogger(__name__)

# Level priority for the min_level check
_LEVEL_PRIORITY = {
    "P0": 0, "P1": 1, "P2": 2, "P3": 3, "P4": 4,
    "L1": 5, "L2": 5, "L3": 5,
    "L4": 6, "L5": 6,
    "L6": 7, "L7": 7, "L8": 8,
    "L9": 9, "L10": 10, "L11": 11,
}


class KGStrategy(MemoryStrategy):
    """Knowledge graph strategy: triples from L6+ memories, graph-traversal retrieval."""

    def __init__(self, agent_name: str, config: dict = None):
        super().__init__(agent_name, config)
        self.auto_extract = bool(self.config.get("auto_extract", True))
        self.min_level = str(self.config.get("min_level", "L6"))
        self.max_triples = int(self.config.get("max_triples", 20))
        self.traverse_depth = int(self.config.get("traverse_depth", 1))
        self._min_priority = _LEVEL_PRIORITY.get(self.min_level, 7)

    def store(self, data: MemoryInput | dict | str, **overrides) -> int:
        """Store, then extract triples if memory level qualifies."""
        mem_id = _capture(data, **overrides)

        if self.auto_extract:
            # Determine level from overrides or data
            level = overrides.get("level", "")
            if not level:
                if isinstance(data, MemoryInput):
                    level = data.level or ""
                elif isinstance(data, dict):
                    level = data.get("level", "")

            if self._should_extract(level):
                content = self._get_content(data)
                if content:
                    self._extract_and_store_triples(mem_id, content)

        return mem_id

    def retrieve(self, query: str = "", top_k: int = 10, **kwargs) -> list | dict:
        """Standard retrieve + KG-augmented retrieve merged."""
        results = _retrieve(query, viewer=self.agent_name, limit=top_k, **kwargs)
        if not isinstance(results, list) or not query:
            return results

        try:
            kg_results = self._kg_augmented_retrieve(query, top_k)
            results = self._merge_results(results, kg_results, top_k)
        except Exception as e:
            logger.debug("KGStrategy: KG retrieve failed: %s", e)

        return results

    def traverse(self, entity_name: str, depth: int = 1) -> dict:
        """Traverse the KG from an entity.

        Returns::

            {"entity": entity_name, "entities": [...], "triples": [...],
             "memories": [...]}
        """
        with pool_conn() as conn:
            entity_row = conn.execute(
                "SELECT id FROM entities WHERE LOWER(name) = LOWER(?) LIMIT 1",
                (entity_name,),
            ).fetchone()
            if not entity_row:
                return {"entity": entity_name, "entities": [], "triples": [], "memories": []}

            eid = entity_row["id"]
            max_depth = min(depth, 3)

            visited_entities = {eid}
            all_triples = []

            current_ids = {eid}
            for _ in range(max_depth):
                if not current_ids:
                    break
                id_list = list(current_ids)
                placeholders = ",".join("?" * len(id_list))

                triples = conn.execute(
                    f"SELECT id, subject_id, predicate, object_id, source_memory_id, confidence, weight "
                    f"FROM knowledge_triples "
                    f"WHERE subject_id IN ({placeholders}) OR object_id IN ({placeholders})",
                    (*id_list, *id_list),
                ).fetchall()

                new_ids = set()
                for t in triples:
                    all_triples.append(dict(t))
                    new_ids.add(t["subject_id"])
                    new_ids.add(t["object_id"])

                current_ids = new_ids - visited_entities
                visited_entities.update(current_ids)

            # Collect entity details
            if visited_entities:
                placeholders = ",".join("?" * len(visited_entities))
                entity_rows = conn.execute(
                    f"SELECT id, name, entity_type, description FROM entities "
                    f"WHERE id IN ({placeholders})",
                    tuple(visited_entities),
                ).fetchall()
            else:
                entity_rows = []

            # Collect memories connected to traversed entities
            memory_rows = []
            if visited_entities:
                placeholders = ",".join("?" * len(visited_entities))
                memory_rows = conn.execute(
                    f"SELECT DISTINCT m.id, m.content, m.subject, m.level, m.agent_name "
                    f"FROM memories m "
                    f"JOIN memory_entities me ON m.id = me.memory_id "
                    f"WHERE me.entity_id IN ({placeholders}) "
                    f"ORDER BY m.created_at DESC LIMIT 50",
                    tuple(visited_entities),
                ).fetchall()

            return {
                "entity": entity_name,
                "entities": [dict(r) for r in entity_rows],
                "triples": all_triples,
                "memories": [dict(r) for r in memory_rows],
            }

    def _should_extract(self, level: str) -> bool:
        """Check if memory level qualifies for triple extraction."""
        prio = _LEVEL_PRIORITY.get(level, 2)
        return prio >= self._min_priority

    def _get_content(self, data: MemoryInput | dict | str) -> str:
        return super()._get_content(data)

    def _extract_and_store_triples(self, mem_id: int, content: str):
        """Extract triples and persist to knowledge_triples table."""
        triples = extract_triples(content, self.agent_name)[:self.max_triples]
        if not triples:
            return

        now = datetime.now(timezone.utc).isoformat()
        with pool_conn() as conn:
            for t in triples:
                subj_id = resolve_entity(t["subject"], t.get("subject_type", "concept"), conn)
                obj_id = resolve_entity(t["object"], t.get("object_type", "concept"), conn)
                conn.execute(
                    "INSERT OR IGNORE INTO knowledge_triples "
                    "(subject_id, predicate, object_id, source_memory_id, confidence, weight, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (subj_id, t["predicate"], obj_id, mem_id,
                     t.get("confidence", 0.8), 1.0, now),
                )
            conn.commit()

    def _kg_augmented_retrieve(self, query: str, top_k: int) -> list[dict]:
        """Run entity extraction, traverse KG, collect connected memories."""
        entities = extract_entities(query)
        if not entities:
            return []

        with pool_conn() as conn:
            # Find entity IDs
            entity_names = [e["name"] for e in entities]
            placeholders = ",".join("?" * len(entity_names))
            entity_rows = conn.execute(
                f"SELECT id, name FROM entities WHERE LOWER(name) IN "
                f"({','.join('?' for _ in entity_names)})",
                tuple(n.lower() for n in entity_names),
            ).fetchall()

            if not entity_rows:
                return []

            eids = [r["id"] for r in entity_rows]

            # Find triples involving these entities (depth 1)
            tp = ",".join("?" * len(eids))
            triples = conn.execute(
                f"SELECT subject_id, object_id FROM knowledge_triples "
                f"WHERE subject_id IN ({tp}) OR object_id IN ({tp})",
                tuple(eids + eids),
            ).fetchall()

            related_eids = set(eids)
            for t in triples:
                related_eids.add(t["subject_id"])
                related_eids.add(t["object_id"])

            # Find memories connected to related entities
            rp = ",".join("?" * len(related_eids))
            rows = conn.execute(
                f"SELECT DISTINCT m.id, m.content, m.subject, m.level, m.category, "
                f"m.created_at, m.agent_name "
                f"FROM memories m "
                f"JOIN memory_entities me ON m.id = me.memory_id "
                f"WHERE me.entity_id IN ({rp}) "
                f"ORDER BY m.created_at DESC LIMIT ?",
                tuple(related_eids) + (top_k,),
            ).fetchall()
            return [dict(r) for r in rows]

    def _merge_results(self, standard: list, kg_results: list[dict], top_k: int) -> list:
        return super()._merge_results(standard, kg_results, top_k, "_kg_match")