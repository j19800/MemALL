"""
MemorySharing — Multi-agent memory sharing via soft references.

Shares are stored in the ``shared_records`` table with source/target agent,
trust level, and optional expiry. No data is copied — only references.

This is a **local** sharing system (same DB, different agents).  For
cross-instance sharing, use the federation module instead.

Sharing is orthogonal to MemoryStrategy — any strategy can share selected
memories after store().

Trust levels (from most restrictive to most open):
    private < trusted < family < shared < public
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from memall.core.db import pool_conn, get_conn

logger = logging.getLogger(__name__)

# Trust level ordering (for filtering)
_TRUST_ORDER = ["private", "trusted", "family", "shared", "public"]


class MemorySharing:
    """Multi-agent memory sharing using soft references.

    Args:
        source_agent: The agent doing the sharing.
    """

    def __init__(self, source_agent: str):
        self.source_agent = source_agent

    def share(
        self,
        memory_id: int,
        target_agent: str,
        trust_level: str = "family",
        ttl_days: int = 0,
    ) -> int:
        """Share a memory with another agent.

        Args:
            memory_id: Memory to share.
            target_agent: Recipient agent name.
            trust_level: One of ``private``, ``trusted``, ``family``, ``shared``, ``public``.
            ttl_days: Auto-expire after N days (0 = no expiry).

        Returns:
            Share record ID, or 0 if already shared.

        Raises:
            ValueError: If memory_id doesn't exist or trust_level is invalid.
        """
        if trust_level not in _TRUST_ORDER:
            raise ValueError(f"Invalid trust_level: {trust_level}. Must be one of {_TRUST_ORDER}")

        now = datetime.now(timezone.utc).isoformat()
        expires_at = ""
        if ttl_days > 0:
            expires_at = (datetime.now(timezone.utc) + timedelta(days=ttl_days)).isoformat()

        with pool_conn() as conn:
            # Verify memory exists
            mem = conn.execute(
                "SELECT id FROM memories WHERE id = ?", (memory_id,),
            ).fetchone()
            if not mem:
                raise ValueError(f"Memory #{memory_id} not found")

            try:
                conn.execute(
                    "INSERT INTO shared_records "
                    "(memory_id, source_agent, target_agent, trust_level, ttl_days, expires_at, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (memory_id, self.source_agent, target_agent, trust_level, ttl_days, expires_at, now),
                )
                conn.commit()
                sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                logger.info(
                    "MemorySharing: #%d shared by '%s' with '%s' (trust=%s)",
                    memory_id, self.source_agent, target_agent, trust_level,
                )
                return sid

            except Exception as e:
                if "UNIQUE" in str(e):
                    logger.debug(
                        "MemorySharing: #%d already shared with '%s'",
                        memory_id, target_agent,
                    )
                    return 0
                raise

    def broadcast(
        self,
        memory_id: int,
        target_agents: list[str],
        trust_level: str = "family",
    ) -> list[int]:
        """Share a memory with multiple agents.

        Returns:
            List of share record IDs (0 for already-shared entries).
        """
        return [
            self.share(memory_id, agent, trust_level=trust_level)
            for agent in target_agents
        ]

    def query_shared(
        self,
        agent_name: str,
        query: str = "",
        trust_min: str = "family",
        top_k: int = 20,
    ) -> list[dict]:
        """Query memories shared with this agent.

        Args:
            agent_name: The recipient agent.
            query: Optional search filter (applied to memory content).
            trust_min: Minimum trust level filter.
            top_k: Max results.

        Returns:
            List of memory dicts from the main memories table.
        """
        min_rank = _TRUST_ORDER.index(trust_min) if trust_min in _TRUST_ORDER else 2

        conn = get_conn()
        try:
            now = datetime.now(timezone.utc).isoformat()
            rows = conn.execute(
                "SELECT m.id, m.content, m.subject, m.level, m.category, "
                "m.created_at, m.agent_name, sr.trust_level, sr.source_agent, sr.created_at AS shared_at "
                "FROM shared_records sr "
                "JOIN memories m ON sr.memory_id = m.id "
                "WHERE sr.target_agent = ? "
                "AND (sr.expires_at IS NULL OR sr.expires_at = '' OR sr.expires_at > ?) "
                "ORDER BY sr.created_at DESC LIMIT ?",
                (agent_name, now, top_k),
            ).fetchall()

            results = []
            for r in rows:
                trust_rank = _TRUST_ORDER.index(r["trust_level"]) if r["trust_level"] in _TRUST_ORDER else -1
                if trust_rank < min_rank:
                    continue
                mem = dict(r)
                mem["_shared_by"] = r["source_agent"]
                mem["_shared_at"] = r["shared_at"]
                results.append(mem)

            # Optional content filter
            if query:
                query_lower = query.lower()
                results = [
                    r for r in results
                    if query_lower in (r.get("content") or "").lower()
                    or query_lower in (r.get("subject") or "").lower()
                ]

            return results[:top_k]

        finally:
            conn.close()

    def unshare(self, memory_id: int, target_agent: str = None) -> int:
        """Remove a share reference.

        Args:
            memory_id: Shared memory ID.
            target_agent: If set, only remove this specific share. Otherwise remove all.

        Returns:
            Count of records removed.
        """
        with pool_conn() as conn:
            if target_agent:
                conn.execute(
                    "DELETE FROM shared_records WHERE memory_id = ? AND target_agent = ?",
                    (memory_id, target_agent),
                )
            else:
                conn.execute(
                    "DELETE FROM shared_records WHERE memory_id = ?",
                    (memory_id,),
                )
            count = conn.execute("SELECT changes()").fetchone()[0]
            conn.commit()
            if count:
                logger.info(
                    "MemorySharing: unshared #%d (%d refs removed)", memory_id, count,
                )
            return count

    @staticmethod
    def get_shared_stats(agent_name: str) -> dict:
        """Get sharing statistics for an agent.

        Returns dict with: shared_out, shared_in, pending_expiry.
        """
        conn = get_conn()
        try:
            now = datetime.now(timezone.utc).isoformat()
            out = conn.execute(
                "SELECT COUNT(*) FROM shared_records WHERE source_agent = ?",
                (agent_name,),
            ).fetchone()[0]
            inp = conn.execute(
                "SELECT COUNT(*) FROM shared_records WHERE target_agent = ?",
                (agent_name,),
            ).fetchone()[0]
            expiring = conn.execute(
                "SELECT COUNT(*) FROM shared_records WHERE (source_agent = ? OR target_agent = ?) "
                "AND expires_at IS NOT NULL AND expires_at != '' AND expires_at < ?",
                (agent_name, agent_name, now),
            ).fetchone()[0]
            return {"shared_out": out, "shared_in": inp, "pending_expiry": expiring}
        finally:
            conn.close()