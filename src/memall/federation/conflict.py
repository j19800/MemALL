import math
import numpy as np
from datetime import datetime, timezone
from itertools import combinations
import sqlite3

from memall.federation.family import get_family_db_path
from memall.core.nlp import (
    cosine_sim, compute_tfidf, tfidf_svd_embed,
)

CONTRADICTION_PAIRS = [
    ("采用", "放弃"), ("推荐", "避免"), ("支持", "反对"),
    ("好", "不好"), ("优", "劣"), ("选择", "排除"),
    ("肯定", "否定"), ("是", "否"), ("需要", "不需要"),
    ("应该", "不应该"), ("必须", "不必"), ("使用", "不用"),
    ("保留", "删除"), ("增加", "减少"), ("开源", "闭源"),
    ("本地", "云端"), ("简单", "复杂"), ("快", "慢"),
    ("安全", "风险"), ("同意", "反对"),
]

CONTRADICTION_KEYWORDS = set()
for a, b in CONTRADICTION_PAIRS:
    CONTRADICTION_KEYWORDS.add(a)
    CONTRADICTION_KEYWORDS.add(b)

TRIGGER_KEYWORDS = {"but", "however", "actually", "其实", "实际上", "不对", "相反", "而是", "但是", "不过"}

# Module-level guard: migration DDL runs once per process lifetime
_CONFLICT_DB_MIGRATED = False


def _migrate_family_db():
    """Add conflict_status to shared_memories, create conflicts table."""
    global _CONFLICT_DB_MIGRATED
    if _CONFLICT_DB_MIGRATED:
        return
    db_path = get_family_db_path()
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        cur = conn.execute("PRAGMA table_info(shared_memories)")
        cols = {r[1] for r in cur.fetchall()}
        if "conflict_status" not in cols:
            conn.execute("ALTER TABLE shared_memories ADD COLUMN conflict_status TEXT DEFAULT 'none'")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conflicts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id_a INTEGER NOT NULL,
                memory_id_b INTEGER NOT NULL,
                cluster_label TEXT DEFAULT '',
                conflict_type TEXT DEFAULT 'potential',
                status TEXT DEFAULT 'open',
                winner_id INTEGER,
                detected_at TEXT NOT NULL,
                resolved_at TEXT,
                UNIQUE(memory_id_a, memory_id_b)
            )
        """)
        conn.commit()
        _CONFLICT_DB_MIGRATED = True
    finally:
        conn.close()


def _detect_contradiction(text_a: str, text_b: str) -> bool:
    """Check if two texts contain contradictory keyword pairs."""
    for kw_a, kw_b in CONTRADICTION_PAIRS:
        has_a = kw_a in text_a or kw_a in text_b
        has_b = kw_b in text_a or kw_b in text_b
        if has_a and has_b:
            return True
    return False


def detect_conflicts(threshold: float = 0.85, mode: str = "all") -> dict:
    _migrate_family_db()
    db_path = get_family_db_path()
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, original_id, source_agent, content, conflict_status FROM shared_memories"
        ).fetchall()
        if len(rows) < 2:
            return {"conflicts_detected": 0, "total_memories": len(rows)}

        texts = [r["content"] for r in rows]
        ids = [r["id"] for r in rows]
        now = datetime.now(timezone.utc).isoformat()

        conn.execute("BEGIN IMMEDIATE")
        # Reset only memories being rechecked (not all shared_memories)
        # This is idempotent: if we crash mid-way, next run won't lose state
        conn.execute("UPDATE shared_memories SET conflict_status = 'none' WHERE conflict_status = 'pending'")

        # Mark existing open conflicts as 'rechecking' instead of deleting
        # This ensures data isn't lost if the detection crashes partway through
        conn.execute("UPDATE conflicts SET status = 'rechecking' WHERE status = 'open'")
        conn.commit()

        seen_pairs = set()
        conflict_count = 0

        if mode in ("keyword", "all"):
            tfidf_docs = compute_tfidf(texts)
            clusters = []
            assigned = [False] * len(rows)
            for i in range(len(rows)):
                if assigned[i]:
                    continue
                cluster = [i]
                assigned[i] = True
                for j in range(i + 1, len(rows)):
                    if not assigned[j] and cosine_sim(tfidf_docs[i], tfidf_docs[j]) >= 0.10:
                        cluster.append(j)
                        assigned[j] = True
                clusters.append(cluster)

            for cluster in clusters:
                if len(cluster) < 2:
                    continue
                for i, j in combinations(cluster, 2):
                    if _detect_contradiction(texts[i], texts[j]):
                        key = (min(ids[i], ids[j]), max(ids[i], ids[j]))
                        if key not in seen_pairs:
                            seen_pairs.add(key)
                            conn.execute(
                                "INSERT OR IGNORE INTO conflicts (memory_id_a, memory_id_b, cluster_label, conflict_type, status, detected_at) VALUES (?, ?, ?, ?, ?, ?)",
                                (key[0], key[1], "", "keyword", "open", now),
                            )
                            conn.execute(
                                "UPDATE shared_memories SET conflict_status = 'potential' WHERE id IN (?, ?)",
                                (key[0], key[1]),
                            )
                            conflict_count += 1

        if mode in ("semantic", "all"):
            vecs = tfidf_svd_embed(texts)
            if vecs is not None:
                n = len(vecs)
                for i in range(n):
                    for j in range(i + 1, n):
                        sim = float(np.dot(vecs[i], vecs[j]) / (np.linalg.norm(vecs[i]) * np.linalg.norm(vecs[j]) + 1e-10))
                        if sim >= threshold:
                            key = (min(ids[i], ids[j]), max(ids[i], ids[j]))
                            if key not in seen_pairs:
                                seen_pairs.add(key)
                                conn.execute(
                                    "INSERT OR IGNORE INTO conflicts (memory_id_a, memory_id_b, cluster_label, conflict_type, status, detected_at) VALUES (?, ?, ?, ?, ?, ?)",
                                    (key[0], key[1], "", "semantic", "open", now),
                                )
                                conn.execute(
                                    "UPDATE shared_memories SET conflict_status = 'potential' WHERE id IN (?, ?)",
                                    (key[0], key[1]),
                                )
                                conflict_count += 1

        conn.commit()

        # Detection succeeded — clean up old rechecking records
        conn.execute("DELETE FROM conflicts WHERE status = 'rechecking'")
        conn.commit()

        return {
            "conflicts_detected": conflict_count,
            "total_memories": len(rows),
            "mode": mode,
            "threshold": threshold,
        }
    finally:
        conn.close()


def list_conflicts(status: str = "open") -> list:
    _migrate_family_db()
    db_path = get_family_db_path()
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        where = ""
        params = []
        if status and status != "all":
            where = "WHERE c.status = ?"
            params = [status]
        rows = conn.execute(
            f"SELECT c.id, c.memory_id_a, c.memory_id_b, c.cluster_label, c.conflict_type, c.status, c.winner_id, c.detected_at, "
            f"a.content as content_a, a.source_agent as agent_a, "
            f"b.content as content_b, b.source_agent as agent_b "
            f"FROM conflicts c "
            f"JOIN shared_memories a ON c.memory_id_a = a.id "
            f"JOIN shared_memories b ON c.memory_id_b = b.id "
            f"{where} ORDER BY c.detected_at DESC LIMIT 50",
            tuple(params),
        ).fetchall()
        results = []
        for r in rows:
            results.append({
                "id": r["id"],
                "memory_id_a": r["memory_id_a"],
                "memory_id_b": r["memory_id_b"],
                "content_a": r["content_a"][:150],
                "agent_a": r["agent_a"],
                "content_b": r["content_b"][:150],
                "agent_b": r["agent_b"],
                "status": r["status"],
                "cluster_label": r["cluster_label"],
                "conflict_type": r["conflict_type"],
                "winner_id": r["winner_id"],
                "detected_at": r["detected_at"],
            })
        return results
    finally:
        conn.close()


def resolve_conflict(conflict_id: int, winner_memory_id: int) -> dict:
    _migrate_family_db()
    db_path = get_family_db_path()
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        now = datetime.now(timezone.utc).isoformat()
        conflict = conn.execute("SELECT * FROM conflicts WHERE id = ?", (conflict_id,)).fetchone()
        if not conflict:
            return {"error": f"conflict #{conflict_id} not found"}
        if conflict["status"] != "open":
            return {"error": f"conflict #{conflict_id} is already {conflict['status']}"}

        if winner_memory_id not in (conflict["memory_id_a"], conflict["memory_id_b"]):
            return {"error": f"winner must be one of the conflict pair ({conflict['memory_id_a']}, {conflict['memory_id_b']})"}

        conn.execute(
            "UPDATE conflicts SET status='resolved', winner_id=?, resolved_at=? WHERE id=?",
            (winner_memory_id, now, conflict_id),
        )
        loser_id = conflict["memory_id_b"] if winner_memory_id == conflict["memory_id_a"] else conflict["memory_id_a"]
        conn.execute("UPDATE shared_memories SET conflict_status='resolved' WHERE id=?", (winner_memory_id,))
        conn.execute("UPDATE shared_memories SET conflict_status='superseded' WHERE id=?", (loser_id,))
        conn.commit()
        return {"resolved": True, "conflict_id": conflict_id, "winner": winner_memory_id, "loser": loser_id}
    finally:
        conn.close()


def auto_resolve() -> dict:
    _migrate_family_db()
    db_path = get_family_db_path()
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conflicts = conn.execute(
            "SELECT c.*, a.content as content_a, b.content as content_b "
            "FROM conflicts c "
            "JOIN shared_memories a ON c.memory_id_a = a.id "
            "JOIN shared_memories b ON c.memory_id_b = b.id "
            "WHERE c.status='open'"
        ).fetchall()
        resolved = 0
        for c in conflicts:
            a_kw = sum(1 for kw in CONTRADICTION_KEYWORDS if kw in c["content_a"])
            b_kw = sum(1 for kw in CONTRADICTION_KEYWORDS if kw in c["content_b"])
            certainty_a = sum(1 for kw in ["必须", "一定", "确定", "结论", "最终", "就这样"] if kw in c["content_a"])
            certainty_b = sum(1 for kw in ["必须", "一定", "确定", "结论", "最终", "就这样"] if kw in c["content_b"])
            score_a = a_kw * 1.5 + certainty_a * 2
            score_b = b_kw * 1.5 + certainty_b * 2
            len_a = len(c["content_a"])
            len_b = len(c["content_b"])
            score_a += math.log(len_a + 1) * 0.5
            score_b += math.log(len_b + 1) * 0.5
            winner = c["memory_id_a"] if score_a >= score_b else c["memory_id_b"]
            result = resolve_conflict(c["id"], winner)
            if result.get("resolved"):
                resolved += 1
        return {"auto_resolved": resolved, "total_processed": len(conflicts)}
    finally:
        conn.close()
