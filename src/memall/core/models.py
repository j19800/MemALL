from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, timezone


def _now():
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Memory:
    id: Optional[int] = None
    content: str = ""
    content_hash: str = ""
    level: str = "P2"
    owner: str = ""
    agent_name: str = ""
    subject: str = ""
    project: str = ""
    category: str = "general"
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


@dataclass
class MemoryInput:
    content: str
    level: str = "P2"
    owner: str = ""
    agent_name: str = ""
    subject: str = ""
    project: str = ""
    category: str = "general"
    summary: str = ""
    occurred_at: str = ""
    supersedes: Optional[str] = None
    confidence: float = 0.5
    visibility: str = "private"
    metadata: str = "{}"
    tags: list = field(default_factory=list)
    thread_id: Optional[int] = None
