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
) -> bool:
    """Persist a search interaction (click / dwell / impression / copy / skip).

    Best-effort: returns ``False`` instead of raising when the table is
    missing or the payload is invalid, so the read path can never be broken
    by telemetry.  ``memory_id`` may be ``None`` for query-only impressions.
    """
    if action not in ("click", "dwell", "impression", "copy", "skip"):
        logger.debug("enhancements: ignoring unknown interaction action %r", action)
        return False
    if memory_id is None and action != "impression":
        return False
    try:
        from datetime import datetime, timezone

        conn.execute(
            "INSERT INTO search_interactions "
            "(query, memory_id, action, dwell_ms, rank_position, agent_name, owner, session_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                (query or "")[:2000],
                memory_id,
                action,
                max(0, int(dwell_ms or 0)),
                rank_position,
                (agent_name or "")[:200],
                (owner or "")[:200],
                (session_id or "")[:200],
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        return True
    except Exception:
        logger.debug("enhancements: record_interaction failed", exc_info=True)
        return False


# ── Orchestration ────────────────────────────────────────────────────


def apply_enhancements(
    results: List[Dict[str, Any]],
    query: str,
    conn=None,
    agent_name: str = "",
    explain: bool = False,
    cluster: bool = False,
    personalize: bool = False,
    context_boost: bool = False,
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
    "load_synonyms",
]
