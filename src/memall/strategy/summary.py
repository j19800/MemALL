"""
SummaryStrategy — Auto-triggers summary generation after N memories.

After every N new memories (configurable via ``trigger_after``), generates
an L9 summary of recent activity and stores it via capture().  The summary
is linked to its source memories via ``refines`` edges.

Config:
    trigger_after (int): Number of new memories before auto-summary (default 10).
    max_sources (int): Max source memories per summary (default 20).
"""

import json
import logging
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Optional

from memall.core.thin_waist import capture as _capture, retrieve as _retrieve
from memall.core.models import MemoryInput
from memall.core.db import get_conn
from .base import MemoryStrategy

logger = logging.getLogger(__name__)


class SummaryStrategy(MemoryStrategy):
    """Auto-triggers summary generation after N memories are stored."""

    def __init__(self, agent_name: str, config: dict = None):
        super().__init__(agent_name, config)
        self.trigger_after = int(self.config.get("trigger_after", 10))
        self.max_sources = int(self.config.get("max_sources", 20))
        # In-memory counter (resets on process restart — acceptable)
        self._counter = 0

    def store(self, data: MemoryInput | dict | str, **overrides) -> int:
        """Store and check if summary trigger is reached."""
        mem_id = _capture(data, **overrides)
        self._counter += 1
        if self._counter >= self.trigger_after:
            try:
                self._generate_and_store_summary()
            except Exception as e:
                logger.warning("SummaryStrategy: auto-summary failed: %s", e)
            self._counter = 0
        return mem_id

    def retrieve(self, query: str = "", top_k: int = 10, **kwargs) -> list | dict:
        """Standard retrieve — L9 summaries are included via normal retrieval."""
        return _retrieve(query, viewer=self.agent_name, limit=top_k, **kwargs)

    def summarize(self, memory_ids: list[int] = None) -> Optional[str]:
        """Explicit summary trigger (bypasses the auto-counter)."""
        if memory_ids:
            return self._generate_summary_from_ids(memory_ids)
        return self._generate_and_store_summary()

    def _generate_and_store_summary(self) -> Optional[str]:
        """Generate an L9 summary from recent memories and store it."""
        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT id, content, subject, category, level FROM memories "
                "WHERE LOWER(agent_name) = LOWER(?) AND level NOT IN ('L9','L10','L11') "
                "ORDER BY created_at DESC LIMIT ?",
                (self.agent_name, self.max_sources),
            ).fetchall()
            if not rows:
                return None

            source_ids = [r["id"] for r in rows]
            now = datetime.now(timezone.utc).isoformat()

            # Build summary text (mirrors distill_step L9 format)
            contents = [r["content"] for r in rows if r["content"]]
            subjects = list(dict.fromkeys(r["subject"] for r in rows if r["subject"]))
            categories = list(dict.fromkeys(r["category"] for r in rows if r["category"]))

            # Keyword frequency
            words: Counter = Counter()
            for c in contents:
                tokens = re.findall(r"[a-zA-Z一-鿿][a-zA-Z一-鿿0-9]{1,20}", c[:300])
                words.update(t.lower() for t in tokens)
            top_keywords = [w for w, _ in words.most_common(8) if w not in _STOPWORDS][:5]

            # Key sentences (first meaningful sentence of first 3 sources)
            key_sentences = []
            seen = set()
            for c in contents:
                first = c.strip()[:200].split("\n")[0][:200]
                dedup_key = first[:30]
                if dedup_key not in seen and len(first) > 15:
                    key_sentences.append(first)
                    seen.add(dedup_key)
                    if len(key_sentences) >= 3:
                        break

            merged = (
                f"[L9 摘要] {self.agent_name} 近期活动 "
                f"({len(rows)} 条记录)\n"
                f"主题: {'; '.join(subjects[:3])}\n"
                f"分类: {', '.join(categories[:3])}\n"
                f"关键词: {', '.join(top_keywords)}\n"
                f"要点:\n"
                + "\n".join(f"  • {s}" for s in key_sentences)
            )

            subject = f"Auto-summary: {categories[0] if categories else 'general'} ({len(rows)} items)"

            # Store as L9 with refines edges
            mid = _capture(
                MemoryInput(
                    content=merged,
                    level="L9",
                    agent_name=self.agent_name,
                    subject=subject,
                    category=categories[0] if categories else "summary",
                    metadata=json.dumps({
                        "source": "summary_strategy",
                        "source_ids": source_ids,
                    }),
                ),
            )

            # Create refines edges
            for sid in source_ids:
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO edges (source_id, target_id, relation_type, weight, created_at, metadata) "
                        "VALUES (?, ?, 'refines', 1.0, ?, '{}')",
                        (mid, sid, now),
                    )
                except Exception:
                    pass
            conn.commit()

            logger.info(
                "SummaryStrategy: stored L9 #%d from %d sources for agent '%s'",
                mid, len(rows), self.agent_name,
            )
            return merged

        except Exception as e:
            logger.error("SummaryStrategy: summary generation failed: %s", e)
            return None
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _generate_summary_from_ids(self, memory_ids: list[int]) -> Optional[str]:
        """Generate summary text from specific memory IDs without storing."""
        conn = get_conn()
        try:
            placeholders = ",".join("?" * len(memory_ids))
            rows = conn.execute(
                f"SELECT content, subject FROM memories WHERE id IN ({placeholders})",
                memory_ids,
            ).fetchall()
            if not rows:
                return None
            parts = []
            for r in rows:
                parts.append(r["content"][:300] if r["content"] else r["subject"] or "")
            return "\n\n".join(parts)
        finally:
            conn.close()


_STOPWORDS = frozenset({
    "the", "and", "for", "are", "but", "not", "you", "all", "can",
    "had", "her", "was", "one", "our", "out", "has", "have", "been",
    "this", "that", "from", "with", "what", "which", "when", "where",
    "will", "would", "could", "should", "about", "their", "there",
    "这些", "那些", "这个", "那个", "什么", "怎么", "如何", "可以",
})