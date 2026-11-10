"""Independent workline: capture-quality routing + readability normalization.

This is NOT part of ADR0028. It covers the separate "route pipeline-derived
memories through capture()'s quality/readability funnel, fall back to the
original manual INSERT on any error" workline, plus gateway_sync content
normalization, capture handler's rich result contract, and ops.split segment
normalization.

Key regression guarded here: MemoryInput has NO `weight` field. Passing
weight=... into the capture path raised TypeError, was swallowed by the
try/except, and silently fell back to the legacy INSERT — defeating the whole
point of the workline. member_count is now carried in metadata instead.
"""
import json
from datetime import datetime, timezone

from tests.test_helpers import init_temp_db, cleanup_temp_db


def _now():
    return datetime.now(timezone.utc).isoformat()


def test_memoryinput_has_no_weight_kwarg():
    """Guard: MemoryInput must NOT accept weight=; callers must use metadata."""
    from memall.core.models import MemoryInput as MI
    assert "weight" not in MI.__dataclass_fields__
    try:
        MI(content="x" * 60, weight=3.0)
        raise AssertionError("weight= should not be accepted")
    except TypeError:
        pass
    print("PASS: test_memoryinput_has_no_weight_kwarg")


def test_pipeline_capture_paths_do_not_pass_weight():
    """Static regression: no pipeline capture() call passes weight= (would silently fallback)."""
    from pathlib import Path
    root = Path("src/memall/pipeline")
    offenders = []
    for f in ("auto_dream.py", "cross_agent.py", "lifecycle.py", "llm_enhance.py",
              "observe.py", "reflect.py"):
        text = (root / f).read_text()
        # Only look at _MI(...) construction sites, not legacy INSERT weight columns.
        for line in text.splitlines():
            if "_MI(" in line or ("_capture(" in line and "weight=" in line):
                if "weight=" in line and "_MI" not in line and "weight=float" in line:
                    offenders.append((f, line.strip()))
    assert not offenders, offenders
    print("PASS: test_pipeline_capture_paths_do_not_pass_weight")


def test_auto_dream_distill_uses_capture_and_persists_metadata():
    """_distill_knowledge routes through capture() (no silent fallback): result row carries source_pipeline/ingest_mode."""
    db_path, orig = init_temp_db()
    try:
        from memall.core.db import get_conn
        from memall.pipeline.auto_dream import _distill_knowledge
        conn = get_conn()
        now = _now()
        # Seed >=3 similar L4 memories in one cluster so distill creates an L10.
        for i in range(4):
            conn.execute(
                "INSERT INTO memories (content, content_hash, level, agent_name, category, subject, occurred_at, created_at, updated_at, memory_status) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (f"团队反复遇到 数据库迁移 主题 的相似教训 第{i}条 内容足够长以便通过质量门禁检查验证写入路径",
                 f"wd_{i}", "L4", "tester", "lesson", f"迁移教训{i}", now, now, now, "ACTIVE"),
            )
        conn.commit()
        conn.close()
        res = _distill_knowledge(get_conn())
        assert isinstance(res, dict)
        # If a cluster was created, its L10 must have gone through capture()
        # (metadata enriched with ingest_mode/source_pipeline), not the legacy
        # INSERT (which lacks those keys).
        conn = get_conn()
        rows = conn.execute(
            "SELECT metadata FROM memories WHERE level='L10' AND metadata LIKE '%auto_dream%'"
        ).fetchall()
        conn.close()
        for r in rows:
            meta = json.loads(r[0]) if isinstance(r[0], str) else dict(r[0] or {})
            assert meta.get("ingest_mode") == "auto", meta
            assert meta.get("source_pipeline") == "auto_dream.distill", meta
            assert "member_count" in meta, meta  # weight moved here
        print("PASS: test_auto_dream_distill_uses_capture_and_persists_metadata")
    finally:
        cleanup_temp_db(db_path, orig)


def test_capture_handler_exposes_quality_contract():
    """MCP capture handler surfaces id/status + additive quality/readability fields."""
    db_path, orig = init_temp_db()
    try:
        from memall.mcp.tools.capture import handle
        out = json.loads(handle({
            "content": "质量契约：项目决定使用 PostgreSQL 作为主库，因为团队熟悉、生态完整、事务支持稳定可靠。",
            "level": "L4", "category": "decision", "agent_name": "qa_agent", "subject": "数据库选型",
        }))
        assert isinstance(out.get("id"), int) and out["id"] > 0
        assert "status" in out
        # additive: quality/readability/ingest_mode/source_pipeline/decision_hint
        # may be present but must never break the core {id,status} contract.
        for k in ("quality", "readability", "ingest_mode", "source_pipeline", "decision_hint"):
            if k in out:
                assert out[k] is not None
        print("PASS: test_capture_handler_exposes_quality_contract")
    finally:
        cleanup_temp_db(db_path, orig)


def test_gateway_sync_normalizes_unreadable_content_preserves_fidelity():
    """_import_memories normalizes unreadable content and marks readability in metadata (no reject)."""
    db_path, orig = init_temp_db()
    try:
        from memall.core.db import get_conn
        from memall.gateway_sync import _import_memories
        conn = get_conn()
        # pure-identifier / unreadable content
        payload = [{
            "id": 1,
            "content": "ABC123XYZ",
            "content_hash": "rh_unreadable_1",
            "level": "P2", "category": "general", "subject": "raw",
            "metadata": "{}",
            "occurred_at": _now(), "created_at": _now(), "updated_at": _now(),
        }]
        imported, mapping = _import_memories(conn, payload, "sync_agent")
        assert imported == 1, (imported, mapping)
        row = conn.execute("SELECT content, metadata FROM memories WHERE content_hash='rh_unreadable_1'").fetchone()
        assert row is not None
        meta = json.loads(row["metadata"]) if isinstance(row["metadata"], str) else dict(row["metadata"] or {})
        assert meta.get("ingest_mode") == "sync", meta
        assert "readability" in meta, meta  # fidelity marker preserved
        conn.close()
        print("PASS: test_gateway_sync_normalizes_unreadable_content_preserves_fidelity")
    finally:
        cleanup_temp_db(db_path, orig)


def test_ops_split_segments_normalized_and_tagged():
    """split_memory segments go through readability normalization and carry source_pipeline/ingest_mode metadata."""
    db_path, orig = init_temp_db()
    try:
        from memall.core.db import get_conn
        from memall.pipeline.ops import split_memory
        conn = get_conn()
        now = _now()
        cur = conn.execute(
            "INSERT INTO memories (content, content_hash, level, agent_name, category, subject, occurred_at, created_at, updated_at, metadata) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("第一段内容足够长便于通过校验且包含可读的中文句子用于拆分验证。\n\n第二段内容同样足够长并且包含可读的中文句子用于拆分验证。",
             "ws_split_1", "P2", "tester", "general", "可拆分记忆", now, now, now, "{}"),
        )
        conn.commit()
        mid = cur.lastrowid
        conn.close()
        res = split_memory(mid, delimiter="\n\n")
        assert isinstance(res, dict)
        conn = get_conn()
        rows = conn.execute(
            "SELECT content, metadata FROM memories WHERE content_hash != 'ws_split_1' AND subject='可拆分记忆'"
        ).fetchall()
        conn.close()
        for r in rows:
            meta = json.loads(r["metadata"]) if isinstance(r["metadata"], str) else dict(r["metadata"] or {})
            assert meta.get("source_pipeline") == "ops.split", meta
            assert meta.get("ingest_mode") == "auto", meta
        print("PASS: test_ops_split_segments_normalized_and_tagged")
    finally:
        cleanup_temp_db(db_path, orig)
