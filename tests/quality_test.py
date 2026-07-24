"""
MemALL 记忆质量评估 — 端到端质量测试

测试维度: 写入质量 | 检索质量 | 去重质量 | 更新质量 | 图谱质量
         实体提取 | 上下文组装 | Smart Store | 记忆状态
"""

import sys, os, json, tempfile, shutil
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

PASS = 0; FAIL = 0; SCORES = []

def ok(name, score=1.0):
    global PASS; PASS += 1; SCORES.append(score)
    print(f"  [+] {name:50s} {score:.0%}")

def ng(name, detail=""):
    global FAIL; FAIL += 1
    print(f"  [-] {name:50s} 0% {detail}")

# Setup
from memall.core import db as core_db
tmp_dir = Path(tempfile.mkdtemp())
db_path = tmp_dir / "quality_test.db"
core_db.DB_PATH = db_path
core_db._global_pool = None
core_db._auto_init_done = False
from memall.core.db import init_db, pool_conn
init_db(migrate=True)

print("=" * 60)
print("MemALL Memory Quality Assessment")
print("=" * 60)


# 1. Capture
print("\n--- 1. Capture Quality ---")
from memall.core.thin_waist import capture, retrieve, smart_store, update, connect, traverse

try:
    m1 = capture(
        "After analysis we chose FastAPI over Django for the backend. "
        "FastAPI has better async support, higher performance, and growing community. "
        "This is the final technology decision.",
        agent_name="qa_test", level="L4", category="decision"
    )
    ok(f"capture L4 decision id={m1}", 1.0)
except Exception as e:
    ng("capture L4", str(e))

try:
    m2 = capture(
        "经过分析和反思，这次重构的教训是：直接修改生产数据库很危险，因为可能导致数据不一致。"
        "根因是缺乏测试流程。下次应该在测试环境验证后再操作数据库。"
        "这是我们的改进方案和总结。",
        agent_name="qa_test", level="L6", category="reflection"
    )
    ok(f"capture L6 reflection id={m2}", 1.0)
except Exception as e:
    ng("capture L6", str(e)[:80])

try:
    m3 = capture("I prefer Python and FastAPI for backend development.", agent_name="qa_test", level="P2")
    ok(f"capture P2 id={m3}", 1.0)
except Exception as e:
    ng("capture P2", str(e))


# 2. Retrieve
print("\n--- 2. Retrieve Quality ---")
try:
    r = retrieve(agent_name="qa_test")
    count = len(r) if isinstance(r, list) else 0
    ok(f"retrieve by agent: {count} results", min(1.0, count / 3))
except Exception as e:
    ng("retrieve by agent", str(e))

try:
    r = retrieve(level="L4")
    count = len(r) if isinstance(r, list) else 0
    ok(f"retrieve by level L4: {count} results", 1.0 if count >= 1 else 0)
except Exception as e:
    ng("retrieve by level", str(e))

try:
    r = retrieve("FastAPI")
    count = len(r) if isinstance(r, list) else 0
    ok(f"retrieve by content FastAPI: {count} results", min(1.0, count / 2))
except Exception as e:
    ng("retrieve by content", str(e))


# 3. Dedup
print("\n--- 3. Dedup Quality ---")
try:
    m1_dup = capture(
        "After analysis we chose FastAPI over Django for the backend. "
        "FastAPI has better async support, higher performance, and growing community. "
        "This is the final technology decision.",
        agent_name="qa_test", level="L4", category="decision"
    )
    ok(f"exact dedup: {m1_dup == m1}", 1.0 if m1_dup == m1 else 0.3)
except Exception as e:
    ng("dedup", str(e))


# 4. Update
print("\n--- 4. Update Quality ---")
try:
    result = update(m1, category="architecture")
    assert result is True
    ok("update category to architecture", 1.0)
except Exception as e:
    ng("update", str(e))


# 5. Graph (connect + traverse)
print("\n--- 5. Graph Quality ---")
try:
    eid = connect(m1, m2, relation_type="refines")
    ok(f"connect edge id={eid}", 1.0 if eid > 0 else 0)
except Exception as e:
    ng("connect", str(e))

try:
    t = traverse(m1, depth=2)
    ok(f"traverse: {len(t.get('nodes',[]))} nodes, {len(t.get('edges',[]))} edges",
       min(1.0, (len(t.get('nodes',[])) + len(t.get('edges',[]))) / 4))
except Exception as e:
    ng("traverse", str(e))


# 6. Entity Extraction
print("\n--- 6. Entity Extraction Quality ---")
from memall.core.entity_extractor import extract_entities, extract_triples
try:
    ents = extract_entities("Python and FastAPI for backend development with PostgreSQL.")
    names = [e["name"] for e in ents]
    score = ("Python" in names) + ("FastAPI" in names) + ("PostgreSQL" in names)
    ok(f"entities: {names}", score / 3)
except Exception as e:
    ng("entities", str(e))

try:
    trips = extract_triples("Python is a programming language. FastAPI is built on top of Starlette.")
    ok(f"triples: {len(trips)}", min(1.0, len(trips) / 2))
except Exception as e:
    ng("triples", str(e))


# 7. Build Context
print("\n--- 7. Context Assembly Quality ---")
from memall.core.context_assembler import build_context
try:
    ctx = build_context("qa_test", query="FastAPI Python", max_tokens=2000)
    ok(f"build_context: {ctx['tokens']} tokens, sources={ctx['sources']}",
       1.0 if ctx.get("tokens", 0) > 0 else 0)
except Exception as e:
    ng("build_context", str(e))


# 8. Smart Store
print("\n--- 8. Smart Store Quality ---")
try:
    result = smart_store("Smart store test memory for quality assessment.", agent_name="qa_test")
    ok(f"smart_store: {json.dumps(result, ensure_ascii=False)[:80]}", 1.0)
except Exception as e:
    ng("smart_store", str(e))


# 9. Memory Status
print("\n--- 9. Memory Status ---")
with pool_conn() as conn:
    total = conn.execute("SELECT COUNT(*) FROM memories WHERE agent_name='qa_test'").fetchone()[0]
    ok(f"total memories: {total}", 1.0 if total >= 3 else 0)


# Summary
print(f"\n{'='*60}")
print(f"Quality Assessment: {PASS} passed, {FAIL} failed")
if SCORES:
    avg = sum(SCORES) / len(SCORES)
    print(f"Overall Quality Score: {avg:.0%}")
    if avg >= 0.9: print("Rating: 🟢 Excellent")
    elif avg >= 0.7: print("Rating: 🟡 Good")
    elif avg >= 0.5: print("Rating: 🟠 Fair")
    else: print("Rating: 🔴 Needs Improvement")

shutil.rmtree(tmp_dir, ignore_errors=True)
if FAIL: sys.exit(1)