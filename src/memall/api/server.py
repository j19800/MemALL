"""MemALL REST API handlers — migrated to gateway.py.

All routes are now served by `memall.gateway.MemAllGateway` on port 9919.
This file retains handler functions and Pydantic models for reference.
"""

from pathlib import Path
from pydantic import BaseModel
from typing import Optional
import json, re, logging

logger = logging.getLogger(__name__)

from memall.core.thin_waist import (
    capture, retrieve, connect, traverse, timeline,
    smart_store, store_batch, update, vector_search,
)
from memall.pipeline.forget import (
    forget_expired, forget_low_value, forget_review, forget_stats, forget_step,
)
from memall.pipeline.security import (
    audit_sensitive, set_permission, check_access,
    list_agents_by_permission, security_score,
)
from memall.pipeline.ops import (
    merge_memories, split_memory, tag_memory, batch_tag,
    batch_archive, batch_restore, deduplicate,
)
from memall.mcp.federation_tools import (
    fed_query, fed_publish, fed_conflicts, auto_inject, auto_extract,
)


# ══════════════════════════════════════════════════════════════════
# Pydantic Request Models
# ══════════════════════════════════════════════════════════════════

class CaptureRequest(BaseModel):
    content: str
    owner: str = ""
    agent_name: str = ""
    subject: str = ""
    project: str = ""
    category: str = "general"
    level: str = "P2"

class UpdateRequest(BaseModel):
    memory_id: int
    content: Optional[str] = None
    category: Optional[str] = None
    project: Optional[str] = None
    summary: Optional[str] = None
    level: Optional[str] = None

class SmartStoreRequest(BaseModel):
    content: str
    owner: str = ""
    agent_name: str = ""
    subject: str = ""
    project: str = ""
    category: str = "general"
    level: str = "P2"
    dedup_threshold: float = 0.85

class BatchStoreItem(BaseModel):
    content: str
    owner: str = ""
    agent_name: str = ""
    subject: str = ""
    project: str = ""
    category: str = "general"
    level: str = "P2"

class ConnectRequest(BaseModel):
    source_id: int
    target_id: int
    relation_type: str = "refines"
    weight: float = 1.0

class SessionStartRequest(BaseModel):
    agent_name: str = ""
    auto_inject: bool = True

class SessionEndRequest(BaseModel):
    session_id: str
    auto_extract: bool = False

class AskRequest(BaseModel):
    question: str
    mode: str = "stance"
    agent_name: Optional[str] = None
    scope: str = "local"
    history: list = []

class ForgetRequest(BaseModel):
    action: str = "all"
    days: int = 90
    agent_name: Optional[str] = None

class SecurityRequest(BaseModel):
    action: str
    agent_name: Optional[str] = None
    level: Optional[str] = None
    requester: Optional[str] = None
    target: Optional[str] = None

class OpsRequest(BaseModel):
    action: str
    source_id: Optional[int] = None
    target_id: Optional[int] = None
    memory_id: Optional[int] = None
    delimiter: Optional[str] = None
    tags: Optional[list[str]] = None
    mode: str = "add"
    agent_name: Optional[str] = None
    category: Optional[str] = None
    days: Optional[int] = None
    threshold: float = 0.85

class FedPublishRequest(BaseModel):
    memory_id: int
    source_agent: str = ""
    trust_level: str = "family"
    category: str = ""

class GatewayRequest(BaseModel):
    action: str
    port: int = 9919
    agent_name: Optional[str] = None
    file_path: Optional[str] = None
    address: Optional[str] = None
    query: Optional[str] = None
    max_peers: int = 3


# ══════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════

DEBT_SCAN_CACHE = Path.home() / ".memall" / "debt_scan_cache.json"


def _ok(data=None):
    return {"success": True, "data": data}


def _load_debt_cache():
    if DEBT_SCAN_CACHE.exists():
        try:
            return json.loads(DEBT_SCAN_CACHE.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _save_debt_cache(data: dict):
    DEBT_SCAN_CACHE.parent.mkdir(parents=True, exist_ok=True)
    prev = _load_debt_cache() or {}
    history = prev.get("history", [])
    scan = data.get("scan", {})
    if scan:
        history.append({
            "scan_time": scan.get("scan_time", ""),
            "line_count": scan.get("line_count", 0),
            "total": sum(scan.get("counts", {}).values()),
            "density": scan.get("density", 0),
            "severity_summary": scan.get("severity_summary", ""),
            "counts": scan.get("counts", {}),
        })
        history = history[-20:]
    data["history"] = history
    DEBT_SCAN_CACHE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")