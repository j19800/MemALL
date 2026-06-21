import re
import math
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from memall.core.db import get_conn, init_db
from memall.core.nlp import tokenize, compute_tfidf, cosine_sim

MIN_MEMORY_LENGTH = 50
CLUSTER_COUNT = 10
COHERENCE_TOP_K = 5


def _kmeans_pp(tfidf_docs: list, k: int, max_iter: int = 20) -> tuple:
    n = len(tfidf_docs)
    if n == 0:
        return [], []
    k = min(k, n)

    centroids = [0]
    for _ in range(1, k):
        dists = [min(cosine_sim(tfidf_docs[i], tfidf_docs[c]) for c in centroids) for i in range(n)]
        total = sum(dists) or 1
        probs = [d / total for d in dists]
        import random
        r = random.random()
        cum = 0
        for i, p in enumerate(probs):
            cum += p
            if r <= cum:
                centroids.append(i)
                break

    assignments = [0] * n
    for _ in range(max_iter):
        changed = False
        for i in range(n):
            best = max((cosine_sim(tfidf_docs[i], tfidf_docs[c]), c) for c in centroids)
            best_c = best[1]
            if assignments[i] != best_c:
                assignments[i] = best_c
                changed = True
        if not changed:
            break
        for j, c in enumerate(centroids):
            members = [i for i, a in enumerate(assignments) if a == c]
            if members:
                avg_sim = {i2: sum(cosine_sim(tfidf_docs[i2], tfidf_docs[m]) for m in members) / len(members) for i2 in members}
                centroids[j] = max(avg_sim, key=avg_sim.get)

    return centroids, assignments


def _cluster_label(tfidf: dict, docs: list, centroid_idx: int, member_indices: list) -> str:
    doc = docs[centroid_idx]
    tokens = tokenize(doc)
    freq = Counter(tokens)
    top = [t for t, _ in freq.most_common(5)]
    return " ".join(top[:3])


def _tfidf_cluster_label(tfidf_docs, docs, member_indices, all_token_lists, cidx):
    if not member_indices:
        return "empty"
    # Label from all member content tokens
    all_tokens = []
    for mi in member_indices:
        all_tokens.extend(all_token_lists[mi])
    freq = Counter(all_tokens)
    top = [t for t, _ in freq.most_common(5)]
    return " ".join(top[:3])


def _cluster_coherence(tfidf_docs: list, assignments: list, centroids: list) -> float:
    scores = []
    for ci in centroids:
        members = [i for i, a in enumerate(assignments) if a == ci]
        if len(members) < 2:
            continue
        intra_sims = []
        for i in members:
            sims = [cosine_sim(tfidf_docs[i], tfidf_docs[j]) for j in members if j != i]
            if sims:
                intra_sims.append(sum(sims) / len(sims))
        if intra_sims:
            scores.append(sum(intra_sims) / len(intra_sims))
    return round(sum(scores) / len(scores), 3) if scores else 0.0


def _cosine_sim_vec(a, b):
    dot = sum(ai * bi for ai, bi in zip(a, b))
    na = math.sqrt(sum(ai * ai for ai in a)) or 1
    nb = math.sqrt(sum(bi * bi for bi in b)) or 1
    return dot / (na * nb)


def _kmeans_pp_vec(vectors: list, k: int, max_iter: int = 20) -> tuple:
    n = len(vectors)
    if n == 0:
        return [], []
    k = min(k, n)

    centroids = [0]
    for _ in range(1, k):
        dists = [min(_cosine_sim_vec(vectors[i], vectors[c]) for c in centroids) for i in range(n)]
        total = sum(dists) or 1
        probs = [d / total for d in dists]
        import random
        r = random.random()
        cum = 0
        for i, p in enumerate(probs):
            cum += p
            if r <= cum:
                centroids.append(i)
                break

    assignments = [0] * n
    for _ in range(max_iter):
        changed = False
        for i in range(n):
            best = max((_cosine_sim_vec(vectors[i], vectors[c]), c) for c in centroids)
            best_c = best[1]
            if assignments[i] != best_c:
                assignments[i] = best_c
                changed = True
        if not changed:
            break
        for j, c in enumerate(centroids):
            members = [i for i, a in enumerate(assignments) if a == c]
            if members:
                avg_sim = {i2: sum(_cosine_sim_vec(vectors[i2], vectors[m]) for m in members) / len(members) for i2 in members}
                centroids[j] = max(avg_sim, key=avg_sim.get)

    return centroids, assignments


def _cluster_coherence_vec(vectors, assignments, centroids):
    scores = []
    for ci in centroids:
        members = [i for i, a in enumerate(assignments) if a == ci]
        if len(members) < 2:
            continue
        intra = []
        for i in members:
            sims = [_cosine_sim_vec(vectors[i], vectors[j]) for j in members if j != i]
            if sims:
                intra.append(sum(sims) / len(sims))
        if intra:
            scores.append(sum(intra) / len(intra))
    return round(sum(scores) / len(scores), 3) if scores else 0.0


def _narrative_tfidf_pca(texts: list, n_components: int = 50):
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.decomposition import PCA
    import numpy as np
    vec = TfidfVectorizer(max_features=2000, stop_words=None, token_pattern=r'(?u)\b\w+\b')
    X = vec.fit_transform(texts)
    n = X.shape[0]
    k = min(n_components, n, X.shape[1])
    if k < 2:
        return np.zeros((n, min(2, n)))
    pca = PCA(n_components=k)
    return pca.fit_transform(X.toarray())


def _embedding_label(vectors, assignments, cluster_idx, texts, all_token_lists):
    """Label a cluster from top TF-IDF terms among its members."""
    members = [i for i, a in enumerate(assignments) if a == cluster_idx]
    if not members:
        return "empty"
    corpus = " ".join(texts[m] for m in members)
    return " ".join(all_token_lists[members[0]][:3]) if all_token_lists else corpus[:40]


def _cluster_method_embedding(conn):
    now = datetime.now(timezone.utc).isoformat()

    # Get only the latest narrative per agent+type (not accumulated)
    rows = conn.execute(
        "SELECT n.id, n.narrative_text, n.agent_name, n.narrative_type FROM narratives n "
        "INNER JOIN (SELECT agent_name, narrative_type, MAX(id) as max_id FROM narratives GROUP BY agent_name, narrative_type) latest "
        "ON n.id = latest.max_id "
        "WHERE LENGTH(TRIM(n.narrative_text)) > 20 "
        "ORDER BY n.agent_name"
    ).fetchall()
    if not rows:
        return {"clusters_created": 0, "memories_clustered": 0, "coherence": 0, "method": "embedding"}

    nids = [r["id"] for r in rows]
    texts = [r["narrative_text"] for r in rows]
    all_token_lists = [tokenize(t) for t in texts]

    # sklearn PCA + sklearn KMeans (verified working, scikit-learn is a dep)
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.decomposition import PCA
    from sklearn.cluster import KMeans
    import numpy as np

    vec = TfidfVectorizer(max_features=2000, stop_words=None, token_pattern=r'(?u)\b\w+\b')
    X = vec.fit_transform(texts)
    n = X.shape[0]
    n_comp = min(50, n, X.shape[1])
    if n_comp < 2:
        return {"clusters_created": 0, "memories_clustered": 0, "coherence": 0, "method": "embedding"}
    pca = PCA(n_components=n_comp)
    vectors = pca.fit_transform(X.toarray())

    k = min(CLUSTER_COUNT, n)
    km = KMeans(n_clusters=k, n_init="auto", random_state=42)
    assignments = km.fit_predict(vectors)

    conn.execute("BEGIN IMMEDIATE")
    conn.execute("DELETE FROM clusters")
    conn.execute("DELETE FROM memory_clusters")
    conn.execute("DELETE FROM narrative_clusters")
    conn.commit()

    cluster_map = {}
    for cidx in range(k):
        members = [i for i, a in enumerate(assignments) if a == cidx]
        label = _embedding_label(vectors, assignments, cidx, texts, all_token_lists)
        cur = conn.execute(
            "INSERT INTO clusters (label, centroid_memory_id, member_count, coherence_score, created_at, updated_at) VALUES (?, NULL, ?, ?, ?, ?)",
            (label, len(members), None, now, now),
        )
        cluster_id = cur.lastrowid
        cluster_map[cidx] = cluster_id

        for mi in members:
            conn.execute(
                "INSERT INTO narrative_clusters (narrative_id, cluster_id, distance) VALUES (?, ?, ?)",
                (nids[mi], cluster_id, 0.0),
            )

    coherence = _cluster_coherence_vec(vectors.tolist(), assignments, list(range(k)))

    for cidx, cid in cluster_map.items():
        members = [i for i, a in enumerate(assignments) if a == cidx]
        if len(members) >= 2:
            intra = []
            for i in members:
                sims = [float(_cosine_sim_vec(vectors[i], vectors[j])) for j in members if j != i]
                if sims:
                    intra.append(sum(sims) / len(sims))
            score = round(sum(intra) / len(intra), 3) if intra else 0.0
            conn.execute("UPDATE clusters SET coherence_score = ? WHERE id = ?", (score, cid))

    conn.commit()

    return {
        "clusters_created": k,
        "memories_clustered": n,
        "total_memories": n,
        "coherence": coherence,
        "coherence_pass": coherence >= 0.25,
        "threshold": 0.25,
        "method": "embedding",
    }


def _cluster_method_tfidf(conn):
    rows = conn.execute(
        "SELECT id, content, category FROM memories WHERE LENGTH(content) >= ? ORDER BY created_at",
        (MIN_MEMORY_LENGTH,),
    ).fetchall()
    if not rows:
        return {"clusters_created": 0, "memories_clustered": 0, "coherence": 0, "method": "tfidf"}

    docs = [r["content"] for r in rows]
    ids = [r["id"] for r in rows]
    all_token_lists = [tokenize(d) for d in docs]

    tfidf_docs = compute_tfidf(docs)
    k = min(CLUSTER_COUNT, len(docs))

    from sklearn.cluster import KMeans
    import numpy as np
    tfidf_vecs = []
    for doc in tfidf_docs:
        v = [doc.get(t, 0.0) for t in sorted(set().union(*[set(d.keys()) for d in tfidf_docs]))]
        tfidf_vecs.append(v)
    arr = np.array(tfidf_vecs)
    km = KMeans(n_clusters=k, n_init="auto", random_state=42)
    assignments = km.fit_predict(arr)

    coherence = _cluster_coherence(tfidf_docs, assignments, list(range(k)))
    now = datetime.now(timezone.utc).isoformat()

    conn.execute("BEGIN IMMEDIATE")
    conn.execute("DELETE FROM clusters")
    conn.execute("DELETE FROM memory_clusters")
    conn.execute("DELETE FROM narrative_clusters")
    conn.commit()

    cluster_map = {}
    for cidx in range(k):
        members = [i for i, a in enumerate(assignments) if a == cidx]
        centroid_id = ids[members[0]] if members else None
        if len(members) >= 2:
            center = km.cluster_centers_[cidx]
            best_mi = min(members, key=lambda mi: float(np.linalg.norm(center - arr[mi])))
            centroid_id = ids[best_mi]
        label = _tfidf_cluster_label(tfidf_docs, docs, members, all_token_lists, cidx)
        cur = conn.execute(
            "INSERT INTO clusters (label, centroid_memory_id, member_count, coherence_score, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (label, centroid_id, len(members), None, now, now),
        )
        cluster_id = cur.lastrowid
        cluster_map[cidx] = cluster_id
        for mi in members:
            conn.execute(
                "INSERT OR IGNORE INTO memory_clusters (memory_id, cluster_id, distance) VALUES (?, ?, ?)",
                (ids[mi], cluster_id, 1.0 - cosine_sim(tfidf_docs[mi], tfidf_docs[members[0]])),
            )

    for cidx, cid in cluster_map.items():
        members = [i for i, a in enumerate(assignments) if a == cidx]
        if len(members) >= 2:
            intra = []
            for i in members:
                sims = [cosine_sim(tfidf_docs[i], tfidf_docs[j]) for j in members if j != i]
                if sims:
                    intra.append(sum(sims) / len(sims))
            score = round(sum(intra) / len(intra), 3) if intra else 0.0
            conn.execute("UPDATE clusters SET coherence_score = ? WHERE id = ?", (score, cid))

    conn.commit()

    return {
        "clusters_created": k,
        "memories_clustered": len(docs),
        "total_memories": len(rows),
        "coherence": coherence,
        "coherence_pass": coherence >= 0.25,
        "threshold": 0.25,
        "method": "tfidf",
    }


def cluster_step(method: str = "tfidf") -> dict:
    conn = get_conn()
    try:
        if method == "embedding":
            return _cluster_method_embedding(conn)
        return _cluster_method_tfidf(conn)
    finally:
        conn.close()
