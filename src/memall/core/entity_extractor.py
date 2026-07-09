"""
Entity Extraction — Reusable entity and triple extraction module.

Provides pattern-based extraction for:
- Named entities (person, tool, technology, project, framework, language, concept)
- Subject–predicate–object triples (declarative sentences)
- Entity resolution (upsert into entities table)

Used by EntityStrategy, KGStrategy, and the entity_extraction pipeline step.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Entity types ─────────────────────────────────────────────────

ENTITY_TYPES = frozenset({
    "person", "tool", "technology", "project", "framework",
    "language", "concept", "organization", "location",
})

# ── Regex patterns ───────────────────────────────────────────────

# Technology / tool references — uppercase acronyms and known tools
_TECH_PATTERN = re.compile(
    r'\b(JavaScript|TypeScript|Python|Rust|Go\b|Java|Kotlin|Swift|'
    r'React|Vue|Angular|Django|Flask|FastAPI|Spring|PyTorch|TensorFlow|'
    r'Docker|Kubernetes|Redis|PostgreSQL|MySQL|MongoDB|SQLite|'
    r'AWS|GCP|Azure|GitHub|GitLab|REST|GraphQL|gRPC|'
    r'LangChain|LangGraph|CrewAI|Mem0|MemGPT|Chroma|Pinecone|Qdrant|Weaviate|'
    r'Figma|Notion|Slack|Discord|Jira|Confluence'
    r')\b'
)

# Chinese technology references (什么技术、什么框架)
_ZH_TECH_PATTERN = re.compile(
    r'(?:使用|用|基于|采用|借助)([A-Za-z0-9一-鿿]{2,40}(?:技术|框架|库|工具|平台|系统))'
)

# Person references (Chinese and English)
_PERSON_PATTERN = re.compile(
    r'(?:@|作者|创始人|开发者|维护者)([A-Za-z一-鿿]{2,20})'
)

# Project references
_PROJECT_PATTERN = re.compile(
    r'(?:项目|工程|仓库|repo)\s*[:：]?\s*([A-Za-z0-9_\-一-鿿]{2,40})'
)

# Language references
_LANG_PATTERN = re.compile(
    r'\b(Python|JavaScript|TypeScript|Rust|Go\b|Java|Kotlin|Swift|'
    r'C\+\+|C#|Ruby|PHP|Scala|Elixir|Haskell|Clojure|Dart|Lua)\b'
)

# ── Triple extraction patterns ───────────────────────────────────

# English: "X is/uses/enables/depends on Y"
_TRIPLE_EN_PATTERN = re.compile(
    r'([A-Za-z][A-Za-z0-9_\- ]{2,40})'
    r'\s+(is|are|was|were|uses|using|enables|powers|runs on|depends on|built with|written in|'
    r'replaces|supersedes|extends|integrates with|built on top of|based on|uses|runs|built)\s+'
    r'([A-Za-z][A-Za-z0-9_\- ]{2,40})'
)

# Chinese: "X 是 Y", "X 使用 Y", "X 依赖 Y"
_TRIPLE_ZH_PATTERN = re.compile(
    r'([一-鿿A-Za-z][一-鿿A-Za-z0-9_\- ]{1,20})'
    r'\s*(?:是|使用|采用|基于|依赖|集成|取代|扩展|构建于)\s+'
    r'([一-鿿A-Za-z][一-鿿A-Za-z0-9_\- ]{1,20})'
)


# ── Public API ────────────────────────────────────────────────────

def extract_entities(text: str, agent_name: str = "") -> list[dict]:
    """Extract named entities from text.

    Returns list of {name, entity_type, context_snippet} dicts.
    Deduplicated by (name, entity_type) within a single call.
    """
    entities: list[dict] = []
    seen: set[tuple[str, str]] = set()

    # 1. Technology / tool names
    for m in _TECH_PATTERN.finditer(text):
        name = m.group(1)
        key = (name.lower(), "technology")
        if key not in seen:
            seen.add(key)
            entities.append({
                "name": name,
                "entity_type": "technology",
                "context_snippet": _snippet(text, m.start(), 60),
            })

    # 2. Chinese technology references
    for m in _ZH_TECH_PATTERN.finditer(text):
        name = m.group(1)
        key = (name.lower(), "technology")
        if key not in seen:
            seen.add(key)
            entities.append({
                "name": name,
                "entity_type": "technology",
                "context_snippet": _snippet(text, m.start(), 60),
            })

    # 3. Person references
    for m in _PERSON_PATTERN.finditer(text):
        name = m.group(1)
        key = (name.lower(), "person")
        if key not in seen:
            seen.add(key)
            entities.append({
                "name": name,
                "entity_type": "person",
                "context_snippet": _snippet(text, m.start(), 60),
            })

    # 4. Project references
    for m in _PROJECT_PATTERN.finditer(text):
        name = m.group(1)
        key = (name.lower(), "project")
        if key not in seen:
            seen.add(key)
            entities.append({
                "name": name,
                "entity_type": "project",
                "context_snippet": _snippet(text, m.start(), 60),
            })

    # 5. Programming languages
    for m in _LANG_PATTERN.finditer(text):
        name = m.group(1)
        key = (name.lower(), "language")
        if key not in seen:
            seen.add(key)
            entities.append({
                "name": name,
                "entity_type": "language",
                "context_snippet": _snippet(text, m.start(), 60),
            })

    return entities


def extract_triples(text: str, agent_name: str = "") -> list[dict]:
    """Extract subject–predicate–object triples from text.

    Returns list of {subject, predicate, object, confidence, subject_type, object_type}
    dicts. Deduplicated by (subject, predicate, object) within a single call.
    """
    triples: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    # English triples
    for m in _TRIPLE_EN_PATTERN.finditer(text):
        subj = m.group(1).strip()
        pred = m.group(2).strip()
        obj = m.group(3).strip()
        key = (subj.lower(), pred.lower(), obj.lower())
        if key not in seen and len(subj) > 1 and len(obj) > 1:
            seen.add(key)
            triples.append({
                "subject": subj,
                "predicate": pred,
                "object": obj,
                "confidence": 0.8,
                "subject_type": _infer_type(subj),
                "object_type": _infer_type(obj),
            })

    # Chinese triples
    for m in _TRIPLE_ZH_PATTERN.finditer(text):
        subj = m.group(1).strip()
        obj = m.group(2).strip()
        key = (subj.lower(), "is", obj.lower())
        if key not in seen and len(subj) > 1 and len(obj) > 1:
            seen.add(key)
            triples.append({
                "subject": subj,
                "predicate": "is",
                "object": obj,
                "confidence": 0.7,
                "subject_type": _infer_type(subj),
                "object_type": _infer_type(obj),
            })

    return triples


def resolve_entity(name: str, entity_type: str, conn) -> int:
    """Insert or retrieve entity ID by (name, entity_type).

    Handles concurrent upsert via INSERT OR IGNORE + SELECT.
    Updates ``updated_at`` on existing entities.
    """
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO entities (name, entity_type, canonical_name, metadata, created_at, updated_at) "
        "VALUES (?, ?, ?, '{}', ?, ?)",
        (name.strip(), entity_type, name.strip(), now, now),
    )
    row = conn.execute(
        "SELECT id FROM entities WHERE name = ? AND entity_type = ?",
        (name.strip(), entity_type),
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE entities SET updated_at = ? WHERE id = ?",
            (now, row["id"]),
        )
        return row["id"]
    return 0


def _infer_type(name: str) -> str:
    """Guess entity type from name heuristics."""
    if name[0].isupper() and not name[0].isascii():
        return "concept"
    if name.isupper() and len(name) <= 8:
        return "technology"
    if "@" in name:
        return "person"
    return "concept"


def _snippet(text: str, pos: int, width: int = 60) -> str:
    """Extract a centered snippet around *pos*."""
    start = max(0, pos - width // 2)
    end = min(len(text), pos + width // 2)
    snippet = text[start:end].replace("\n", " ").strip()
    if start > 0:
        snippet = "..." + snippet
    if end < len(text):
        snippet = snippet + "..."
    return snippet[:80]