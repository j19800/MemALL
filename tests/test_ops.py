"""
Test Suite — Pipeline Ops
==========================
Tests merge/split/tag/dedup operations.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_merge_memories():
    """Test: merge_memories merges source into target."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.ops import merge_memories
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        aid = insert_memory(conn, "Content of source memory", agent_name="test")
        bid = insert_memory(conn, "Content of target memory", agent_name="test")
        conn.close()

        result = merge_memories(source_id=aid, target_id=bid)
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert result.get("merged_into") == bid, f"Expected merged_into={bid}, got {result}"
        assert result.get("deleted_source") == aid, f"Expected deleted_source={aid}, got {result}"
        print("  PASS test_merge_memories")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_merge_same_id_fails():
    """Test: merge_memories raises ValueError for same IDs."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.ops import merge_memories
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        aid = insert_memory(conn, "Content", agent_name="test")
        conn.close()

        try:
            merge_memories(source_id=aid, target_id=aid)
            assert False, "Should have raised ValueError"
        except ValueError:
            pass
        print("  PASS test_merge_same_id_fails")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_split_memory_not_found():
    """Test: split_memory raises ValueError for non-existent memory."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db
    from memall.pipeline.ops import split_memory
    import pytest

    db_path, patcher = init_temp_db()
    try:
        with pytest.raises(ValueError, match="memory 9999 not found"):
            split_memory(memory_id=9999)
        print("  PASS test_split_memory_not_found")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_split_single_segment():
    """Test: split_memory handles single-segment content gracefully."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.ops import split_memory
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        mid = insert_memory(conn, "Single segment only", agent_name="test")
        conn.close()

        result = split_memory(memory_id=mid)
        assert result["split_count"] == 0, f"Expected 0 splits, got {result['split_count']}"
        assert "note" in result, f"Expected note in result: {result}"
        print("  PASS test_split_single_segment")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_tag_memory():
    """Test: tag_memory adds and retrieves tags."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.ops import tag_memory
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        mid = insert_memory(conn, "Tagged memory content", agent_name="test")
        conn.close()

        result = tag_memory(mid, ["important", "architecture"], mode="add")
        assert result["mode"] == "add", f"Expected mode 'add', got {result}"
        assert "important" in result["tags"], f"Expected 'important' in tags: {result['tags']}"

        # Now remove one tag
        result2 = tag_memory(mid, ["important"], mode="remove")
        assert "important" not in result2["tags"], f"'important' should be removed: {result2['tags']}"
        print("  PASS test_tag_memory")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_deduplicate_no_dupes():
    """Test: deduplicate returns 0 when no near-duplicates exist."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.ops import deduplicate
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        insert_memory(conn, "The quick brown fox jumps over the lazy dog", agent_name="test")
        insert_memory(conn, "Python programming language is great for data science", agent_name="test")
        conn.close()

        result = deduplicate(agent_name="test", threshold=0.9)
        assert result["duplicates_found"] == 0, f"Expected 0 duplicates, got {result}"
        print("  PASS test_deduplicate_no_dupes")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_merge_with_separator():
    """Test: merge_memories accepts custom separator."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.ops import merge_memories
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        aid = insert_memory(conn, "A", agent_name="test")
        bid = insert_memory(conn, "B", agent_name="test")
        conn.close()

        result = merge_memories(source_id=aid, target_id=bid, separator="\n<br>\n")
        assert result["separator"] == "\n<br>\n", f"wrong separator: {result}"
        assert result["merged_into"] == bid

        conn2 = get_conn()
        row = conn2.execute("SELECT content FROM memories WHERE id=?", (bid,)).fetchone()
        assert row["content"] == "B\n<br>\nA", f"wrong merged content: {row['content']!r}"
        conn2.close()
        print("  PASS test_merge_with_separator")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_batch_archive_global_dry_run():
    """Test: batch_archive works with agent_name=None + dry_run."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.ops import batch_archive, batch_restore
    from memall.core.db import get_conn
    from datetime import datetime, timedelta, timezone

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        # Insert a very old memory
        nid = insert_memory(conn, "Very old content", agent_name="test")
        conn.execute("UPDATE memories SET occurred_at=? WHERE id=?",
                     ((datetime.now(timezone.utc) - timedelta(days=60)).isoformat(), nid))
        conn.commit()
        conn.close()

        # dry-run: should not write
        dr = batch_archive(days=30, dry_run=True)
        assert dr["dry_run"] == True, f"expected dry_run: {dr}"
        assert dr["archived"] == 0, f"expected 0 archived: {dr}"
        assert len(dr["preview"]) > 0, f"expected preview: {dr}"

        # real archive
        r = batch_archive(days=30)
        assert r["archived"] >= 1, f"expected ≥1 archived: {r}"
        assert r["dry_run"] == False

        # verify original_level saved in metadata
        conn2 = get_conn()
        row = conn2.execute("SELECT level, metadata FROM memories WHERE id=?", (nid,)).fetchone()
        assert row["level"] == "archived", f"expected archived: {row}"
        assert '"original_level"' in row["metadata"], f"original_level missing: {row['metadata']}"
        conn2.close()

        # restore — should get original level back
        rr = batch_restore(dry_run=False)
        assert rr["restored"] >= 1, f"expected ≥1 restored: {rr}"
        assert rr["fallback_p2"] == 0, f"expected 0 fallback: {rr}"

        # dry-run restore
        dr2 = batch_restore(dry_run=True)
        assert dr2["dry_run"] == True

        print("  PASS test_batch_archive_global_dry_run")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_batch_tag_multi_condition_dry_run():
    """Test: batch_tag supports multi-condition + dry_run."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.ops import batch_tag
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        insert_memory(conn, "a1", agent_name="alice", category="chat", level="P0")
        insert_memory(conn, "a2", agent_name="alice", category="chat", level="P1")
        insert_memory(conn, "b1", agent_name="bob", category="chat", level="P0")
        conn.close()

        # dry-run with level filter
        dr = batch_tag(agent_name="alice", level="P0", tags=["urgent"], dry_run=True)
        assert dr["dry_run"] == True
        assert dr["matched"] == 1, f"expected 1 match: {dr}"
        assert dr["updated"] == 0

        # real batch_tag with agent_name=None (global) + multi-condition
        r = batch_tag(level="P0", tags=["urgent"], mode="add")
        assert r["matched"] >= 2, f"expected ≥2 matched: {r}"

        # tags_include filter
        r2 = batch_tag(agent_name="alice", tags_include=["urgent"], tags=["critical"], mode="add")
        assert r2["matched"] == 1, f"expected 1 match with urgent: {r2}"

        print("  PASS test_batch_tag_multi_condition_dry_run")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_dedup_dry_run_and_truncation():
    """Test: deduplicate supports dry_run + max_pairs truncation."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.ops import deduplicate
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        insert_memory(conn, "This is a test memory for dedup", agent_name="test")
        insert_memory(conn, "This is a test memory for dedup with extra", agent_name="test")
        conn.close()

        # dry-run
        dr = deduplicate(agent_name="test", threshold=0.5, dry_run=True)
        assert dr["dry_run"] == True
        assert dr["duplicates_found"] >= 1

        # too few for truncation, but verify the param is accepted
        r = deduplicate(agent_name="test", threshold=0.5, max_pairs=1, max_memories=5)
        assert r["scanned"] <= 5

        print("  PASS test_dedup_dry_run_and_truncation")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_undo_batch_tag():
    """Test: undo restores batch_tag changes."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.ops import batch_tag, undo, _ensure_ops_log
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        insert_memory(conn, "tag-test", agent_name="test", category="test", level="P0")
        conn.close()

        batch_tag(agent_name="test", tags=["applied"], mode="add")

        conn2 = get_conn()
        row = conn2.execute("SELECT tags FROM memories WHERE agent_name='test'").fetchone()
        assert '"applied"' in row["tags"], f"expected applied tag: {row['tags']}"

        # read ops_log and undo
        op_row = conn2.execute("SELECT id FROM ops_log WHERE op_type='batch_tag' ORDER BY id DESC LIMIT 1").fetchone()
        conn2.close()

        if op_row:
            result = undo(op_row["id"])
            assert result["undone"] >= 1, f"undo failed: {result}"

            conn3 = get_conn()
            row2 = conn3.execute("SELECT tags FROM memories WHERE agent_name='test'").fetchone()
            assert '"applied"' not in row2["tags"], f"undo should have removed tag: {row2['tags']}"
            conn3.close()

        print("  PASS test_undo_batch_tag")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_merge_metadata_subject():
    """Test: merge_memories merges metadata and prefers longer subject."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.ops import merge_memories
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        aid = insert_memory(conn, "short content", agent_name="test")
        bid = insert_memory(conn, "longer content here", agent_name="test",
                            summary="Detailed summary")
        conn.execute("UPDATE memories SET subject=? WHERE id=?", ("Short", aid))
        conn.execute("UPDATE memories SET subject=? WHERE id=?", ("Very Long Descriptive Title", bid))
        conn.execute("UPDATE memories SET metadata=? WHERE id=?",
                     ('{"enrich":{"value":{"entities":["test"]},"_meta":{"version":1}}}', aid))
        conn.commit()
        conn.close()

        result = merge_memories(source_id=aid, target_id=bid)
        assert result["merged_into"] == bid
        assert "subject_merged" in result

        conn2 = get_conn()
        row = conn2.execute(
            "SELECT subject, summary, content, metadata, access_count FROM memories WHERE id=?",
            (bid,)
        ).fetchone()
        assert row["subject"] == "Very Long Descriptive Title", f"expected longer subject: {row['subject']}"
        assert 'merge_src_' in str(row["metadata"]), f"expected merge_src in metadata: {row['metadata'][:100]}"
        assert row["access_count"] >= 1, f"expected access_count incremented: {row['access_count']}"
        assert "Detailed" in row["summary"], f"expected longer summary: {row['summary']}"
        conn2.close()

        print("  PASS test_merge_metadata_subject")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_batch_archive_undo_snapshot():
    """Test: batch_archive snapshot captures pre-archive state (correct undo)."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.ops import batch_archive, undo
    from memall.core.db import get_conn
    from datetime import datetime, timedelta, timezone
    import json

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        nid = insert_memory(conn, "preserve me", agent_name="test", level="P0")
        conn.execute("UPDATE memories SET occurred_at=? WHERE id=?",
                     ((datetime.now(timezone.utc) - timedelta(days=60)).isoformat(), nid))
        conn.commit()
        conn.close()

        batch_archive(days=30)

        # Read ops_log snapshot (should show P0, not archived)
        conn2 = get_conn()
        row = conn2.execute("SELECT before_snapshot FROM ops_log WHERE op_type='batch_archive' ORDER BY id DESC LIMIT 1").fetchone()
        assert row is not None, "no ops_log entry"

        snapshot = json.loads(row["before_snapshot"])
        found = False
        for mem_id_str, cols in snapshot.items():
            if int(mem_id_str) == nid:
                assert cols["level"] == "P0", f"snapshot should show P0, got {cols['level']}"
                found = True
        assert found, f"test memory {nid} not in snapshot"

        # Read op_id for undo
        op_row = conn2.execute("SELECT id FROM ops_log WHERE op_type='batch_archive' ORDER BY id DESC LIMIT 1").fetchone()
        conn2.close()

        # Undo should restore to pre-archive state
        result = undo(op_row["id"])
        assert result["undone"] >= 1, f"undo failed: {result}"

        conn3 = get_conn()
        row3 = conn3.execute("SELECT level FROM memories WHERE id=?", (nid,)).fetchone()
        assert row3["level"] == "P0", f"undo should restore P0, got {row3['level']}"
        conn3.close()

        print("  PASS test_batch_archive_undo_snapshot")
    finally:
        cleanup_temp_db(db_path, patcher)


def test_dedup_error_counting():
    """Test: deduplicate reports merge errors instead of silent pass."""
    from tests.test_helpers import init_temp_db, cleanup_temp_db, insert_memory
    from memall.pipeline.ops import deduplicate
    from memall.core.db import get_conn

    db_path, patcher = init_temp_db()
    try:
        conn = get_conn()
        insert_memory(conn, "This is a test memory for dedup error counting", agent_name="test")
        insert_memory(conn, "This is a test memory for dedup error counting with extra", agent_name="test")
        conn.close()

        result = deduplicate(agent_name="test", threshold=0.5)
        assert "errors" in result, f"expected errors key: {result}"
        assert isinstance(result["errors"], list)
        assert result["merged"] >= 1 or result["duplicates_found"] == 0
        print("  PASS test_dedup_error_counting")
    finally:
        cleanup_temp_db(db_path, patcher)


# ── Runner ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("MemALL Pipeline Ops Tests")
    print("=" * 60)
    passed = 0
    failed = 0

    tests = [
        ("test_merge_memories", test_merge_memories),
        ("test_merge_same_id_fails", test_merge_same_id_fails),
        ("test_split_memory_not_found", test_split_memory_not_found),
        ("test_split_single_segment", test_split_single_segment),
        ("test_tag_memory", test_tag_memory),
        ("test_deduplicate_no_dupes", test_deduplicate_no_dupes),
        ("test_merge_with_separator", test_merge_with_separator),
        ("test_batch_archive_global_dry_run", test_batch_archive_global_dry_run),
        ("test_batch_tag_multi_condition_dry_run", test_batch_tag_multi_condition_dry_run),
        ("test_dedup_dry_run_and_truncation", test_dedup_dry_run_and_truncation),
        ("test_undo_batch_tag", test_undo_batch_tag),
        ("test_merge_metadata_subject", test_merge_metadata_subject),
        ("test_batch_archive_undo_snapshot", test_batch_archive_undo_snapshot),
        ("test_dedup_error_counting", test_dedup_error_counting),
    ]

    for name, func in tests:
        try:
            func()
            passed += 1
        except Exception as e:
            print(f"  FAIL {name}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\nResults: {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)