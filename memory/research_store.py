"""SQLite persistence for research memories, isolated from the legacy memory store."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

from core.config import DEFAULT_RESEARCH_MEMORY_DB_PATH
from core.constants import (
    ApprovalDecision,
    ApprovalStatus,
    ResearchMemoryKind,
    ResearchMemoryStatus,
    RiskLevel,
    VerificationStatus,
)
from core.research_models import (
    ApprovalPackage,
    ResearchMemory,
    ScopeKey,
    VerificationRun,
    derive_receipt_event_key,
)


class ResearchStore:
    """Persist research-domain memories in their own SQLite database."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_RESEARCH_MEMORY_DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def save(self, memory: ResearchMemory) -> ResearchMemory:
        """Insert or update an in-scope memory with UTC-normalized timestamps."""
        source_refs = self._to_json(memory.source_refs)
        applicability = self._to_json(memory.applicability)
        related_memory_ids = list(memory.related_memory_ids)
        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO research_memories (
                    memory_id, team_id, project_id, repository, branch,
                    experiment_environment, kind, title, content, source_refs,
                    confidence, applicability, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(memory_id) DO UPDATE SET
                    team_id = excluded.team_id,
                    project_id = excluded.project_id,
                    repository = excluded.repository,
                    branch = excluded.branch,
                    experiment_environment = excluded.experiment_environment,
                    kind = excluded.kind,
                    title = excluded.title,
                    content = excluded.content,
                    source_refs = excluded.source_refs,
                    confidence = excluded.confidence,
                    applicability = excluded.applicability,
                    status = excluded.status,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at
                WHERE research_memories.team_id = excluded.team_id
                  AND research_memories.project_id = excluded.project_id
                  AND research_memories.repository = excluded.repository
                  AND research_memories.branch = excluded.branch
                  AND research_memories.experiment_environment = excluded.experiment_environment
                """,
                (
                    memory.memory_id,
                    memory.scope.team_id,
                    memory.scope.project_id,
                    memory.scope.repository,
                    memory.scope.branch,
                    memory.scope.experiment_environment,
                    memory.kind.value,
                    memory.title,
                    memory.content,
                    source_refs,
                    memory.confidence,
                    applicability,
                    memory.status.value,
                    self._format_timestamp(memory.created_at),
                    self._format_timestamp(memory.updated_at),
                ),
            )
            if cursor.rowcount == 0:
                raise ValueError("memory_id already belongs to a different scope")
            connection.execute(
                """
                DELETE FROM research_memory_links
                WHERE research_memory_id = ?
                  AND EXISTS (
                      SELECT 1 FROM research_memories
                      WHERE memory_id = ? AND team_id = ? AND project_id = ?
                        AND repository = ? AND branch = ?
                        AND experiment_environment = ?
                  )
                """,
                (memory.memory_id, memory.memory_id, *self._scope_values(memory.scope)),
            )
            connection.executemany(
                """
                INSERT INTO research_memory_links (
                    research_memory_id, related_memory_id, position
                ) VALUES (?, ?, ?)
                """,
                [
                    (memory.memory_id, related_memory_id, position)
                    for position, related_memory_id in enumerate(related_memory_ids)
                ],
            )
        return memory

    def get(self, memory_id: str, scope: ScopeKey) -> ResearchMemory | None:
        """Return a memory only when its exact five-dimensional scope matches."""
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM research_memories
                WHERE memory_id = ? AND team_id = ? AND project_id = ?
                  AND repository = ? AND branch = ? AND experiment_environment = ?
                """,
                (memory_id, *self._scope_values(scope)),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_memory(connection, row)

    def list_published(self, scope: ScopeKey) -> list[ResearchMemory]:
        """Return only published memories from the exact five-dimensional scope."""
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM research_memories
                WHERE team_id = ? AND project_id = ? AND repository = ?
                  AND branch = ? AND experiment_environment = ? AND status = ?
                ORDER BY updated_at DESC, memory_id ASC
                """,
                (
                    scope.team_id,
                    scope.project_id,
                    scope.repository,
                    scope.branch,
                    scope.experiment_environment,
                    ResearchMemoryStatus.PUBLISHED.value,
                ),
            ).fetchall()
            return [self._row_to_memory(connection, row) for row in rows]

    def get_updated_at(self, scope: ScopeKey) -> datetime | None:
        """返回 Scope 内已发布记忆的最新有效 UTC 时间，未知时返回 ``None``。"""

        if type(scope) is not ScopeKey:
            return None
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    """
                    SELECT updated_at FROM research_memories
                    WHERE team_id = ? AND project_id = ? AND repository = ?
                      AND branch = ? AND experiment_environment = ? AND status = ?
                    """,
                    (*self._scope_values(scope), ResearchMemoryStatus.PUBLISHED.value),
                ).fetchall()
            if not rows:
                return None
            timestamps = [self._parse_timestamp(row["updated_at"]) for row in rows]
            if any(timestamp > datetime.now(timezone.utc) for timestamp in timestamps):
                return None
            return max(timestamps)
        except (KeyError, TypeError, ValueError, OverflowError, sqlite3.Error):
            return None

    def list_by_related_memory_id(
        self, related_memory_id: str, scope: ScopeKey
    ) -> list[ResearchMemory]:
        """Return linked memories only from the exact five-dimensional scope."""
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT memory.* FROM research_memories AS memory
                INNER JOIN research_memory_links AS link
                    ON link.research_memory_id = memory.memory_id
                WHERE link.related_memory_id = ?
                  AND memory.team_id = ? AND memory.project_id = ?
                  AND memory.repository = ? AND memory.branch = ?
                  AND memory.experiment_environment = ?
                ORDER BY memory.updated_at DESC, memory.memory_id ASC
                """,
                (related_memory_id, *self._scope_values(scope)),
            ).fetchall()
            return [self._row_to_memory(connection, row) for row in rows]

    def revoke(self, memory_id: str, scope: ScopeKey) -> ResearchMemory | None:
        """Revoke an in-scope non-revoked memory, otherwise return ``None``."""
        updated_at = self._format_timestamp(datetime.now(timezone.utc))
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE research_memories SET status = ?, updated_at = ?
                WHERE memory_id = ? AND team_id = ? AND project_id = ?
                  AND repository = ? AND branch = ? AND experiment_environment = ?
                  AND status != ?
                """,
                (
                    ResearchMemoryStatus.REVOKED.value,
                    updated_at,
                    memory_id,
                    *self._scope_values(scope),
                    ResearchMemoryStatus.REVOKED.value,
                ),
            )
            if cursor.rowcount == 0:
                return None
            self._append_audit(connection, memory_id, "revoked", f"revoke:{memory_id}", None)
            row = connection.execute(
                """
                SELECT * FROM research_memories
                WHERE memory_id = ? AND team_id = ? AND project_id = ?
                  AND repository = ? AND branch = ? AND experiment_environment = ?
                """,
                (memory_id, *self._scope_values(scope)),
            ).fetchone()
            return self._row_to_memory(connection, row)

    def append_audit(
        self,
        memory_id: str,
        scope: ScopeKey,
        action: str,
        event_key: str,
        details: dict[str, Any] | None = None,
    ) -> bool:
        """Record an event-keyed action only for an existing memory in ``scope``.

        Returns ``False`` without writing when the memory is absent or belongs to
        another scope. Repeated calls with the same memory ID and event key are
        accepted without creating another audit row.
        """
        with self._connection() as connection:
            if not self._exists_in_scope(connection, memory_id, scope):
                return False
            self._append_audit(connection, memory_id, action, event_key, details)
            return True

    def save_approval_package(self, package: ApprovalPackage) -> ApprovalPackage:
        """Persist an immutable approval package without allowing an overwrite."""
        if package.status is not ApprovalStatus.PENDING:
            raise ValueError("approval package must be created pending")
        with self._connection() as connection:
            if not self._is_research_case_in_scope(
                connection, package.case_memory_id, package.scope
            ):
                raise ValueError("approval package case must be an in-scope research_case")
            existing = connection.execute(
                "SELECT * FROM approval_packages WHERE package_id = ?",
                (package.package_id,),
            ).fetchone()
            if existing is not None:
                persisted = self._row_to_approval_package(existing)
                if not self._approval_packages_match(persisted, package):
                    raise ValueError("package_id conflicts with an existing approval package")
                return persisted
            connection.execute(
                """
                INSERT INTO approval_packages (
                    package_id, case_memory_id, team_id, project_id, repository,
                    branch, experiment_environment, requested_by, payload_json,
                    payload_hash, risk_level, status, created_at, expires_at,
                    receipt_token
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    package.package_id,
                    package.case_memory_id,
                    *self._scope_values(package.scope),
                    package.requested_by,
                    self._to_json(package.payload),
                    package.payload_hash,
                    package.risk_level.value,
                    package.status.value,
                    self._format_timestamp(package.created_at),
                    self._format_timestamp(package.expires_at),
                    package.receipt_token,
                ),
            )
            self._append_audit(
                connection,
                package.case_memory_id,
                "approval_package_saved",
                f"approval-package:{package.package_id}",
                {"package_id": package.package_id, "status": package.status.value, "payload_hash": package.payload_hash},
            )
        return package

    def get_approval_package(
        self, package_id: str, scope: ScopeKey
    ) -> ApprovalPackage | None:
        """Return an approval package only from its exact ScopeKey."""
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM approval_packages
                WHERE package_id = ? AND team_id = ? AND project_id = ?
                  AND repository = ? AND branch = ? AND experiment_environment = ?
                """,
                (package_id, *self._scope_values(scope)),
            ).fetchone()
            return self._row_to_approval_package(row) if row is not None else None

    def save_approval_decision(
        self,
        package_id: str,
        scope: ScopeKey,
        decision: ApprovalDecision,
        approver_id: str,
        reason: str | None,
        decided_at: datetime,
    ) -> ApprovalPackage | None:
        """Compatibility wrapper for the sole atomic approval-decision writer."""
        if self.get_approval_package(package_id, scope) is None:
            return None
        return self.finalize_approval_decision(
            package_id, scope, decision, approver_id, reason, decided_at
        )

    def finalize_approval_decision(
        self,
        package_id: str,
        scope: ScopeKey,
        decision: ApprovalDecision,
        approver_id: str,
        reason: str | None,
        decided_at: datetime,
    ) -> ApprovalPackage:
        """Atomically persist one service-validated final decision for a pending package.

        Applicant identity, expiry, risk, and state-transition authorization are
        deliberately validated by the service layer before this narrow write.
        """
        expired = False
        finalized: ApprovalPackage | None = None
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM approval_packages
                WHERE package_id = ? AND team_id = ? AND project_id = ?
                  AND repository = ? AND branch = ? AND experiment_environment = ?
                """,
                (package_id, *self._scope_values(scope)),
            ).fetchone()
            if row is None:
                raise ValueError("approval package does not exist in scope")
            package = self._row_to_approval_package(row)
            if package.status is not ApprovalStatus.PENDING:
                raise ValueError("approval package is not pending")
            if self._format_timestamp(package.expires_at) <= self._format_timestamp(decided_at):
                connection.execute(
                    "UPDATE approval_packages SET status = ? WHERE package_id = ? AND status = ?",
                    (ApprovalStatus.EXPIRED.value, package_id, ApprovalStatus.PENDING.value),
                )
                self._append_audit(
                    connection,
                    package.case_memory_id,
                    "approval_expired",
                    f"approval-expired:{package_id}",
                    {"package_id": package_id, "status": ApprovalStatus.EXPIRED.value},
                )
                expired = True
            else:
                if connection.execute(
                    "SELECT 1 FROM approval_decisions WHERE package_id = ?", (package_id,)
                ).fetchone() is not None:
                    raise ValueError("approval decision already exists for package_id")
                status = ApprovalStatus(decision.value)
                cursor = connection.execute(
                    """
                    UPDATE approval_packages SET status = ?
                    WHERE package_id = ? AND team_id = ? AND project_id = ?
                      AND repository = ? AND branch = ? AND experiment_environment = ?
                      AND status = ?
                    """,
                    (
                        status.value,
                        package_id,
                        *self._scope_values(scope),
                        ApprovalStatus.PENDING.value,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ValueError("approval package is not pending")
                connection.execute(
                    """
                    INSERT INTO approval_decisions (
                        package_id, decision, approver_id, reason, decided_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (package_id, decision.value, approver_id, reason, self._format_timestamp(decided_at)),
                )
                self._append_audit(
                    connection,
                    package.case_memory_id,
                    "approval_finalized",
                    f"approval-finalized:{package_id}",
                    {"package_id": package_id, "decision": decision.value, "approver_id": approver_id},
                )
                finalized = ApprovalPackage(
                    package_id=package.package_id,
                    case_memory_id=package.case_memory_id,
                    scope=package.scope,
                    requested_by=package.requested_by,
                    payload_hash=package.payload_hash,
                    risk_level=package.risk_level,
                    expires_at=package.expires_at,
                    receipt_token=package.receipt_token,
                    payload=package.payload,
                    status=status,
                    created_at=package.created_at,
                )
        if expired:
            raise ValueError("approval package has expired")
        assert finalized is not None
        return finalized

    def expire_pending_approval(
        self, package_id: str, scope: ScopeKey, now: datetime
    ) -> ApprovalPackage | None:
        """Mark a pending, already-expired in-scope package as expired."""
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE approval_packages SET status = ?
                WHERE package_id = ? AND team_id = ? AND project_id = ?
                  AND repository = ? AND branch = ? AND experiment_environment = ?
                  AND status = ? AND expires_at <= ?
                """,
                (
                    ApprovalStatus.EXPIRED.value,
                    package_id,
                    *self._scope_values(scope),
                    ApprovalStatus.PENDING.value,
                    self._format_timestamp(now),
                ),
            )
            if cursor.rowcount == 0:
                return None
            row = connection.execute(
                """
                SELECT * FROM approval_packages
                WHERE package_id = ? AND team_id = ? AND project_id = ?
                  AND repository = ? AND branch = ? AND experiment_environment = ?
                """,
                (package_id, *self._scope_values(scope)),
            ).fetchone()
            package = self._row_to_approval_package(row)
            self._append_audit(
                connection,
                package.case_memory_id,
                "approval_expired",
                f"approval-expired:{package_id}",
                {"package_id": package_id, "status": package.status.value},
            )
            return package

    def save_verification_run(self, run: VerificationRun) -> VerificationRun:
        """Persist an event-keyed receipt, accepting only identical retries."""
        with self._connection() as connection:
            existing = connection.execute(
                "SELECT * FROM verification_runs WHERE event_key = ?",
                (run.event_key,),
            ).fetchone()
            if existing is not None:
                persisted = self._row_to_verification_run(existing)
                package_row = connection.execute(
                    """
                    SELECT * FROM approval_packages
                    WHERE package_id = ? AND team_id = ? AND project_id = ?
                      AND repository = ? AND branch = ? AND experiment_environment = ?
                    """,
                    (run.package_id, *self._scope_values(run.scope)),
                ).fetchone()
                if package_row is None:
                    raise ValueError("verification event conflict")
                package = self._row_to_approval_package(package_row)
                if run.event_key != derive_receipt_event_key(
                    package.package_id, package.receipt_token, run.receipt_id
                ):
                    raise ValueError("verification event key is invalid")
                if (
                    persisted.run_id != run.run_id
                    or persisted.package_id != run.package_id
                    or persisted.case_memory_id != run.case_memory_id
                    or persisted.scope != run.scope
                    or persisted.payload_hash != run.payload_hash
                    or persisted.receipt_id != run.receipt_id
                    or persisted.receipt != run.receipt
                    or persisted.status != run.status
                    or not self._timestamps_match(persisted.created_at, run.created_at)
                    or not self._optional_timestamps_match(
                        persisted.verified_at, run.verified_at
                    )
                ):
                    raise ValueError("verification event conflict")
                return persisted
            package_row = connection.execute(
                """
                SELECT * FROM approval_packages
                WHERE package_id = ? AND team_id = ? AND project_id = ?
                  AND repository = ? AND branch = ? AND experiment_environment = ?
                """,
                (run.package_id, *self._scope_values(run.scope)),
            ).fetchone()
            if package_row is None:
                raise ValueError("approval package is invalid for verification")
            package = self._row_to_approval_package(package_row)
            if (
                package.case_memory_id != run.case_memory_id
                or package.payload_hash != run.payload_hash
            ):
                raise ValueError("approval package is invalid for verification")
            expected_event_key = derive_receipt_event_key(
                package.package_id, package.receipt_token, run.receipt_id
            )
            if run.event_key != expected_event_key:
                raise ValueError("verification event key is invalid")
            run_id = connection.execute(
                "SELECT 1 FROM verification_runs WHERE run_id = ?", (run.run_id,)
            ).fetchone()
            if run_id is not None:
                raise ValueError("run_id already belongs to another verification run")
            connection.execute(
                """
                INSERT INTO verification_runs (
                    run_id, package_id, case_memory_id, team_id, project_id,
                    repository, branch, experiment_environment, payload_hash,
                    receipt_id, event_key, receipt_json, status, created_at, verified_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    run.package_id,
                    run.case_memory_id,
                    *self._scope_values(run.scope),
                    run.payload_hash,
                    run.receipt_id,
                    run.event_key,
                    self._to_json(run.receipt),
                    run.status.value,
                    self._format_timestamp(run.created_at),
                    self._format_timestamp(run.verified_at) if run.verified_at else None,
                ),
            )
            self._append_audit(
                connection,
                run.case_memory_id,
                "verification_saved",
                f"verification-run:{run.run_id}",
                {"run_id": run.run_id, "package_id": run.package_id, "status": run.status.value, "payload_hash": run.payload_hash},
            )
        return run

    def finalize_verification_receipt(
        self, run: VerificationRun, now: datetime
    ) -> VerificationRun:
        """Atomically persist a receipt for an approved, unexpired package.

        Existing semantically identical event-key retries return their original
        record without re-evaluating package expiry.
        """
        with self._connection() as connection:
            existing = connection.execute(
                "SELECT * FROM verification_runs WHERE event_key = ?",
                (run.event_key,),
            ).fetchone()
            if existing is not None:
                persisted = self._row_to_verification_run(existing)
                if not self._verification_runs_semantically_match(persisted, run):
                    raise ValueError("verification event conflict")
                return persisted
            package_row = connection.execute(
                """
                SELECT * FROM approval_packages
                WHERE package_id = ? AND team_id = ? AND project_id = ?
                  AND repository = ? AND branch = ? AND experiment_environment = ?
                """,
                (run.package_id, *self._scope_values(run.scope)),
            ).fetchone()
            if package_row is None:
                raise ValueError("approval package is invalid for verification")
            package = self._row_to_approval_package(package_row)
            expected_event_key = derive_receipt_event_key(
                package.package_id, package.receipt_token, run.receipt_id
            )
            if run.event_key != expected_event_key:
                raise ValueError("verification event key is invalid")
            if package.status is not ApprovalStatus.APPROVED:
                raise ValueError("approval package is not approved")
            if self._format_timestamp(package.expires_at) <= self._format_timestamp(now):
                raise ValueError("approval package has expired")
            if (
                package.case_memory_id != run.case_memory_id
                or package.payload_hash != run.payload_hash
            ):
                raise ValueError("approval package is invalid for verification")
            if connection.execute(
                "SELECT 1 FROM verification_runs WHERE run_id = ?", (run.run_id,)
            ).fetchone() is not None:
                raise ValueError("run_id already belongs to another verification run")
            cursor = connection.execute(
                """
                INSERT INTO verification_runs (
                    run_id, package_id, case_memory_id, team_id, project_id,
                    repository, branch, experiment_environment, payload_hash,
                    receipt_id, event_key, receipt_json, status, created_at, verified_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_key) DO NOTHING
                """,
                (
                    run.run_id,
                    run.package_id,
                    run.case_memory_id,
                    *self._scope_values(run.scope),
                    run.payload_hash,
                    run.receipt_id,
                    run.event_key,
                    self._to_json(run.receipt),
                    run.status.value,
                    self._format_timestamp(run.created_at),
                    self._format_timestamp(run.verified_at) if run.verified_at else None,
                ),
            )
            if cursor.rowcount == 0:
                existing = connection.execute(
                    "SELECT * FROM verification_runs WHERE event_key = ?",
                    (run.event_key,),
                ).fetchone()
                if existing is None:
                    raise ValueError("verification event conflict")
                persisted = self._row_to_verification_run(existing)
                if not self._verification_runs_semantically_match(persisted, run):
                    raise ValueError("verification event conflict")
                return persisted
            self._append_audit(
                connection,
                run.case_memory_id,
                "verification_finalized",
                f"verification-run:{run.run_id}",
                {
                    "run_id": run.run_id,
                    "package_id": run.package_id,
                    "status": run.status.value,
                    "payload_hash": run.payload_hash,
                },
            )
        return run

    def get_verification_run(self, run_id: str, scope: ScopeKey) -> VerificationRun | None:
        """Return a verification run only from its exact ScopeKey."""
        with self._connection() as connection:
            row = connection.execute(
                """SELECT * FROM verification_runs WHERE run_id = ? AND team_id = ?
                   AND project_id = ? AND repository = ? AND branch = ?
                   AND experiment_environment = ?""",
                (run_id, *self._scope_values(scope)),
            ).fetchone()
            return self._row_to_verification_run(row) if row is not None else None

    def get_verification_by_event_key(
        self, event_key: str, scope: ScopeKey
    ) -> VerificationRun | None:
        """Return an event-keyed verification run only from its exact ScopeKey."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM verification_runs WHERE event_key = ?",
                (event_key,),
            ).fetchone()
            if row is None:
                return None
            run = self._row_to_verification_run(row)
            return run if run.scope == scope else None

    def list_verification_runs(
        self, package_id: str, scope: ScopeKey
    ) -> list[VerificationRun]:
        """List a package's verification runs from its exact ScopeKey."""
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT * FROM verification_runs WHERE package_id = ? AND team_id = ?
                   AND project_id = ? AND repository = ? AND branch = ?
                   AND experiment_environment = ? ORDER BY created_at ASC, run_id ASC""",
                (package_id, *self._scope_values(scope)),
            ).fetchall()
            return [self._row_to_verification_run(row) for row in rows]

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("BEGIN")
            for statement in self._schema_statements():
                connection.execute(statement)
            self._migrate_schema(connection)
            self._normalize_persisted_timestamps(connection)

    @staticmethod
    def _schema_statements() -> tuple[str, ...]:
        return (
            """
            CREATE TABLE IF NOT EXISTS research_memories (
                memory_id TEXT PRIMARY KEY,
                team_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                repository TEXT NOT NULL,
                branch TEXT NOT NULL,
                experiment_environment TEXT NOT NULL,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                source_refs TEXT NOT NULL,
                confidence REAL NOT NULL,
                applicability TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS research_memory_links (
                research_memory_id TEXT NOT NULL,
                related_memory_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                PRIMARY KEY (research_memory_id, related_memory_id),
                FOREIGN KEY (research_memory_id)
                    REFERENCES research_memories(memory_id) ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS research_audit (
                audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id TEXT NOT NULL,
                action TEXT NOT NULL,
                event_key TEXT,
                details TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_research_memories_scope_status_updated
            ON research_memories (
                team_id, project_id, repository, branch,
                experiment_environment, status, updated_at
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_research_memory_links_related_memory_id
            ON research_memory_links (related_memory_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_research_audit_memory_id
            ON research_audit (memory_id)
            """,
            """
            CREATE TABLE IF NOT EXISTS approval_packages (
                package_id TEXT PRIMARY KEY,
                case_memory_id TEXT NOT NULL,
                team_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                repository TEXT NOT NULL,
                branch TEXT NOT NULL,
                experiment_environment TEXT NOT NULL,
                requested_by TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                risk_level TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                receipt_token TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS approval_decisions (
                package_id TEXT PRIMARY KEY,
                decision TEXT NOT NULL,
                approver_id TEXT NOT NULL,
                reason TEXT,
                decided_at TEXT NOT NULL,
                FOREIGN KEY (package_id) REFERENCES approval_packages(package_id)
                    ON DELETE CASCADE
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS verification_runs (
                run_id TEXT PRIMARY KEY,
                package_id TEXT NOT NULL,
                case_memory_id TEXT NOT NULL,
                team_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                repository TEXT NOT NULL,
                branch TEXT NOT NULL,
                experiment_environment TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                receipt_id TEXT NOT NULL,
                event_key TEXT NOT NULL,
                receipt_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                verified_at TEXT
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_approval_packages_scope_status_expires
            ON approval_packages (
                team_id, project_id, repository, branch,
                experiment_environment, status, expires_at
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_verification_runs_package_scope
            ON verification_runs (
                package_id, team_id, project_id, repository, branch,
                experiment_environment
            )
            """,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_verification_runs_event_key
            ON verification_runs (event_key)
            """,
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
        except Exception:
            connection.close()
            raise
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _to_json(value: Any) -> str:
        return json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True)

    @staticmethod
    def _format_timestamp(value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _parse_timestamp(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _migrate_schema(self, connection: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(research_audit)").fetchall()
        }
        if "event_key" not in columns:
            connection.execute("ALTER TABLE research_audit ADD COLUMN event_key TEXT")
        approval_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(approval_packages)").fetchall()
        }
        if "receipt_token" not in approval_columns:
            if connection.execute(
                "SELECT 1 FROM approval_packages LIMIT 1"
            ).fetchone() is not None:
                raise RuntimeError("legacy D1 receipt migration required")
            connection.execute(
                "ALTER TABLE approval_packages ADD COLUMN receipt_token TEXT NOT NULL DEFAULT ''"
            )
        verification_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(verification_runs)").fetchall()
        }
        if "receipt_id" not in verification_columns:
            if connection.execute(
                "SELECT 1 FROM verification_runs LIMIT 1"
            ).fetchone() is not None:
                raise RuntimeError("legacy D1 receipt migration required")
            connection.execute(
                "ALTER TABLE verification_runs ADD COLUMN receipt_id TEXT NOT NULL DEFAULT ''"
            )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_research_audit_memory_event_key
            ON research_audit (memory_id, event_key)
            WHERE event_key IS NOT NULL
            """
        )
        connection.execute("DROP INDEX IF EXISTS idx_verification_runs_scope_event_key")
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_verification_runs_event_key
            ON verification_runs (event_key)
            """
        )

    def _normalize_persisted_timestamps(self, connection: sqlite3.Connection) -> None:
        memories = connection.execute(
            "SELECT memory_id, created_at, updated_at FROM research_memories"
        ).fetchall()
        for memory in memories:
            created_at = self._format_timestamp(self._parse_timestamp(memory["created_at"]))
            updated_at = self._format_timestamp(self._parse_timestamp(memory["updated_at"]))
            if created_at != memory["created_at"] or updated_at != memory["updated_at"]:
                connection.execute(
                    """
                    UPDATE research_memories SET created_at = ?, updated_at = ?
                    WHERE memory_id = ?
                    """,
                    (created_at, updated_at, memory["memory_id"]),
                )
        audit_rows = connection.execute(
            "SELECT audit_id, created_at FROM research_audit"
        ).fetchall()
        for audit in audit_rows:
            created_at = self._format_timestamp(self._parse_timestamp(audit["created_at"]))
            if created_at != audit["created_at"]:
                connection.execute(
                    "UPDATE research_audit SET created_at = ? WHERE audit_id = ?",
                    (created_at, audit["audit_id"]),
                )

    @staticmethod
    def _scope_values(scope: ScopeKey) -> tuple[str, str, str, str, str]:
        return (
            scope.team_id,
            scope.project_id,
            scope.repository,
            scope.branch,
            scope.experiment_environment,
        )

    def _exists_in_scope(
        self, connection: sqlite3.Connection, memory_id: str, scope: ScopeKey
    ) -> bool:
        return (
            connection.execute(
                """
                SELECT 1 FROM research_memories
                WHERE memory_id = ? AND team_id = ? AND project_id = ?
                  AND repository = ? AND branch = ? AND experiment_environment = ?
                """,
                (memory_id, *self._scope_values(scope)),
            ).fetchone()
            is not None
        )

    def _is_research_case_in_scope(
        self, connection: sqlite3.Connection, memory_id: str, scope: ScopeKey
    ) -> bool:
        """Return whether an exact-scope memory is a persisted research case."""
        return (
            connection.execute(
                """
                SELECT 1 FROM research_memories
                WHERE memory_id = ? AND team_id = ? AND project_id = ?
                  AND repository = ? AND branch = ? AND experiment_environment = ?
                  AND kind = ?
                """,
                (
                    memory_id,
                    *self._scope_values(scope),
                    ResearchMemoryKind.RESEARCH_CASE.value,
                ),
            ).fetchone()
            is not None
        )

    def _timestamps_match(self, persisted: datetime, incoming: datetime) -> bool:
        return self._format_timestamp(persisted) == self._format_timestamp(incoming)

    def _optional_timestamps_match(
        self, persisted: datetime | None, incoming: datetime | None
    ) -> bool:
        if persisted is None or incoming is None:
            return persisted is None and incoming is None
        return self._timestamps_match(persisted, incoming)

    def _approval_packages_match(
        self, persisted: ApprovalPackage, incoming: ApprovalPackage
    ) -> bool:
        """Compare frozen packages with timestamps normalized to UTC."""
        return (
            persisted.package_id == incoming.package_id
            and persisted.case_memory_id == incoming.case_memory_id
            and persisted.scope == incoming.scope
            and persisted.requested_by == incoming.requested_by
            and persisted.payload == incoming.payload
            and persisted.payload_hash == incoming.payload_hash
            and persisted.risk_level == incoming.risk_level
            and persisted.status == incoming.status
            and persisted.receipt_token == incoming.receipt_token
            and self._timestamps_match(persisted.created_at, incoming.created_at)
            and self._timestamps_match(persisted.expires_at, incoming.expires_at)
        )

    @staticmethod
    def _verification_runs_semantically_match(
        persisted: VerificationRun, incoming: VerificationRun
    ) -> bool:
        """Compare receipt idempotency fields, excluding generated ID and times."""
        return (
            persisted.package_id == incoming.package_id
            and persisted.case_memory_id == incoming.case_memory_id
            and persisted.scope == incoming.scope
            and persisted.payload_hash == incoming.payload_hash
            and persisted.receipt_id == incoming.receipt_id
            and persisted.status == incoming.status
            and persisted.receipt == incoming.receipt
        )

    def _append_audit(
        self,
        connection: sqlite3.Connection,
        memory_id: str,
        action: str,
        event_key: str,
        details: dict[str, Any] | None,
    ) -> None:
        if not event_key.strip():
            raise ValueError("event_key must not be blank")
        connection.execute(
            """
            INSERT INTO research_audit (
                memory_id, action, event_key, details, created_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(memory_id, event_key) WHERE event_key IS NOT NULL DO NOTHING
            """,
            (
                memory_id,
                action,
                event_key,
                self._to_json(details or {}),
                self._format_timestamp(datetime.now(timezone.utc)),
            ),
        )

    def _row_to_memory(
        self, connection: sqlite3.Connection, row: sqlite3.Row
    ) -> ResearchMemory:
        link_rows = connection.execute(
            """
            SELECT related_memory_id FROM research_memory_links
            WHERE research_memory_id = ? ORDER BY position ASC
            """,
            (row["memory_id"],),
        ).fetchall()
        return ResearchMemory(
            memory_id=row["memory_id"],
            scope=ScopeKey(
                team_id=row["team_id"],
                project_id=row["project_id"],
                repository=row["repository"],
                branch=row["branch"],
                experiment_environment=row["experiment_environment"],
            ),
            kind=ResearchMemoryKind(row["kind"]),
            title=row["title"],
            content=row["content"],
            source_refs=json.loads(row["source_refs"]),
            confidence=row["confidence"],
            applicability=json.loads(row["applicability"]),
            status=ResearchMemoryStatus(row["status"]),
            created_at=self._parse_timestamp(row["created_at"]),
            updated_at=self._parse_timestamp(row["updated_at"]),
            related_memory_ids=[link_row["related_memory_id"] for link_row in link_rows],
        )

    def _row_to_approval_package(self, row: sqlite3.Row) -> ApprovalPackage:
        return ApprovalPackage(
            package_id=row["package_id"],
            case_memory_id=row["case_memory_id"],
            scope=ScopeKey(
                team_id=row["team_id"],
                project_id=row["project_id"],
                repository=row["repository"],
                branch=row["branch"],
                experiment_environment=row["experiment_environment"],
            ),
            requested_by=row["requested_by"],
            payload=json.loads(row["payload_json"]),
            payload_hash=row["payload_hash"],
            risk_level=RiskLevel(row["risk_level"]),
            receipt_token=row["receipt_token"],
            status=ApprovalStatus(row["status"]),
            created_at=self._parse_timestamp(row["created_at"]),
            expires_at=self._parse_timestamp(row["expires_at"]),
        )

    def _row_to_verification_run(self, row: sqlite3.Row) -> VerificationRun:
        return VerificationRun(
            run_id=row["run_id"],
            package_id=row["package_id"],
            case_memory_id=row["case_memory_id"],
            scope=ScopeKey(
                team_id=row["team_id"],
                project_id=row["project_id"],
                repository=row["repository"],
                branch=row["branch"],
                experiment_environment=row["experiment_environment"],
            ),
            payload_hash=row["payload_hash"],
            receipt_id=row["receipt_id"],
            event_key=row["event_key"],
            receipt=json.loads(row["receipt_json"]),
            status=VerificationStatus(row["status"]),
            created_at=self._parse_timestamp(row["created_at"]),
            verified_at=(
                self._parse_timestamp(row["verified_at"])
                if row["verified_at"] is not None
                else None
            ),
        )
