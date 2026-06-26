"""
Family / multi-user space management (GAP-8).

Enhances family.db with family_circle table for member management,
supports memall family init / invite / search, and trust-level-aware publishing.
"""

import logging
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from memall.core.db import get_db_path


logger = logging.getLogger("memall.federation.family")

_FAMILY_DB_INITIALIZED = False

_SQLI_PATTERNS = [
    re.compile(r"'.*--", re.IGNORECASE),
    re.compile(r"'.*#", re.IGNORECASE),
    re.compile(r"'.*;", re.IGNORECASE),
    re.compile(r"\bdrop\b\s+\btable\b", re.IGNORECASE),
    re.compile(r"\bdelete\b\s+\bfrom\b", re.IGNORECASE),
    re.compile(r"\balter\b\s+\btable\b", re.IGNORECASE),
    re.compile(r"\binsert\b\s+\bin(to)?\b", re.IGNORECASE),
    re.compile(r"\bupdate\b\s+\w+\s+\bset\b", re.IGNORECASE),
    re.compile(r"\bexec(ute)?\b", re.IGNORECASE),
    re.compile(r"\bshutdown\b", re.IGNORECASE),
]

# Trust levels allowed for family publishing (private excluded)
FAMILY_TRUST_LEVELS = {"trusted", "family", "shared", "public"}


def _log_sql_warning(text: str) -> str:
    """Detect potential SQL injection patterns in text (defense-in-depth logging).

    MemALL already uses parameterized queries for all SQL operations.
    This function logs a warning on detection but does NOT modify the input,
    to avoid data loss from over-aggressive sanitization.

    Returns:
        The original text unchanged.
    """
    for pattern in _SQLI_PATTERNS:
        if pattern.search(text):
            logger.warning(
                "Potential SQL injection pattern detected in text (len=%d): %r",
                len(text), pattern.pattern,
            )
    return text


def _get_memory_by_id(memory_id: int):
    from memall.core.thin_waist import retrieve
    result = retrieve(memory_id)
    if hasattr(result, "id"):
        return result
    return None


def get_family_db_path() -> Path:
    return Path.home() / ".memall" / "family.db"


def init_family_db(force: bool = False):
    """Initialize family database with shared_memories and family_circle tables."""
    global _FAMILY_DB_INITIALIZED
    if _FAMILY_DB_INITIALIZED and not force:
        return
    db_path = get_family_db_path()
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")

    # ── shared_memories table ──
    conn.execute("""
        CREATE TABLE IF NOT EXISTS shared_memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            original_id INTEGER NOT NULL,
            source_agent TEXT NOT NULL,
            source_db TEXT NOT NULL,
            content TEXT NOT NULL,
            category TEXT DEFAULT '',
            level TEXT DEFAULT 'P2',
            owner TEXT DEFAULT '',
            published_at TEXT NOT NULL,
            UNIQUE(original_id, source_agent)
        )
    """)

    # Migrate: add columns that may be missing from older family.db
    cur = conn.execute("PRAGMA table_info(shared_memories)")
    cols = [r["name"] for r in cur.fetchall()]
    if "trust_level" not in cols:
        conn.execute("ALTER TABLE shared_memories ADD COLUMN trust_level TEXT DEFAULT 'family'")
    if "project" not in cols:
        conn.execute("ALTER TABLE shared_memories ADD COLUMN project TEXT DEFAULT ''")

    # Indexes: create after columns are guaranteed
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_shared_agent ON shared_memories(source_agent)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_shared_content ON shared_memories(content)
    """)

    # ── family_circle table (GAP-8) ──
    conn.execute("""
        CREATE TABLE IF NOT EXISTS family_circle (
            circle_id TEXT NOT NULL,
            name TEXT NOT NULL,
            member_name TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'member',
            joined_at TEXT NOT NULL,
            invited_by TEXT DEFAULT '',
            status TEXT DEFAULT 'active',
            metadata TEXT DEFAULT '{}',
            PRIMARY KEY (circle_id, member_name)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_family_circle_id ON family_circle(circle_id)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_family_member ON family_circle(member_name)
    """)

    conn.commit()
    conn.close()
    _FAMILY_DB_INITIALIZED = True


# ══════════════════════════════════════════════════════════════════
# Family Circle Management (GAP-8)
# ══════════════════════════════════════════════════════════════════

def family_init(circle_name: str, owner_name: str = "admin") -> dict:
    """Initialize a new family circle.

    Usage: memall family init <circle_name> [--owner NAME]
    """
    init_family_db()
    circle_id = str(uuid.uuid4())[:8]
    now = datetime.now(timezone.utc).isoformat()

    db_path = get_family_db_path()
    conn = sqlite3.connect(str(db_path), timeout=10)
    try:
        # Check if circle already exists
        existing = conn.execute(
            "SELECT circle_id FROM family_circle WHERE name = ? LIMIT 1",
            (circle_name,),
        ).fetchone()
        if existing:
            return {"status": "error", "reason": f"Family circle '{circle_name}' already exists"}

        conn.execute(
            "INSERT INTO family_circle (circle_id, name, member_name, role, joined_at, status) VALUES (?, ?, ?, ?, ?, ?)",
            (circle_id, circle_name, owner_name, "admin", now, "active"),
        )
        conn.commit()
        return {
            "status": "ok",
            "circle_id": circle_id,
            "circle_name": circle_name,
            "owner": owner_name,
            "message": f"Family circle '{circle_name}' created (id={circle_id}). Owner: {owner_name}",
        }
    finally:
        conn.close()


def family_invite(circle_name: str, member_name: str, role: str = "member",
                   invited_by: str = "") -> dict:
    """Invite a member to a family circle.

    Usage: memall family invite <member_name> --circle <circle_name> [--role admin|member] [--invited-by NAME]
    """
    init_family_db()
    now = datetime.now(timezone.utc).isoformat()

    db_path = get_family_db_path()
    conn = sqlite3.connect(str(db_path), timeout=10)
    try:
        # Find circle
        circle = conn.execute(
            "SELECT circle_id FROM family_circle WHERE name = ? LIMIT 1",
            (circle_name,),
        ).fetchone()
        if not circle:
            return {"status": "error", "reason": f"Family circle '{circle_name}' not found. Run 'memall family init {circle_name}' first."}

        circle_id = circle[0]

        # Check if already a member
        existing = conn.execute(
            "SELECT 1 FROM family_circle WHERE circle_id = ? AND member_name = ?",
            (circle_id, member_name),
        ).fetchone()
        if existing:
            return {"status": "already_member", "message": f"'{member_name}' is already in '{circle_name}'"}

        conn.execute(
            "INSERT INTO family_circle (circle_id, name, member_name, role, joined_at, invited_by) VALUES (?, ?, ?, ?, ?, ?)",
            (circle_id, circle_name, member_name, role, now, invited_by),
        )
        conn.commit()
        return {
            "status": "ok",
            "circle_id": circle_id,
            "circle_name": circle_name,
            "member": member_name,
            "role": role,
            "message": f"Invited '{member_name}' to family circle '{circle_name}' as {role}",
        }
    finally:
        conn.close()


def family_list(circle_name: str = "") -> list:
    """List members of a family circle (or all circles).

    Usage: memall family list [--circle NAME]
    """
    init_family_db()
    db_path = get_family_db_path()
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        if circle_name:
            rows = conn.execute(
                "SELECT * FROM family_circle WHERE name = ? ORDER BY role, joined_at",
                (circle_name,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM family_circle ORDER BY name, role, joined_at"
            ).fetchall()

        results = []
        for r in rows:
            results.append({
                "circle_id": r["circle_id"],
                "circle_name": r["name"],
                "member": r["member_name"],
                "role": r["role"],
                "joined_at": r["joined_at"],
                "invited_by": r["invited_by"],
                "status": r["status"],
            })
        return results
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════
# Publishing (with trust_level auto-filtering — GAP-8)
# ══════════════════════════════════════════════════════════════════

def publish_memory(memory_id: int, scope: str = "family",
                   trust_level: str = "",
                   enforce_trust: bool = True) -> dict:
    """Publish a memory to the family library.

    If enforce_trust is True, only memories with visibility in
    FAMILY_TRUST_LEVELS (trusted/family/shared/public) can be published.
    Private memories are rejected unless enforce_trust is False.

    Usage: memall publish <id> [--scope family] [--trust-level family]
    """
    mem = _get_memory_by_id(memory_id)
    if not mem:
        return {"error": f"memory #{memory_id} not found"}
    if scope != "family":
        return {"error": f"unsupported scope: {scope}"}

    # Trust-level filtering (GAP-8)
    mem_visibility = getattr(mem, "visibility", "private") or "private"
    if enforce_trust and mem_visibility not in FAMILY_TRUST_LEVELS:
        return {
            "error": f"memory #{memory_id} has visibility '{mem_visibility}', "
                     f"not allowed for family publishing. "
                     f"Change visibility with: memall trust {memory_id} --level family",
            "current_visibility": mem_visibility,
            "allowed_levels": list(FAMILY_TRUST_LEVELS),
        }

    effective_trust = trust_level or mem_visibility

    init_family_db()
    db_path = get_family_db_path()
    conn = sqlite3.connect(str(db_path), timeout=10)
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT OR REPLACE INTO shared_memories "
            "(original_id, source_agent, source_db, content, category, level, owner, trust_level, published_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (memory_id, mem.agent_name, str(get_db_path()), _log_sql_warning(mem.content),
             _log_sql_warning(mem.category), mem.level, _log_sql_warning(mem.owner),
             effective_trust, now),
        )
        conn.commit()
        return {
            "published": True,
            "memory_id": memory_id,
            "scope": scope,
            "trust_level": effective_trust,
            "target_db": str(db_path),
        }
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════
# Search (cross-member — already exists, enhanced for GAP-8)
# ══════════════════════════════════════════════════════════════════

def search_family(query: str, limit: int = 20, trust_level: str = "",
                  member_filter: str = "") -> list:
    """Search family shared_memories (cross-member search).

    Supports optional trust_level and member filtering (GAP-8).

    Usage: memall family search <query> [--trust-level family] [--member NAME]
    """
    init_family_db()
    db_path = get_family_db_path()
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        # Escape SQL LIKE wildcards (% and _) to prevent unintended pattern matching
        safe_query = query.replace("%", "\\%").replace("_", "\\_")
        like = f"%{safe_query}%"
        conditions = ["(content LIKE ? ESCAPE '\\' OR category LIKE ? ESCAPE '\\')"]
        params = [like, like]

        if trust_level:
            conditions.append("trust_level = ?")
            params.append(trust_level)

        if member_filter:
            conditions.append("owner = ?")
            params.append(member_filter)

        where = " AND ".join(conditions)
        sql = f"SELECT * FROM shared_memories WHERE {where} ORDER BY published_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()
        results = []
        for r in rows:
            results.append({
                "source": "family",
                "family_id": r["id"],
                "original_id": r["original_id"],
                "source_agent": r["source_agent"],
                "source_db": r["source_db"],
                "content": r["content"][:200],
                "category": r["category"],
                "level": r["level"],
                "trust_level": r["trust_level"],
                "published_at": r["published_at"],
            })
        return results
    finally:
        conn.close()


def get_family_stats() -> dict:
    """Get family library statistics."""
    init_family_db()
    db_path = get_family_db_path()
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        total = conn.execute("SELECT COUNT(*) FROM shared_memories").fetchone()[0]
        agents = conn.execute(
            "SELECT source_agent, COUNT(*) as cnt FROM shared_memories GROUP BY source_agent ORDER BY cnt DESC"
        ).fetchall()
        members = conn.execute(
            "SELECT COUNT(DISTINCT member_name) FROM family_circle WHERE status = 'active'"
        ).fetchone()[0]
        circles = conn.execute(
            "SELECT COUNT(DISTINCT circle_id) FROM family_circle"
        ).fetchone()[0]

        # Trust level distribution
        trust_dist = {}
        trust_rows = conn.execute(
            "SELECT trust_level, COUNT(*) as cnt FROM shared_memories GROUP BY trust_level"
        ).fetchall()
        for row in trust_rows:
            trust_dist[row["trust_level"]] = row["cnt"]

        return {
            "total": total,
            "agents": {r["source_agent"]: r["cnt"] for r in agents},
            "members": members,
            "circles": circles,
            "trust_distribution": trust_dist,
        }
    finally:
        conn.close()
