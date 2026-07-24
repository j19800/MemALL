"""Unified NLP utilities for MemALL.

Provides tokenization, TF-IDF, cosine similarity, and TF-IDF → SVD embedding
used across federation/, graph/, and pipeline/ modules.

v2 improvements:
- Model persistence (save/load fitted TfidfVectorizer + TruncatedSVD)
- LRU cache for precomputed embeddings
- Chinese text detection
- Optional sentence-transformers with graceful fallback
"""

import hashlib
import json
import math
import os
import pickle
import re
from collections import Counter, OrderedDict
from typing import Optional

from memall.config import get_config


# ── Constants ───────────────────────────────────────────────────────

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

_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]")


# ── Chinese text detection ──────────────────────────────────────────


def contains_chinese(text: str, threshold: float = 0.05) -> bool:
    """Return True if *threshold* fraction or more of the non-space characters
    in *text* are CJK Unified Ideographs."""
    if not text:
        return False
    chars = text.replace(" ", "").replace("\t", "")
    if not chars:
        return False
    cjk_count = len(_CJK_RE.findall(chars))
    return (cjk_count / len(chars)) >= threshold


# ── LRU cache for embeddings ────────────────────────────────────────


class LRUEmbeddingCache:
    """Thread-safe LRU cache for computed embedding vectors.

    Uses an OrderedDict as a simple bounded LRU.  Not thread-safe by itself;
    callers in async contexts should guard with a lock if needed.
    """

    def __init__(self, maxsize: int = 10000):
        self._maxsize = maxsize
        self._cache: OrderedDict = OrderedDict()
        self._hits = 0
        self._misses = 0

    def _make_key(self, text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def get(self, text: str) -> Optional["np.ndarray"]:  # noqa: F821
        key = self._make_key(text)
        if key in self._cache:
            self._hits += 1
            self._cache.move_to_end(key)
            return self._cache[key]
        self._misses += 1
        return None

    def put(self, text: str, vec: "np.ndarray") -> None:  # noqa: F821
        key = self._make_key(text)
        self._cache[key] = vec
        self._cache.move_to_end(key)
        if len(self._cache) > self._maxsize:
            self._cache.popitem(last=False)

    @property
    def hit_rate(self) -> float:
        total = self._hits + self._misses
        return self._hits / total if total > 0 else 0.0

    @property
    def size(self) -> int:
        return len(self._cache)


# Global singleton cache.
_EMBEDDING_CACHE = LRUEmbeddingCache(
    maxsize=get_config("nlp.embedding_cache_size", 10000)
)


# ── Tokenization ────────────────────────────────────────────────────


def tokenize(text: str) -> list:
    """Tokenize text: lowercase → extract CJK/word tokens → remove stopwords + short tokens.

    Returns a list of tokens (not a set). Callers needing a set should wrap
    with ``set(tokenize(text))``.
    """
    text = text.lower()
    tokens = re.findall(r"[\w\u4e00-\u9fff]+", text)
    # Keep single CJK characters (e.g. 爱, 好, 大, 小) but drop single ASCII letters.
    return [
        t for t in tokens
        if t not in STOPWORDS_CJK_EN
        and (len(t) > 1 or bool(re.match(r"[一-鿿]", t)))
    ]


# ── Similarity functions ────────────────────────────────────────────


def jaccard(a: set, b: set) -> float:
    """Jaccard similarity coefficient for two sets."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


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


# ── Sentence splitting & extractive summarization ───────────────────


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
        parts = re.split(r"(?<=[。！？.!?])\s*", line)
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


# ── Model persistence ───────────────────────────────────────────────


def _get_model_dir() -> str:
    """Resolve the NLP model persistence directory from config."""
    path = get_config("nlp.model_dir", os.path.expanduser("~/.memall/.vector_model/"))
    os.makedirs(path, exist_ok=True)
    return path


MODEL_FILES = {
    "vectorizer": "tfidf_vectorizer.pkl",
    "svd": "truncated_svd.pkl",
    "config": "model_config.json",
}


def _model_version() -> str:
    """Return a string that changes when model hyper-params change."""
    import json
    cfg = {
        "max_features": get_config("nlp.max_features", 2000),
        "svd_dims": get_config("nlp.svd_dims", 256),
        "token_pattern": None,
    }
    return hashlib.md5(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:8]


def save_model(vec: "TfidfVectorizer", svd: "TruncatedSVD") -> None:  # noqa: F821
    """Persist a fitted TfidfVectorizer and TruncatedSVD to disk."""
    import json
    model_dir = _get_model_dir()
    ver = _model_version()
    try:
        with open(os.path.join(model_dir, MODEL_FILES["vectorizer"]), "wb") as f:
            pickle.dump(vec, f, protocol=pickle.HIGHEST_PROTOCOL)
        with open(os.path.join(model_dir, MODEL_FILES["svd"]), "wb") as f:
            pickle.dump(svd, f, protocol=pickle.HIGHEST_PROTOCOL)
        with open(os.path.join(model_dir, MODEL_FILES["config"]), "w", encoding="utf-8") as f:
            json.dump({"version": ver}, f)
    except Exception:
        import logging
        logging.getLogger(__name__).warning("Failed to persist NLP model", exc_info=True)


def load_model() -> tuple:
    """Load previously saved TfidfVectorizer and TruncatedSVD.

    Returns ``(vec, svd)`` or ``(None, None)`` if no saved model exists or
    the version has changed.
    """
    import json
    model_dir = _get_model_dir()
    ver = _model_version()
    config_path = os.path.join(model_dir, MODEL_FILES["config"])
    try:
        if os.path.isfile(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if saved.get("version") != ver:
                return None, None  # param change → retrain
        for name in (MODEL_FILES["vectorizer"], MODEL_FILES["svd"]):
            path = os.path.join(model_dir, name)
            if not os.path.isfile(path):
                return None, None
        with open(os.path.join(model_dir, MODEL_FILES["vectorizer"]), "rb") as f:
            vec = pickle.load(f)
        with open(os.path.join(model_dir, MODEL_FILES["svd"]), "rb") as f:
            svd = pickle.load(f)
        return vec, svd
    except Exception:
        return None, None


# ── TF-IDF → SVD embedding (with cache) ─────────────────────────────


def tfidf_svd_embed(
    texts: list,
    dims: int = 256,
    token_pattern: str = None,
    use_cache: bool = True,
) -> Optional["np.ndarray"]:  # noqa: F821
    """TF-IDF → TruncatedSVD pipeline with model persistence + LRU cache.

    On first call (or when hyper-params change) the model is trained from
    scratch and persisted.  Subsequent calls reload the saved model.

    When *use_cache* is True single-text embeddings are cached keyed by
    SHA-256 of the text.

    Returns an (n, k) numpy array where k ≤ min(dims, n, vocab_size).
    Returns ``None`` when k < 2 (not enough data for SVD).
    """
    # ── Single-text cache hit ──────────────────────────────────────────
    if use_cache and len(texts) == 1:
        cached = _EMBEDDING_CACHE.get(texts[0])
        if cached is not None:
            return cached

    # ── OpenBLAS OOM mitigation ────────────────────────────────────────
    import os as _os
    for _k in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
               "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
        if _k not in _os.environ:
            _os.environ[_k] = "1"

    from sklearn.decomposition import TruncatedSVD
    from sklearn.feature_extraction.text import TfidfVectorizer

    # ── Try loading persisted model ────────────────────────────────────
    vec, svd = load_model()

    if vec is not None and svd is not None:
        # Transform using saved model
        X = vec.transform(texts)
        result = svd.transform(X)
    else:
        # Train from scratch
        if token_pattern is not None:
            vec = TfidfVectorizer(
                max_features=2000,
                stop_words=None,
                token_pattern=token_pattern,
            )
        else:
            vec = TfidfVectorizer(
                max_features=2000,
                stop_words=None,
                tokenizer=tokenize,
            )
        X = vec.fit_transform(texts)
        n = X.shape[0]
        k = min(dims, n, X.shape[1])
        if k < 2:
            return None
        svd = TruncatedSVD(n_components=k, random_state=42)
        result = svd.fit_transform(X)
        # Persist for future calls
        save_model(vec, svd)

    # ── Single-text cache write-back ────────────────────────────────────
    if use_cache and len(texts) == 1 and result is not None:
        _EMBEDDING_CACHE.put(texts[0], result[0])

    return result


# ── Optional sentence-transformers integration ──────────────────────


def _st_available() -> bool:
    """Check whether sentence-transformers can be imported."""
    try:
        import sentence_transformers  # noqa: F401
        return True
    except ImportError:
        return False


_SENTENCE_TRANSFORMER_MODEL = None  # lazy-loaded singleton


def _get_st_model(model_name: str = None):
    """Lazy-load and cache the sentence-transformers model."""
    global _SENTENCE_TRANSFORMER_MODEL
    if _SENTENCE_TRANSFORMER_MODEL is not None:
        return _SENTENCE_TRANSFORMER_MODEL
    if not _st_available():
        return None
    if model_name is None:
        model_name = get_config(
            "nlp.sentence_transformers_model",
            "paraphrase-multilingual-MiniLM-L12-v2",
        )
    try:
        from sentence_transformers import SentenceTransformer
        _SENTENCE_TRANSFORMER_MODEL = SentenceTransformer(model_name)
    except Exception:
        import logging
        logging.getLogger(__name__).warning(
            "Failed to load sentence-transformers model '%s'", model_name, exc_info=True
        )
        _SENTENCE_TRANSFORMER_MODEL = None
    return _SENTENCE_TRANSFORMER_MODEL


def embed_texts_st(texts: list, model_name: str = None) -> Optional["np.ndarray"]:  # noqa: F821
    """Embed texts using sentence-transformers (multilingual).

    Falls back to ``tfidf_svd_embed`` when sentence-transformers is not
    installed (N-07: graceful fallback).
    """
    model = _get_st_model(model_name)
    if model is None:
        # Graceful fallback to TF-IDF + SVD
        return tfidf_svd_embed(texts)
    try:
        return model.encode(texts, show_progress_bar=False)
    except Exception:
        import logging
        logging.getLogger(__name__).warning(
            "sentence-transformers encode failed, falling back to TF-IDF/SVD",
            exc_info=True,
        )
        return tfidf_svd_embed(texts)


def get_embedding_cache_stats() -> dict:
    """Return current LRU cache diagnostic stats."""
    return {
        "size": _EMBEDDING_CACHE.size,
        "maxsize": _EMBEDDING_CACHE._maxsize,
        "hit_rate": round(_EMBEDDING_CACHE.hit_rate, 4),
    }
