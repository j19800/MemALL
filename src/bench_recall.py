"""Recall benchmark over the production store.

Method
------
Stratified sample of live memories across level + category + agent. For each
sampled row we build queries of three realistic kinds:

  exact   – a distinctive substring of the row's own content (known-item
            query; what a user pastes from memory)
  subject – the row's subject truncated to ~12 CJK chars (headline query)
  paraphr – first 6 chars of subject + a synonym-ish tail (weakest signal;
            this is where vectors are supposed to beat FTS)

Scored per mode: fts5, vec, hybrid (the three paths memall_read exposes).
Metrics: recall@10 (target in top 10), hit@1, MRR@10 (mean reciprocal rank).
Target rows are identified by id; archived rows are excluded from sampling
but a hit on an archived row still counts as a MISS (surfacing deleted
material is a failure, not a near-hit).
"""
import json
import random
import statistics
import sys
import time

from memall.mcp.adapter import handle_call

random.seed(42)

SAMPLE_N = int(sys.argv[1]) if len(sys.argv) > 1 else 60

conn_query = {
    "action": "unified_search", "query": "", "limit": 1, "mode": "fts5",
}

# ── sample stratified across levels ──
from memall.core.db import get_conn
conn = get_conn()
levels = [r[0] for r in conn.execute(
    "SELECT DISTINCT level FROM memories WHERE COALESCE(memory_status,'')!='archived' "
    "AND LENGTH(content) > 60").fetchall()]
rows = []
for lvl in levels:
    cand = conn.execute(
        "SELECT id, subject, content, level FROM memories "
        "WHERE level=? AND COALESCE(memory_status,'')!='archived' "
        "AND LENGTH(content)>60 AND subject != '' ORDER BY RANDOM() LIMIT 6",
        (lvl,)).fetchall()
    rows.extend(dict(c) for c in cand)
conn.close()
if len(rows) > SAMPLE_N:
    rows = random.sample(rows, SAMPLE_N)
print(f"sampled {len(rows)} rows across {len(levels)} levels")


def clean_subject(s: str) -> str:
    for p in ("[P2 原始]老陈: ", "[L4 会话] ", "[L6 反思] ", "[L9 蒸馏] ",
              "[讨论] ", "[L10 全局知识] ", "[L11] ", "[任务] "):
        s = s.replace(p, "")
    return s.strip()


def make_queries(row):
    """Word-boundary-aligned queries.

    First run used raw character slices; for ASCII text that cuts words in
    half ("...services with lon|g..."), and unicode61 word tokens never
    match a partial word — producing false misses that measured the query
    generator, not the retrieval.  CJK is unaffected (its index tokens are
    single characters).  Real users copy whole words; align slices to
    word/punct boundaries for a fair known-item probe.
    """
    subj = clean_subject(row["subject"])
    body = row["content"]

    def _aligned(s: str, start: int, want: int) -> str:
        seg = s[start:start + want]
        # extend left to the next whitespace run
        i = max(0, start - 1)
        while i > 0 and not s[i - 1].isspace():
            i -= 1
        seg = s[i:start + want]
        # cut right at the last whitespace inside the window
        j = len(seg)
        while j > 0 and not seg[j - 1].isspace():
            j -= 1
        seg = seg[:j].strip() if j > 4 else seg.strip()
        return seg

    mid = len(body) // 3
    ex = _aligned(body, mid, 24)[:40].strip()
    words = subj.split()
    subject_q = " ".join(words[:3])[:16].strip() or subj[:12]
    # CJK-leading phrase from the subject head (safe to slice: single-char tokens)
    head = subj.lstrip("[(《【")
    paraph_q = head[:6].strip()
    return {"exact": ex, "subject": subject_q, "paraphr": paraph_q}


def search(mode, q, limit=10):
    res = json.loads(handle_call("memall_read", {
        "action": "search", "mode": mode, "query": q, "top_k": limit,
    }))
    # A protocol/validator error must NOT be silently scored as recall 0 —
    # that would masquerade a harness bug as a retrieval-quality result.
    if isinstance(res, dict) and res.get("status") == "error":
        raise RuntimeError(f"mode {mode}: {res.get('error')!r}"[:120])
    if isinstance(res, list):
        return [r["id"] for r in res]
    return [r["memory_id"] for r in res.get("results", [])]


def fts_candidate_of(target_id, q):
    """Is the target even IN the FTS candidate set for this query?

    Separates "index/tokenisation loss" (target not a candidate at all —
    no ranking can recover it) from "ranking loss" (candidate but outside
    top-k).  Recall@k = candidate_rate × within-candidate top-k rate.
    """
    from memall.core.db import pool_conn
    from memall.core.thin_waist import fts_query
    fts = fts_query(q)
    if not fts:
        return None
    try:
        with pool_conn() as c:
            return bool(c.execute(
                "SELECT 1 FROM memories_fts WHERE memories_fts MATCH ? AND rowid=?",
                (fts, target_id)).fetchone())
    except Exception:
        return None


MODES = ["fts5", "vector", "hybrid"]  # schema enum: auto|direct|fts5|vector|hybrid
stats = {(m, k): {"recall@10": [], "hit@1": [], "rrf": []}
         for m in MODES for k in ("exact", "subject", "paraphr")}
cand_rate: dict = {"exact": [], "subject": [], "paraphr": []}
t0 = time.time()
n_done = 0
for row in rows:
    qs = make_queries(row)
    for kind, q in qs.items():
        if not q or len(q.strip()) < 3:
            continue
        cr = fts_candidate_of(row["id"], q)
        if cr is not None:
            cand_rate[kind].append(1.0 if cr else 0.0)
        for m in MODES:
            try:
                ids = search(m, q)
            except RuntimeError:
                raise  # protocol error must not be scored as recall 0
            except Exception:
                ids = []
            hit = row["id"] in ids
            stats[(m, kind)]["recall@10"].append(1.0 if hit else 0.0)
            stats[(m, kind)]["hit@1"].append(1.0 if ids and ids[0] == row["id"] else 0.0)
            rank = ids.index(row["id"]) + 1 if hit else 0
            stats[(m, kind)]["rrf"].append(1.0 / rank if rank else 0.0)
    n_done += 1
    if n_done % 15 == 0:
        print(f"  {n_done}/{len(rows)} rows ({time.time()-t0:.0f}s)")

print(f"\n=== recall @10 over {n_done} sampled memories ({n_done*3} queries) ===")
for kind in ("exact", "subject", "paraphr"):
    cr = cand_rate[kind]
    if cr:
        print(f"FTS candidate rate [{kind}]: {statistics.mean(cr)*100:.1f}% "
              f"(0% = target not in index candidates at all; no ranking can recover it)")
hdr = f"{'mode':8s} {'query':9s} {'n':>4s} {'recall@10':>10s} {'hit@1':>7s} {'MRR@10':>7s}"
print(hdr)
for m in MODES:
    for k in ("exact", "subject", "paraphr"):
        s = stats[(m, k)]
        n = len(s["recall@10"])
        if not n:
            continue
        print(f"{m:8s} {k:9s} {n:4d} {statistics.mean(s['recall@10'])*100:9.1f}% "
              f"{statistics.mean(s['hit@1'])*100:6.1f}% {statistics.mean(s['rrf']):7.3f}")
