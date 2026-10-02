"""
Migration 028 — add a CHECK constraint on ``memories.level``.

ADR-0001 fixes the canonical level enum at P0/P1/P2 + L1..L11. Migration 027
repaired the rows that had drifted outside it (``archived`` / ``medium`` / ``P4``),
but nothing stopped a future producer from writing a bogus value again. This
migration adds a database-level CHECK constraint so the enum can no longer be
violated at the storage layer.

SQLite cannot ``ALTER TABLE ... ADD CONSTRAINT``, so the table is rebuilt using
the documented 12-step procedure: create a new table carrying the CHECK, copy the
rows (ids preserved so ``mem_vec`` rowids and the six child tables stay valid),
drop the old table, rename, then replay the captured indexes and FTS triggers.

Idempotent: a database whose ``memories`` DDL already carries the CHECK is left
untouched.
"""

import re

MIGRATION_ID = "028_add_level_check_constraint"
DESCRIPTION = "Add CHECK constraint on memories.level (canonical enum P0/P1/P2 + L1..L11)"

_LEVELS = ("P0", "P1", "P2", "L1", "L2", "L3", "L4",
           "L5", "L6", "L7", "L8", "L9", "L10", "L11")
_CHECK = "CHECK (level IN (" + ", ".join("'%s'" % lv for lv in _LEVELS) + "))"

_LEVEL_COL_RE = re.compile(r"(\blevel\s+TEXT\s+NOT\s+NULL\s+DEFAULT\s+'P2')", re.IGNORECASE)
_TABLE_RE = re.compile(r'^(\s*CREATE\s+TABLE\s+)("?memories"?)', re.IGNORECASE)


def _already_has_check(ddl: str) -> bool:
    return "check" in ddl.lower() and "level in" in ddl.lower()


def apply(conn) -> None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='memories'"
    ).fetchone()
    if not row or not row[0]:
        return
    ddl = row[0]
    if _already_has_check(ddl):
        return  # nothing to do

    # Capture dependent objects before the DROP wipes them.
    index_sql = [
        r[0] for r in conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='memories' "
            "AND sql IS NOT NULL"
        )
    ]
    trigger_sql = [
        r[0] for r in conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' AND tbl_name='memories' "
            "AND sql IS NOT NULL"
        )
    ]
    cols = [r[1] for r in conn.execute("PRAGMA table_info(memories)")]
    if not cols:
        raise RuntimeError("migration 028: memories table has no columns")

    # Build the replacement DDL: rename the table and inject the CHECK.
    new_ddl, n = _TABLE_RE.subn(r"\1memories_new", ddl, count=1)
    if n == 0:
        raise RuntimeError("migration 028: could not rewrite memories DDL")
    new_ddl, n = _LEVEL_COL_RE.subn(lambda m: m.group(1) + " " + _CHECK, new_ddl, count=1)
    if n == 0:
        raise RuntimeError("migration 028: could not locate level column definition")

    collist = ", ".join('"%s"' % c for c in cols)

    # PRAGMA foreign_keys is a no-op inside a transaction — make sure we are in
    # autocommit before disabling it, or the DROP below would trip the 6 child FKs.
    if getattr(conn, "in_transaction", False):
        conn.commit()
    conn.execute("PRAGMA foreign_keys=OFF")
    if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 0:
        raise RuntimeError("migration 028: could not disable foreign_keys for rebuild")

    try:
        conn.execute("BEGIN")
        conn.execute(new_ddl)
        conn.execute(
            f"INSERT INTO memories_new ({collist}) SELECT {collist} FROM memories"
        )
        n_old = conn.execute("SELECT count(*) FROM memories").fetchone()[0]
        n_new = conn.execute("SELECT count(*) FROM memories_new").fetchone()[0]
        if n_old != n_new:
            raise RuntimeError(
                f"migration 028: row count mismatch ({n_old} -> {n_new}); aborting"
            )
        conn.execute("DROP TABLE memories")
        conn.execute("ALTER TABLE memories_new RENAME TO memories")
        for sql in index_sql:
            conn.execute(sql)
        for sql in trigger_sql:
            conn.execute(sql)

        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(
                f"migration 028: foreign_key_check reported {len(violations)} violation(s)"
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def rollback(conn) -> None:
    # A CHECK constraint can only be removed by another table rebuild; leaving it
    # in place is the safe outcome. No-op.
    return