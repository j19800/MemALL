# mcp/models.py - Pydantic models for MCP tool input validation
from pydantic import BaseModel, Field
from typing import Optional, List


# ── Core tools ──

class CaptureInput(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000, description="Memory content")
    owner: str = Field("", max_length=200)
    agent_name: str = Field("", max_length=200)
    subject: str = Field("", max_length=500)
    summary: str = ""
    project: str = Field("", max_length=500)
    category: str = Field("general", max_length=100)
    level: str = Field("P2", pattern=r"^(P[0-2]|L[1-9]|L10)$")
    metadata: str = "{}"


class RetrieveInput(BaseModel):
    query: Optional[str] = Field(None, max_length=5000)
    owner: Optional[str] = Field(None, max_length=200)
    agent_name: Optional[str] = Field(None, max_length=200)
    category: Optional[str] = Field(None, max_length=100)
    project: Optional[str] = Field(None, max_length=500)
    level: Optional[str] = Field(None, max_length=20)
    limit: int = Field(20, ge=1, le=500)


class ConnectInput(BaseModel):
    source_id: int = Field(..., ge=1)
    target_id: int = Field(..., ge=1)
    relation_type: str = Field(
        "refines",
        pattern=r"^(extends|contradicts|refines|cites|supersedes|related)$",
    )
    weight: float = Field(1.0, ge=0.0, le=2.0)


class TraverseInput(BaseModel):
    node_id: int = Field(..., ge=1)
    depth: int = Field(1, ge=1, le=10)
    relation_filter: Optional[str] = None
    thread_aware: bool = False


class TimelineInput(BaseModel):
    query: Optional[str] = Field(None, max_length=5000)
    hours: int = Field(24, ge=1, le=8760)
    category: Optional[str] = None
    project: Optional[str] = None
    limit: int = Field(50, ge=1, le=1000)
    days: Optional[int] = Field(None, ge=1, le=365)
    start: Optional[str] = None
    end: Optional[str] = None


# ── Persona & Ask tools ──

class PersonaInput(BaseModel):
    agent_name: str = Field(..., min_length=1, max_length=200)
    evolution: bool = False
    window_days: int = Field(30, ge=1, le=365)


class PersonaProfileInput(BaseModel):
    agent_name: str = Field(..., min_length=1, max_length=200)
    layer: str = Field("all", pattern=r"^([1-3]|all)$")


class AskInput(BaseModel):
    question: str = Field(..., min_length=1, max_length=5000)
    mode: str = Field("stance", pattern=r"^(stance|pattern|predict)$")
    # Fix Bug-1: 显式声明 subject 字段，agent_name 保留为向后兼容 alias
    subject: Optional[str] = Field(None, max_length=200)
    agent_name: Optional[str] = Field(None, max_length=200)


# ── Smart / Batch / Update / VectorSearch tools ──

class SmartStoreInput(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000)
    owner: str = Field("", max_length=200)
    agent_name: str = Field("", max_length=200)
    subject: str = Field("", max_length=500)
    project: str = Field("", max_length=500)
    category: str = Field("general", max_length=100)
    level: str = Field("P2", pattern=r"^(P[0-2]|L[1-9]|L10)$")
    dedup_threshold: float = Field(0.85, ge=0.0, le=1.0)


class StoreBatchItem(BaseModel):
    content: str = Field(..., min_length=1)
    owner: str = ""
    agent_name: str = ""
    subject: str = ""
    project: str = ""
    category: str = "general"
    level: str = "P2"


class StoreBatchInput(BaseModel):
    items: List[StoreBatchItem] = Field(..., min_length=1, max_length=200)


class UpdateInput(BaseModel):
    memory_id: int = Field(..., ge=1)
    content: Optional[str] = Field(None, max_length=10000)
    category: Optional[str] = Field(None, max_length=100)
    project: Optional[str] = Field(None, max_length=500)
    summary: Optional[str] = Field(None, max_length=2000)
    level: Optional[str] = Field(None, pattern=r"^(P[0-4]|L[1-9]|L10)$")


class VectorSearchInput(BaseModel):
    query: str = Field(..., min_length=1, max_length=5000)
    top_k: int = Field(10, ge=1, le=500)


# ── Session tools (Phase 8) ──

class SessionStartInput(BaseModel):
    agent_name: str = Field("", max_length=200)
    auto_inject: bool = True


class SessionEndInput(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=200)
    auto_extract: bool = False


class SessionSummaryInput(BaseModel):
    session_id: Optional[str] = Field(None, max_length=200)
    agent_name: Optional[str] = Field(None, max_length=200)
    limit: int = Field(5, ge=1, le=500)


class GraphInput(BaseModel):
    node_id: int = Field(..., ge=1)
    depth: int = Field(1, ge=1, le=5)
    relation_filter: Optional[str] = None


# ── Federation tools (Phase 8) ──

class FedQueryInput(BaseModel):
    query: str = ""
    agent_name: str = ""
    category: str = ""
    trust_level: str = ""
    limit: int = Field(20, ge=1, le=500)


class FedPublishInput(BaseModel):
    memory_id: int = Field(..., ge=1)
    source_agent: str = ""
    trust_level: str = Field("family", pattern=r"^(trusted|family|shared|public)$")
    category: str = ""


class FedConflictsInput(BaseModel):
    limit: int = Field(20, ge=1, le=500)


class FedInjectInput(BaseModel):
    agent_name: str = Field(..., min_length=1, max_length=200)


class FedExtractInput(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=200)


# ── Forgetting (Phase 11) ──

class ForgetInput(BaseModel):
    action: str = Field(
        ..., pattern=r"^(expired|low_value|review|stats|all)$"
    )
    days: int = Field(90, ge=1, le=3650)
    agent_name: Optional[str] = Field(None, max_length=200)


# ── Security (Phase 13) ──

class SecurityInput(BaseModel):
    action: str = Field(
        ..., pattern=r"^(audit|permit|check|score|list)$"
    )
    agent_name: Optional[str] = Field(None, max_length=200)
    level: Optional[str] = Field(None, pattern=r"^(public|trusted|private)$")
    requester: Optional[str] = Field(None, max_length=200)
    target: Optional[str] = Field(None, max_length=200)


# ── Ops (Phase 14) ──

class OpsInput(BaseModel):
    action: str = Field(
        ..., pattern=r"^(merge|split|tag|batch_tag|archive|restore|dedup|undo)$"
    )
    source_id: Optional[int] = Field(None, ge=1)
    target_id: Optional[int] = Field(None, ge=1)
    memory_id: Optional[int] = Field(None, ge=1)
    delimiter: Optional[str] = None
    separator: Optional[str] = None
    tags: Optional[List[str]] = None
    mode: Optional[str] = Field(None, pattern=r"^(add|set|remove)$")
    agent_name: Optional[str] = Field(None, max_length=200)
    category: Optional[str] = Field(None, max_length=100)
    level: Optional[str] = Field(None, max_length=20)
    tags_include: Optional[List[str]] = None
    before: Optional[str] = None
    after: Optional[str] = None
    days: Optional[int] = Field(None, ge=1, le=3650)
    threshold: float = Field(0.9, ge=0.0, le=1.0)
    max_pairs: int = Field(5000, ge=1, le=100000)
    max_memories: int = Field(10000, ge=2, le=50000)
    length_ratio_max: float = Field(5.0, ge=1.0, le=100.0)
    dry_run: bool = False
    op_id: Optional[int] = Field(None, ge=1)


# ── Adaptive (Phase 12) ──

class AdaptiveInput(BaseModel):
    action: str = Field(
        ..., pattern=r"^(clean|index|distill|all|report)$"
    )
    agent_name: Optional[str] = Field(None, max_length=200)


# ── Gateway (Phase 15) ──

class GatewayInput(BaseModel):
    action: str = Field(
        ..., pattern=r"^(start|stop|export|import|discover|pair|peers|federated)$"
    )
    port: Optional[int] = Field(None, ge=1024, le=65535)
    agent_name: Optional[str] = Field(None, max_length=200)
    file_path: Optional[str] = Field(None, max_length=500)
    address: Optional[str] = Field(None, max_length=200)
    query: Optional[str] = Field(None, max_length=5000)
    max_peers: Optional[int] = Field(None, ge=1, le=100)


# ── DB Maintenance (Phase 21) ──

class DBInput(BaseModel):
    action: str = Field(
        ..., pattern=r"^(optimize|stats|vacuum)$"
    )


# ── Discussion tools (Phase 1.5) ──


class DiscussionCreateInput(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    background: str = Field("", max_length=10000)
    options: Optional[list] = None
    participants: list = Field(..., min_length=1)
    open_questions: Optional[list] = None
    recommendation: str = Field("", max_length=5000)
    action_items: Optional[list] = None
    timeout_hours: int = Field(24, ge=1, le=720)


class DiscussionRespondInput(BaseModel):
    discussion_id: int = Field(..., ge=1)
    agent_name: str = Field(..., min_length=1, max_length=200)
    stance: str = Field(..., pattern=r"^(agree|disagree|pass|abstain)$")
    arguments: str = ""
    round_num: int = Field(1, ge=1, le=100)


class DiscussionStatusInput(BaseModel):
    discussion_id: Optional[int] = Field(None, ge=1)


# ── Trace & Identity ──


class TraceInput(BaseModel):
    memory_id: int = Field(..., ge=1)


class IdentityInput(BaseModel):
    agent_name: str = Field(..., min_length=1, max_length=200)


# ── Reflection interaction ──


class ReflectInteractInput(BaseModel):
    memory_id: int = Field(..., ge=1)
    action: str = Field(..., pattern=r"^(agree|disagree|probe)$")
    context: str = ""


# ── Onboarding ──


class OnboardingInput(BaseModel):
    action: str = Field(..., pattern=r"^(status|start|reset|submit_step|skip)$")
    step: Optional[int] = Field(None, ge=1, le=5)
    input_data: Optional[dict] = None
    user_id: str = "default"


# ── Pipeline ──


class RunPipelineInput(BaseModel):
    include_reflect: bool = True
    include_distill: bool = True
    include_integrate: bool = True
    include_persona: bool = True
    timeout: int = Field(300, ge=30, le=3600)


# ── Agent Hub bridge ──


class HubConnectInput(BaseModel):
    pass


class HubSyncInput(BaseModel):
    direction: str = Field("bidirectional", pattern=r"^(bidirectional|to_hub|from_hub)$")
    limit: int = Field(20, ge=1, le=500)