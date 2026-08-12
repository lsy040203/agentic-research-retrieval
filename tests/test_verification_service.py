from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.constants import ApprovalDecision, ResearchMemoryKind, RiskLevel, VerificationStatus
from core.research_models import ResearchMemory, ScopeKey
from memory.research_store import ResearchStore
from policy.approval_service import ApprovalService
from policy.verification_service import (
    VerificationService,
    VerificationStateError,
    VerificationValidationError,
)


def _scope(branch: str = "main") -> ScopeKey:
    return ScopeKey("team-a", "project-a", "org/repo", branch, "staging")


def _save_case(store: ResearchStore, scope: ScopeKey, case_id: str = "case-1") -> None:
    store.save(ResearchMemory(case_id, scope, ResearchMemoryKind.RESEARCH_CASE, "Case", "Content"))


def _receipt() -> dict[str, object]:
    return {"environment": "staging", "verification_summary": "tests passed", "evidence_refs": ["log://1"]}


def _approved(tmp_path, *, scope: ScopeKey | None = None):
    now = [datetime(2026, 7, 29, tzinfo=timezone.utc)]
    store = ResearchStore(tmp_path / "research.db")
    scope = scope or _scope()
    _save_case(store, scope)
    approvals = ApprovalService(store, clock=lambda: now[0], id_factory=lambda: "package-1", token_factory=lambda: "b" * 64)
    package = approvals.create_package(scope, "case-1", "alice", RiskLevel.HIGH, {"action": "patch"})
    package = approvals.decide(package.package_id, scope, "bob", ApprovalDecision.APPROVED, "reviewed")
    return now, store, approvals, package


def test_matching_receipt_is_saved_and_same_receipt_id_is_idempotent(tmp_path) -> None:
    _, store, approvals, package = _approved(tmp_path)
    service = VerificationService(store, approvals, id_factory=lambda: "run-1")

    run = service.record_receipt(package.scope, package.package_id, "case-1", package.payload_hash, package.receipt_token, "receipt-1", VerificationStatus.PASSED, _receipt())
    retried = service.record_receipt(package.scope, package.package_id, "case-1", package.payload_hash, package.receipt_token, "receipt-1", VerificationStatus.PASSED, _receipt())

    assert retried == run
    assert store.get_verification_run(run.run_id, package.scope) == run


def test_saved_receipt_retry_is_returned_even_after_the_package_expires(tmp_path) -> None:
    now, store, approvals, package = _approved(tmp_path)
    service = VerificationService(store, approvals, id_factory=lambda: "run-1")
    run = service.record_receipt(package.scope, package.package_id, "case-1", package.payload_hash, package.receipt_token, "receipt-1", VerificationStatus.PASSED, _receipt())
    now[0] += timedelta(hours=24)

    retried = service.record_receipt(package.scope, package.package_id, "case-1", package.payload_hash, package.receipt_token, "receipt-1", VerificationStatus.PASSED, _receipt())

    assert retried == run


@pytest.mark.parametrize("field,value,match", [
    ("case_memory_id", "case-other", "case"),
    ("payload_hash", "c" * 64, "payload hash"),
    ("receipt_token", "d" * 64, "receipt token"),
])
def test_receipt_must_match_the_approved_package(tmp_path, field, value, match) -> None:
    _, store, approvals, package = _approved(tmp_path)
    service = VerificationService(store, approvals, id_factory=lambda: "run-1")
    values = {"case_memory_id": "case-1", "payload_hash": package.payload_hash, "receipt_token": package.receipt_token}
    values[field] = value

    with pytest.raises(VerificationValidationError, match=match):
        service.record_receipt(package.scope, package.package_id, values["case_memory_id"], values["payload_hash"], values["receipt_token"], "receipt-1", VerificationStatus.PASSED, _receipt())


def test_receipt_requires_evidence_fields(tmp_path) -> None:
    _, store, approvals, package = _approved(tmp_path)
    service = VerificationService(store, approvals)

    with pytest.raises(VerificationValidationError, match="evidence_refs"):
        service.record_receipt(package.scope, package.package_id, "case-1", package.payload_hash, package.receipt_token, "receipt-1", VerificationStatus.PASSED, {"environment": "staging", "verification_summary": "ok", "evidence_refs": []})


@pytest.mark.parametrize(
    "receipt,match",
    [
        ({**_receipt(), "token": "leak"}, "sensitive"),
        ({**_receipt(), "assertions": [{"private_key": "leak"}]}, "sensitive"),
        ({**_receipt(), "log_summary": "x" * 4097}, "too long"),
    ],
)
def test_receipt_rejects_sensitive_unapproved_or_oversized_log_data(tmp_path, receipt, match) -> None:
    _, store, approvals, package = _approved(tmp_path)

    with pytest.raises(VerificationValidationError, match=match):
        VerificationService(store, approvals).record_receipt(
            package.scope,
            package.package_id,
            "case-1",
            package.payload_hash,
            package.receipt_token,
            "receipt-1",
            VerificationStatus.PASSED,
            receipt,
        )


@pytest.mark.parametrize(
    "receipt",
    [
        {**_receipt(), "Api-Key": "leak"},
        {**_receipt(), "assertions": [{"accessKey": "leak"}]},
        {**_receipt(), "verification_summary": "request used Bearer secret-value"},
        {**_receipt(), "log_summary": "API-key: secret-value"},
    ],
)
def test_receipt_rejects_normalized_sensitive_keys_and_credential_patterns(tmp_path, receipt) -> None:
    _, store, approvals, package = _approved(tmp_path)

    with pytest.raises(VerificationValidationError, match="sensitive|credential"):
        VerificationService(store, approvals).record_receipt(
            package.scope,
            package.package_id,
            "case-1",
            package.payload_hash,
            package.receipt_token,
            "receipt-1",
            VerificationStatus.PASSED,
            receipt,
        )


@pytest.mark.parametrize(
    "receipt",
    [
        {**_receipt(), "assertions": [{"detail": "Authorization: Bearer secret-value"}]},
        {**_receipt(), "evidence_refs": [{"url": "https://logs.example.test/run?api_key=secret-value"}]},
    ],
)
def test_receipt_rejects_credentials_in_nested_string_leaves_without_saving_a_run(tmp_path, receipt) -> None:
    _, store, approvals, package = _approved(tmp_path)
    service = VerificationService(store, approvals, id_factory=lambda: "run-1")

    with pytest.raises(VerificationValidationError, match="credential"):
        service.record_receipt(
            package.scope,
            package.package_id,
            "case-1",
            package.payload_hash,
            package.receipt_token,
            "receipt-1",
            VerificationStatus.PASSED,
            receipt,
        )

    assert store.get_verification_run("run-1", package.scope) is None


def test_receipt_allows_safe_nested_string_leaves(tmp_path) -> None:
    _, store, approvals, package = _approved(tmp_path)
    service = VerificationService(store, approvals, id_factory=lambda: "run-1")
    receipt = {
        **_receipt(),
        "assertions": [{"detail": "Authorization header was validated"}],
        "evidence_refs": [{"url": "https://logs.example.test/run?attempt=1"}],
    }

    run = service.record_receipt(
        package.scope,
        package.package_id,
        "case-1",
        package.payload_hash,
        package.receipt_token,
        "receipt-1",
        VerificationStatus.PASSED,
        receipt,
    )

    assert store.get_verification_run(run.run_id, package.scope) == run


def test_rejected_or_expired_package_cannot_accept_a_receipt(tmp_path) -> None:
    now, store, approvals, package = _approved(tmp_path)
    service = VerificationService(store, approvals)
    now[0] += timedelta(hours=24)

    with pytest.raises(VerificationStateError, match="expired"):
        service.record_receipt(package.scope, package.package_id, "case-1", package.payload_hash, package.receipt_token, "receipt-1", VerificationStatus.PASSED, _receipt())


def test_receipt_is_rejected_when_package_is_not_approved(tmp_path) -> None:
    now = [datetime(2026, 7, 29, tzinfo=timezone.utc)]
    store = ResearchStore(tmp_path / "research.db")
    _save_case(store, _scope())
    approvals = ApprovalService(store, clock=lambda: now[0], id_factory=lambda: "package-1", token_factory=lambda: "b" * 64)
    package = approvals.create_package(_scope(), "case-1", "alice", RiskLevel.LOW, {})

    with pytest.raises(VerificationStateError, match="approved"):
        VerificationService(store, approvals).record_receipt(package.scope, package.package_id, "case-1", package.payload_hash, package.receipt_token, "receipt-1", VerificationStatus.PASSED, _receipt())


def test_same_receipt_id_on_different_packages_does_not_collide(tmp_path) -> None:
    _, store, approvals, first = _approved(tmp_path)
    _save_case(store, first.scope, "case-2")
    second = ApprovalService(store, clock=approvals.clock, id_factory=lambda: "package-2", token_factory=lambda: "e" * 64).create_package(first.scope, "case-2", "alice", RiskLevel.LOW, {})
    second = ApprovalService(store, clock=approvals.clock, id_factory=lambda: "ignored", token_factory=lambda: "e" * 64).decide(second.package_id, second.scope, "bob", ApprovalDecision.APPROVED, "ok")
    run_ids = iter(("run-1", "run-2"))
    service = VerificationService(store, approvals, id_factory=lambda: next(run_ids))

    one = service.record_receipt(first.scope, first.package_id, first.case_memory_id, first.payload_hash, first.receipt_token, "receipt-1", VerificationStatus.PASSED, _receipt())
    two = service.record_receipt(second.scope, second.package_id, second.case_memory_id, second.payload_hash, second.receipt_token, "receipt-1", VerificationStatus.PASSED, _receipt())

    assert one.event_key != two.event_key
