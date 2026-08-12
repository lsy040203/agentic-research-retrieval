"""Contract tests for the ARR research-memory domain models."""

from datetime import datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
import sys

import pytest

from core.constants import (
    ApprovalDecision,
    ApprovalStatus,
    RiskLevel,
    ResearchMemoryKind,
    ResearchMemoryStatus,
    VerificationStatus,
)
from core.research_models import (
    ApprovalPackage,
    EvidenceChunk,
    ResearchCase,
    ResearchMemory,
    ScopeKey,
    VerificationRun,
    derive_receipt_event_key,
)


def test_scope_key_keeps_research_isolation_dimensions() -> None:
    scope = ScopeKey(
        team_id="team-1",
        project_id="project-1",
        repository="org/research-repo",
        branch="experiment/a",
        experiment_environment="gpu-a100",
    )

    assert scope.team_id == "team-1"
    assert scope.project_id == "project-1"
    assert scope.repository == "org/research-repo"
    assert scope.branch == "experiment/a"
    assert scope.experiment_environment == "gpu-a100"


def test_scope_key_is_immutable() -> None:
    scope = ScopeKey("team-1", "project-1", "org/research-repo", "main", "local")

    with pytest.raises(AttributeError):
        scope.branch = "other"  # type: ignore[misc]


def test_scope_key_normalizes_each_dimension_and_rejects_invalid_values() -> None:
    scope = ScopeKey(" team-1 ", " project-1 ", " org/repo ", " main ", " local ")

    assert scope == ScopeKey("team-1", "project-1", "org/repo", "main", "local")
    dimensions = ("team_id", "project_id", "repository", "branch", "experiment_environment")
    for dimension in dimensions:
        values = dict(
            team_id="team-1",
            project_id="project-1",
            repository="org/repo",
            branch="main",
            experiment_environment="local",
        )
        values[dimension] = "   "
        with pytest.raises(ValueError):
            ScopeKey(**values)
        values[dimension] = 1
        with pytest.raises(TypeError):
            ScopeKey(**values)


def test_research_memory_enums_expose_arr_values() -> None:
    assert {item.value for item in ResearchMemoryKind} == {
        "knowledge",
        "literature",
        "experiment",
        "workflow",
        "preference",
        "research_case",
    }
    assert {item.value for item in ResearchMemoryStatus} == {
        "candidate",
        "verified",
        "published",
        "deprecated",
        "revoked",
        "conflict",
        "expired",
    }


def test_knowledge_factory_builds_a_candidate_knowledge_memory() -> None:
    scope = ScopeKey("team-1", "project-1", "org/research-repo", "main", "local")

    memory = ResearchMemory.knowledge(
        scope=scope,
        memory_id="research-1",
        title="Retrieval evaluation protocol",
        content="Use nDCG@10 and record the corpus revision.",
    )

    assert memory.memory_id == "research-1"
    assert memory.scope is scope
    assert memory.kind is ResearchMemoryKind.KNOWLEDGE
    assert memory.title == "Retrieval evaluation protocol"
    assert memory.content == "Use nDCG@10 and record the corpus revision."
    assert memory.status is ResearchMemoryStatus.CANDIDATE
    assert memory.source_refs == []
    assert memory.applicability == {}
    assert memory.related_memory_ids == []
    assert memory.created_at <= memory.updated_at


def test_mutable_defaults_are_not_shared_between_research_objects() -> None:
    scope = ScopeKey("team-1", "project-1", "org/research-repo", "main", "local")
    first = ResearchMemory.knowledge(scope, "research-1", "First", "First content")
    second = ResearchMemory.knowledge(scope, "research-2", "Second", "Second content")
    evidence_a = EvidenceChunk("evidence-1", scope, "First excerpt", "paper.pdf")
    evidence_b = EvidenceChunk("evidence-2", scope, "Second excerpt", "paper.pdf")
    case_a = ResearchCase(memory_id="case-1", scope=scope, title="Case A", content="Summary")
    case_b = ResearchCase(memory_id="case-2", scope=scope, title="Case B", content="Summary")
    approval_a = ApprovalPackage("approval-1", "case-1", scope, "reviewer", payload_hash="a" * 64, risk_level=RiskLevel.LOW, expires_at=datetime.now() + timedelta(days=1), receipt_token="1" * 64)
    approval_b = ApprovalPackage("approval-2", "case-2", scope, "reviewer", payload_hash="b" * 64, risk_level=RiskLevel.LOW, expires_at=datetime.now() + timedelta(days=1), receipt_token="2" * 64)
    run_a = VerificationRun("run-1", "case-1", scope, package_id="approval-1", payload_hash="a" * 64, receipt_id="receipt-1", event_key=derive_receipt_event_key("approval-1", "1" * 64, "receipt-1"))
    run_b = VerificationRun("run-2", "case-2", scope, package_id="approval-2", payload_hash="b" * 64, receipt_id="receipt-2", event_key=derive_receipt_event_key("approval-2", "2" * 64, "receipt-2"))

    first.source_refs.append("source-1")
    first.applicability["language"] = "en"
    first.related_memory_ids.append("related-1")
    evidence_a.metadata["page"] = 1
    case_a.evidence_chunk_ids.append("evidence-1")
    case_a.related_memory_ids.append("research-1")
    case_a.proposed_actions.append("rerun")
    case_a.metadata["priority"] = "high"
    approval_a.payload["risk"] = "low"
    run_a.receipt["passed"] = True

    assert second.source_refs == []
    assert second.applicability == {}
    assert second.related_memory_ids == []
    assert evidence_b.metadata == {}
    assert case_b.evidence_chunk_ids == []
    assert case_b.related_memory_ids == []
    assert case_b.proposed_actions == []
    assert case_b.metadata == {}
    assert approval_b.payload == {}
    assert run_b.receipt == {}


def test_research_supporting_models_capture_scope_and_audit_fields() -> None:
    scope = ScopeKey("team-1", "project-1", "org/research-repo", "main", "gpu-a100")
    created_at = datetime.now()
    expires_at = created_at + timedelta(days=1)

    evidence = EvidenceChunk(
        chunk_id="evidence-1",
        scope=scope,
        content="The result was independently reproduced.",
        source_ref="doi:10.1/example",
        locator="section 4",
        vector_score=0.81,
        rerank_score=0.93,
        rerank_reason="Matches the experiment protocol.",
    )
    case = ResearchCase(
        memory_id="case-memory-1",
        scope=scope,
        title="Reproduce retrieval result",
        content="Rerun the evaluation with the documented corpus.",
        evidence_chunk_ids=[evidence.chunk_id],
        related_memory_ids=["research-1"],
    )
    approval = ApprovalPackage(
        package_id="approval-1",
        case_memory_id=case.memory_id,
        scope=scope,
        requested_by="researcher",
        payload_hash="a" * 64,
        risk_level=RiskLevel.LOW,
        payload={"plan": "rerun"},
        expires_at=expires_at,
        receipt_token="1" * 64,
    )
    run = VerificationRun(
        run_id="run-1",
        case_memory_id=case.memory_id,
        scope=scope,
        package_id="approval-1",
        payload_hash="a" * 64,
        receipt_id="receipt-1",
        event_key=derive_receipt_event_key("approval-1", "1" * 64, "receipt-1"),
        receipt={"result": "passed"},
        verified_at=created_at,
    )

    assert evidence.locator == "section 4"
    evidence_audit = evidence.to_dict()
    assert evidence_audit["vector_score"] == 0.81
    assert evidence_audit["rerank_score"] == 0.93
    assert evidence_audit["rerank_reason"] == "Matches the experiment protocol."
    assert json.loads(json.dumps(evidence_audit))["rerank_score"] == 0.93
    assert isinstance(case, ResearchMemory)
    assert case.memory_id == "case-memory-1"
    assert case.case_id == "case-memory-1"
    assert case.summary == case.content
    assert case.kind is ResearchMemoryKind.RESEARCH_CASE
    assert case.content == "Rerun the evaluation with the documented corpus."
    assert case.evidence_chunk_ids == ["evidence-1"]
    assert case.related_memory_ids == ["research-1"]
    assert approval.expires_at == expires_at
    assert approval.payload == {"plan": "rerun"}
    assert run.receipt == {"result": "passed"}
    assert run.verified_at == created_at
    assert approval.status is ApprovalStatus.PENDING
    assert run.status is VerificationStatus.PENDING


def test_research_case_accepts_only_canonical_identity_and_content_inputs() -> None:
    scope = ScopeKey("team-1", "project-1", "org/research-repo", "main", "local")
    case = ResearchCase(
        memory_id="case-memory-1",
        scope=scope,
        title="Canonical case",
        content="Canonical content",
    )

    assert case.case_id == case.memory_id
    assert case.summary == case.content
    with pytest.raises(AttributeError):
        case.case_id = "other-memory"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        case.summary = "other-content"  # type: ignore[misc]
    with pytest.raises(TypeError):
        ResearchCase(
            memory_id="case-memory-1",
            case_id="case-memory-1",
            scope=scope,
            title="Canonical case",
            content="Canonical content",
        )
    with pytest.raises(TypeError):
        ResearchCase(
            memory_id="case-memory-1",
            scope=scope,
            title="Canonical case",
            content="Canonical content",
            summary="conflicting content",
        )


@pytest.mark.parametrize(
    ("field_name", "score"),
    [("vector_score", float("nan")), ("vector_score", float("inf")), ("rerank_score", float("-inf"))],
)
def test_evidence_chunk_rejects_non_finite_scores(field_name: str, score: float) -> None:
    scope = ScopeKey("team-1", "project-1", "org/research-repo", "main", "local")

    with pytest.raises(ValueError):
        EvidenceChunk("evidence-1", scope, "excerpt", "paper.pdf", **{field_name: score})


@pytest.mark.parametrize("score", [True, Decimal("0.5"), "0.5"])
def test_evidence_chunk_accepts_only_json_number_scores(score: object) -> None:
    scope = ScopeKey("team-1", "project-1", "org/research-repo", "main", "local")

    with pytest.raises(ValueError):
        EvidenceChunk("evidence-1", scope, "excerpt", "paper.pdf", vector_score=score)  # type: ignore[arg-type]

    evidence = EvidenceChunk(
        "evidence-2",
        scope,
        "excerpt",
        "paper.pdf",
        vector_score=1,
        rerank_score=0.5,
    )
    audit = evidence.to_dict()
    assert type(audit["vector_score"]) is float
    assert type(audit["rerank_score"]) is float
    json.dumps(audit, allow_nan=False)


def test_evidence_chunk_rejects_non_json_metadata_and_keeps_json_safe_audit() -> None:
    scope = ScopeKey("team-1", "project-1", "org/research-repo", "main", "local")

    with pytest.raises(ValueError):
        EvidenceChunk("evidence-1", scope, "excerpt", "paper.pdf", metadata={"nested": [float("nan")]})
    with pytest.raises(ValueError):
        EvidenceChunk("evidence-2", scope, "excerpt", "paper.pdf", metadata={"nested": object()})

    evidence = EvidenceChunk(
        "evidence-3",
        scope,
        "excerpt",
        "paper.pdf",
        metadata={"nested": {"pages": [1, 2]}},
    )
    json.dumps(evidence.to_dict(), allow_nan=False)
    evidence.metadata["later_invalid"] = object()
    with pytest.raises(ValueError):
        evidence.to_dict()


def test_approval_and_verification_use_dedicated_statuses_and_memory_references() -> None:
    scope = ScopeKey("team-1", "project-1", "org/research-repo", "main", "local")
    approval = ApprovalPackage(
        package_id="approval-1",
        case_memory_id="case-memory-1",
        scope=scope,
        requested_by="reviewer",
        payload_hash="a" * 64,
        risk_level=RiskLevel.LOW,
        expires_at=datetime.now() + timedelta(days=1),
        receipt_token="1" * 64,
        status=ApprovalStatus.APPROVED,
    )
    run = VerificationRun(
        run_id="run-1",
        case_memory_id="case-memory-1",
        scope=scope,
        package_id="approval-1",
        payload_hash="a" * 64,
        receipt_id="receipt-1",
        event_key=derive_receipt_event_key("approval-1", "1" * 64, "receipt-1"),
        status=VerificationStatus.PASSED,
    )

    assert approval.case_memory_id == "case-memory-1"
    assert approval.status is ApprovalStatus.APPROVED
    assert run.case_memory_id == "case-memory-1"
    assert run.status is VerificationStatus.PASSED
    assert {status.value for status in ApprovalStatus} == {"pending", "approved", "rejected", "expired"}
    assert {status.value for status in VerificationStatus} == {"pending", "passed", "failed", "blocked"}


def test_d1_approval_contract_freezes_payload_hash_and_links_verification_receipt() -> None:
    scope = ScopeKey("team-1", "project-1", "org/research-repo", "main", "local")
    payload_hash = "a" * 64
    expires_at = datetime.now() + timedelta(days=1)

    approval = ApprovalPackage(
        package_id="approval-1",
        case_memory_id="case-memory-1",
        scope=scope,
        requested_by="researcher",
        payload_hash=payload_hash,
        risk_level=RiskLevel.HIGH,
        expires_at=expires_at,
        receipt_token="b" * 64,
    )
    verification = VerificationRun(
        run_id="run-1",
        case_memory_id="case-memory-1",
        scope=scope,
        package_id=approval.package_id,
        payload_hash=approval.payload_hash,
        receipt_id="receipt-1",
        event_key=derive_receipt_event_key(approval.package_id, approval.receipt_token, "receipt-1"),
    )

    assert approval.payload_hash == payload_hash
    assert approval.receipt_token == "b" * 64
    assert approval.risk_level is RiskLevel.HIGH
    assert verification.package_id == approval.package_id
    assert verification.payload_hash == approval.payload_hash
    assert verification.receipt_id == "receipt-1"
    assert verification.event_key == derive_receipt_event_key(
        approval.package_id, approval.receipt_token, verification.receipt_id
    )
    assert {level.value for level in RiskLevel} == {"low", "medium", "high"}
    assert {decision.value for decision in ApprovalDecision} == {"approved", "rejected"}


def test_d1_receipt_event_key_derivation_is_stable_and_input_sensitive() -> None:
    token = "b" * 64

    event_key = derive_receipt_event_key("approval-1", token, "receipt-1")

    assert event_key == derive_receipt_event_key("approval-1", token, "receipt-1")
    assert event_key != derive_receipt_event_key("approval-1", token, "receipt-2")
    assert len(event_key) == 64
    assert event_key.isascii() and event_key.islower()


@pytest.mark.parametrize(
    "factory",
    [
        lambda scope: ApprovalPackage("", "case-1", scope, "researcher", payload_hash="a" * 64, risk_level=RiskLevel.LOW, expires_at=datetime.now() + timedelta(days=1), receipt_token="1" * 64),
        lambda scope: VerificationRun("", "case-1", scope, package_id="approval-1", payload_hash="a" * 64, receipt_id="receipt-1", event_key="a" * 64),
        lambda scope: VerificationRun("run-1", "case-1", scope, package_id="", payload_hash="a" * 64, receipt_id="receipt-1", event_key="a" * 64),
        lambda scope: ApprovalPackage("approval-1", "case-1", scope, "researcher", payload_hash="A" * 64, risk_level=RiskLevel.LOW, expires_at=datetime.now() + timedelta(days=1), receipt_token="1" * 64),
        lambda scope: VerificationRun("run-1", "case-1", scope, package_id="approval-1", payload_hash="g" * 64, receipt_id="receipt-1", event_key="a" * 64),
        lambda scope: VerificationRun("run-1", "case-1", scope, package_id="approval-1", payload_hash="a" * 64, receipt_id="receipt-1", event_key="   "),
        lambda scope: ApprovalPackage("approval-1", "case-1", scope, "researcher", payload_hash="a" * 64, risk_level=RiskLevel.LOW, expires_at=datetime.now() + timedelta(days=1), receipt_token="A" * 64),
        lambda scope: VerificationRun("run-1", "case-1", scope, package_id="approval-1", payload_hash="a" * 64, receipt_id="   ", event_key="a" * 64),
    ],
)
def test_d1_models_reject_blank_ids_invalid_hashes_and_blank_event_keys(factory: object) -> None:
    scope = ScopeKey("team-1", "project-1", "org/research-repo", "main", "local")

    with pytest.raises(ValueError):
        factory(scope)  # type: ignore[operator]


def test_d1_models_require_explicit_frozen_linkage_fields() -> None:
    scope = ScopeKey("team-1", "project-1", "org/research-repo", "main", "local")

    with pytest.raises(TypeError):
        ApprovalPackage("approval-1", "case-1", scope, "researcher")
    with pytest.raises(TypeError):
        VerificationRun("run-1", "case-1", scope)


@pytest.mark.parametrize(
    "factory",
    [
        lambda scope: ApprovalPackage("approval-1", "case-1", scope, "   ", payload_hash="a" * 64, risk_level=RiskLevel.LOW, expires_at=datetime.now() + timedelta(days=1), receipt_token="1" * 64),
        lambda scope: ApprovalPackage("approval-1", "case-1", scope, "researcher", payload_hash="a" * 64, risk_level="low", expires_at=datetime.now() + timedelta(days=1), receipt_token="1" * 64),
        lambda scope: ApprovalPackage("approval-1", "case-1", scope, "researcher", payload_hash="a" * 64, risk_level=RiskLevel.LOW, expires_at=datetime.now() + timedelta(days=1), receipt_token="1" * 64, status="approved"),
        lambda scope: VerificationRun("run-1", "case-1", scope, package_id="approval-1", payload_hash="a" * 64, receipt_id="receipt-1", event_key="a" * 64, status="passed"),
    ],
)
def test_d1_models_reject_blank_requester_and_non_enum_values(factory: object) -> None:
    scope = ScopeKey("team-1", "project-1", "org/research-repo", "main", "local")

    with pytest.raises(ValueError):
        factory(scope)  # type: ignore[operator]


def test_d1_schemas_reject_blank_actors_and_pending_verification_receipts() -> None:
    workspace_parent = str(Path(__file__).resolve().parents[2])
    if workspace_parent not in sys.path:
        sys.path.insert(0, workspace_parent)

    from pydantic import ValidationError
    from os_agent_memory.api.schemas import (
        ApprovalDecisionResponse,
        ApprovalDecisionRequest,
        ApprovalPackageResponse,
        CreateApprovalRequest,
        CreateVerificationRequest,
        ResearchScopeSchema,
        VerificationRunResponse,
    )

    scope = ResearchScopeSchema(
        team_id="team-1",
        project_id="project-1",
        repository="org/research-repo",
        branch="main",
        experiment_environment="local",
    )
    approval = CreateApprovalRequest(
        scope=scope,
        case_memory_id=" case-1 ",
        requester_id=" researcher ",
        payload={"action": "publish"},
        payload_hash="a" * 64,
        risk_level="medium",
        expires_at=datetime.now() + timedelta(days=1),
    )

    assert approval.requester_id == "researcher"
    assert approval.case_memory_id == "case-1"
    assert ApprovalDecisionRequest(
        package_id=" approval-1 ", approver_id=" reviewer ", decision="approved"
    ).package_id == "approval-1"
    with pytest.raises(ValidationError):
        ApprovalDecisionRequest(package_id="approval-1", approver_id="   ", decision="approved")
    with pytest.raises(ValidationError):
        ApprovalDecisionRequest(package_id="   ", approver_id="reviewer", decision="approved")
    verification = CreateVerificationRequest(
        scope=scope,
        case_memory_id=" case-1 ",
        package_id=" approval-1 ",
        payload_hash="a" * 64,
        receipt_token="b" * 64,
        receipt_id=" receipt-1 ",
        status="passed",
    )
    assert verification.case_memory_id == "case-1"
    assert verification.package_id == "approval-1"
    assert verification.receipt_id == "receipt-1"
    with pytest.raises(ValidationError):
        CreateVerificationRequest(
            scope=scope,
            case_memory_id="case-1",
            package_id="approval-1",
            payload_hash="a" * 64,
            receipt_token="b" * 64,
            receipt_id="receipt-1",
            status="pending",
        )
    with pytest.raises(ValidationError):
        CreateVerificationRequest(
            scope=scope,
            case_memory_id="   ",
            package_id="approval-1",
            payload_hash="a" * 64,
            receipt_token="b" * 64,
            receipt_id="receipt-1",
            status="passed",
        )
    with pytest.raises(ValidationError):
        CreateVerificationRequest(
            scope=scope,
            case_memory_id="case-1",
            package_id="approval-1",
            payload_hash="a" * 64,
            receipt_token="b" * 64,
            receipt_id="receipt-1",
            event_key="external-key",
            status="passed",
        )
    with pytest.raises(ValidationError):
        CreateVerificationRequest(
            scope=scope,
            case_memory_id="case-1",
            package_id="approval-1",
            payload_hash="a" * 64,
            receipt_token="B" * 64,
            receipt_id="receipt-1",
            status="passed",
        )
    with pytest.raises(ValidationError):
        VerificationRunResponse(
            run_id="run-1",
            case_memory_id="case-1",
            package_id="approval-1",
            payload_hash="a" * 64,
            receipt_id="receipt-1",
            event_key="a" * 64,
            status="pending",
            created_at=datetime.now(),
        )
    response_factories = (
        lambda: ApprovalPackageResponse(
            package_id="   ", case_memory_id="case-1", scope=scope, requester_id="researcher",
            payload_hash="a" * 64, receipt_token="b" * 64, risk_level="low", status="pending",
            created_at=datetime.now(), expires_at=datetime.now() + timedelta(days=1),
        ),
        lambda: ApprovalPackageResponse(
            package_id="approval-1", case_memory_id="   ", scope=scope, requester_id="researcher",
            payload_hash="a" * 64, receipt_token="b" * 64, risk_level="low", status="pending",
            created_at=datetime.now(), expires_at=datetime.now() + timedelta(days=1),
        ),
        lambda: ApprovalPackageResponse(
            package_id="approval-1", case_memory_id="case-1", scope=scope, requester_id="   ",
            payload_hash="a" * 64, receipt_token="b" * 64, risk_level="low", status="pending",
            created_at=datetime.now(), expires_at=datetime.now() + timedelta(days=1),
        ),
        lambda: ApprovalDecisionResponse(
            package_id="   ", approver_id="reviewer", decision="approved", decided_at=datetime.now()
        ),
        lambda: ApprovalDecisionResponse(
            package_id="approval-1", approver_id="   ", decision="approved", decided_at=datetime.now()
        ),
        lambda: VerificationRunResponse(
            run_id="   ", case_memory_id="case-1", package_id="approval-1", payload_hash="a" * 64,
            receipt_id="receipt-1", event_key="a" * 64, status="passed", created_at=datetime.now(),
        ),
        lambda: VerificationRunResponse(
            run_id="run-1", case_memory_id="   ", package_id="approval-1", payload_hash="a" * 64,
            receipt_id="receipt-1", event_key="a" * 64, status="passed", created_at=datetime.now(),
        ),
        lambda: VerificationRunResponse(
            run_id="run-1", case_memory_id="case-1", package_id="   ", payload_hash="a" * 64,
            receipt_id="receipt-1", event_key="a" * 64, status="passed", created_at=datetime.now(),
        ),
        lambda: VerificationRunResponse(
            run_id="run-1", case_memory_id="case-1", package_id="approval-1", payload_hash="a" * 64,
            receipt_id="receipt-1", event_key="   ", status="passed", created_at=datetime.now(),
        ),
    )
    for factory in response_factories:
        with pytest.raises(ValidationError):
            factory()


def test_research_schemas_validate_create_read_and_status_contracts() -> None:
    workspace_parent = str(Path(__file__).resolve().parents[2])
    if workspace_parent not in sys.path:
        sys.path.insert(0, workspace_parent)

    from pydantic import ValidationError
    from os_agent_memory.api.schemas import (
        CreateResearchMemoryRequest,
        ResearchMemoryResponse,
        ResearchScopeSchema,
        UpdateResearchMemoryStatusRequest,
    )

    scope = ResearchScopeSchema(
        team_id=" team-1 ",
        project_id=" project-1 ",
        repository=" org/research-repo ",
        branch=" main ",
        experiment_environment=" local ",
    )
    request = CreateResearchMemoryRequest(
        scope=scope,
        kind="knowledge",
        title="Protocol",
        content="Document the corpus revision.",
        confidence=0.9,
    )
    response = ResearchMemoryResponse(
        memory_id="research-1",
        scope=scope,
        kind="knowledge",
        title=request.title,
        content=request.content,
        confidence=request.confidence,
        status="candidate",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )

    assert scope.model_dump() == {
        "team_id": "team-1",
        "project_id": "project-1",
        "repository": "org/research-repo",
        "branch": "main",
        "experiment_environment": "local",
    }
    assert request.source_refs == []
    assert response.memory_id == "research-1"
    assert UpdateResearchMemoryStatusRequest(status="verified").status.value == "verified"
    with pytest.raises(ValidationError):
        CreateResearchMemoryRequest(
            scope=scope,
            kind="knowledge",
            title="Protocol",
            content="Document the corpus revision.",
            confidence=1.1,
        )
    with pytest.raises(ValidationError):
        ResearchScopeSchema(
            team_id="   ",
            project_id="   ",
            repository="   ",
            branch="   ",
            experiment_environment="   ",
        )


def test_research_memory_keeps_explicit_traceability_and_lifecycle_fields() -> None:
    scope = ScopeKey("team-1", "project-1", "org/research-repo", "main", "local")

    memory = ResearchMemory(
        memory_id="experiment-1",
        scope=scope,
        kind=ResearchMemoryKind.EXPERIMENT,
        title="Embedding experiment",
        content="The multilingual model improved recall.",
        source_refs=["doi:10.1/example", "run:42"],
        confidence=0.92,
        applicability={"languages": ["zh", "en"]},
        status=ResearchMemoryStatus.VERIFIED,
        related_memory_ids=["literature-1"],
    )

    assert memory.source_refs == ["doi:10.1/example", "run:42"]
    assert memory.confidence == 0.92
    assert memory.applicability == {"languages": ["zh", "en"]}
    assert memory.status is ResearchMemoryStatus.VERIFIED
    assert memory.related_memory_ids == ["literature-1"]
    assert memory.created_at <= memory.updated_at
