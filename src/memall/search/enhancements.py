"""Query enhancement layer for MemALL search.

Implements the six retrieval-quality features that sit *between* the raw
candidate set and the caller:

1. **Query expansion** — synonym + query-history expansion before retrieval.
2. **Query suggestions** — completions from the memory corpus vocabulary.
3. **Faceted search** — aggregate counts over candidate fields.
4. **Result clustering** — grouping results by topic (TF-IDF cosine).
5. **Explainability** — per-result score breakdown / matched terms.
6. **Personalised ranking** — per-agent affinity learned from
   ``search_interactions``.
7. **Context-aware boosting** — boost results matching the agent's active
   project / category.

Design notes
------------
* Every feature is **off by default** and gated by a config flag, so the
  hot path (plain FTS5 / hybrid retrieval) is unchanged until a deployment
  opts in.  :func:`apply_enhancements` is the single entry point that
  respects those flags.
* All SQL identifiers that reach a query string are validated against a
  hard whitelist (:data:`_FACET_WHITELIST`), never interpolated from raw
  caller input — facet fields come from an LLM, so they are untrusted.
* Personalisation reads only from the ``search_interactions`` table, which
  is written by :func:`record_interaction` (front-end / gateway side).
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


# ── Facet whitelist ──────────────────────────────────────────────────
# Only real ``memories`` columns may be faceted.  Anything else is dropped
# (and logged) instead of reaching the SQL string.
_FACET_WHITELIST = frozenset(
    {
        "level",
        "category",
        "project",
        "agent_name",
        "owner",
        "stream",
        "visibility",
        "memory_status",
        "confidence",
    }
)

# Rough per-facet cap: facets with more distinct values than this are dropped
# from the response to keep the payload usable (e.g. a free-text owner field).
_FACET_MAX_VALUES = 50


def _norm_facet_fields(fields: Any, max_fields: int = 8) -> List[str]:
    """Normalise the ``facet_fields`` argument into a safe column list.

    Accepts ``None``, ``""``, ``"auto"``, a comma/space-separated string, or a
    list of strings.  Unknown columns are dropped.
    """
    if not fields:
        return []
    if isinstance(fields, str):
        raw = [f.strip() for f in re.split(r"[,\s]+", fields) if f.strip()]
    elif isinstance(fields, (list, tuple, set)):
        raw = [str(f).strip() for f in fields if str(f).strip()]
    else:
        return []

    out: List[str] = []
    for f in raw:
        if f.lower() == "auto":
            continue  # handled by the caller (config defaults)
        if f in _FACET_WHITELIST and f not in out:
            out.append(f)
        else:
            logger.debug("enhancements: dropped unknown facet field %r", f)
        if len(out) >= max_fields:
            break
    return out


def _default_facet_fields() -> List[str]:
    from memall.config import get_config

    cfg = get_config("search.facets.default_fields", ["level", "category", "project"])
    max_fields = get_config("search.facets.max_fields", 8)
    return _norm_facet_fields(cfg, max_fields=max_fields)


# ── 1. Query expansion ───────────────────────────────────────────────


def load_synonyms() -> Dict[str, List[str]]:
    """Load the synonym dictionary.

    Precedence: ``search.query_expansion.synonyms`` (config.json / YAML /
    ``MEMALL_*`` env) → ``~/.memall/synonyms.json`` → ``{}``.
    """
    from memall.config import config_dir, get_config

    cfg = get_config("search.query_expansion.synonyms", {}) or {}
    if isinstance(cfg, dict) and cfg:
        return {str(k).lower(): [str(v) for v in vals] for k, vals in cfg.items()}

    try:
        path = config_dir() / "synonyms.json"
        if path.exists():
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return {
                    str(k).lower(): [str(v) for v in vals]
                    for k, vals in data.items()
                    if isinstance(vals, (list, tuple))
                }
    except Exception:
        logger.debug("enhancements: synonyms.json unreadable", exc_info=True)
    return {}


def expand_query(
    query: str,
    synonyms: Optional[Dict[str, List[str]]] = None,
    history_terms: Optional[Sequence[str]] = None,
) -> str:
    """Expand *query* with synonyms and optional history terms.

    Expansion is OR-only by construction: the returned string keeps every
    original token (AND-ed, as :func:`memall.core.thin_waist.fts_query` does)
    and appends synonym alternatives as an extra OR-group.  Recall never
    drops because of expansion — precision is tuned by the caller keeping
    ``expand_query`` off by default.

    Returns the expanded string; empty/blank input is returned unchanged.
    """
    if not query or not query.strip():
        return query or ""

    if synonyms is None:
        synonyms = load_synonyms()

    extras: List[str] = []
    seen: set[str] = set()
    for token in query.strip().split():
        low = token.lower()
        for cand in synonyms.get(low, []) or synonyms.get(token, []):
            if cand and cand.lower() not in seen:
                seen.add(cand.lower())
                extras.append(cand)

    for term in history_terms or []:
        t = (term or "").strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            extras.append(t)

    if not extras:
        return query
    return f"{query.strip()} ({' OR '.join(extras)})"


def _recent_query_terms(conn, agent_name: str = "", limit: int = 3) -> List[str]:
    """Top tokens from the agent's recent queries (query-history expansion)."""
    try:
        rows = conn.execute(
            "SELECT query FROM search_interactions "
            "WHERE agent_name = ? AND action = 'click' "
            "AND created_at > datetime('now', '-7 days') "
            "GROUP BY query ORDER BY COUNT(*) DESC LIMIT 20",
            (agent_name,),
        ).fetchall()
    except Exception:
        return []

    counts: Counter = Counter()
    for r in rows:
        for tok in re.findall(r"[\w\u4e00-\u9fff]+", (r["query"] or "").lower()):
            if len(tok) >= 2:
                counts[tok] += 1
    return [t for t, _ in counts.most_common(limit)]


# ── 2. Query suggestions ─────────────────────────────────────────────


def suggest_queries(conn, partial: str, limit: int = 8) -> List[str]:
    """Suggest query completions from the memory corpus vocabulary.

    Sources, in order:
      1. ``memories.subject`` values that start with / contain the partial.
      2. Frequent query terms from ``search_interactions``.

    Cheap and index-backed (``idx_memories_*`` + the FTS subject column);
    no model load.
    """
    partial = (partial or "").strip()
    if not partial:
        return []
    like = f"%{partial}%"
    out: List[str] = []
    seen: set[str] = set()

    def _add(v: str) -> None:
        v = (v or "").strip()
        if v and v.lower() not in seen:
            seen.add(v.lower())
            out.append(v)

    try:
        for row in conn.execute(
            "SELECT subject FROM memories WHERE subject LIKE ? "
            "AND subject != '' ORDER BY access_count DESC LIMIT ?",
            (like, limit * 3),
        ):
            _add(row["subject"])
            if len(out) >= limit:
                break
    except Exception:
        logger.debug("enhancements: subject suggestions failed", exc_info=True)

    if len(out) < limit:
        try:
            for row in conn.execute(
                "SELECT query FROM search_interactions WHERE query LIKE ? "
                "GROUP BY query ORDER BY COUNT(*) DESC LIMIT ?",
                (like, limit * 3),
            ):
                _add(row["query"])
                if len(out) >= limit:
                    break
        except Exception:
            logger.debug("enhancements: history suggestions failed", exc_info=True)

    return out[:limit]


# ── 3. Faceted search ────────────────────────────────────────────────


def compute_facets(
    conn,
    where_sql: str,
    params: Sequence[Any],
    fields: Sequence[str],
) -> Dict[str, Dict[str, int]]:
    """Aggregate counts per facet field over the *unlimited* candidate set.

    ``where_sql`` must be the already-assembled WHERE body (without the
    ``WHERE`` keyword) qualified against the ``memories`` alias, exactly as
    used by the retrieval query — so facets reflect the same filters the
    results do, minus LIMIT/OFFSET.

    Column names come from :data:`_FACET_WHITELIST` only; values are always
    bound parameters, never interpolated.
    """
    if not where_sql or where_sql == "1=1":
        where = ""
    else:
        where = f" WHERE {where_sql}"

    facets: Dict[str, Dict[str, int]] = {}
    for field in fields:
        try:
            rows = conn.execute(
                f"SELECT {field} AS v, COUNT(*) AS cnt FROM memories{where} "
                f"GROUP BY {field} ORDER BY cnt DESC, v ASC LIMIT ?",
                (*params, _FACET_MAX_VALUES),
            ).fetchall()
        except Exception:
            logger.debug("enhancements: facet query failed for %s", field, exc_info=True)
            continue
        counts: Dict[str, int] = {}
        for r in rows:
            v = r["v"]
            if v is None or (isinstance(v, str) and v == ""):
                v = "(none)"
            if isinstance(v, float):
                v = f"{v:.1f}"
            counts[str(v)] = int(r["cnt"])
        facets[field] = counts
    return facets


# ── 4. Result clustering ─────────────────────────────────────────────


def cluster_results(
    results: List[Dict[str, Any]],
    k: int = 0,
    min_cluster_size: int = 2,
    max_iterations: int = 12,
) -> List[Dict[str, Any]]:
    """Group results into topic clusters via spherical k-means on TF-IDF.

    Deterministic (seeded by the first ``k`` results, no RNG) so the same
    candidate set always clusters the same way.  Returns a list of
    ``{"label": str, "size": int, "results": [...]}`` with each member
    carrying ``cluster`` (index) and ``cluster_label``.

    ``k=0`` auto-derives the cluster count from the candidate count
    (``ceil(n / 4)``, capped at 8).  Results are never dropped: if k-means
    degenerates (fewer distinct centroids than k) the leftovers form their
    own singleton-free groups or a trailing "其他" cluster.
    """
    from memall.core.nlp import compute_tfidf, cosine_sim

    n = len(results)
    if n < 2:
        return []

    if k <= 0:
        k = min(8, max(1, math.ceil(n / 4)))
    k = max(1, min(k, n))

    docs = [(r.get("subject") or "") + " " + (r.get("content") or "")[:200] for r in results]
    vectors = compute_tfidf(docs, tokenizer=_cluster_tokenizer)

    # Deterministic seeding: spread initial centroids over the candidate list.
    if k >= n:
        seeds = list(range(n))
    else:
        step = n / k
        seeds = [min(n - 1, int(i * step)) for i in range(k)]
    centroids: List[Dict[str, float]] = []
    for s in seeds:
        if vectors[s] and vectors[s] not in centroids:
            centroids.append(vectors[s])
        if len(centroids) == k:
            break
    while len(centroids) < k:  # degenerate corpus (identical docs)
        centroids.append({})

    assign = [-1] * n
    for _ in range(max_iterations):
        changed = False
        for i, vec in enumerate(vectors):
            best, best_sim = -1, -1.0
            for ci, cen in enumerate(centroids):
                sim = cosine_sim(vec, cen)
                if sim > best_sim:
                    best, best_sim = ci, sim
            if best != assign[i]:
                assign[i] = best
                changed = True
        if not changed:
            break
        # Recompute centroids as the mean of assigned vectors.
        new_centroids: List[Dict[str, float]] = [dict() for _ in range(len(centroids))]
        counts = [0] * len(centroids)
        for i, ci in enumerate(assign):
            if ci < 0:
                continue
            counts[ci] += 1
            for term, val in vectors[i].items():
                new_centroids[ci][term] = new_centroids[ci].get(term, 0.0) + val
        for ci in range(len(centroids)):
            if counts[ci]:
                new_centroids[ci] = {t: v / counts[ci] for t, v in new_centroids[ci].items()}
            else:
                new_centroids[ci] = centroids[ci]
        centroids = new_centroids

    groups: Dict[int, List[int]] = {}
    for i, ci in enumerate(assign):
        if ci < 0:
            ci = 0
        groups.setdefault(ci, []).append(i)

    ordered = sorted(groups.values(), key=lambda members: -len(members))
    clusters: List[Dict[str, Any]] = []
    other_idx = 0
    for members in ordered:
        if len(members) < min_cluster_size:
            if not clusters:
                clusters.append({"label": "其他", "size": 0, "results": []})
            other_idx = len(clusters) - 1
            clusters[other_idx]["results"].extend(results[i] for i in members)
            clusters[other_idx]["size"] += len(members)
            continue
        label = _cluster_label([docs[i] for i in members])
        clusters.append(
            {
                "label": label,
                "size": len(members),
                "results": [results[i] for i in members],
            }
        )

    # Merge tiny "其他" group away if it ended up empty.
    clusters = [c for c in clusters if c["size"] > 0]
    return clusters


def _cluster_tokenizer(text: str) -> List[str]:
    """Tokenizer for clustering: word tokens + CJK bigrams (topic overlap)."""
    from memall.core.nlp import tokenize_cjk

    return tokenize_cjk(text)


def _cluster_label(texts: List[str]) -> str:
    """Pick the most distinctive shared token as a human-readable label."""
    from memall.core.nlp import tokenize

    if not texts:
        return "其他"
    counts: Counter = Counter()
    for t in texts:
        for tok in set(tokenize(t)):
            counts[tok] += 1
    if not counts:
        return (texts[0] or "").strip()[:20] or "其他"
    # Prefer terms present in most members, then longer terms.
    best = sorted(counts.items(), key=lambda kv: (-kv[1], -len(kv[0]), kv[0]))
    label = best[0][0]
    return label[:20] if label else "其他"


# ── 5. Explainability ────────────────────────────────────────────────


def explain_result(
    result: Dict[str, Any],
    query: str,
    synonyms: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, Any]:
    """Attach an ``explanation`` block to a single search result.

    Explains *why* this result ranked where it did using the signals already
    present on the result dict (no extra model inference):

    * ``matched_terms`` — query tokens literally present in the content.
    * ``fts_rank`` / ``vec_rank`` — per-engine position.
    * ``rrf_score`` / ``rerank_score`` — the numeric contributions.
    * ``boost`` — level / recency / personalisation multipliers applied.
    * ``reason`` — one-line human-readable summary.
    """
    if synonyms is None:
        synonyms = load_synonyms()

    content = (result.get("content") or "").lower()
    subject = (result.get("subject") or "").lower()
    haystack = f"{subject} {content}"

    tokens = [t.lower() for t in re.findall(r"[\w\u4e00-\u9fff]+", query or "") if len(t) >= 1]
    matched = [t for t in tokens if t in haystack]

    parts: List[str] = []
    if matched:
        # dict.fromkeys preserves insertion order while de-duplicating.
        parts.append("匹配词: " + ", ".join(list(dict.fromkeys(matched))[:6]))
    if result.get("fts_rank"):
        parts.append(f"FTS5 排名 #{result['fts_rank']}")
    if result.get("vec_rank"):
        parts.append(f"向量排名 #{result['vec_rank']}")
    if result.get("rerank_score") is not None:
        parts.append(f"重排分 {round(float(result['rerank_score']), 3)}")
    if result.get("personalized_score"):
        parts.append("个性化加权")
    if result.get("context_score"):
        parts.append("上下文加权")

    return {
        "matched_terms": list(dict.fromkeys(matched)),        "fts_rank": result.get("fts_rank"),
        "vec_rank": result.get("vec_rank"),
        "rrf_score": round(float(result.get("rrf_score") or 0.0), 6),
        "rerank_score": (
            round(float(result["rerank_score"]), 4)
            if result.get("rerank_score") is not None
            else None
        ),
        "boost": result.get("boost") or 1.0,
        "reason": "; ".join(parts) if parts else "综合排序命中",
    }


def highlight_matches(content: str, query: str, max_snips: int = 2) -> List[str]:
    """Return short snippets of *content* around query-token matches."""
    if not content or not query:
        return []
    tokens = [t for t in re.findall(r"[\w\u4e00-\u9fff]+", query) if len(t) >= 1]
    if not tokens:
        return []

    snips: List[str] = []
    low = content.lower()
    for tok in tokens:
        start = 0
        while True:
            idx = low.find(tok.lower(), start)
            if idx < 0:
                break
            lo = max(0, idx - 30)
            hi = min(len(content), idx + len(tok) + 30)
            snippet = content[lo:hi].replace("\n", " ").strip()
            if snippet and snippet not in snips:
                snips.append(snippet)
            start = idx + len(tok)
            if len(snips) >= max_snips:
                break
        if len(snips) >= max_snips:
            break
    return snips[:max_snips]


# ── 6. Personalised ranking ──────────────────────────────────────────


def build_affinity(
    conn, agent_name: str, window_days: int = 30
) -> Dict[int, float]:
    """Learn a per-memory affinity score from the agent's interactions.

    Affinity = weighted interaction counts (dwell weighted highest), then
    normalised to ``[0, 1]``.  Returns ``{}`` when the agent has no history
    or the table is missing — personalisation is then a no-op.
    """
    if not agent_name:
        return {}
    weights = {"dwell": 1.0, "click": 0.6, "copy": 0.8, "impression": 0.05}
    try:
        rows = conn.execute(
            "SELECT memory_id, action, COUNT(*) AS cnt, "
            "SUM(CASE WHEN dwell_ms > 0 THEN MIN(dwell_ms, 60000) ELSE 0 END) AS dwell_sum "
            "FROM search_interactions "
            "WHERE agent_name = ? AND memory_id IS NOT NULL "
            "AND created_at > datetime('now', ?) "
            "GROUP BY memory_id, action",
            (agent_name, f"-{int(window_days)} days"),
        ).fetchall()
    except Exception:
        logger.debug("enhancements: affinity query failed", exc_info=True)
        return {}

    raw: Counter = Counter()
    for r in rows:
        w = weights.get(r["action"], 0.3)
        score = w * int(r["cnt"] or 0)
        if r["dwell_sum"]:
            score += min(float(r["dwell_sum"] or 0) / 60000.0, 1.0)
        raw[int(r["memory_id"])] += score

    if not raw:
        return {}
    top = raw.most_common(1)[0][1] or 1.0
    return {mid: min(1.0, val / top) for mid, val in raw.items()}


def apply_personalization(
    results: List[Dict[str, Any]],
    affinity: Dict[int, float],
    weight: float = 0.25,
) -> List[Dict[str, Any]]:
    """Blend learned affinity into the result ordering.

    Each result gets ``personalized_score = base_score * (1 + weight*affinity)``
    where ``base_score`` is the best available ranking signal
    (``rerank_score`` > ``context_score`` > ``rrf_score``).  Ordering is
    stable: ties keep their original relative order.
    """
    if not affinity or not results:
        return results
    out = list(results)
    for r in out:
        mid = r.get("memory_id")
        if mid is None:
            continue
        aff = affinity.get(int(mid))
        if not aff:
            r.setdefault("personalized_score", None)
            continue
        base = (
            r.get("rerank_score")
            if r.get("rerank_score") is not None
            else r.get("context_score")
            if r.get("context_score") is not None
            else r.get("rrf_score")
            or 0.0
        )
        r["personalized_score"] = round(float(base) * (1 + weight * float(aff)), 6)
        r["affinity"] = round(float(aff), 3)

    out.sort(
        key=lambda r: -(
            r.get("personalized_score")
            if r.get("personalized_score") is not None
            else (
                r.get("rerank_score")
                if r.get("rerank_score") is not None
                else r.get("context_score")
                if r.get("context_score") is not None
                else r.get("rrf_score")
                or 0.0
            )
        )
    )
    return out


# ── 7. Context-aware boosting ────────────────────────────────────────


def build_context_profile(conn, agent_name: str, window_days: int = 7) -> Dict[str, Any]:
    """Infer the agent's active project / category from recent interactions.

    Returns ``{"projects": {...}, "categories": {...}}`` with normalised
    weights; empty dicts when there is no history.
    """
    if not agent_name:
        return {"projects": {}, "categories": {}}
    try:
        proj = conn.execute(
            "SELECT m.project AS p, COUNT(*) AS cnt FROM search_interactions si "
            "JOIN memories m ON m.id = si.memory_id "
            "WHERE si.agent_name = ? AND si.memory_id IS NOT NULL AND m.project != '' "
            "AND si.created_at > datetime('now', ?) "
            "GROUP BY m.project ORDER BY cnt DESC LIMIT 5",
            (agent_name, f"-{int(window_days)} days"),
        ).fetchall()
        cat = conn.execute(
            "SELECT m.category AS c, COUNT(*) AS cnt FROM search_interactions si "
            "JOIN memories m ON m.id = si.memory_id "
            "WHERE si.agent_name = ? AND si.memory_id IS NOT NULL AND m.category != '' "
            "AND si.created_at > datetime('now', ?) "
            "GROUP BY m.category ORDER BY cnt DESC LIMIT 5",
            (agent_name, f"-{int(window_days)} days"),
        ).fetchall()
    except Exception:
        logger.debug("enhancements: context profile query failed", exc_info=True)
        return {"projects": {}, "categories": {}}

    def _norm(rows, key) -> Dict[str, float]:
        total = sum(int(r["cnt"]) for r in rows) or 1
        return {r[key]: round(int(r["cnt"]) / total, 3) for r in rows}

    return {"projects": _norm(proj, "p"), "categories": _norm(cat, "c")}


def apply_context_boost(
    results: List[Dict[str, Any]],
    profile: Dict[str, Any],
    weight: float = 0.15,
) -> List[Dict[str, Any]]:
    """Boost results matching the agent's active project / category.

    Multiplicative on the base score: ``score *= 1 + weight * (proj_w + cat_w)``
    capped at ``1 + 2*weight``.  A no-op when the profile is empty.
    """
    if not profile or not results:
        return results
    projects = profile.get("projects") or {}
    categories = profile.get("categories") or {}
    if not projects and not categories:
        return results

    cap = 1 + 2 * weight
    for r in results:
        bonus = projects.get(r.get("project") or "", 0.0) + categories.get(
            r.get("category") or "", 0.0
        )
        if bonus <= 0:
            continue
        mult = min(cap, 1 + weight * bonus)
        base = (
            r.get("rerank_score")
            if r.get("rerank_score") is not None
            else r.get("context_score")
            if r.get("context_score") is not None
            else r.get("rrf_score")
            or 0.0
        )
        r["context_score"] = round(float(base) * mult, 6)
        r["context_boost"] = round(mult - 1, 3)

    def _key(r):
        for f in ("personalized_score", "context_score", "rerank_score", "rrf_score"):
            if r.get(f) is not None:
                return -float(r[f])
        return 0.0

    results.sort(key=_key)
    return results


# ── Interaction recording ────────────────────────────────────────────


def record_interaction(
    conn,
    query: str,
    memory_id: Optional[int] = None,
    action: str = "click",
    dwell_ms: int = 0,
    rank_position: Optional[int] = None,
    agent_name: str = "",
    owner: str = "",
    session_id: str = "",
    # ADR 0028 P3: richer feedback signals (all optional, additive).
    task_id: str = "",
    accepted: bool = False,
    rejected: bool = False,
    corrected: bool = False,
    ignored: bool = False,
    slot_key: str = "",
    source_pipeline: str = "",
    scope: str = "",
) -> bool:
    """Persist a search interaction (click / dwell / impression / copy / skip).

    Best-effort: returns ``False`` instead of raising when the table is
    missing or the payload is invalid, so the read path can never be broken
    by telemetry.  ``memory_id`` may be ``None`` for query-only impressions.

    ADR 0028 P3 adds optional feedback signals (task_id / accepted / rejected
    / corrected / ignored) and ledger dimensions (slot_key / source_pipeline /
    scope). All default to empty/false so existing callers are unaffected. On
    an old DB lacking the new columns the INSERT falls back to the legacy
    column list (non-fatal).
    """
    if action not in ("click", "dwell", "impression", "copy", "skip"):
        logger.debug("enhancements: ignoring unknown interaction action %r", action)
        return False
    if memory_id is None and action != "impression":
        return False
    try:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        base = (
            (query or "")[:2000], memory_id, action,
            max(0, int(dwell_ms or 0)), rank_position,
            (agent_name or "")[:200], (owner or "")[:200],
            (session_id or "")[:200], now,
        )
        try:
            conn.execute(
                "INSERT INTO search_interactions "
                "(query, memory_id, action, dwell_ms, rank_position, agent_name, owner, session_id, created_at, "
                " task_id, accepted, rejected, corrected, ignored, slot_key, source_pipeline, scope) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                base + (
                    (task_id or "")[:200],
                    int(bool(accepted)), int(bool(rejected)),
                    int(bool(corrected)), int(bool(ignored)),
                    (slot_key or "")[:200], (source_pipeline or "")[:200],
                    (scope or "")[:200],
                ),
            )
        except Exception:
            # Legacy DB without the P3 columns: retry with the original shape.
            conn.execute(
                "INSERT INTO search_interactions "
                "(query, memory_id, action, dwell_ms, rank_position, agent_name, owner, session_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                base,
            )
        conn.commit()
        return True
    except Exception:
        logger.debug("enhancements: record_interaction failed", exc_info=True)
        return False




def aggregate_interaction_utility(conn, agent_name: str = "", days: int = 30, limit: int = 500) -> Dict[str, Any]:
    """Read-only aggregation of search_interactions for P3 utility ledger experiments.

    **Not wired into ranking by default.** Returns conservative aggregates;
    callers must opt in via explicit config/strategy switches and tests.
    """
    if conn is None:
        return {"ok": False, "error": "no_connection", "agent_name": agent_name, "by_memory": []}
    try:
        agent_name = (agent_name or "")[:200]
        days = max(1, min(int(days or 30), 365))
        limit = max(1, min(int(limit or 500), 5000))
        rows = conn.execute(
            """SELECT memory_id,
                      SUM(CASE WHEN action='click' THEN 1 ELSE 0 END) AS clicks,
                      SUM(CASE WHEN action='copy' THEN 1 ELSE 0 END) AS copies,
                      SUM(CASE WHEN action='dwell' THEN 1 ELSE 0 END) AS dwells,
                      SUM(CASE WHEN action='skip' THEN 1 ELSE 0 END) AS skips,
                      SUM(CASE WHEN action='impression' THEN 1 ELSE 0 END) AS impressions,
                      COALESCE(SUM(dwell_ms), 0) AS dwell_ms_total,
                      MAX(created_at) AS last_seen
                 FROM search_interactions
                WHERE memory_id IS NOT NULL
                  AND (? = '' OR agent_name = ?)
                  AND created_at >= datetime('now', ?)
                GROUP BY memory_id
                ORDER BY (clicks*3 + copies*4 + dwells - skips*2) DESC, impressions DESC
                LIMIT ?""",
            (agent_name, agent_name, f"-{days} days", limit),
        ).fetchall()
        by_memory = []
        for r in rows:
            if hasattr(r, "keys"):
                d = {k: r[k] for k in r.keys()}
            else:
                d = {"memory_id": r[0], "clicks": r[1], "copies": r[2], "dwells": r[3], "skips": r[4], "impressions": r[5], "dwell_ms_total": r[6], "last_seen": r[7]}
            d["utility_score"] = int(d.get("clicks", 0) or 0) * 3 + int(d.get("copies", 0) or 0) * 4 + int(d.get("dwells", 0) or 0) - int(d.get("skips", 0) or 0) * 2
            by_memory.append(d)
        return {"ok": True, "agent_name": agent_name, "days": days, "by_memory": by_memory, "count": len(by_memory)}
    except Exception as e:
        logger.debug("enhancements: aggregate_interaction_utility failed", exc_info=True)
        return {"ok": False, "error": str(e)[:200], "agent_name": agent_name, "by_memory": []}


def aggregate_utility_ledger(conn, agent_name: str = "", days: int = 30,
                             limit: int = 500, dims: Optional[List[str]] = None) -> Dict[str, Any]:
    """ADR 0028 P3: persistent utility ledger aggregated by dimension.

    Groups ``search_interactions`` by ``slot_key`` / ``source_pipeline`` /
    ``scope`` (and optionally ``task_id``), counting the feedback signals
    (accepted / rejected / corrected / ignored) plus interaction actions, and
    computes a conservative ``utility_score``. Read-only; nothing here changes
    ranking by itself — consumers (resolve policy / rerank) must opt in.

    On a legacy DB without the P3 columns it degrades to a per-memory
    aggregation keyed by ``memory_id`` (ok=True, dims available reflect that).
    """
    if conn is None:
        return {"ok": False, "error": "no_connection", "by_dimension": []}
    dims = dims or ["slot_key", "source_pipeline", "scope"]
    allowed = {"slot_key", "source_pipeline", "scope", "task_id"}
    dims = [d for d in dims if d in allowed]
    if not dims:
        dims = ["slot_key"]
    try:
        agent_name = (agent_name or "")[:200]
        days = max(1, min(int(days or 30), 365))
        limit = max(1, min(int(limit or 500), 5000))
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(search_interactions)").fetchall()}
        has_feedback = {"accepted", "rejected", "corrected", "ignored"}.issubset(cols)
        dim_cols = [d for d in dims if d in cols]

        if not dim_cols:
            # Legacy fallback: aggregate by memory_id only.
            return _legacy_utility_ledger(conn, agent_name, days, limit)

        sel = ", ".join(dim_cols)
        fb = ""
        if has_feedback:
            fb = """,
                       SUM(CASE WHEN action='click' THEN 1 ELSE 0 END) AS clicks,
                       SUM(CASE WHEN action='copy' THEN 1 ELSE 0 END) AS copies,
                       SUM(CASE WHEN action='dwell' THEN 1 ELSE 0 END) AS dwells,
                       SUM(CASE WHEN action='skip' THEN 1 ELSE 0 END) AS skips,
                       SUM(CASE WHEN action='impression' THEN 1 ELSE 0 END) AS impressions,
                       COALESCE(SUM(accepted),0) AS accepted,
                       COALESCE(SUM(rejected),0) AS rejected,
                       COALESCE(SUM(corrected),0) AS corrected,
                       COALESCE(SUM(ignored),0) AS ignored"""
        rows = conn.execute(
            f"""SELECT {sel}, COUNT(*) AS interactions{fb}
                  FROM search_interactions
                 WHERE (? = '' OR agent_name = ?)
                   AND created_at >= datetime('now', ?)
                 GROUP BY {sel}
                 ORDER BY interactions DESC
                 LIMIT ?""",
            (agent_name, agent_name, f"-{days} days", limit),
        ).fetchall()
        out = []
        for r in rows:
            keys = r.keys() if hasattr(r, "keys") else []
            d = {k: r[k] for k in keys} if keys else {}
            if not keys:
                d = {c: r[i] for i, c in enumerate(dim_cols)}
                d["interactions"] = r[len(dim_cols)]
            a = int(d.get("accepted", 0) or 0)
            rj = int(d.get("rejected", 0) or 0)
            co = int(d.get("corrected", 0) or 0)
            ig = int(d.get("ignored", 0) or 0)
            cl = int(d.get("clicks", 0) or 0)
            cp = int(d.get("copies", 0) or 0)
            dw = int(d.get("dwells", 0) or 0)
            sk = int(d.get("skips", 0) or 0)
            # Conservative: positive adoption signals add, negative subtract.
            d["utility_score"] = (a * 5 + co * 3 + cl * 3 + cp * 4 + dw
                                  - rj * 5 - ig * 1 - sk * 2)
            d["feedback"] = {"accepted": a, "rejected": rj, "corrected": co, "ignored": ig}
            out.append(d)
        return {
            "ok": True, "agent_name": agent_name, "days": days,
            "dims": dim_cols, "by_dimension": out, "count": len(out),
            "utility_score_formula": "accepted*5+corrected*3+clicks*3+copies*4+dwells-rejected*5-ignored-skips*2",
        }
    except Exception as e:
        logger.debug("enhancements: aggregate_utility_ledger failed", exc_info=True)
        return {"ok": False, "error": str(e)[:200], "by_dimension": []}


def _legacy_utility_ledger(conn, agent_name, days, limit):
    """Fallback when the P3 interaction columns are absent (old DB)."""
    try:
        rows = conn.execute(
            """SELECT memory_id,
                      SUM(CASE WHEN action='click' THEN 1 ELSE 0 END) AS clicks,
                      SUM(CASE WHEN action='copy' THEN 1 ELSE 0 END) AS copies,
                      SUM(CASE WHEN action='skip' THEN 1 ELSE 0 END) AS skips,
                      COUNT(*) AS interactions
                 FROM search_interactions
                WHERE memory_id IS NOT NULL AND (? = '' OR agent_name = ?)
                  AND created_at >= datetime('now', ?)
                GROUP BY memory_id ORDER BY interactions DESC LIMIT ?""",
            (agent_name, agent_name, f"-{days} days", limit),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(zip(("memory_id", "clicks", "copies", "skips", "interactions"), tuple(r)))
            d["utility_score"] = int(d["clicks"]) * 3 + int(d["copies"]) * 4 - int(d["skips"]) * 2
            out.append(d)
        return {"ok": True, "agent_name": agent_name, "days": days,
                "dims": ["memory_id"], "by_dimension": out, "count": len(out),
                "legacy": True}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200], "by_dimension": []}


# ── Orchestration ────────────────────────────────────────────────────


def _apply_utility_rerank(
    results: List[Dict[str, Any]],
    conn,
    agent_name: str = "",
    window_days: int = 30,
    weight: float = 0.12,
    min_interactions: int = 2,
) -> List[Dict[str, Any]]:
    """P3 opt-in: blend read-only interaction utility into final ordering.

    **Default off** (config-gated). Conservative: requires >=min_interactions
    aggregate evidence per memory; never drops items; stores debug fields.
    """
    if not results or conn is None or not agent_name:
        return results
    try:
        agg = aggregate_interaction_utility(conn, agent_name=agent_name, days=window_days, limit=1000)
        if not agg.get("ok"):
            return results
        util = {}
        for m in agg.get("by_memory", []):
            mid = m.get("memory_id")
            if mid is None:
                continue
            evidence = int(m.get("clicks", 0) or 0) + int(m.get("copies", 0) or 0) + int(m.get("dwells", 0) or 0) + int(m.get("impressions", 0) or 0)
            if evidence < max(1, int(min_interactions or 1)):
                continue
            util[int(mid)] = float(m.get("utility_score", 0) or 0)
        if not util:
            return results
        vals = list(util.values())
        denom = max(abs(min(vals)), abs(max(vals)), 1.0)
        for r in results:
            rid = r.get("id") or r.get("memory_id")
            try:
                rid_i = int(rid) if rid is not None else None
            except Exception:
                rid_i = None
            u = util.get(rid_i, 0.0) / denom
            r["utility_rerank_signal"] = round(u, 6)
            base = float(r.get("context_score") or r.get("personalized_score") or r.get("rerank_score") or r.get("rrf_score") or r.get("score") or 0.0)
            r["utility_rerank_score"] = round(base * (1.0 + max(0.0, min(float(weight), 0.5)) * u), 6)
        results.sort(key=lambda r: (r.get("utility_rerank_score") if r.get("utility_rerank_score") is not None else -1e9), reverse=True)
    except Exception:
        logger.debug("enhancements: utility rerank failed", exc_info=True)
    return results


def build_continuity_enhancement(agent_name: str = "", project: str = "",
                                 limit: int = 8, include_conflicts: bool = False) -> Dict[str, Any]:
    """ADR 0028 P1: assemble a validity-aware continuity pack for the search path.

    Read-only; delegates to ``session.build_continuity_pack`` (the single
    authority for KB exclusion / temporal validity). Best-effort: any failure
    returns an explicit ``ok=False`` shape, never raises into retrieval.
    """
    try:
        from memall.pipeline.session import build_continuity_pack
        pack = build_continuity_pack(
            agent_name=agent_name, viewer=agent_name, project=project,
            limit=max(1, min(int(limit or 8), 100)),
            include_conflicts=bool(include_conflicts),
        )
        return {"ok": True, "continuity_pack": pack}
    except Exception as e:
        logger.debug("enhancements: continuity pack assembly failed: %s", e)
        return {"ok": False, "error": str(e)[:200], "continuity_pack": None}


def apply_enhancements(
    results: List[Dict[str, Any]],
    query: str,
    conn=None,
    agent_name: str = "",
    explain: bool = False,
    cluster: bool = False,
    personalize: bool = False,
    context_boost: bool = False,
    utility_rerank: bool = False,
    continuity: bool = False,
    project: str = "",
) -> Dict[str, Any]:
    """Apply the enabled post-retrieval enhancements to *results*.

    Single entry point used by ``unified_search``.  Each enhancement is
    independently gated by both its explicit argument and the corresponding
    ``search.*`` config flag, so an argument alone cannot turn a feature on
    for a deployment that left it disabled.

    Returns ``{"results": [...], "clusters": [...], "explained": bool}``.
    """
    from memall.config import get_config

    out = {"results": list(results), "clusters": [], "explained": False}

    # 7. Context-aware boosting (first: it defines the base ordering that
    # personalisation then adjusts).
    if context_boost and get_config("search.context_boost.enabled", False):
        profile = build_context_profile(
            conn,
            agent_name,
            window_days=get_config("search.context_boost.window_days", 7),
        )
        out["results"] = apply_context_boost(
            out["results"],
            profile,
            weight=get_config("search.context_boost.weight", 0.15),
        )

    # 6. Personalised ranking.
    if personalize and get_config("search.personalization.enabled", False):
        affinity = build_affinity(
            conn,
            agent_name,
            window_days=get_config("search.personalization.window_days", 30),
        )
        out["results"] = apply_personalization(
            out["results"],
            affinity,
            weight=get_config("search.personalization.weight", 0.25),
        )

    # P3 conservative utility rerank (explicit flag + config gate; default off).
    # ADR 0028 acceptance key: `quality.utility.tracking_enabled=True` must
    # also make retrieve ordering utility-aware. OR it with the older
    # `search.utility_rerank.enabled` switch so neither deployment breaks;
    # both default False, so the feature stays off unless explicitly enabled.
    _utility_on = bool(get_config("search.utility_rerank.enabled", False)) or bool(
        get_config("quality.utility.tracking_enabled", False)
    )
    if utility_rerank and _utility_on:
        out["results"] = _apply_utility_rerank(
            out["results"],
            conn,
            agent_name=agent_name,
            window_days=int(get_config("search.utility_rerank.window_days", 30) or 30),
            weight=float(get_config("search.utility_rerank.weight", 0.12) or 0.12),
            min_interactions=int(get_config("search.utility_rerank.min_interactions", 2) or 2),
        )

    # 4. Clustering (computed on the final ordering, cheap TF-IDF only).
    if cluster and get_config("search.clustering.enabled", False):
        out["clusters"] = cluster_results(
            out["results"],
            k=get_config("search.clustering.k", 0),
            min_cluster_size=get_config("search.clustering.min_cluster_size", 2),
        )

    # 5. Explainability (last: it reads the final per-result signals).
    if explain and get_config("search.explain.enabled", False):
        synonyms = load_synonyms()
        for r in out["results"]:
            r["explanation"] = explain_result(r, query, synonyms=synonyms)
            r["highlights"] = highlight_matches(r.get("content") or "", query)
        out["explained"] = True

    # ADR 0028 P1: opt-in continuity pack auto-assembly (default off).
    # Additive payload field; never mutates result ordering or drops items.
    if continuity and get_config("search.continuity.enabled", False):
        ce = build_continuity_enhancement(
            agent_name=agent_name, project=project,
            limit=int(get_config("search.continuity.limit", 8) or 8),
            include_conflicts=bool(get_config("search.continuity.include_conflicts", False)),
        )
        out["continuity"] = ce

    return out


__all__ = [
    "compute_facets",
    "expand_query",
    "suggest_queries",
    "cluster_results",
    "explain_result",
    "highlight_matches",
    "build_affinity",
    "apply_personalization",
    "build_context_profile",
    "apply_context_boost",
    "record_interaction",
    "apply_enhancements",
    "build_continuity_enhancement",
    "aggregate_interaction_utility",
    "aggregate_utility_ledger",
    "load_synonyms",
]
