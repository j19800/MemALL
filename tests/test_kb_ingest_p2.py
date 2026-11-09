"""P2: KB ingestion (document.import) — provenance, dedup/supersede, and user-state separation."""

import json

from tests.test_helpers import init_temp_db, cleanup_temp_db


def _meta(r):
    m = getattr(r, "metadata", "{}")
    if isinstance(m, dict):
        return m
    try:
        return json.loads(m or "{}")
    except Exception:
        return {}


def test_kb_ingest_dedup_and_supersede():
    db_path = init_temp_db()
    try:
        from memall.mcp.tools.memory_write import handle_kb_ingest
        from memall.core.thin_waist import retrieve

        content_v1 = ("MemALL KB provenance document section alpha. " * 30).strip() + "\n" + (
            "Section beta carries deterministic chunking details. " * 20
        )
        a = json.loads(
            handle_kb_ingest(
                {
                    "content": content_v1,
                    "doc_id": "kb-doc-1",
                    "title": "KB Doc 1",
                    "path": "/kb/doc1.md",
                    "project": "memall",
                    "chunk_size": 280,
                    "chunk_overlap": 30,
                    "doc_version": "v1",
                }
            )
        )
        assert "error" not in a, a
        assert a["created_or_updated"] >= 1
        assert a["deduped"] == 0

        b = json.loads(
            handle_kb_ingest(
                {
                    "content": content_v1,
                    "doc_id": "kb-doc-1",
                    "title": "KB Doc 1",
                    "path": "/kb/doc1.md",
                    "project": "memall",
                    "chunk_size": 280,
                    "chunk_overlap": 30,
                    "doc_version": "v1",
                }
            )
        )
        assert b["deduped"] >= 1
        assert b["superseded"] == 0

        content_v2 = content_v1 + "\n" + ("Version two introduces a new verified fact. " * 15)
        c = json.loads(
            handle_kb_ingest(
                {
                    "content": content_v2,
                    "doc_id": "kb-doc-1",
                    "title": "KB Doc 1",
                    "path": "/kb/doc1.md",
                    "project": "memall",
                    "chunk_size": 280,
                    "chunk_overlap": 30,
                    "doc_version": "v2",
                }
            )
        )
        assert c["superseded"] >= 1
        assert c["created_or_updated"] >= 1

        rows = retrieve(query="provenance document section", source_pipeline="document.import", limit=30)
        rows = rows if isinstance(rows, list) else ([] if rows is None else [rows])
        assert rows, "expected KB rows retrievable by source_pipeline"
        metas = [_meta(r) for r in rows]
        assert any(m.get("doc_id") == "kb-doc-1" for m in metas)
        assert any(m.get("source_pipeline") == "document.import" for m in metas)
        assert any(m.get("chunk_hash") for m in metas)
        assert all(m.get("kb_user_state") is False for m in metas if m.get("doc_id") == "kb-doc-1")
    finally:
        cleanup_temp_db(db_path)


def test_kb_excluded_from_continuity_pack():
    db_path = init_temp_db()
    try:
        from memall.mcp.tools.memory_write import handle_kb_ingest
        from memall.core.thin_waist import capture
        from memall.core.models import MemoryInput
        from memall.pipeline.session import build_continuity_pack

        # User-state memory
        capture(
            MemoryInput(
                content="User prefers concise Chinese replies and uses MemALL for project memory continuity.",
                agent_name="tester",
                project="memall",
                category="preference",
                level="P1",
                metadata='{"source_pipeline":"conversation.manual"}',
            )
        )
        # KB evidence (must not become continuity/user state)
        handle_kb_ingest(
            {
                "content": "KB evidence: MemALL stores document chunks with provenance and content hashes. " * 20,
                "doc_id": "kb-doc-state",
                "title": "KB State",
                "project": "memall",
                "chunk_size": 240,
            }
        )
        pack = build_continuity_pack(agent_name="tester", viewer="tester", project="memall", limit=20)
        blob = json.dumps(pack, ensure_ascii=False)
        assert "kb-doc-state" not in blob
        for item in (pack.get("active_state") or pack.get("active_memories") or []):
            assert (item.get("category") or "") != "knowledge"
            assert (item.get("source_pipeline") or "") not in ("document.import","kb_ingest")
        assert pack.get("kb_excluded") is True
        assert "prefers concise Chinese replies" in blob or pack.get("active_memories")
    finally:
        cleanup_temp_db(db_path)
