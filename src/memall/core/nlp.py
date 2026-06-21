"""Unified NLP utilities for MemALL.

Provides tokenization, TF-IDF, cosine similarity, and TF-IDF → SVD embedding
used across federation/, graph/, and pipeline/ modules.
"""

import math
import re
from collections import Counter
from typing import Optional

import numpy as np

STOPWORDS_CJK_EN: set = {
    # Chinese stopwords
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一",
    "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着",
    "没有", "看", "好", "自己", "这", "他", "她", "它", "们", "那", "些",
    "什么", "怎么", "为什么", "因为", "所以", "但是", "如果", "虽然",
    # English stopwords
    "the", "a", "an", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "can", "shall",
    "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "as", "into", "through", "during", "before", "after",
    "and", "or", "but", "not", "no", "if", "so", "than", "that",
    "this", "these", "those", "it", "its", "we", "our", "you",
    "your", "they", "them", "their", "what", "which", "who",
    "whom", "when", "where", "how",
}


def tokenize(text: str) -> list:
    """Tokenize text: lowercase → extract CJK/word tokens → remove stopwords + short tokens.

    Returns a list of tokens (not a set). Callers needing a set should wrap
    with ``set(tokenize(text))``.
    """
    text = text.lower()
    tokens = re.findall(r'[\w\u4e00-\u9fff]+', text)
    return [t for t in tokens if t not in STOPWORDS_CJK_EN and len(t) > 1]


def compute_tfidf(docs: list) -> list:
    """Compute TF-IDF vectors for a list of documents.

    Returns a list of dicts: ``[{term: score, ...}, ...]``.
    """
    n = len(docs)
    df = Counter()
    doc_tokens = []
    for d in docs:
        tokens = tokenize(d)
        doc_tokens.append(tokens)
        df.update(set(tokens))

    tfidf_docs = []
    for tokens in doc_tokens:
        tf = Counter(tokens)
        max_tf = max(tf.values()) if tf else 1
        scores = {}
        for term, count in tf.items():
            tf_val = count / max_tf
            idf_val = math.log((n + 1) / (df.get(term, 0) + 1)) + 1
            scores[term] = tf_val * idf_val
        tfidf_docs.append(scores)
    return tfidf_docs


def cosine_sim(a: dict, b: dict) -> float:
    """Cosine similarity between two sparse TF-IDF term dicts.

    Returns float in [0, 1].  Zero-division is guarded with ``or 1``.
    """
    common = set(a) & set(b)
    if not common:
        return 0.0
    dot = sum(a[t] * b[t] for t in common)
    norm_a = math.sqrt(sum(v * v for v in a.values())) or 1
    norm_b = math.sqrt(sum(v * v for v in b.values())) or 1
    return dot / (norm_a * norm_b)


def _split_sentences(text: str) -> list:
    """Split text into sentences, handling CJK, English, and noisy content."""
    lines = text.split("\n")
    sentences = []
    for line in lines:
        line = line.strip()
        if not line or len(line) < 8:
            continue
        if line.startswith("{") or line.startswith("["):
            continue
        if line.startswith("```") or line.startswith("---"):
            continue
        parts = re.split(r'(?<=[。！？.!?])\s*', line)
        for p in parts:
            p = p.strip()
            if len(p) > 8:
                sentences.append(p)
    return sentences


def summarize_extractive(texts: list, top_n: int = 5, max_chars: int = 2000) -> str:
    """Extractive summarization using TF-IDF sentence scoring.

    Combines source texts, splits into sentences, scores each sentence
    by its TF-IDF sum against the corpus, returns top-N sentences joined.

    Args:
        texts: List of source text strings.
        top_n: Number of top sentences to select.
        max_chars: Maximum total characters in output.

    Returns:
        A string of selected sentences joined by newlines.
    """
    if not texts:
        return ""

    sentences = _split_sentences("\n".join(texts))

    if len(sentences) <= top_n:
        return "\n".join(sentences)

    tfidf_vecs = compute_tfidf(sentences)
    scored = [(sum(v.values()), idx) for idx, v in enumerate(tfidf_vecs)]
    scored.sort(reverse=True)

    seen = set()
    selected = []
    char_count = 0
    for _, idx in scored:
        s = sentences[idx]
        if char_count + len(s) > max_chars:
            continue
        norm = s.lower().strip()
        if norm in seen:
            continue
        seen.add(norm)
        selected.append(s)
        char_count += len(s)
        if len(selected) >= top_n:
            break

    return "\n".join(selected)


def tfidf_svd_embed(
    texts: list,
    dims: int = 256,
    token_pattern: str = None,
) -> Optional["np.ndarray"]:
    """TF-IDF → TruncatedSVD pipeline.

    Returns an (n, k) numpy array where k ≤ min(dims, n, vocab_size).
    Returns ``None`` when k < 2 (not enough data for SVD).

    **OpenBLAS OOM mitigation**: Limits OpenBLAS/OpenMP threads to 1
    before importing sklearn, preventing memory allocation failures
    under high load on Windows.
    """
    if np is None:
        raise ImportError("numpy is required for SVD embedding; install with: pip install numpy")
    # ── OpenBLAS OOM fix ─────────────────────────────────────────
    # Windows/OpenBLAS can OOM under memory pressure during SVD.
    # Limit threads before importing sklearn to avoid fork bomb.
    import os as _os
    for _k in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
               "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
        if _k not in _os.environ:
            _os.environ[_k] = "1"
    # ──────────────────────────────────────────────────────────────
    from sklearn.decomposition import TruncatedSVD
    from sklearn.feature_extraction.text import TfidfVectorizer

    if token_pattern is None:
        token_pattern = r'(?u)\b\w+\b'

    vec = TfidfVectorizer(
        max_features=2000,
        stop_words=None,
        token_pattern=token_pattern,
    )
    X = vec.fit_transform(texts)
    n = X.shape[0]
    k = min(dims, n, X.shape[1])
    if k < 2:
        return None
    svd = TruncatedSVD(n_components=k, random_state=42)
    return svd.fit_transform(X)
