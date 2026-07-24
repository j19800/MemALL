"""
MemALL 性能基准测试
使用 time.perf_counter() 获取精确测量，1000 次迭代取中位数。
"""

import sys, os, time, statistics, tempfile, shutil
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

RESULTS = {}

def bench(name, fn, iterations=1000):
    """Run fn N times, report median latency in ms."""
    # Warmup
    for _ in range(10):
        fn()
    # Measure
    samples = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000)
    median = statistics.median(samples)
    p99 = sorted(samples)[int(len(samples) * 0.99)]
    RESULTS[name] = (median, p99, iterations)
    print(f"  [+] {name:30s} median={median:.2f}ms  p99={p99:.2f}ms  n={iterations}")

# Setup temp DB
from memall.core import db as core_db
tmp_dir = Path(tempfile.mkdtemp())
db_path = tmp_dir / "perf_test.db"
core_db.DB_PATH = db_path
core_db._global_pool = None
core_db._auto_init_done = False

from memall.core.db import init_db, pool_conn
init_db(migrate=True)

# Seed data using direct SQL (bypass quality gate)
from memall.core.db import get_conn, content_hash as ch
from datetime import datetime, timezone
now = datetime.now(timezone.utc).isoformat()

print("=== Seeding 1000 memories ===")
conn = get_conn()
for i in range(1000):
    h = ch(f"seed_{i}")
    conn.execute(
        "INSERT OR IGNORE INTO memories (content, content_hash, level, agent_name, category, occurred_at, created_at, updated_at) "
        "VALUES (?, ?, 'P2', 'perf_test', 'benchmark', ?, ?, ?)",
        (f"Seed memory number {i} for performance benchmark testing purposes. This is a test memory with enough content for benchmark.", h, now, now, now),
    )
conn.commit()
conn.close()

# Import modules
from memall.core.thin_waist import capture, retrieve, connect, traverse
from memall.core.context_assembler import build_context
from memall.core.entity_extractor import extract_entities, extract_triples
from memall.core.nlp import tokenize, cosine_sim

print("\n=== Benchmark ===")

# capture
def _capture():
    capture("Benchmark capture test memory with sufficient content for quality gate validation and performance testing.", agent_name="perf_test")
bench("capture", _capture, 200)

# retrieve by agent
def _retrieve_agent():
    retrieve(agent_name="perf_test")
bench("retrieve (by agent)", _retrieve_agent, 500)

# retrieve by ID
def _retrieve_id():
    retrieve(1)
bench("retrieve (by ID)", _retrieve_id, 500)

# retrieve by level
def _retrieve_level():
    retrieve(level="P2")
bench("retrieve (by level)", _retrieve_level, 200)

# build_context
def _build_ctx():
    build_context("perf_test", query="performance benchmark", max_tokens=2000)
bench("build_context", _build_ctx, 200)

# connect
conn = get_conn()
conn.execute("INSERT OR IGNORE INTO memories (id, content, content_hash, level, agent_name, occurred_at, created_at, updated_at) "
             "VALUES (-1, 'connect target', 'conn_target', 'P2', 'perf_test', ?, ?, ?)", (now, now, now))
conn.close()

def _connect():
    connect(1, 2, relation_type="refines")
bench("connect", _connect, 200)

# traverse
def _traverse():
    traverse(1, depth=2)
bench("traverse", _traverse, 200)

# entity extraction
def _extract():
    extract_entities("Python and FastAPI for backend development with PostgreSQL database.")
bench("extract_entities", _extract, 500)

# tokenize
def _tokenize():
    tokenize("performance benchmark test for CJK tokenization and English word processing")
bench("tokenize", _tokenize, 1000)

# cosine_sim
def _cos():
    cosine_sim({"a": 1, "b": 2}, {"b": 1, "c": 2})
bench("cosine_sim", _cos, 1000)

# Summary
print(f"\n{'='*60}")
print(f"Benchmark Results (median latency, lower is better)")
print(f"{'='*60}")
for name, (median, p99, n) in sorted(RESULTS.items(), key=lambda x: x[1][0]):
    bar = "█" * max(1, int(median))
    print(f"  {name:30s} {median:6.2f}ms  {bar}")
print(f"\n  P99: ", end="")
for name, (median, p99, n) in sorted(RESULTS.items(), key=lambda x: x[1][0]):
    print(f"{name.split()[0]}:{p99:.1f}ms ", end="")
print()

shutil.rmtree(tmp_dir, ignore_errors=True)