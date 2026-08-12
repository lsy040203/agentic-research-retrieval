
"""
API 请求/响应数据模型

该文件只定义 HTTP API 层的请求与响应结构。
不要在这里写业务逻辑。

职责：
- 校验外部请求
- 约束字段类型
- 定义统一响应格式
- 与 core/models.py 通过 api/mappers.py 转换

对应接口：
- POST /memory/events
- POST /memory/extract
- POST /memory/retrieve
- POST /memory/forget
- GET  /memory/health
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from os_agent_memory.core.constants import (
    ApprovalDecision,
    ApprovalStatus,
    ActorType,
    EvaluationTask,
    EventSource,
    EventType,
    ForgetMode,
    MemoryStatus,
    MemoryType,
    MetricName,
    ResearchMemoryKind,
    ResearchMemoryStatus,
    RiskLevel,
    RetrievalMode,
    Scene,
    VerificationStatus,
)


def _require_final_verification_status(value: VerificationStatus) -> VerificationStatus:
    """Reject non-final statuses for executor verification receipts."""

    if value is VerificationStatus.PENDING:
        raise ValueError("verification receipt status must not be pending")
    return value


# =========================
# Research-memory schemas
# =========================

class ResearchScopeSchema(BaseModel):
    """HTTP representation of a research-memory isolation scope."""

    team_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    repository: str = Field(min_length=1)
    branch: str = Field(min_length=1)
    experiment_environment: str = Field(min_length=1)

    @field_validator(
        "team_id",
        "project_id",
        "repository",
        "branch",
        "experiment_environment",
        mode="before",
    )
    @classmethod
    def strip_scope_dimension(cls, value: Any) -> Any:
        """Normalize scope dimensions before rejecting empty values."""

        return value.strip() if isinstance(value, str) else value


class CreateResearchMemoryRequest(BaseModel):
    """Request payload for creating a research-domain memory."""

    scope: ResearchScopeSchema
    kind: ResearchMemoryKind
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    source_refs: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    applicability: dict[str, Any] = Field(default_factory=dict)
    related_memory_ids: list[str] = Field(default_factory=list)


class ResearchMemoryResponse(BaseModel):
    """Read representation of a research-domain memory."""

    memory_id: str
    scope: ResearchScopeSchema
    kind: ResearchMemoryKind
    title: str
    content: str
    source_refs: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    applicability: dict[str, Any] = Field(default_factory=dict)
    status: ResearchMemoryStatus
    created_at: datetime
    updated_at: datetime
    related_memory_ids: list[str] = Field(default_factory=list)


class UpdateResearchMemoryStatusRequest(BaseModel):
    """Request payload for changing a research memory's lifecycle state."""

    status: ResearchMemoryStatus
    reason: str | None = None


class CreateApprovalRequest(BaseModel):
    """Request payload for freezing a research case approval package."""

    scope: ResearchScopeSchema
    case_memory_id: str = Field(min_length=1)
    requester_id: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    risk_level: RiskLevel

    @field_validator("case_memory_id", "requester_id", mode="before")
    @classmethod
    def strip_identifier(cls, value: Any) -> Any:
        """Normalize request IDs and actors before validating them."""

        return value.strip() if isinstance(value, str) else value


class ApprovalPackageResponse(BaseModel):
    """HTTP representation of a frozen approval package."""

    package_id: str = Field(min_length=1)
    case_memory_id: str = Field(min_length=1)
    scope: ResearchScopeSchema
    requester_id: str = Field(min_length=1)
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    risk_level: RiskLevel
    status: ApprovalStatus
    created_at: datetime
    expires_at: datetime

    @field_validator("package_id", "case_memory_id", "requester_id", mode="before")
    @classmethod
    def strip_identifier(cls, value: Any) -> Any:
        """Normalize approval response IDs and actor fields."""

        return value.strip() if isinstance(value, str) else value


class ApprovalDecisionRequest(BaseModel):
    """Request payload for recording an approver's decision."""

    package_id: str = Field(min_length=1)
    approver_id: str = Field(min_length=1)
    decision: ApprovalDecision
    reason: str | None = None

    @field_validator("package_id", "approver_id", mode="before")
    @classmethod
    def strip_identifier(cls, value: Any) -> Any:
        """Normalize decision package IDs and actors before validating them."""

        return value.strip() if isinstance(value, str) else value


class ApprovalDecisionResponse(BaseModel):
    """HTTP representation of a recorded approval decision."""

    package_id: str = Field(min_length=1)
    approver_id: str = Field(min_length=1)
    decision: ApprovalDecision
    decided_at: datetime

    @field_validator("package_id", "approver_id", mode="before")
    @classmethod
    def strip_identifier(cls, value: Any) -> Any:
        """Normalize decision response IDs and actors."""

        return value.strip() if isinstance(value, str) else value


class CreateVerificationRequest(BaseModel):
    """执行器提交的回执；事件键仅由 ARR 在内部派生。"""

    model_config = ConfigDict(extra="forbid")

    scope: ResearchScopeSchema
    case_memory_id: str = Field(min_length=1)
    package_id: str = Field(min_length=1)
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    receipt_token: str = Field(pattern=r"^[0-9a-f]{64}$")
    receipt_id: str = Field(min_length=1)
    receipt: dict[str, Any] = Field(default_factory=dict)
    status: VerificationStatus

    @field_validator("case_memory_id", "package_id", "receipt_id", mode="before")
    @classmethod
    def strip_identifier(cls, value: Any) -> Any:
        """Normalize verification link fields before validating them."""

        return value.strip() if isinstance(value, str) else value

    @field_validator("status")
    @classmethod
    def reject_pending_status(cls, value: VerificationStatus) -> VerificationStatus:
        """Verification receipts must contain a final executor outcome."""

        return _require_final_verification_status(value)


class VerificationRunResponse(BaseModel):
    """HTTP representation of an executor verification receipt."""

    run_id: str = Field(min_length=1)
    case_memory_id: str = Field(min_length=1)
    package_id: str = Field(min_length=1)
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    receipt_id: str = Field(min_length=1)
    event_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    receipt: dict[str, Any] = Field(default_factory=dict)
    status: VerificationStatus
    created_at: datetime
    verified_at: datetime | None = None

    @field_validator("run_id", "case_memory_id", "package_id", "receipt_id", "event_key", mode="before")
    @classmethod
    def strip_identifier(cls, value: Any) -> Any:
        """Normalize verification response linkage fields."""

        return value.strip() if isinstance(value, str) else value

    @field_validator("status")
    @classmethod
    def reject_pending_status(cls, value: VerificationStatus) -> VerificationStatus:
        """Verification receipts must expose a final executor outcome."""

        return _require_final_verification_status(value)


# =========================
# 通用响应
# =========================

class APIResponse(BaseModel):
    """
    统一 API 响应。

    code = 0 表示成功。
    非 0 表示失败。
    """

    code: int = 0
    message: str = "success"
    data: Any | None = None
    request_id: str | None = None
    timestamp: datetime = Field(default_factory=datetime.now)


# =========================
# /memory/eval
# =========================

class PostEvalRunRequest(BaseModel):
    """Request payload for running one or more evaluation tasks."""

    tasks: list[EvaluationTask] = Field(default_factory=list)
    dataset_path: str | None = None


class MetricItem(BaseModel):
    """A single metric emitted by an evaluation run."""

    name: MetricName
    value: float
    description: str | None = None


class EvalRunResponse(BaseModel):
    """Response payload produced by the evaluation run endpoint."""

    report_id: str
    tasks: list[EvaluationTask]
    metrics: list[MetricItem] = Field(default_factory=list)


# =========================
# POST /memory/events
# =========================

class PostEventsRequest(BaseModel):
    """
    POST /memory/events 请求。

    作用：
    OS Agent 或 Mock Agent 将原始事件写入 Memory 系统。

    注意：
    这里接收的是 Agent Raw Payload。
    后续由 api/mappers.py 封装成 RawEvent，
    再由 ingestion/adapter.py 转成 MemoryEvent。
    """

    event_type: EventType
    user_id: str
    session_id: str
    task_id: str

    scenario: Scene = Scene.GLOBAL

    # 事件来源通道，例如 os_agent / mock_agent / tool_runtime
    source: EventSource = EventSource.OS_AGENT

    # 事件主体，例如 user / agent / tool / system
    actor: ActorType | None = None

    # Agent 原始 payload，保留原始结构
    payload: dict[str, Any]

    # 外部系统传入的时间，可选；不传则由服务端生成
    timestamp: datetime | None = None


class EventResponse(BaseModel):
    """
    事件保存响应。
    """

    raw_event_id: str
    memory_event_id: str
    user_id: str
    session_id: str
    task_id: str
    event_type: EventType
    status: str = "saved"


class PostEventsBatchRequest(BaseModel):
    """Request payload for storing a batch of raw events."""

    events: list[PostEventsRequest] = Field(default_factory=list)


class EventBatchResponse(BaseModel):
    """Summary of a batch event write."""

    total: int
    saved_count: int
    failed_count: int
    event_ids: list[str] = Field(default_factory=list)


class EventItem(BaseModel):
    """Event metadata returned by the event-list endpoint."""

    event_id: str
    user_id: str
    session_id: str
    task_id: str
    event_type: EventType
    scenario: Scene
    timestamp: datetime


# =========================
# POST /memory/extract
# =========================

class PostExtractRequest(BaseModel):
    """
    POST /memory/extract 请求。

    作用：
    从某个 session/task 下的事件中抽取候选记忆，
    并可选择是否直接提交为正式记忆。
    """

    user_id: str

    # 可以按 session 抽取，也可以按 task 抽取
    session_id: str | None = None
    task_id: str | None = None

    # 指定抽取哪些记忆类型；为空表示自动抽取全部支持类型
    memory_types: list[MemoryType] = Field(default_factory=list)

    # 是否将候选记忆直接保存为 MemoryRecord
    commit: bool = True


class CandidateItem(BaseModel):
    """
    抽取返回的候选记忆。
    """

    candidate_id: str
    memory_type: MemoryType
    key: str
    content: str
    scenario: Scene
    confidence: float
    source_events: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class ExtractionResponse(BaseModel):
    """
    抽取响应。
    """

    user_id: str
    session_id: str | None = None
    task_id: str | None = None

    candidates_count: int
    saved_count: int

    candidates: list[CandidateItem] = Field(default_factory=list)
    saved_memory_ids: list[str] = Field(default_factory=list)


# =========================
# POST /memory/retrieve
# =========================

class PostRetrieveRequest(BaseModel):
    """
    POST /memory/retrieve 请求。

    作用：
    Agent 根据当前任务 query 检索相关长期记忆。
    """

    user_id: str
    query: str

    scenario: Scene = Scene.GLOBAL

    # 需要检索的记忆类型；为空表示不过滤
    memory_types: list[MemoryType] = Field(default_factory=list)

    # 检索模式：keyword / vector / hybrid / exact_key / filter
    retrieval_mode: RetrievalMode = RetrievalMode.HYBRID

    # 返回条数
    top_k: int = Field(default=5, ge=1, le=50)

    # 默认不返回 deleted / expired / rejected 等无效记忆
    include_statuses: list[MemoryStatus] = Field(
        default_factory=lambda: [MemoryStatus.ACTIVE]
    )

    # 是否返回调试信息，例如命中原因、分数构成
    debug: bool = False


class MemoryItem(BaseModel):
    """
    检索返回的单条记忆。
    """

    memory_id: str
    memory_type: MemoryType
    key: str
    content: str
    scenario: Scene

    score: float | None = None
    confidence: float | None = None
    tags: list[str] = Field(default_factory=list)
    reason: str | None = None
    status: MemoryStatus | None = None
    version: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class RetrievedMemoryItem(MemoryItem):
    """A memory item returned from the retrieval endpoint."""


class MemoryListResponse(BaseModel):
    """Response payload for the active-memory list endpoint."""

    user_id: str
    total: int
    memories: list[MemoryItem] = Field(default_factory=list)


class RetrievalResponse(BaseModel):
    """
    检索响应。
    """

    user_id: str
    query: str
    scenario: Scene

    results: list[MemoryItem] = Field(default_factory=list)

    top_k: int
    retrieval_mode: RetrievalMode
    latency_ms: float | None = None


# =========================
# POST /memory/forget
# =========================

class PostForgetRequest(BaseModel):
    """
    POST /memory/forget 请求。

    作用：
    根据用户自然语言指令执行精准遗忘。
    """

    user_id: str
    instruction: str

    scenario: Scene = Scene.GLOBAL

    # 限制遗忘的记忆类型；为空表示不限制
    memory_types: list[MemoryType] = Field(default_factory=list)

    # 遗忘模式，第一阶段默认 soft_delete
    forget_mode: ForgetMode = ForgetMode.SOFT_DELETE

    # 是否只预览将被删除的记忆，不真正执行删除
    dry_run: bool = False


class ForgetResponse(BaseModel):
    """
    遗忘响应。
    """

    user_id: str
    instruction: str
    scenario: Scene

    dry_run: bool = False

    matched_count: int
    deleted_count: int

    matched_memory_ids: list[str] = Field(default_factory=list)
    deleted_memory_ids: list[str] = Field(default_factory=list)

    log_id: str | None = None


# =========================
# GET /memory/health
# =========================

class HealthResponse(BaseModel):
    """
    健康检查响应。
    """

    service: str = "os_agent_memory"
    status: str = "ok"
    timestamp: datetime = Field(default_factory=datetime.now)

