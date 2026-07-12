__test__ = False  # not for pytest collection (uses sys.exit)

"""
全方位冒烟测试 — MemALL core modules end-to-end.

Covers: import -> DB init -> capture -> retrieve -> connect -> traverse ->
update -> build_context -> strategies -> entity extraction -> pipeline steps ->
gateway HTML -> MCP tools -> rate limiter -> config -> backup.
"""

import sys, json, os, tempfile, logging, shutil
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

PASS = 0
FAIL = 0

def ok(msg):
    global PASS; PASS += 1
    print(f"  [+] {msg}")

def ng(msg, detail=""):
    global FAIL; FAIL += 1
    detail_str = str(detail)[:200] if detail else ""
    print(f"  [-] {msg}: {detail_str}")

# Setup
from memall.core import db as core_db
tmp_dir = Path(tempfile.mkdtemp())
db_path = tmp_dir / "memall_test.db"
core_db.DB_PATH = db_path
core_db._global_pool = None
core_db._auto_init_done = False

# 1. init_db
print("\n--- 1. init_db ---")
try:
    from memall.core.db import init_db, get_conn, pool_conn
    init_db(migrate=True)
    ok("init_db")
except Exception as e:
    ng("init_db", e)

# 2. capture
print("\n--- 2. capture ---")
from memall.core.thin_waist import capture, retrieve, connect, traverse, update
try:
    m1 = capture("This is a test memory about Python FastAPI framework selection. After analysis we chose FastAPI for its async support.", agent_name="e2e_bot", level="L4")
    ok(f"capture L4 id={m1}")
except Exception as e:
    ng("capture L4", e)
try:
    m2 = capture("经过分析和反思，教训是：直接修改生产数据库很危险，因为可能导致数据不一致。下次应该在测试环境验证后再操作。这是我们的根因分析结论。", agent_name="e2e_bot", level="L6")
    ok(f"capture L6 id={m2}")
except Exception as e:
    ng("capture L6", e)

# 3. retrieve
print("\n--- 3. retrieve ---")
try:
    r = retrieve(m1)
    assert r is not None
    ok("by ID")
except Exception as e:
    ng("by ID", e)
try:
    rs = retrieve(agent_name="e2e_bot")
    assert len(rs) >= 2
    ok(f"by agent ({len(rs)})")
except Exception as e:
    ng("by agent", e)

# 4. connect + traverse
print("\n--- 4. graph ---")
try:
    eid = connect(m1, m2, relation_type="refines")
    ok(f"connect id={eid}")
except Exception as e:
    ng("connect", e)
try:
    t = traverse(m1, depth=2)
    assert len(t.get("nodes", [])) >= 1
    ok(f"traverse ({len(t['nodes'])} nodes)")
except Exception as e:
    ng("traverse", e)

# 5. update
print("\n--- 5. update ---")
try:
    assert update(m1, category="architecture") is True
    ok("update")
except Exception as e:
    ng("update", e)

# 6. build_context
print("\n--- 6. build_context ---")
try:
    from memall.core.context_assembler import build_context
    ctx = build_context("e2e_bot", query="Python FastAPI", max_tokens=2000)
    assert ctx.get("tokens", 0) > 0
    ok(f"{ctx['tokens']} tokens")
except Exception as e:
    ng("build_context", e)

# 7. strategies
print("\n--- 7. strategies ---")
try:
    from memall.strategy import get_strategy, BufferStrategy
    s = get_strategy("e2e_bot", "buffer")
    assert isinstance(s, BufferStrategy)
    ok("BufferStrategy")
except Exception as e:
    ng("BufferStrategy", e)
try:
    from memall.strategy import EntityStrategy
    from memall.core.models import MemoryInput
    es = EntityStrategy("e2e_bot")
    eid = es.store(MemoryInput(content="Python is a programming language used for data science and web development. FastAPI is a web framework for building APIs.", agent_name="e2e_bot"))
    with pool_conn() as c:
        ec = c.execute("SELECT COUNT(*) FROM memory_entities WHERE memory_id=?", (eid,)).fetchone()[0]
    ok(f"EntityStrategy ({ec} entities)")
except Exception as e:
    ng("EntityStrategy", e)

# 8. MemorySharing
print("\n--- 8. sharing ---")
try:
    from memall.strategy.sharing import MemorySharing
    ms = MemorySharing("e2e_bot")
    ms.share(m1, "other", trust_level="family")
    r = ms.query_shared("other", trust_min="family")
    ok(f"share ({len(r)} results)")
except Exception as e:
    ng("sharing", e)

# 9. entity extraction
print("\n--- 9. entity_extractor ---")
try:
    from memall.core.entity_extractor import extract_entities, extract_triples
    e = extract_entities("Python and FastAPI for backend.")
    t = extract_triples("Python is a language.")
    ok(f"entities={len(e)} triples={len(t)}")
except Exception as e:
    ng("entity_extractor", e)

# 10. entity pipeline
print("\n--- 10. entity_pipeline ---")
try:
    from memall.pipeline.entity_pipeline import entity_extraction_step
    r = entity_extraction_step()
    ok(f"scanned={r.get('scanned',0)}")
except Exception as e:
    ng("entity_pipeline", e)

# 11. pipeline steps
print("\n--- 11. pipeline steps ---")
for name, mod, fn in [
    ("classify", "memall.pipeline.classify", "classify_step"),
    ("decay", "memall.pipeline.decay", "decay_step"),
    ("time_slice", "memall.pipeline.time_slice", "time_slice_step"),
    ("epoch", "memall.pipeline.epoch", "epoch_step"),
]:
    try:
        import importlib
        step = getattr(importlib.import_module(mod), fn)
        r = step()
        ok(f"{name}")
    except Exception as e:
        ng(f"{name}", e)

# 12. forget
print("\n--- 12. forget ---")
try:
    from memall.pipeline.forget import forget_stats
    s = forget_stats()
    ok(f"total={s.get('total_memories', '?')}")
except Exception as e:
    ng("forget_stats", e)

# 13. gateway_html
print("\n--- 13. gateway_html ---")
try:
    from memall.gateway_html import handle_recent, handle_artifact
    with pool_conn() as c:
        h = handle_recent(c)
    ok(f"handle_recent ({len(h)}b)")
except Exception as e:
    ng("handle_recent", e)
try:
    h = handle_artifact()
    ok(f"handle_artifact ({len(h)}b)")
except Exception as e:
    ng("handle_artifact", e)

# 14. MCP tools
print("\n--- 14. MCP tools ---")
try:
    from memall.mcp.tools.__init__ import _handle_write
    r = json.loads(_handle_write({"action": "capture", "content": "MCP smoke test memory with enough content for quality validation. Testing the MCP tool handler integration.", "agent_name": "mcp_smoke"}))
    assert r.get("id")
    ok(f"_handle_write id={r['id']}")
except Exception as e:
    ng("_handle_write", e)

# 15. rate limiter + config
print("\n--- 15. rate_limiter + config ---")
try:
    from memall.core.rate_limiter import get_rate_limiter
    lim = get_rate_limiter()
    assert isinstance(lim.allow("smoke", limit=100), bool)
    ok("rate_limiter")
except Exception as e:
    ng("rate_limiter", e)
try:
    from memall.config import get_config
    assert get_config("gateway.port") == 9919
    ok("config")
except Exception as e:
    ng("config", e)

# 16. backup
print("\n--- 16. backup ---")
try:
    from memall.pipeline.backup import backup_step
    r = backup_step()
    ok(f"status={r.get('status')}")
except Exception as e:
    ng("backup", e)

# 17. NLP
print("\n--- 17. NLP ---")
try:
    from memall.core.nlp import tokenize, cosine_sim
    tokenize("test tokenization")
    cosine_sim({"a": 1, "b": 2}, {"b": 1, "c": 2})
    ok("tokenize + cosine_sim")
except Exception as e:
    ng("NLP", e)

# Summary
print(f"\n{'='*50}")
print(f"Smoke Test: {PASS} passed, {FAIL} failed")
if FAIL:
    print(f"*** {FAIL} failure(s) ***")
else:
    print("ALL PASSED!")

shutil.rmtree(tmp_dir, ignore_errors=True)
if FAIL:
    sys.exit(1)