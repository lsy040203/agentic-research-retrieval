from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.constants import ApprovalDecision, ApprovalStatus, ResearchMemoryKind, RiskLevel
from core.research_models import ResearchMemory, ScopeKey
from memory.research_store import ResearchStore
from policy.approval_service import (
    ApprovalService,
    ApprovalStateError,
    ApprovalValidationError,
)


def _scope(branch: str = "main") -> ScopeKey:
    return ScopeKey("team-a", "project-a", "org/repo", branch, "staging")


def _save_case(store: ResearchStore, scope: ScopeKey, case_id: str = "case-1") -> None:
    store.save(ResearchMemory(case_id, scope, ResearchMemoryKind.RESEARCH_CASE, "Case", "Content"))


def _service(store: ResearchStore, now: list[datetime]) -> ApprovalService:
    return ApprovalService(
        store,
        clock=lambda: now[0],
        id_factory=lambda: "package-1",
        token_factory=lambda: "a" * 64,
    )


def test_create_package_hashes_canonical_payload_and_expires_after_24_hours(tmp_path) -> None:
    now = [datetime(2026, 7, 29, tzinfo=timezone.utc)]
    store = ResearchStore(tmp_path / "research.db")
    _save_case(store, _scope())

    package = _service(store, now).create_package(
        _scope(), "case-1", "alice", RiskLevel.HIGH, {"b": 2, "a": ["patch"]}
    )

    assert package.status is ApprovalStatus.PENDING
    assert package.payload_hash == "19ec25a137245c2118916a8fa8e0682b57ccc03e6a4e9fbc5d1dfea1d7e3532c"
    assert package.receipt_token == "a" * 64
    assert package.expires_at == now[0] + timedelta(hours=24)


def test_create_package_requires_an_existing_research_case_in_the_same_scope(tmp_path) -> None:
    now = [datetime(2026, 7, 29, tzinfo=timezone.utc)]
    store = ResearchStore(tmp_path / "research.db")
    _save_case(store, _scope("release"))

    with pytest.raises(ApprovalValidationError, match="research case"):
        _service(store, now).create_package(_scope(), "case-1", "alice", RiskLevel.LOW, {})


def test_decide_rejects_self_approval_and_a_duplicate_decision(tmp_path) -> None:
    now = [datetime(2026, 7, 29, tzinfo=timezone.utc)]
    store = ResearchStore(tmp_path / "research.db")
    _save_case(store, _scope())
    service = _service(store, now)
    package = service.create_package(_scope(), "case-1", "alice", RiskLevel.HIGH, {})

    with pytest.raises(ApprovalValidationError, match="cannot approve own request"):
        service.decide(package.package_id, package.scope, "alice", ApprovalDecision.APPROVED, "no")

    assert service.decide(package.package_id, package.scope, "bob", ApprovalDecision.APPROVED, "reviewed").status is ApprovalStatus.APPROVED
    with pytest.raises(ApprovalStateError, match="not pending"):
        service.decide(package.package_id, package.scope, "carol", ApprovalDecision.REJECTED, "late")


def test_get_and_decide_lazily_expire_pending_packages(tmp_path) -> None:
    now = [datetime(2026, 7, 29, tzinfo=timezone.utc)]
    store = ResearchStore(tmp_path / "research.db")
    _save_case(store, _scope())
    service = _service(store, now)
    package = service.create_package(_scope(), "case-1", "alice", RiskLevel.HIGH, {})

    now[0] += timedelta(hours=24)

    result = service.get_package(package.package_id, package.scope)
    assert result is not None
    assert result.status is ApprovalStatus.EXPIRED
    with pytest.raises(ApprovalStateError, match="expired"):
        service.decide(package.package_id, package.scope, "bob", ApprovalDecision.APPROVED, "late")


def test_decide_maps_an_atomic_expiry_at_the_finalization_boundary(tmp_path) -> None:
    started_at = datetime(2026, 7, 29, tzinfo=timezone.utc)
    expires_at = started_at + timedelta(hours=24)
    clock_values = iter((started_at, expires_at - timedelta(microseconds=1), expires_at))
    store = ResearchStore(tmp_path / "research.db")
    _save_case(store, _scope())
    service = ApprovalService(
        store,
        clock=lambda: next(clock_values),
        id_factory=lambda: "package-1",
        token_factory=lambda: "a" * 64,
    )
    package = service.create_package(_scope(), "case-1", "alice", RiskLevel.HIGH, {})

    with pytest.raises(ApprovalStateError, match="expired"):
        service.decide(package.package_id, package.scope, "bob", ApprovalDecision.APPROVED, "late")
