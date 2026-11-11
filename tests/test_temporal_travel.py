"""Temporal validity: every memory knows when it became true, and date filters work.

Both were completely dead: valid_from was only written on SUPERSEDE (so a
normal memory had NULL), and the SQL looked for date_start/date_end while every
caller sent from_occurred_at/to_occurred_at — a date-range query silently
returned the whole corpus.
"""
import json

from tests.test_helpers import init_temp_db, cleanup_temp_db


def _seed_timeline():
    from memall.core.models import MemoryInput
    from memall.core.thin_waist import capture
    ids = []
    for ts, text in (
        ("2026-09-01T10:00:00+00:00", "数据库选 PostgreSQL 16，团队熟悉事务稳定。"),
        ("2026-10-01T10:00:00+00:00", "数据库改用 MySQL 8.0，DBA 只熟悉 MySQL。"),
        ("2026-11-01T10:00:00+00:00", "数据库最终改回 PostgreSQL 16 版本。"),
    ):
        ids.append(int(capture(MemoryInput(
            content=text, level="L4", category="decision",
            agent_name="tt", project="pay", occurred_at=ts))))
    return ids


def test_every_memory_gets_valid_from_from_occurred_at():
    db, orig = init_temp_db()
    try:
        from memall.core.db import get_conn
        a, b, c = _seed_timeline()
        conn = get_conn()
        rows = {r[0]: r[1] for r in conn.execute(
            "SELECT id, valid_from FROM memories").fetchall()}
        conn.close()
        # valid_from is when the FACT became true, not when the row was written.
        assert rows[a].startswith("2026-09-01"), rows[a]
        assert rows[b].startswith("2026-10-01"), rows[b]
        assert rows[c].startswith("2026-11-01"), rows[c]
        print("PASS: test_every_memory_gets_valid_from_from_occurred_at")
    finally:
        cleanup_temp_db(db, orig)


def test_date_range_filter_actually_filters():
    db, orig = init_temp_db()
    try:
        from memall.core.thin_waist import unified_search
        a, b, c = _seed_timeline()

        def ids(**extra):
            args = {"query": "数据库", "mode": "fts5", "agent_name": "tt",
                    "top_k": 10, "include_review": True}
            args.update(extra)
            out = unified_search(args)
            d = json.loads(out) if isinstance(out, str) else out
            return [r.get("memory_id") for r in (d.get("results") or [])]

        sept = ids(from_occurred_at="2026-09-01T00:00:00+00:00",
                   to_occurred_at="2026-09-30T23:59:59+00:00")
        oct_ = ids(from_occurred_at="2026-10-01T00:00:00+00:00",
                   to_occurred_at="2026-10-31T23:59:59+00:00")
        nov = ids(from_occurred_at="2026-11-01T00:00:00+00:00",
                  to_occurred_at="2026-11-30T23:59:59+00:00")
        assert sept == [a], f"September window wrong: {sept}"
        assert oct_ == [b], f"October window wrong: {oct_}"
        assert nov == [c], f"November window wrong: {nov}"

        # An open-ended window must still work.
        from_oct = ids(from_occurred_at="2026-10-01T00:00:00+00:00")
        assert set(from_oct) == {b, c}, from_oct
        print("PASS: test_date_range_filter_actually_filters")
    finally:
        cleanup_temp_db(db, orig)


def test_temporal_params_survive_the_mcp_validator():
    """Undeclared fields are stripped by validate_tool_input's exclude_unset."""
    db, orig = init_temp_db()
    try:
        from memall.mcp.validator import validate_tool_input
        for action in ("search", "unified_search", "retrieve"):
            ok, data, err = validate_tool_input("memall_read", {
                "action": action, "query": "x",
                "from_occurred_at": "2026-10-01T00:00:00+00:00",
                "to_occurred_at": "2026-10-31T23:59:59+00:00",
                "include_archived": True,
            })
            assert ok, err
            assert data.get("from_occurred_at"), (action, data)
            assert data.get("to_occurred_at"), (action, data)
            assert data.get("include_archived") is True, (action, data)
        print("PASS: test_temporal_params_survive_the_mcp_validator")
    finally:
        cleanup_temp_db(db, orig)
