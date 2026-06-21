"""
Save/Load vectorizer model for consistent vector search.

Uses JSON + .npy instead of pickle to avoid RCE via arbitrary code execution
during deserialization (CVE-style pickle.load risk).

Stores:
  - tfidf_vocab.json       — word→index mapping, stop words, constructor params
  - tfidf_idf.npy          — IDF weight array
  - svd_components.npy     — SVD components matrix
  - svd_variance.npy       — explained variance ratio
  - svd_singular.npy       — singular values
  - svd_params.json        — SVD constructor params
"""

import json
import os
import numpy as np
import scipy.sparse as sp
from typing import Optional
from sklearn.feature_extraction.text import TfidfVectorizer, TfidfTransformer
from sklearn.decomposition import TruncatedSVD

_MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".vector_model")

# File paths
_VOCAB_PATH = os.path.join(_MODEL_DIR, "tfidf_vocab.json")
_IDF_PATH = os.path.join(_MODEL_DIR, "tfidf_idf.npy")
_SVD_COMPONENTS_PATH = os.path.join(_MODEL_DIR, "svd_components.npy")
_SVD_VAR_PATH = os.path.join(_MODEL_DIR, "svd_variance.npy")
_SVD_SINGULAR_PATH = os.path.join(_MODEL_DIR, "svd_singular.npy")
_SVD_PARAMS_PATH = os.path.join(_MODEL_DIR, "svd_params.json")

# Keys in vec.get_params() that belong to TfidfTransformer (not CountVectorizer)
_TFIDF_PARAM_KEYS = {"norm", "use_idf", "smooth_idf", "sublinear_tf"}


def save_model(vec: TfidfVectorizer, svd: TruncatedSVD) -> None:
    """Save fitted TfidfVectorizer + TruncatedSVD to JSON + .npy files."""
    os.makedirs(_MODEL_DIR, exist_ok=True)

    # ── TfidfVectorizer state ──
    vocab = {k: int(v) for k, v in vec.vocabulary_.items()}
    stop_words = sorted(vec.stop_words_) if hasattr(vec, 'stop_words_') and vec.stop_words_ else sorted(vec.stop_words) if vec.stop_words else []
    # Split params: CountVectorizer params go to vec, TfidfTransformer params kept separately
    all_params = vec.get_params()
    tfidf_params = {k: all_params[k] for k in _TFIDF_PARAM_KEYS if k in all_params}
    count_params = {k: all_params[k] for k in all_params if k not in _TFIDF_PARAM_KEYS}
    # Custom tokenizer is a function ref, not JSON-serializable.  Save its name
    # so _reconstruct_vectorizer can re-attach it on load.
    tokenizer_name = None
    tokenizer = count_params.pop("tokenizer", None)
    if tokenizer is not None:
        tokenizer_name = f"{tokenizer.__module__}.{tokenizer.__name__}"
    token_pattern = count_params.pop("token_pattern", None)
    if token_pattern is not None:
        count_params["_saved_token_pattern"] = token_pattern

    with open(_VOCAB_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "vocabulary": vocab,
            "stop_words": stop_words,
            "count_params": {k: _safe_json(v) for k, v in count_params.items()},
            "tfidf_params": {k: _safe_json(v) for k, v in tfidf_params.items()},
            "tokenizer_name": tokenizer_name,
        }, f, ensure_ascii=False)

    np.save(_IDF_PATH, vec.idf_)

    # ── TruncatedSVD state ──
    np.save(_SVD_COMPONENTS_PATH, svd.components_)
    np.save(_SVD_VAR_PATH, getattr(svd, "explained_variance_ratio_", np.array([])))
    np.save(_SVD_SINGULAR_PATH, getattr(svd, "singular_values_", np.array([])))

    with open(_SVD_PARAMS_PATH, "w", encoding="utf-8") as f:
        svd_params = {}
        for k, v in svd.get_params().items():
            try:
                json.dumps(v)
                svd_params[k] = v
            except (TypeError, OverflowError):
                svd_params[k] = repr(v)
        json.dump(svd_params, f, ensure_ascii=False)


def load_model() -> Optional[dict]:
    """Load fitted TfidfVectorizer + TruncatedSVD from JSON + .npy files."""
    # Require all essential files to exist
    essential = [_VOCAB_PATH, _IDF_PATH, _SVD_COMPONENTS_PATH, _SVD_PARAMS_PATH]
    if not all(os.path.exists(p) for p in essential):
        return None

    try:
        # ── Load TfidfVectorizer ──
        with open(_VOCAB_PATH, "r", encoding="utf-8") as f:
            vocab_data = json.load(f)

        vec = _reconstruct_vectorizer(vocab_data)

        # ── Load TruncatedSVD ──
        with open(_SVD_PARAMS_PATH, "r", encoding="utf-8") as f:
            svd_params = json.load(f)

        svd = TruncatedSVD(**svd_params)
        svd.components_ = np.load(_SVD_COMPONENTS_PATH)

        var_path = _SVD_VAR_PATH
        if os.path.exists(var_path):
            svd.explained_variance_ratio_ = np.load(var_path)

        sing_path = _SVD_SINGULAR_PATH
        if os.path.exists(sing_path):
            svd.singular_values_ = np.load(sing_path)

        return {"vectorizer": vec, "svd": svd}

    except Exception:
        return None


def has_model() -> bool:
    return os.path.exists(_VOCAB_PATH)


# ── Internal helpers ──


def _safe_json(val):
    """Convert numpy/scipy types to JSON-safe Python types."""
    if isinstance(val, type):
        return val.__name__
    if isinstance(val, (np.integer,)):
        return int(val)
    if isinstance(val, (np.floating,)):
        return float(val)
    if isinstance(val, np.ndarray):
        return val.tolist()
    if isinstance(val, tuple):
        return list(val)
    return val


def _reconstruct_vectorizer(vocab_data: dict) -> TfidfVectorizer:
    """Reconstruct a fitted TfidfVectorizer from saved JSON state (no pickle)."""
    idf = np.load(_IDF_PATH)

    # Recreate TfidfVectorizer by bypassing fit() — same path as pickle.load()
    # but using safe primitives (JSON + .npy) instead of arbitrary code exec.
    all_params = {**vocab_data.get("count_params", {}), **vocab_data.get("tfidf_params", {})}

    # Restore custom tokenizer if one was saved — import it by module path
    tokenizer_name = vocab_data.get("tokenizer_name")
    if tokenizer_name:
        mod_path, func_name = tokenizer_name.rsplit(".", 1)
        import importlib
        mod = importlib.import_module(mod_path)
        tokenizer_fn = getattr(mod, func_name, None)
        if tokenizer_fn is not None:
            all_params["tokenizer"] = tokenizer_fn
            all_params.pop("token_pattern", None)  # tokenizer takes precedence
            all_params.pop("_saved_token_pattern", None)
    elif "_saved_token_pattern" in all_params:
        all_params["token_pattern"] = all_params.pop("_saved_token_pattern")

    vec = TfidfVectorizer(**all_params)

    # Restore fitted attributes directly (standard sklearn pattern: pickle does this)
    vec.vocabulary_ = vocab_data["vocabulary"]
    vec.stop_words_ = set(vocab_data.get("stop_words", []))

    # Restore IDF state on the internal TfidfTransformer
    _restore_idf(vec, idf)

    return vec


def _restore_idf(vec: TfidfVectorizer, idf: np.ndarray) -> None:
    """Set IDF state on a TfidfVectorizer's internal transformer."""
    if hasattr(vec, "_tfidf") and isinstance(vec._tfidf, TfidfTransformer):
        # sklearn >= 1.0: idf_ lives on self._tfidf
        vec._tfidf.idf_ = idf
        vec._tfidf.smooth_idf = vec.smooth_idf
        vec._tfidf.use_idf = vec.use_idf
        if idf.size > 0:
            vec._tfidf._idf_diag = sp.diags(
                idf, offsets=0,
                shape=(idf.size, idf.size),
                format="csr",
            )
    elif idf.size > 0:
        # Older sklearn fallback
        vec.idf_ = idf
        vec._idf_diag = sp.diags(
            idf, offsets=0,
            shape=(idf.size, idf.size),
            format="csr",
        )
