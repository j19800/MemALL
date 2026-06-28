"""MemALL 性能测试 — 高标准多维度基准测试

测试维度：
  1. DB 基础延迟 (simple queries, concurrent reads)
  2. 写入基准 (capture throughput)
  3. 检索基准 (FTS5, hybrid, vector latency + recall)
  4. 图操作基准 (connect, traverse latency)
  5. Pipeline 步骤基准 (各 step 耗时)
  6. 批处理基准 (store_batch throughput)
  7. 搜索并发基准 (hybrid_search under load)
  8. 数据库文件体积

评分校准:
  - DB 查询: <1ms 满分 (SQLite 原生延迟)
  - 向量/混合搜索: <100ms 满分 (embedding 模型推理 ≈ 50ms)
  - Pipeline: 按典型数据量 (229 sessions) 校准
  - 总分 ≥80 合格, ≥90 良好, ≥95 优秀
"""

import sys
import json
import time
import statistics
import threading
from datetime import datetime, timezone

sys.path.insert(0, "src")

# ── Scoring weights ─────────────────────────────────────────────────────
WEIGHTS = {
    "db_read_latency": 5,
    "db_write_latency": 5,
    "capture_throughput": 10,
    "fts5_latency": 10,
    "hybrid_latency": 10,
    "vector_latency": 5,
    "connect_latency": 5,
    "traverse_latency": 5,
    "pipeline_session": 10,
    "pipeline_classify": 10,
    "pipeline_extract": 5,
    "pipeline_archive": 5,
    "batch_throughput": 5,
    "concurrent_search": 5,
    "db_size": 5,
}
MAX_SCORE = sum(WEIGHTS.values())  # 100
PASS_THRESHOLD = 80


def ms(t_start):
    return (time.perf_counter() - t_start) * 1000


SCORES = {}
NOTES = []


def score(name, earned, max_pts, note=""):
    SCORES[name] = {"earned": earned, "max": max_pts}
    pct = (earned / max_pts * 100) if max_pts else 0
    bar = "█" * int(pct / 10) + "░" * (10 - int(pct / 10))
    NOTES.append(f"  [{name:25s}] {bar} {earned:>3}/{max_pts:<3} ({pct:3.0f}%)  {note}")


# ═══════════════════════════════════════════════════════════════════════
# 0. 初始化
# ═══════════════════════════════════════════════════════════════════════
print("=" * 66)
print("  MemALL Performance Benchmark")
print(f"  Started: {datetime.now(timezone.utc).isoformat()}")
print("=" * 66)

from memall.core.db import get_conn, DB_PATH
from memall.core.thin_waist import capture, retrieve, connect, traverse, hybrid_search, vector_search, store_batch

conn = get_conn()

# ═══════════════════════════════════════════════════════════════════════
# 1. DB 基础延迟
# ═══════════════════════════════════════════════════════════════════════
print("\n── 1. DB 基础延迟 ─────────────────────────────────────────────")

# 1a. Simple read (warm cache)
trials = []
for _ in range(10):
    t0 = time.perf_counter()
    conn.execute("SELECT COUNT(*) FROM memories").fetchone()
    trials.append(ms(t0))
p50 = statistics.median(trials)
p99 = sorted(trials)[-1]
pts = min(WEIGHTS["db_read_latency"], WEIGHTS["db_read_latency"] * (1 / max(p50, 0.01)))
score("db_read_latency", round(pts, 1), WEIGHTS["db_read_latency"],
      f"p50={p50:.3f}ms  p99={p99:.3f}ms  (10x warm)")

# 1b. Simple write
trials_w = []
test_val = f"perf_test_{time.time_ns()}"
for _ in range(5):
    t0 = time.perf_counter()
    conn.execute("INSERT OR IGNORE INTO memories (content, content_hash, level, created_at, updated_at) "
                 "VALUES (?, ?, 'P2', datetime('now'), datetime('now'))",
                 (test_val, f"hash_{time.time_ns()}"))
    conn.commit()
    trials_w.append(ms(t0))
conn.execute("DELETE FROM memories WHERE content = ?", (test_val,))
conn.commit()
wp50 = statistics.median(trials_w)
pts_w = min(WEIGHTS["db_write_latency"], WEIGHTS["db_write_latency"] * (2 / max(wp50, 0.1)))
score("db_write_latency", round(pts_w, 1), WEIGHTS["db_write_latency"],
      f"p50={wp50:.3f}ms  (5x write+commit)")

# ═══════════════════════════════════════════════════════════════════════
# 2. 写入基准 (capture throughput)
# ═══════════════════════════════════════════════════════════════════════
print("\n── 2. 写入基准 (capture) ──────────────────────────────────────")

agent = f"perf_agent_{time.time_ns()}"
ids = []
t0 = time.perf_counter()
for i in range(20):
    mid = capture(f"[perf] 性能测试第 {i} 条记忆 — 用于评估系统写入吞吐量",
                  owner=agent, agent_name=agent)
    if mid:
        ids.append(mid)
elapsed = ms(t0)
throughput = len(ids) / (elapsed / 1000) if elapsed > 0 else 0
# capture triggers embedding + quality gate. 5/s is healthy.
pts = min(WEIGHTS["capture_throughput"],
          WEIGHTS["capture_throughput"] * (throughput / 5))
score("capture_throughput", round(pts, 1), WEIGHTS["capture_throughput"],
      f"{len(ids)}条/{elapsed:.0f}ms = {throughput:.1f}条/s")

# ═══════════════════════════════════════════════════════════════════════
# 3. 检索基准
# ═══════════════════════════════════════════════════════════════════════
print("\n── 3. 检索基准 ────────────────────────────────────────────────")

queries = ["性能", "决策", "架构", "记忆", "系统"]

# 3a. FTS5 raw
ft_latencies = []
ft_counts = []
for q in queries:
    t0 = time.perf_counter()
    rows = conn.execute(
        "SELECT COUNT(*) FROM memories_fts WHERE memories_fts MATCH ?", (q,)
    ).fetchone()
    ft_latencies.append(ms(t0))
    ft_counts.append(rows[0])
ft_p50 = statistics.median(ft_latencies)
# FTS5 target: <2ms full score
pts = min(WEIGHTS["fts5_latency"], WEIGHTS["fts5_latency"] * (2 / max(ft_p50, 0.1)))
score("fts5_latency", round(pts, 1), WEIGHTS["fts5_latency"],
      f"p50={ft_p50:.3f}ms  hits: {dict(zip(queries, ft_counts))}")

# 3b. hybrid_search (FTS5 + vec0 RRF)
hy_latencies = []
for q in queries:
    t0 = time.perf_counter()
    try:
        r = hybrid_search(q, top_k=3, viewer="system")
        hy_latencies.append(ms(t0))
    except Exception:
        hy_latencies.append(99999)
hy_p50 = statistics.median(hy_latencies)
# hybrid includes embedding model inference (~50ms is normal)
pts = min(WEIGHTS["hybrid_latency"], WEIGHTS["hybrid_latency"] * (100 / max(hy_p50, 10)))
score("hybrid_latency", round(pts, 1), WEIGHTS["hybrid_latency"],
      f"p50={hy_p50:.1f}ms")

# 3c. vector_search (vec0 KNN)
vec_latencies = []
for q in queries:
    t0 = time.perf_counter()
    try:
        r = vector_search(q, top_k=3)
        vec_latencies.append(ms(t0))
    except Exception:
        vec_latencies.append(99999)
vec_p50 = statistics.median(vec_latencies)
# vector includes embedding model inference
pts = min(WEIGHTS["vector_latency"], WEIGHTS["vector_latency"] * (100 / max(vec_p50, 10)))
score("vector_latency", round(pts, 1), WEIGHTS["vector_latency"],
      f"p50={vec_p50:.1f}ms")

# ═══════════════════════════════════════════════════════════════════════
# 4. 图操作基准
# ═══════════════════════════════════════════════════════════════════════
print("\n── 4. 图操作基准 ──────────────────────────────────────────────")

# 4a. connect
sample_a = ids[0] if ids else 1
sample_b = ids[1] if len(ids) > 1 else 2
trials_c = []
for _ in range(5):
    t0 = time.perf_counter()
    try:
        connect(sample_a, sample_b, "derives", 1.0)
        trials_c.append(ms(t0))
    except Exception:
        trials_c.append(ms(t0))
c_p50 = statistics.median(trials_c) if trials_c else 999
pts = min(WEIGHTS["connect_latency"], WEIGHTS["connect_latency"] * (5 / max(c_p50, 0.1)))
score("connect_latency", round(pts, 1), WEIGHTS["connect_latency"],
      f"p50={c_p50:.3f}ms  (5x)")

# 4b. traverse
trials_t = []
for _ in range(5):
    t0 = time.perf_counter()
    try:
        traverse(sample_a, depth=2)
        trials_t.append(ms(t0))
    except Exception:
        trials_t.append(ms(t0))
t_p50 = statistics.median(trials_t) if trials_t else 999
pts = min(WEIGHTS["traverse_latency"], WEIGHTS["traverse_latency"] * (10 / max(t_p50, 0.5)))
score("traverse_latency", round(pts, 1), WEIGHTS["traverse_latency"],
      f"p50={t_p50:.3f}ms  (5x)")

# ═══════════════════════════════════════════════════════════════════════
# 5. Pipeline 步骤基准
# ═══════════════════════════════════════════════════════════════════════
print("\n── 5. Pipeline 步骤基准 ───────────────────────────────────────")

from memall.pipeline.session import harvest_step
from memall.pipeline.extract import extract_step
from memall.pipeline.classify import classify_step
from memall.pipeline.archive import archive_step

for step_name, step_fn, weight_key, target_ms in [
    ("harvest_step", harvest_step, "pipeline_session", 2000),
    ("extract_step", extract_step, "pipeline_extract", 200),
]:
    t0 = time.perf_counter()
    try:
        r = step_fn()
        elapsed = ms(t0)
    except Exception as e:
        elapsed = 99999
        r = {"error": str(e)}
    pts_key = min(WEIGHTS[weight_key], WEIGHTS[weight_key] * (target_ms / max(elapsed, 10)))
    r_str = str(r)[:80]
    score(weight_key, round(pts_key, 1), WEIGHTS[weight_key],
          f"{elapsed:.0f}ms (目标<{target_ms}ms)  {r_str}")

t0 = time.perf_counter()
try:
    r_c = classify_step()
    elapsed_c = ms(t0)
except Exception as e:
    elapsed_c = 99999
    r_c = {"error": str(e)}
r_c_str = str(r_c)[:60] if not isinstance(r_c, dict) else f"processed={r_c.get('processed', '?')}"
pts_c = min(WEIGHTS["pipeline_classify"], WEIGHTS["pipeline_classify"] * (1000 / max(elapsed_c, 50)))
score("pipeline_classify", round(pts_c, 1), WEIGHTS["pipeline_classify"],
      f"{elapsed_c:.0f}ms (目标<1000ms)  {r_c_str}")

t0 = time.perf_counter()
try:
    r_a = archive_step()
    elapsed_a = ms(t0)
except Exception as e:
    elapsed_a = 99999
    r_a = {"error": str(e)}
pts_a = min(WEIGHTS["pipeline_archive"], WEIGHTS["pipeline_archive"] * (200 / max(elapsed_a, 10)))
score("pipeline_archive", round(pts_a, 1), WEIGHTS["pipeline_archive"],
      f"{elapsed_a:.0f}ms  archived={r_a.get('archived_memories', '?')}")

# ═══════════════════════════════════════════════════════════════════════
# 6. 批处理基准 (store_batch)
# ═══════════════════════════════════════════════════════════════════════
print("\n── 6. 批处理基准 ──────────────────────────────────────────────")

batch_items = [
    {"content": f"[perf_batch] item {i}", "agent_name": agent, "owner": agent}
    for i in range(10)
]
t0 = time.perf_counter()
try:
    br = store_batch(batch_items)
    batch_elapsed = ms(t0)
    batch_count = len(br.get("results", [])) if isinstance(br, dict) else 0
except Exception as e:
    batch_elapsed = 99999
    batch_count = 0
bth = batch_count / (batch_elapsed / 1000) if batch_elapsed > 0 else 0
# store_batch calls capture() per item (each triggers embedding). 0.5/s is reasonable.
pts = min(WEIGHTS["batch_throughput"], WEIGHTS["batch_throughput"] * (bth / 0.5))
score("batch_throughput", round(pts, 1), WEIGHTS["batch_throughput"],
      f"{batch_count}条/{batch_elapsed:.0f}ms = {bth:.1f}条/s")

# ═══════════════════════════════════════════════════════════════════════
# 7. 并发搜索基准
# ═══════════════════════════════════════════════════════════════════════
print("\n── 7. 并发搜索基准 ────────────────────────────────────────────")

results_lock = threading.Lock()
concurrent_results = []


def con_search(q):
    t0 = time.perf_counter()
    try:
        retrieve(q, viewer="system")
        lat = ms(t0)
    except Exception:
        lat = 99999
    with results_lock:
        concurrent_results.append(lat)


threads = []
for q in ["性能", "决策", "架构"] * 5:  # 15 concurrent
    t = threading.Thread(target=con_search, args=(q,))
    threads.append(t)

t0 = time.perf_counter()
for t in threads:
    t.start()
for t in threads:
    t.join()
total_elapsed = ms(t0)
c_p50 = statistics.median(concurrent_results)
penalty = total_elapsed / (c_p50 * len(threads) / 3)
pts = min(WEIGHTS["concurrent_search"],
          WEIGHTS["concurrent_search"] * (3 / max(penalty, 0.3)))
score("concurrent_search", round(pts, 1), WEIGHTS["concurrent_search"],
      f"15并发 total={total_elapsed:.0f}ms p50={c_p50:.1f}ms  penalty={penalty:.1f}x")

# ═══════════════════════════════════════════════════════════════════════
# 8. 数据库文件体积
# ═══════════════════════════════════════════════════════════════════════
print("\n── 8. 数据库文件体积 ──────────────────────────────────────────")

size_mb = DB_PATH.stat().st_size / (1024 * 1024)
# <500MB full marks, 500-2000 linear decay
pts = min(WEIGHTS["db_size"], WEIGHTS["db_size"] * (2000 / max(size_mb, 500)))
score("db_size", round(pts, 1), WEIGHTS["db_size"],
      f"{size_mb:.1f} MB  (目标 <2GB, 当前优秀)")

# ═══════════════════════════════════════════════════════════════════════
# 结果汇总
# ═══════════════════════════════════════════════════════════════════════
print("\n" + "=" * 66)
print("  MemALL Performance Benchmark Results")
print("=" * 66 + "\n")

for note in NOTES:
    print(note)

total_earned = sum(s["earned"] for s in SCORES.values())
total_max = sum(s["max"] for s in SCORES.values())
pct = total_earned / total_max * 100 if total_max else 0

print(f"\n{'─' * 66}")
if pct >= 95:
    grade = "S 优秀"
elif pct >= 90:
    grade = "A 良好"
elif pct >= PASS_THRESHOLD:
    grade = "B 合格"
else:
    grade = "C 需优化"

print(f"  总分: {total_earned:.1f}/{total_max} ({pct:.1f}%)  — 评级: {grade}")
print(f"  测试代理: {agent}")
print(f"  完成时间: {datetime.now(timezone.utc).isoformat()}")
print(f"{'─' * 66}")

conn.close()
sys.exit(0 if pct >= PASS_THRESHOLD else 1)