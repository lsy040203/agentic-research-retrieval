"""Receipt validation policy; this module never executes external work."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import re
from typing import Any
from uuid import uuid4

from core.constants import VerificationStatus
from core.research_models import VerificationRun, ScopeKey, derive_receipt_event_key
from memory.research_store import ResearchStore
from policy.approval_service import ApprovalService


class VerificationValidationError(ValueError):
    """The supplied receipt cannot be tied to its approval package."""


class VerificationStateError(RuntimeError):
    """The referenced approval package cannot currently accept receipts."""


class VerificationService:
    """Record externally produced receipts after policy validation only."""

    _ALLOWED_RECEIPT_FIELDS = frozenset(
        {"environment", "verification_summary", "evidence_refs", "assertions", "log_summary"}
    )
    _SENSITIVE_KEY_PARTS = (
        "apikey",
        "accesskey",
        "authorization",
        "credential",
        "bearer",
        "token",
        "password",
        "secret",
        "privatekey",
    )
    _CREDENTIAL_PATTERN = re.compile(
        r"\b(?:bearer\s+\S+|(?:api[-_ ]?key|access[-_ ]?key|authorization|credential|token|password|secret|private[-_ ]?key)\s*[:=]\s*\S+)",
        re.IGNORECASE,
    )
    _MAX_LOG_SUMMARY_LENGTH = 4096

    def __init__(
        self,
        store: ResearchStore,
        approvals: ApprovalService,
        *,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.store = store
        self.approvals = approvals
        self.id_factory = id_factory or (lambda: uuid4().hex)

    def record_receipt(
        self,
        scope: ScopeKey,
        package_id: str,
        case_memory_id: str,
        payload_hash: str,
        receipt_token: str,
        receipt_id: str,
        status: VerificationStatus,
        receipt: dict[str, Any],
    ) -> VerificationRun:
        package = self.store.get_approval_package(package_id, scope)
        if package is None:
            raise VerificationStateError("approval package does not exist")
        if case_memory_id != package.case_memory_id:
            raise VerificationValidationError("case memory does not match approval package")
        if payload_hash != package.payload_hash:
            raise VerificationValidationError("payload hash does not match approval package")
        if receipt_token != package.receipt_token:
            raise VerificationValidationError("receipt token does not match approval package")
        if not isinstance(status, VerificationStatus) or status is VerificationStatus.PENDING:
            raise VerificationValidationError("status must be passed, failed, or blocked")
        self._validate_receipt(receipt)
        try:
            event_key = derive_receipt_event_key(package_id, package.receipt_token, receipt_id)
        except ValueError as error:
            raise VerificationValidationError(str(error)) from error
        now = self.approvals.clock()
        run = VerificationRun(
            run_id=self.id_factory(),
            case_memory_id=case_memory_id,
            scope=scope,
            package_id=package_id,
            payload_hash=payload_hash,
            receipt_id=receipt_id,
            event_key=event_key,
            receipt=receipt,
            status=status,
            created_at=now,
            verified_at=now,
        )
        try:
            return self.store.finalize_verification_receipt(run, now)
        except ValueError as error:
            if any(
                message in str(error)
                for message in ("does not exist", "not approved", "has expired")
            ):
                raise VerificationStateError(str(error)) from error
            raise VerificationValidationError(str(error)) from error

    def get_run(self, run_id: str, scope: ScopeKey) -> VerificationRun | None:
        return self.store.get_verification_run(run_id, scope)

    @staticmethod
    def _validate_receipt(receipt: dict[str, Any]) -> None:
        if not isinstance(receipt, dict):
            raise VerificationValidationError("receipt must be an object")
        VerificationService._reject_sensitive_keys(receipt)
        disallowed_fields = set(receipt) - VerificationService._ALLOWED_RECEIPT_FIELDS
        if disallowed_fields:
            raise VerificationValidationError("receipt fields are not allowed")
        for field in ("environment", "verification_summary", "evidence_refs"):
            value = receipt.get(field)
            if isinstance(value, str):
                valid = bool(value.strip())
            else:
                valid = bool(value)
            if not valid:
                raise VerificationValidationError(f"receipt {field} must not be empty")
        log_summary = receipt.get("log_summary")
        if isinstance(log_summary, str) and len(log_summary) > VerificationService._MAX_LOG_SUMMARY_LENGTH:
            raise VerificationValidationError("receipt log_summary is too long")
        for field in VerificationService._ALLOWED_RECEIPT_FIELDS:
            VerificationService._reject_credential_patterns(receipt.get(field))

    @staticmethod
    def _reject_sensitive_keys(value: Any) -> None:
        if isinstance(value, dict):
            for key, nested_value in value.items():
                normalized_key = "".join(
                    character for character in key.lower() if character.isalnum()
                ) if isinstance(key, str) else ""
                if any(
                    part in normalized_key
                    for part in VerificationService._SENSITIVE_KEY_PARTS
                ):
                    raise VerificationValidationError("receipt contains a sensitive field")
                VerificationService._reject_sensitive_keys(nested_value)
        elif isinstance(value, (list, tuple)):
            for nested_value in value:
                VerificationService._reject_sensitive_keys(nested_value)

    @staticmethod
    def _reject_credential_patterns(value: Any) -> None:
        if isinstance(value, str):
            if VerificationService._CREDENTIAL_PATTERN.search(value):
                raise VerificationValidationError("receipt contains a credential pattern")
        elif isinstance(value, dict):
            for nested_value in value.values():
                VerificationService._reject_credential_patterns(nested_value)
        elif isinstance(value, (list, tuple)):
            for nested_value in value:
                VerificationService._reject_credential_patterns(nested_value)
