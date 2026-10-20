"""Latency benchmark: cold (first call after idle) vs warm, per search mode.

This measures the REAL user-facing path (thin_waist.retrieve / unified
search) against the production store.
"""
import json
import statistics
import time

from memall.core.db import get_conn
from memall.mcp.adapter import handle_call

QUERIES = [
    "FTS5 CJK 分词检索",
    "连接强度阈值",
    "pipeline 步骤失败",
    "拒绝名单 bypass",
    "会话总结",
    "记忆质量门",
]

conn = get_conn()
n = conn.execute("SELECT COUNT(*) FROM memories WHERE COALESCE(memory_status,'')!='archived'").fetchone()[0]
print(f"store: {n} active memories")
conn.close()


def bench(label, args, repeat=6):
    times = []
    for i in range(repeat):
        t0 = time.perf_counter()
        res = json.loads(handle_call("memall_read", args))
        dt = (time.perf_counter() - t0) * 1000
        times.append(dt)
    cold, rest = times[0], times[1:]
    med = statistics.median(rest)
    p95 = sorted(rest)[max(0, int(len(rest) * 0.95) - 1)]
    hits = None
    if isinstance(res, dict):
        hits = res.get("total", len(res.get("results", [])))
    elif isinstance(res, list):
        hits = len(res)
    print(f"{label:34s} cold={cold:7.1f}ms warm_med={med:7.1f}ms p95={p95:7.1f}ms hits≈{hits}")
    return cold, med


bench("fts5 (unicode61→cjk path)",
      {"action": "retrieve", "query": QUERIES[0], "limit": 10})
bench("fts5 jieba-assisted",
      {"action": "search", "mode": "fts5", "query": QUERIES[4], "top_k": 10})
bench("hybrid (fts+vec+rrf)",
      {"action": "search", "mode": "hybrid", "query": QUERIES[1], "top_k": 10})
bench("enhanced (facets+explain)",
      {"action": "search", "mode": "fts5", "query": QUERIES[2], "top_k": 10,
       "facet_fields": ["level", "category"], "explain": True})
bench("unified (query=chat-style)",
      {"action": "unified_search", "query": "拒绝名单是怎么被绕过的", "limit": 10,
       "mode": "auto"})
