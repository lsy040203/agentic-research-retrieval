"""Stateful approval policy over the persistence-only research store."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import hashlib
import json
import secrets
from typing import Any
from uuid import uuid4

from core.constants import ApprovalDecision, ApprovalStatus, RiskLevel
from core.research_models import ApprovalPackage, ScopeKey
from memory.research_store import ResearchStore


class ApprovalValidationError(ValueError):
    """The caller supplied an invalid approval request or transition."""


class ApprovalStateError(RuntimeError):
    """An approval package is absent, expired, or in the wrong state."""


class ApprovalService:
    """Create and authorize approval package state transitions."""

    def __init__(
        self,
        store: ResearchStore,
        *,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        self.store = store
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.id_factory = id_factory or (lambda: uuid4().hex)
        self.token_factory = token_factory or (lambda: secrets.token_hex(32))

    def create_package(
        self,
        scope: ScopeKey,
        case_memory_id: str,
        requester_id: str,
        risk_level: RiskLevel,
        payload: dict[str, Any],
    ) -> ApprovalPackage:
        if not isinstance(payload, dict):
            raise ApprovalValidationError("payload must be an object")
        self._require_identifier("case_memory_id", case_memory_id)
        self._require_identifier("requester_id", requester_id)
        if not isinstance(risk_level, RiskLevel):
            raise ApprovalValidationError("risk_level is invalid")
        try:
            canonical_payload = json.dumps(
                payload, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
        except (TypeError, ValueError) as error:
            raise ApprovalValidationError("payload must be JSON-compatible") from error
        now = self.clock()
        package = ApprovalPackage(
            package_id=self.id_factory(),
            case_memory_id=case_memory_id,
            scope=scope,
            requested_by=requester_id,
            payload_hash=hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest(),
            risk_level=risk_level,
            expires_at=now + timedelta(hours=24),
            receipt_token=self.token_factory(),
            payload=payload,
            created_at=now,
        )
        try:
            return self.store.save_approval_package(package)
        except ValueError as error:
            if "in-scope research_case" in str(error):
                raise ApprovalValidationError(
                    "research case must exist in the same scope"
                ) from error
            raise ApprovalValidationError(str(error)) from error

    def get_package(self, package_id: str, scope: ScopeKey) -> ApprovalPackage | None:
        package = self.store.get_approval_package(package_id, scope)
        if package is not None and package.status is ApprovalStatus.PENDING:
            expired = self.store.expire_pending_approval(package_id, scope, self.clock())
            if expired is not None:
                return expired
        return package

    def decide(
        self,
        package_id: str,
        scope: ScopeKey,
        approver_id: str,
        decision: ApprovalDecision,
        reason: str | None,
    ) -> ApprovalPackage:
        self._require_identifier("approver_id", approver_id)
        if not isinstance(decision, ApprovalDecision):
            raise ApprovalValidationError("decision must be approved or rejected")
        package = self.get_package(package_id, scope)
        if package is None:
            raise ApprovalStateError("approval package does not exist")
        if package.status is ApprovalStatus.EXPIRED:
            raise ApprovalStateError("approval package is expired")
        if package.status is not ApprovalStatus.PENDING:
            raise ApprovalStateError("approval package is not pending")
        if approver_id == package.requested_by:
            raise ApprovalValidationError("approver cannot approve own request")
        try:
            return self.store.finalize_approval_decision(
                package_id, scope, decision, approver_id, reason, self.clock()
            )
        except ValueError as error:
            raise ApprovalStateError(str(error)) from error

    @staticmethod
    def _require_identifier(name: str, value: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ApprovalValidationError(f"{name} must not be blank")
