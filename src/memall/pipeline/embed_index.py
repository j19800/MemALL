"""
Pipeline step: build/refresh embedding index for vector search.

Callers that write new memories should run this step periodically to
keep the vector index in sync.  The step is incremental — only
previously-unembedded memories are processed.
"""


def embed_index_step() -> dict:
    """Run incremental embedding index build.

    Returns:
        ``build_index()`` result dict with keys:
        ``total``, ``embedded``, ``new``, ``status``, ``model``.
    """
    try:
        from memall.graph.embeddings import build_index
        return build_index()
    except ImportError as e:
        return {"total": 0, "embedded": 0, "new": 0, "status": "skipped", "reason": str(e)}