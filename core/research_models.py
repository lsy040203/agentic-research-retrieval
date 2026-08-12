"""Domain models for the ARR research-memory subsystem."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import math
import re
from typing import Any

from .constants import (
    ApprovalStatus,
    RiskLevel,
    ResearchMemoryKind,
    ResearchMemoryStatus,
    VerificationStatus,
)


_PAYLOAD_HASH_PATTERN = re.compile(r"[0-9a-f]{64}")


def _require_non_blank_identifier(name: str, value: str) -> None:
    """Reject blank identifiers used to link audit records."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be blank")


def _require_payload_hash(value: str) -> None:
    """Require the canonical lower-case SHA-256 digest form."""

    if not isinstance(value, str) or not _PAYLOAD_HASH_PATTERN.fullmatch(value):
        raise ValueError("payload_hash must be a 64-character lowercase hexadecimal hash")


def _require_receipt_token(value: str) -> None:
    """校验审批包绑定的一次性高熵回执令牌。"""

    if not isinstance(value, str) or not _PAYLOAD_HASH_PATTERN.fullmatch(value):
        raise ValueError("receipt_token must be a 64-character lowercase hexadecimal token")


def derive_receipt_event_key(package_id: str, receipt_token: str, receipt_id: str) -> str:
    """从审批包、一次性令牌和回执标识稳定派生 ARR 内部事件键。"""

    _require_non_blank_identifier("package_id", package_id)
    _require_receipt_token(receipt_token)
    _require_non_blank_identifier("receipt_id", receipt_id)
    canonical_input = json.dumps(
        [package_id, receipt_token, receipt_id],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical_input.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ScopeKey:
    """Identifies the isolated scope in which a research memory applies."""

    team_id: str
    project_id: str
    repository: str
    branch: str
    experiment_environment: str

    def __post_init__(self) -> None:
        for name in (
            "team_id",
            "project_id",
            "repository",
            "branch",
            "experiment_environment",
        ):
            value = getattr(self, name)
            if not isinstance(value, str):
                raise TypeError(f"{name} must be a string")
            normalized = value.strip()
            if not normalized:
                raise ValueError(f"{name} must not be blank")
            object.__setattr__(self, name, normalized)


@dataclass
class ResearchMemory:
    """A traceable research-domain memory kept separately from ``MemoryRecord``."""

    memory_id: str
    scope: ScopeKey
    kind: ResearchMemoryKind
    title: str
    content: str

    source_refs: list[str] = field(default_factory=list)
    confidence: float = 0.8
    applicability: dict[str, Any] = field(default_factory=dict)
    status: ResearchMemoryStatus = ResearchMemoryStatus.CANDIDATE
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    related_memory_ids: list[str] = field(default_factory=list)

    @classmethod
    def knowledge(
        cls,
        scope: ScopeKey,
        memory_id: str,
        title: str,
        content: str,
    ) -> "ResearchMemory":
        """Create a candidate knowledge memory in ``scope``."""

        return cls(
            memory_id=memory_id,
            scope=scope,
            kind=ResearchMemoryKind.KNOWLEDGE,
            title=title,
            content=content,
        )


@dataclass
class EvidenceChunk:
    """A source-local excerpt used as traceable research evidence."""

    chunk_id: str
    scope: ScopeKey
    content: str
    source_ref: str
    locator: str | None = None
    vector_score: float | None = None
    rerank_score: float | None = None
    rerank_reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self) -> None:
        self._validate_scores()
        self._validate_metadata()

    def _validate_scores(self) -> None:
        for name, score in (
            ("vector_score", self.vector_score),
            ("rerank_score", self.rerank_score),
        ):
            if score is None:
                continue
            if isinstance(score, bool) or not isinstance(score, (int, float)):
                raise ValueError(f"{name} must be a JSON number")
            normalized = float(score)
            if not math.isfinite(normalized):
                raise ValueError(f"{name} must be finite")
            setattr(self, name, normalized)

    def _validate_metadata(self) -> None:
        try:
            json.dumps(self.metadata, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise ValueError("metadata must be JSON-compatible") from error

    def to_dict(self) -> dict[str, Any]:
        """Serialize evidence and its retrieval audit fields."""

        self._validate_scores()
        self._validate_metadata()

        return {
            "chunk_id": self.chunk_id,
            "scope": {
                "team_id": self.scope.team_id,
                "project_id": self.scope.project_id,
                "repository": self.scope.repository,
                "branch": self.scope.branch,
                "experiment_environment": self.scope.experiment_environment,
            },
            "content": self.content,
            "source_ref": self.source_ref,
            "locator": self.locator,
            "vector_score": self.vector_score,
            "rerank_score": self.rerank_score,
            "rerank_reason": self.rerank_reason,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(init=False)
class ResearchCase(ResearchMemory):
    """A research case that groups evidence and proposed follow-up work."""

    evidence_chunk_ids: list[str] = field(default_factory=list)
    proposed_actions: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __init__(
        self,
        *,
        memory_id: str,
        scope: ScopeKey,
        title: str,
        content: str,
        source_refs: list[str] | None = None,
        confidence: float = 0.8,
        applicability: dict[str, Any] | None = None,
        status: ResearchMemoryStatus = ResearchMemoryStatus.CANDIDATE,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
        related_memory_ids: list[str] | None = None,
        evidence_chunk_ids: list[str] | None = None,
        proposed_actions: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        now = datetime.now()
        super().__init__(
            memory_id=memory_id,
            scope=scope,
            kind=ResearchMemoryKind.RESEARCH_CASE,
            title=title,
            content=content,
            source_refs=list(source_refs or []),
            confidence=confidence,
            applicability=dict(applicability or {}),
            status=status,
            created_at=created_at or now,
            updated_at=updated_at or now,
            related_memory_ids=list(related_memory_ids or []),
        )
        self.evidence_chunk_ids = list(evidence_chunk_ids or [])
        self.proposed_actions = list(proposed_actions or [])
        self.metadata = dict(metadata or {})

    @property
    def case_id(self) -> str:
        """Deprecated read-only alias for the canonical memory ID."""

        return self.memory_id

    @property
    def summary(self) -> str:
        """Deprecated read-only alias for the canonical content."""

        return self.content


@dataclass
class ApprovalPackage:
    """An auditable approval request for a research case's external effects."""

    package_id: str
    case_memory_id: str
    scope: ScopeKey
    requested_by: str
    payload_hash: str
    risk_level: RiskLevel
    expires_at: datetime
    receipt_token: str
    payload: dict[str, Any] = field(default_factory=dict)
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self) -> None:
        for name in ("package_id", "case_memory_id"):
            _require_non_blank_identifier(name, getattr(self, name))
        _require_non_blank_identifier("requested_by", self.requested_by)
        _require_payload_hash(self.payload_hash)
        _require_receipt_token(self.receipt_token)
        if not isinstance(self.expires_at, datetime):
            raise ValueError("expires_at must be a datetime")
        if not isinstance(self.risk_level, RiskLevel):
            raise ValueError("risk_level must be a RiskLevel")
        if not isinstance(self.status, ApprovalStatus):
            raise ValueError("status must be an ApprovalStatus")


@dataclass
class VerificationRun:
    """A verification receipt recorded after an external executor has run."""

    run_id: str
    case_memory_id: str
    scope: ScopeKey
    package_id: str
    payload_hash: str
    receipt_id: str
    event_key: str
    receipt: dict[str, Any] = field(default_factory=dict)
    status: VerificationStatus = VerificationStatus.PENDING
    created_at: datetime = field(default_factory=datetime.now)
    verified_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("run_id", "case_memory_id", "package_id", "receipt_id"):
            _require_non_blank_identifier(name, getattr(self, name))
        _require_payload_hash(self.payload_hash)
        _require_payload_hash(self.event_key)
        if not isinstance(self.status, VerificationStatus):
            raise ValueError("status must be a VerificationStatus")
