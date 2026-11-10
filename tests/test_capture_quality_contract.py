import json
from tests.test_helpers import init_temp_db, cleanup_temp_db


def _capture_ok():
    from memall.core.models import MemoryInput
    from memall.core.thin_waist import capture
    return capture(MemoryInput(
        content="质量契约测试：项目决定使用 PostgreSQL 作为主库，因为团队熟悉、生态完整、事务支持稳定。",
        level="L4",
        category="decision",
        owner="human",
        agent_name="qa_agent",
        subject="数据库选型",
        metadata=json.dumps({"ingest_mode": "auto", "source_pipeline": "pytest"}),
    ))


def test_capture_returns_int_compatible_contract():
    db_path, orig = init_temp_db()
    try:
        r = _capture_ok()
        assert isinstance(r, int)
        assert int(r) > 0
        assert r == int(r)
        d = r.to_dict()
        assert d["id"] == int(r)
        assert d["status"] in ("ok", "new", "dedup", "dedup_hash", "dedup_semantic", "dedup_accumulate")
        assert d["quality"]["gate"] in ("accepted", "review")
        assert isinstance(d["quality"]["score"], (int, float))
        assert isinstance(d["quality"]["reasons"], list)
        assert isinstance(d["quality"]["dimensions"], dict)
        assert d["ingest_mode"] == "auto"
        assert d["source_pipeline"] == "pytest"
        print("  PASS test_capture_returns_int_compatible_contract")
    finally:
        cleanup_temp_db(db_path, orig)


def test_capture_persists_quality_contract_metadata():
    db_path, orig = init_temp_db()
    try:
        r = _capture_ok()
        from memall.core.db import get_conn
        conn = get_conn()
        row = conn.execute("SELECT metadata FROM memories WHERE id=?", (int(r),)).fetchone()
        meta = json.loads(row["metadata"] or "{}")
        q = meta.get("quality") or {}
        assert q.get("gate") in ("accepted", "review")
        assert q.get("score") is not None
        assert isinstance(q.get("reasons"), list)
        assert isinstance(q.get("dimensions"), dict)
        assert meta.get("ingest_mode") == "auto"
        assert meta.get("source_pipeline") == "pytest"
        assert isinstance(meta.get("readability"), dict)
        assert "ok" in meta["readability"] or "normalized" in meta["readability"]
        print("  PASS test_capture_persists_quality_contract_metadata")
    finally:
        cleanup_temp_db(db_path, orig)


def test_capture_dedup_still_int_compatible():
    db_path, orig = init_temp_db()
    try:
        r1 = _capture_ok()
        r2 = _capture_ok()
        assert isinstance(r2, int)
        assert int(r1) == int(r2)
        print("  PASS test_capture_dedup_still_int_compatible")
    finally:
        cleanup_temp_db(db_path, orig)
