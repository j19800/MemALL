from dataclasses import dataclass
from typing import Optional


@dataclass
class Memory:
    id: Optional[int] = None
    content: str = ""
    content_hash: str = ""
    level: str = "P2"
    owner: str = ""
    agent_name: str = ""
    creator: str = ""
    subject: str = ""
    project: str = ""
    category: str = "general"
    stream: str = "knowledge"
    summary: str = ""
    occurred_at: str = ""
    created_at: str = ""
    updated_at: str = ""
    supersedes: Optional[str] = None
    confidence: float = 0.5
    visibility: str = "private"
    access_count: int = 0
    metadata: str = "{}"
    thread_id: Optional[int] = None
    agent_name_locked: bool = False
    tags: str = "[]"
    # Source IDs for provenance tracking (M3): track which source memories contributed to this memory
    # Especially useful for distilled knowledge (L9+) to trace back to source memories
    source_ids: str = "[]"


@dataclass
class MemoryInput:
    content: str
    level: str = "P2"
    owner: str = ""
    agent_name: str = ""
    creator: str = ""
    subject: str = ""
    project: str = ""
    category: str = "general"
    stream: str = "knowledge"
    summary: str = ""
    occurred_at: str = ""
    supersedes: Optional[str] = None
    confidence: float = 0.5
    visibility: str = "private"
    metadata: str = "{}"
    tags: str = "[]"
    thread_id: Optional[int] = None
    # Source IDs for provenance tracking (M3): specify which source memories this memory is derived from
    # Particularly useful when creating distilled memories (L9+) from multiple sources
    source_ids: str = "[]"
    # Pinned classification fields (filled by capture() from caller intent,
    # not passed by callers): a comma list like "category,level".  The
    # rule-based classifiers honour it and never overwrite pinned values —
    # see memall.pipeline.classify.is_field_pinned.
    _pinned: str = ""
