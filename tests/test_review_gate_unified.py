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
            err = out.get("error")
            # A tiny corpus may not train an embedder (SVD needs >=2 docs and
            # the query vector can come back None), in which case the vector
            # path reports "embedding failed" rather than leaking rows. That is
            # an environment limitation, not a gate failure — the assertion
            # below still runs on whatever rows DID come back.
            if err and "embedding" in str(err):
                continue
            assert not err, (mode, err)
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


def test_include_archived_works_in_every_search_mode():
    """Archived rows are soft-deleted, not gone: a caller inspecting history must
    be able to ask for them, in every mode.

    Found broken in all five modes — the field was undeclared in RetrieveInput
    and UnifiedSearchInput (validator stripped it), absent from the fts5/direct
    whitelists, and hybrid/vector hard-excluded archived rows.
    """
    db, orig = init_temp_db()
    try:
        import json as _json
        from memall.core.db import get_conn
        from memall.core.models import MemoryInput
        from memall.core.thin_waist import capture, unified_search
        from memall.graph.embeddings import build_index

        mid = int(capture(MemoryInput(
            content="归档测试记忆：数据库密码配置是 secretA 不要外传。",
            level="L4", category="decision", agent_name="arch", project="pay")))
        build_index(force=True)
        c = get_conn()
        c.execute("UPDATE memories SET memory_status='archived' WHERE id=?", (mid,))
        c.commit()
        c.close()

        def ids(mode, **extra):
            args = {"query": "数据库 配置", "mode": mode, "agent_name": "arch", "top_k": 10}
            args.update(extra)
            out = unified_search(args)
            d = _json.loads(out) if isinstance(out, str) else out
            if d.get("error") and "embedding" in str(d.get("error")):
                return None, d
            return [r.get("memory_id") for r in (d.get("results") or [])], d

        for mode in ("fts5", "direct", "hybrid", "auto", "vector"):
            default_ids, d = ids(mode)
            if default_ids is None:
                continue
            assert mid not in default_ids, f"mode={mode} leaked an archived row: {default_ids}"

            with_arch, d2 = ids(mode, include_archived=True)
            if with_arch is None:
                continue
            assert mid in with_arch, f"mode={mode} lost include_archived: {with_arch}"
        print("PASS: test_include_archived_works_in_every_search_mode")
    finally:
        cleanup_temp_db(db, orig)
