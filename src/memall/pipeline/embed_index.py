"""
Pipeline step: build/refresh embedding index for vector search.

Callers that write new memories should run this step periodically to
keep the vector index in sync.  The step is incremental — only
previously-unembedded memories are processed.
"""

from memall.graph.embeddings import build_index


def embed_index_step() -> dict:
    """Run incremental embedding index build.

    Returns:
        ``build_index()`` result dict with keys:
        ``total``, ``embedded``, ``new``, ``status``, ``model``.
    """
    return build_index()