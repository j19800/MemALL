"""
Migration 029: Reindex FTS5 with per-character CJK tokens.

Root cause
----------
The FTS5 sync triggers used to insert ``new.content`` verbatim.  FTS5's
built-in ``unicode61`` tokenizer treats a contiguous CJK run as a single
token, so "检索架构" indexed as one token and a query for "检索" matched
nothing — Chinese keyword search silently returned zero rows.  Production
databases built before the CJK fix carry that index (their vocabulary was
produced by a CJK-aware tokenizer in an older SQLite build, so they happen
to work, but they are inconsistent with the current ``fts_query()`` output
and with any database created from scratch today).

Fix
---
For databases whose FTS table is still the legacy 3-column shape (no
``tags`` column): rewrite the triggers to call ``memall_split_cjk()`` so
each CJK character becomes its own token, then rebuild the whole index so
existing rows are tokenised the same way.

The trigger SQL below is deliberately frozen at the 3-column shape: this
migration is a point-in-time repair for old databases.  Databases created
after the CJK fix already carry the canonical triggers (``db.FTS5_TRIGGERS``)
and the 4-column FTS table — for them this migration is a no-op, and
migration 030 owns any further index upgrade.  Re-creating the *current*
canonical triggers from here would clobber a newer schema (the bug this
comment documents).

FTS5's own ``'rebuild'`` command re-reads the content columns *without*
applying our SQL function, so it cannot change the tokenisation — the
rebuild below deletes and re-inserts through the split explicitly.
"""

MIGRATION_ID = "029_reindex_fts_cjk"
DESCRIPTION = "Reindex FTS5 with per-character CJK tokens via memall_split_cjk"


def apply(conn):
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='memories_fts'"
    ).fetchone()
    ddl = (row[0] if row and row[0] else "").lower()
    if "tags" in ddl:
        # Fresh database: SCHEMA_SQL + init_db already created the 4-column
        # index and the canonical triggers.  Nothing to repair here.
        return

    # 1. Make sure the SQL function the new triggers depend on exists.
    from memall.core.db import _register_sql_functions

    _register_sql_functions(conn)

    # 2. Drop and recreate the sync triggers with CJK splitting (frozen
    #    3-column shape — see module docstring).
    conn.execute("DROP TRIGGER IF EXISTS memories_ai")
    conn.execute("DROP TRIGGER IF EXISTS memories_ad")
    conn.execute("DROP TRIGGER IF EXISTS memories_au")

    conn.execute(
        "CREATE TRIGGER memories_ai AFTER INSERT ON memories BEGIN "
        "INSERT INTO memories_fts(rowid, content, subject, summary) VALUES "
        "(new.id, memall_split_cjk(new.content), memall_split_cjk(new.subject), "
        "memall_split_cjk(new.summary)); END"
    )
    conn.execute(
        "CREATE TRIGGER memories_ad AFTER DELETE ON memories BEGIN "
        "INSERT INTO memories_fts(memories_fts, rowid, content, subject, summary) VALUES "
        "('delete', old.id, memall_split_cjk(old.content), memall_split_cjk(old.subject), "
        "memall_split_cjk(old.summary)); END"
    )
    conn.execute(
        "CREATE TRIGGER memories_au AFTER UPDATE ON memories BEGIN "
        "INSERT INTO memories_fts(memories_fts, rowid, content, subject, summary) VALUES "
        "('delete', old.id, memall_split_cjk(old.content), memall_split_cjk(old.subject), "
        "memall_split_cjk(old.summary)); "
        "INSERT INTO memories_fts(rowid, content, subject, summary) VALUES "
        "(new.id, memall_split_cjk(new.content), memall_split_cjk(new.subject), "
        "memall_split_cjk(new.summary)); END"
    )

    # 3. Rebuild the index so every existing row uses the new tokenisation.
    conn.execute("DELETE FROM memories_fts")
    conn.execute(
        "INSERT INTO memories_fts(rowid, content, subject, summary) "
        "SELECT id, memall_split_cjk(content), memall_split_cjk(subject), "
        "memall_split_cjk(summary) FROM memories"
    )
    conn.commit()
