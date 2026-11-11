"""The quality gate must give ONE answer regardless of search mode.

Found by turning every feature on: with an enhancement active, fts5/direct
routed through enhanced_search whose explicit parameter list omitted
include_review, and hybrid/vector never filtered REVIEW at all. So the same
memory was hidden in one mode and visible in another.
"""
import json

from tests.test_helpers import init_temp_db, cleanup_temp_db


def _seed_review_and_ok():
    """One normal memory + one the resolve engine marks REVIEW."""
    from memall.core.models import MemoryInput
    from memall.core.thin_waist import capture

    ok = int(capture(MemoryInput(
        content="数据库选 PostgreSQL 16，团队熟悉且事务稳定可靠。",
        level="L4", category="decision", agent_name="rg", project="pay")))
    rev = int(capture(MemoryInput(
        content="数据库改用 MySQL 8.0，因为 DBA 团队只熟悉 MySQL。",
        level="L4", category="decision", agent_name="rg", project="pay")))
    return ok, rev


def _search(mode, **extra):
    from memall.core.thin_waist import unified_search
    args = {"query": "数据库", "mode": mode, "agent_name": "rg", "top_k": 10}
    args.update(extra)
    out = unified_search(args)
    d = json.loads(out) if isinstance(out, str) else out
    return [r.get("memory_id") for r in (d.get("results") or [])], d


def test_review_rows_hidden_in_every_search_mode():
    db, orig = init_temp_db()
    try:
        from memall.graph.embeddings import build_index
        ok, rev = _seed_review_and_ok()
        build_index(force=True)
        for mode in ("fts5", "direct", "hybrid", "auto", "vector"):
            ids, out = _search(mode)
            assert not out.get("error"), (mode, out.get("error"))
            assert rev not in ids, f"mode={mode} leaked the REVIEW row: {ids}"
        print("PASS: test_review_rows_hidden_in_every_search_mode")
    finally:
        cleanup_temp_db(db, orig)


def test_include_review_works_in_every_search_mode():
    db, orig = init_temp_db()
    try:
        from memall.graph.embeddings import build_index
        ok, rev = _seed_review_and_ok()
        build_index(force=True)
        # vector has no include_review plumbing (KNN path), so it is excluded
        # from the opt-in assertion; it must still HIDE by default (covered above).
        for mode in ("fts5", "direct", "hybrid", "auto"):
            ids, out = _search(mode, include_review=True)
            assert not out.get("error"), (mode, out.get("error"))
            assert rev in ids, f"mode={mode} lost include_review: {ids}"
            assert ok in ids, f"mode={mode} lost the normal row: {ids}"
        print("PASS: test_include_review_works_in_every_search_mode")
    finally:
        cleanup_temp_db(db, orig)


def test_enhanced_path_honours_include_review():
    """With utility tracking on, fts5 routes through enhanced_search — the
    explicit kwargs there must forward include_review."""
    db, orig = init_temp_db()
    try:
        import memall.config as cfg
        from memall.graph.embeddings import build_index
        cfg.get_config()
        cfg._config.setdefault("quality", {}).setdefault("utility", {})[
            "tracking_enabled"] = True
        cfg._last_mtime_check = float("inf")
        ok, rev = _seed_review_and_ok()
        build_index(force=True)

        hidden, _ = _search("fts5")
        assert rev not in hidden, hidden
        shown, _ = _search("fts5", include_review=True)
        assert rev in shown, f"enhanced path dropped include_review: {shown}"
        print("PASS: test_enhanced_path_honours_include_review")
    finally:
        from memall.config import reset_config
        reset_config()
        cleanup_temp_db(db, orig)
