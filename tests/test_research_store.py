"""Tests for the SQLite-backed, isolated research-memory store."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone

import pytest

from core.constants import (
    ApprovalDecision,
    ApprovalStatus,
    ResearchMemoryKind,
    ResearchMemoryStatus,
    RiskLevel,
    VerificationStatus,
)
from core.config import DEFAULT_RESEARCH_MEMORY_DB_PATH
from core.research_models import (
    ApprovalPackage,
    ResearchMemory,
    ScopeKey,
    VerificationRun,
    derive_receipt_event_key,
)
from memory.research_store import ResearchStore


@pytest.fixture
def store(tmp_path):
    return ResearchStore(tmp_path / "research_memories.db")


def make_scope(**overrides) -> ScopeKey:
    values = {
        "team_id": "team-a",
        "project_id": "project-a",
        "repository": "org/repository",
        "branch": "main",
        "experiment_environment": "staging",
    }
    values.update(overrides)
    return ScopeKey(**values)


def make_memory(scope: ScopeKey, memory_id: str, **overrides) -> ResearchMemory:
    values = {
        "memory_id": memory_id,
        "scope": scope,
        "kind": ResearchMemoryKind.KNOWLEDGE,
        "title": "Observed result",
        "content": "The retrieval threshold improved precision.",
        "source_refs": ["run://42"],
        "confidence": 0.9,
        "applicability": {"language": "python"},
        "status": ResearchMemoryStatus.PUBLISHED,
        "created_at": datetime(2026, 7, 1, 9, 30, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 7, 1, 10, 30, tzinfo=timezone.utc),
        "related_memory_ids": [],
    }
    values.update(overrides)
    return ResearchMemory(**values)


def make_approval_package(
    scope: ScopeKey, package_id: str = "approval-1", **overrides
) -> ApprovalPackage:
    values = {
        "package_id": package_id,
        "case_memory_id": "case-1",
        "scope": scope,
        "requested_by": "researcher",
        "payload_hash": "a" * 64,
        "risk_level": RiskLevel.HIGH,
        "expires_at": datetime(2026, 7, 2, 9, 30, tzinfo=timezone.utc),
        "receipt_token": "c" * 64,
        "payload": {"action": "patch", "secret": "payload-value"},
        "created_at": datetime(2026, 7, 1, 9, 30, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return ApprovalPackage(**values)


def make_verification_run(
    scope: ScopeKey, run_id: str = "run-1", **overrides
) -> VerificationRun:
    values = {
        "run_id": run_id,
        "case_memory_id": "case-1",
        "scope": scope,
        "package_id": "approval-1",
        "payload_hash": "a" * 64,
        "receipt_id": "step-1",
        "event_key": "",
        "receipt": {"result": "passed", "secret": "receipt-value"},
        "status": VerificationStatus.PASSED,
        "created_at": datetime(2026, 7, 1, 10, 30, tzinfo=timezone.utc),
        "verified_at": datetime(2026, 7, 1, 10, 31, tzinfo=timezone.utc),
    }
    values.update(overrides)
    if "event_key" not in overrides:
        values["event_key"] = derive_receipt_event_key(
            values["package_id"], "c" * 64, values["receipt_id"]
        )
    return VerificationRun(**values)


def save_research_case(store: ResearchStore, scope: ScopeKey, case_memory_id: str = "case-1") -> None:
    """Persist the case required by approval and verification foreign references."""
    store.save(
        make_memory(
            scope,
            case_memory_id,
            kind=ResearchMemoryKind.RESEARCH_CASE,
            title="Research case",
        )
    )


def test_list_published_is_strictly_isolated_by_experiment_environment(store):
    scope_a = make_scope(experiment_environment="staging")
    scope_b = make_scope(experiment_environment="production")
    store.save(make_memory(scope_a, "research-a"))
    store.save(make_memory(scope_b, "research-b"))

    published = store.list_published(scope_a)

    assert [memory.memory_id for memory in published] == ["research-a"]


def test_get_updated_at_returns_only_latest_safe_published_timestamp_for_scope(store):
    scope = make_scope()
    other_scope = make_scope(branch="feature/other")
    now = datetime.now(timezone.utc)
    expected = now - timedelta(seconds=5)
    store.save(make_memory(scope, "older", updated_at=now - timedelta(seconds=10)))
    store.save(make_memory(scope, "latest", updated_at=expected))
    store.save(make_memory(other_scope, "foreign", updated_at=now - timedelta(seconds=1)))

    assert store.get_updated_at(scope) == expected
    assert store.get_updated_at(make_scope(branch="empty")) is None

    store.save(make_memory(scope, "future", updated_at=now + timedelta(days=1)))
    assert store.get_updated_at(scope) is None


def test_list_by_related_memory_id_returns_linked_research_memory(store):
    memory = make_memory(
        make_scope(),
        "research-linked",
        related_memory_ids=["memory-123"],
    )
    store.save(memory)

    linked = store.list_by_related_memory_id("memory-123", memory.scope)

    assert [item.memory_id for item in linked] == ["research-linked"]
    assert linked[0].related_memory_ids == ["memory-123"]


def test_revoke_hides_published_memory_and_records_audit(store):
    scope = make_scope()
    store.save(make_memory(scope, "research-revoked"))

    store.revoke("research-revoked", scope)

    assert store.list_published(scope) == []
    revoked = store.get("research-revoked", scope)
    assert revoked is not None
    assert revoked.status is ResearchMemoryStatus.REVOKED

    with closing(sqlite3.connect(store.path)) as connection:
        audit_rows = connection.execute(
            "SELECT memory_id, action FROM research_audit WHERE memory_id = ?",
            ("research-revoked",),
        ).fetchall()
    assert audit_rows == [("research-revoked", "revoked")]


def test_revoke_is_idempotent_without_changing_timestamp_or_audit(store):
    scope = make_scope()
    store.save(make_memory(scope, "research-revoke-idempotent"))

    first = store.revoke("research-revoke-idempotent", scope)
    assert first is not None
    second = store.revoke("research-revoke-idempotent", scope)

    assert second is None
    assert store.get("research-revoke-idempotent", scope).updated_at == first.updated_at
    with closing(sqlite3.connect(store.path)) as connection:
        audit_count = connection.execute(
            "SELECT COUNT(*) FROM research_audit WHERE memory_id = ? AND action = ?",
            ("research-revoke-idempotent", "revoked"),
        ).fetchone()[0]
    assert audit_count == 1


def test_append_audit_is_idempotent_for_an_event_key(store):
    scope = make_scope()
    store.save(make_memory(scope, "research-audit-idempotent"))

    assert store.append_audit("research-audit-idempotent", scope, "reviewed", "evt-1")
    assert store.append_audit("research-audit-idempotent", scope, "reviewed", "evt-1")

    with closing(sqlite3.connect(store.path)) as connection:
        audit_rows = connection.execute(
            "SELECT action, event_key FROM research_audit WHERE memory_id = ?",
            ("research-audit-idempotent",),
        ).fetchall()
    assert audit_rows == [("reviewed", "evt-1")]


def test_append_audit_propagates_unrelated_integrity_errors(store):
    scope = make_scope()
    memory_id = "research-invalid-audit"
    store.save(make_memory(scope, memory_id))

    with pytest.raises(sqlite3.IntegrityError):
        store.append_audit(memory_id, scope, None, "invalid-action")

    with closing(sqlite3.connect(store.path)) as connection:
        audit_count = connection.execute(
            "SELECT COUNT(*) FROM research_audit WHERE memory_id = ?", (memory_id,)
        ).fetchone()[0]
    assert audit_count == 0


def test_save_round_trips_all_research_memory_fields(store):
    scope = make_scope()
    original = make_memory(
        scope,
        "research-round-trip",
        kind=ResearchMemoryKind.EXPERIMENT,
        source_refs=["doi:10.1000/example", "run://43"],
        applicability={"languages": ["python", "rust"], "minimum": 3},
        related_memory_ids=["memory-123", "memory-456"],
    )

    store.save(original)

    assert store.get("research-round-trip", scope) == original


def test_save_normalizes_offset_aware_timestamps_to_utc(store):
    scope = make_scope()
    memory = make_memory(
        scope,
        "research-utc",
        created_at=datetime(2026, 7, 1, 17, 30, tzinfo=timezone(timedelta(hours=8))),
        updated_at=datetime(2026, 7, 1, 18, 30, tzinfo=timezone(timedelta(hours=8))),
    )
    store.save(memory)

    loaded = store.get(memory.memory_id, scope)
    assert loaded.created_at == datetime(2026, 7, 1, 9, 30, tzinfo=timezone.utc)
    assert loaded.updated_at == datetime(2026, 7, 1, 10, 30, tzinfo=timezone.utc)
    with closing(sqlite3.connect(store.path)) as connection:
        stored_updated_at = connection.execute(
            "SELECT updated_at FROM research_memories WHERE memory_id = ?",
            (memory.memory_id,),
        ).fetchone()[0]
    assert stored_updated_at == "2026-07-01T10:30:00Z"


def test_list_published_orders_offset_aware_timestamps_by_utc_instant(store):
    scope = make_scope()
    store.save(
        make_memory(
            scope,
            "research-earlier",
            updated_at=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
        )
    )
    store.save(
        make_memory(
            scope,
            "research-later",
            updated_at=datetime(2026, 7, 1, 9, 0, tzinfo=timezone(timedelta(hours=-4))),
        )
    )

    assert [memory.memory_id for memory in store.list_published(scope)] == [
        "research-later",
        "research-earlier",
    ]


def test_initialization_migrates_legacy_audit_schema_and_timestamps(tmp_path):
    path = tmp_path / "legacy_research.db"
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(
            """
            CREATE TABLE research_memories (
                memory_id TEXT PRIMARY KEY, team_id TEXT NOT NULL,
                project_id TEXT NOT NULL, repository TEXT NOT NULL,
                branch TEXT NOT NULL, experiment_environment TEXT NOT NULL,
                kind TEXT NOT NULL, title TEXT NOT NULL, content TEXT NOT NULL,
                source_refs TEXT NOT NULL, confidence REAL NOT NULL,
                applicability TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE research_audit (
                audit_id INTEGER PRIMARY KEY AUTOINCREMENT, memory_id TEXT NOT NULL,
                action TEXT NOT NULL, details TEXT NOT NULL, created_at TEXT NOT NULL
            );
            """
        )
        connection.execute(
            """
            INSERT INTO research_memories VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                "legacy-memory", "team-a", "project-a", "org/repository", "main",
                "staging", "knowledge", "legacy", "content", "[]", 0.8, "{}",
                "published", "2026-07-01T17:30:00+08:00", "2026-07-01T18:30:00+08:00",
            ),
        )
        connection.execute(
            "INSERT INTO research_audit (memory_id, action, details, created_at) VALUES (?, ?, ?, ?)",
            ("legacy-memory", "reviewed", "{}", "2026-07-01T18:30:00+08:00"),
        )
        connection.commit()

    store = ResearchStore(path)

    assert store.get("legacy-memory", make_scope()).updated_at == datetime(
        2026, 7, 1, 10, 30, tzinfo=timezone.utc
    )
    with closing(sqlite3.connect(path)) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(research_audit)")
        }
        audit_time = connection.execute(
            "SELECT created_at FROM research_audit WHERE memory_id = ?",
            ("legacy-memory",),
        ).fetchone()[0]
    assert "event_key" in columns
    assert audit_time == "2026-07-01T10:30:00Z"


def test_initialization_rolls_back_schema_when_legacy_event_keys_are_duplicate(tmp_path):
    path = tmp_path / "duplicate_legacy_audit.db"
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(
            """
            CREATE TABLE research_audit (
                audit_id INTEGER PRIMARY KEY AUTOINCREMENT, memory_id TEXT NOT NULL,
                action TEXT NOT NULL, event_key TEXT, details TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            INSERT INTO research_audit (memory_id, action, event_key, details, created_at)
            VALUES
                ('legacy-memory', 'reviewed', 'duplicate-event', '{}', '2026-07-01T10:30:00Z'),
                ('legacy-memory', 'reviewed', 'duplicate-event', '{}', '2026-07-01T10:30:00Z');
            """
        )
        connection.commit()

    with pytest.raises(sqlite3.IntegrityError):
        ResearchStore(path)

    with closing(sqlite3.connect(path)) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
    assert tables == {"research_audit", "sqlite_sequence"}
    assert "idx_research_audit_memory_event_key" not in indexes


def test_save_replaces_related_memory_links(store):
    original = make_memory(
        make_scope(), "research-link-update", related_memory_ids=["memory-old"]
    )
    store.save(original)
    original.related_memory_ids = ["memory-new"]

    store.save(original)

    assert store.list_by_related_memory_id("memory-old", original.scope) == []
    assert [
        item.memory_id
        for item in store.list_by_related_memory_id("memory-new", original.scope)
    ] == [
        "research-link-update"
    ]


@pytest.mark.parametrize(
    "scope_override",
    [
        {"team_id": "team-b"},
        {"project_id": "project-b"},
        {"repository": "other/repository"},
        {"branch": "feature/isolated"},
        {"experiment_environment": "production"},
    ],
)
def test_retrieval_and_mutation_cannot_cross_any_scope_dimension(store, scope_override):
    scope = make_scope()
    out_of_scope = make_scope(**scope_override)
    memory = make_memory(
        scope, "research-scope-guard", related_memory_ids=["memory-123"]
    )
    store.save(memory)

    assert store.get(memory.memory_id, out_of_scope) is None
    assert store.list_by_related_memory_id("memory-123", out_of_scope) == []
    assert store.revoke(memory.memory_id, out_of_scope) is None
    assert (
        store.append_audit(memory.memory_id, out_of_scope, "cross-scope", "cross-event")
        is False
    )
    assert store.get(memory.memory_id, scope).status is ResearchMemoryStatus.PUBLISHED

    with closing(sqlite3.connect(store.path)) as connection:
        audit_rows = connection.execute(
            "SELECT action FROM research_audit WHERE memory_id = ?",
            (memory.memory_id,),
        ).fetchall()
    assert audit_rows == []


class TrackingConnection:
    """Proxy that records whether a store operation explicitly closes SQLite."""

    def __init__(self, connection):
        self._connection = connection
        self.closed = False

    def __enter__(self):
        self._connection.__enter__()
        return self

    def __exit__(self, *args):
        return self._connection.__exit__(*args)

    def __getattr__(self, name):
        return getattr(self._connection, name)

    def close(self):
        self.closed = True
        self._connection.close()


def test_public_operations_close_every_sqlite_connection(tmp_path, monkeypatch):
    connections = []

    def tracked_connect(store):
        connection = sqlite3.connect(store.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        tracked = TrackingConnection(connection)
        connections.append(tracked)
        return tracked

    monkeypatch.setattr(ResearchStore, "_connect", tracked_connect)
    store = ResearchStore(tmp_path / "research_memories.db")
    scope = make_scope()
    memory = make_memory(scope, "research-close", related_memory_ids=["memory-123"])

    store.save(memory)
    store.get(memory.memory_id, scope)
    store.list_published(scope)
    store.list_by_related_memory_id("memory-123", scope)
    store.revoke(memory.memory_id, scope)
    store.append_audit(memory.memory_id, scope, "reviewed", "close-event")

    assert connections
    assert all(connection.closed for connection in connections)


def test_public_operation_closes_connection_when_json_serialization_fails(
    tmp_path, monkeypatch
):
    connections = []

    def tracked_connect(store):
        connection = sqlite3.connect(store.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        tracked = TrackingConnection(connection)
        connections.append(tracked)
        return tracked

    monkeypatch.setattr(ResearchStore, "_connect", tracked_connect)
    store = ResearchStore(tmp_path / "research_memories.db")
    scope = make_scope()
    store.save(make_memory(scope, "research-close"))

    with pytest.raises(ValueError):
        store.append_audit(
            "research-close", scope, "invalid", "invalid-event", {"value": float("nan")}
        )

    assert connections[-1].closed


def test_default_research_database_file_name_matches_design_contract():
    assert DEFAULT_RESEARCH_MEMORY_DB_PATH.name == "research_memory.db"


def test_connect_closes_connection_when_foreign_key_pragma_fails(monkeypatch, tmp_path):
    class FailingPragmaConnection:
        def __init__(self):
            self.row_factory = None
            self.closed = False

        def execute(self, statement):
            assert statement == "PRAGMA foreign_keys = ON"
            raise RuntimeError("pragma unavailable")

        def close(self):
            self.closed = True

    connection = FailingPragmaConnection()
    monkeypatch.setattr("memory.research_store.sqlite3.connect", lambda path: connection)

    with pytest.raises(RuntimeError, match="pragma unavailable"):
        ResearchStore(tmp_path / "research_memories.db")

    assert connection.closed


def test_approval_package_round_trips_and_is_hidden_outside_its_full_scope(store):
    scope = make_scope()
    package = make_approval_package(scope)
    save_research_case(store, scope)

    assert store.save_approval_package(package) == package
    assert store.get_approval_package(package.package_id, scope) == package
    assert store.get_approval_package(package.package_id, make_scope(branch="other")) is None


def test_save_approval_decision_delegates_to_the_atomic_finalization_path(store):
    scope = make_scope()
    package = make_approval_package(scope)
    save_research_case(store, scope)
    store.save_approval_package(package)

    assert (
        store.save_approval_decision(
            package.package_id,
            make_scope(branch="other"),
            ApprovalDecision.APPROVED,
            "reviewer",
            "looks good",
            datetime(2026, 7, 1, 11, 0, tzinfo=timezone.utc),
        )
        is None
    )
    decided = store.save_approval_decision(
        package.package_id,
        scope,
        ApprovalDecision.APPROVED,
        "reviewer",
        "looks good",
        datetime(2026, 7, 1, 11, 0, tzinfo=timezone.utc),
    )

    assert decided is not None
    assert decided.status is ApprovalStatus.APPROVED
    assert store.get_approval_package(package.package_id, scope).status is ApprovalStatus.APPROVED
    with pytest.raises(ValueError, match="decision|pending"):
        store.save_approval_decision(
            package.package_id,
            scope,
            ApprovalDecision.REJECTED,
            "other-reviewer",
            "different decision",
            datetime(2026, 7, 1, 11, 1, tzinfo=timezone.utc),
        )


def test_expire_pending_approval_only_expires_pending_package(store):
    scope = make_scope()
    pending = make_approval_package(scope, expires_at=datetime(2026, 7, 1, 10, tzinfo=timezone.utc))
    approved = make_approval_package(
        scope, package_id="approval-approved", expires_at=datetime(2026, 7, 1, 10, tzinfo=timezone.utc)
    )
    save_research_case(store, scope)
    store.save_approval_package(pending)
    store.save_approval_package(approved)
    store.finalize_approval_decision(
        approved.package_id,
        scope,
        ApprovalDecision.APPROVED,
        "reviewer",
        "approved before expiry",
        datetime(2026, 7, 1, 9, 45, tzinfo=timezone.utc),
    )

    expired = store.expire_pending_approval(
        pending.package_id, scope, datetime(2026, 7, 1, 11, tzinfo=timezone.utc)
    )

    assert expired is not None
    assert expired.status is ApprovalStatus.EXPIRED
    assert store.get_approval_package(pending.package_id, scope).status is ApprovalStatus.EXPIRED
    assert store.expire_pending_approval(
        approved.package_id, scope, datetime(2026, 7, 1, 11, tzinfo=timezone.utc)
    ) is None


def test_verification_event_key_is_idempotent_and_conflicts_are_rejected(store):
    scope = make_scope()
    run = make_verification_run(scope)
    save_research_case(store, scope)
    store.save_approval_package(make_approval_package(scope))

    assert store.save_verification_run(run) == run
    assert store.save_verification_run(run) == run
    assert store.list_verification_runs(run.package_id, scope) == [run]
    with pytest.raises(ValueError, match="verification event conflict"):
        store.save_verification_run(
            make_verification_run(scope, run_id="run-2", receipt={"result": "different"})
        )


def test_different_packages_can_use_the_same_external_receipt_id(store):
    scope = make_scope()
    run = make_verification_run(scope)
    save_research_case(store, scope)
    store.save_approval_package(make_approval_package(scope))
    store.save_verification_run(run)
    other_scope = make_scope(branch="other")
    save_research_case(store, other_scope, "case-other")
    store.save_approval_package(
        make_approval_package(
            other_scope, package_id="approval-other", case_memory_id="case-other"
        )
    )

    other_run = make_verification_run(
        other_scope,
        run_id="run-other",
        package_id="approval-other",
        case_memory_id="case-other",
    )

    assert other_run.receipt_id == run.receipt_id == "step-1"
    assert other_run.event_key != run.event_key
    assert store.save_verification_run(other_run) == other_run


def test_approval_package_is_frozen_after_its_first_save(store):
    scope = make_scope()
    package = make_approval_package(scope)
    save_research_case(store, scope)
    store.save_approval_package(package)

    assert store.save_approval_package(package) == package
    with pytest.raises(ValueError, match="package_id"):
        store.save_approval_package(
            make_approval_package(scope, payload_hash="b" * 64)
        )


def test_approval_package_retry_canonicalizes_naive_timestamps(store):
    scope = make_scope()
    save_research_case(store, scope)
    package = make_approval_package(
        scope,
        created_at=datetime(2026, 7, 1, 9, 30),
        expires_at=datetime(2026, 7, 2, 9, 30),
    )

    store.save_approval_package(package)
    retried = store.save_approval_package(package)

    assert retried.created_at.tzinfo is timezone.utc
    assert retried.expires_at.tzinfo is timezone.utc


def test_verification_runs_are_hidden_outside_their_full_scope(store):
    scope = make_scope()
    run = make_verification_run(scope)
    save_research_case(store, scope)
    store.save_approval_package(make_approval_package(scope))
    store.save_verification_run(run)
    other_scope = make_scope(experiment_environment="production")

    assert store.get_verification_run(run.run_id, other_scope) is None
    assert store.get_verification_by_event_key(run.event_key, other_scope) is None
    assert store.list_verification_runs(run.package_id, other_scope) == []


def test_verification_rejects_an_event_key_not_derived_from_the_receipt_token(store):
    scope = make_scope()
    save_research_case(store, scope)
    store.save_approval_package(make_approval_package(scope))

    with pytest.raises(ValueError, match="event key"):
        store.save_verification_run(make_verification_run(scope, event_key="d" * 64))


def test_approval_and_verification_audits_exclude_payload_and_receipt_contents(store):
    scope = make_scope()
    package = make_approval_package(scope)
    run = make_verification_run(scope)
    save_research_case(store, scope)
    store.save_approval_package(package)
    store.save_approval_decision(
        package.package_id,
        scope,
        ApprovalDecision.APPROVED,
        "reviewer",
        "sensitive decision reason",
        datetime(2026, 7, 1, 11, tzinfo=timezone.utc),
    )
    store.save_verification_run(run)

    with closing(sqlite3.connect(store.path)) as connection:
        details = [row[0] for row in connection.execute("SELECT details FROM research_audit")]
    assert details
    serialized = " ".join(details)
    for sensitive_text in ("payload-value", "receipt-value", "sensitive decision reason"):
        assert sensitive_text not in serialized


@pytest.mark.parametrize(
    ("decision", "expected_status"),
    [
        (ApprovalDecision.APPROVED, ApprovalStatus.APPROVED),
        (ApprovalDecision.REJECTED, ApprovalStatus.REJECTED),
    ],
)
def test_finalize_approval_decision_persists_one_pending_final_decision(
    store, decision, expected_status
):
    scope = make_scope()
    package = make_approval_package(scope, package_id=f"approval-{decision.value}")
    save_research_case(store, scope)
    store.save_approval_package(package)

    finalized = store.finalize_approval_decision(
        package.package_id,
        scope,
        decision,
        "reviewer",
        "service-validated",
        datetime(2026, 7, 1, 11, tzinfo=timezone.utc),
    )

    assert finalized.status is expected_status
    assert store.get_approval_package(package.package_id, scope).status is expected_status
    with pytest.raises(ValueError, match="decision|pending"):
        store.finalize_approval_decision(
            package.package_id,
            scope,
            decision,
            "reviewer",
            "second attempt",
            datetime(2026, 7, 1, 11, 1, tzinfo=timezone.utc),
        )


def test_finalize_approval_decision_rejects_a_non_pending_package(store):
    scope = make_scope()
    package = make_approval_package(scope)
    save_research_case(store, scope)
    store.save_approval_package(package)
    store.finalize_approval_decision(
        package.package_id,
        scope,
        ApprovalDecision.APPROVED,
        "reviewer",
        "already finalized",
        datetime(2026, 7, 1, 10, tzinfo=timezone.utc),
    )

    with pytest.raises(ValueError, match="pending"):
        store.finalize_approval_decision(
            package.package_id,
            scope,
            ApprovalDecision.REJECTED,
            "reviewer",
            "cannot override",
            datetime(2026, 7, 1, 11, tzinfo=timezone.utc),
        )


@pytest.mark.parametrize(
    "status", [ApprovalStatus.APPROVED, ApprovalStatus.REJECTED, ApprovalStatus.EXPIRED]
)
def test_approval_package_must_be_created_pending(store, status):
    scope = make_scope()
    save_research_case(store, scope)

    with pytest.raises(ValueError, match="pending"):
        store.save_approval_package(make_approval_package(scope, status=status))


def test_approval_package_requires_an_in_scope_research_case(store):
    scope = make_scope()

    with pytest.raises(ValueError, match="case"):
        store.save_approval_package(make_approval_package(scope))

    store.save(make_memory(scope, "case-1", kind=ResearchMemoryKind.KNOWLEDGE))
    with pytest.raises(ValueError, match="case"):
        store.save_approval_package(make_approval_package(scope))


def test_verification_requires_a_matching_in_scope_approval_package(store):
    scope = make_scope()
    save_research_case(store, scope)
    package = make_approval_package(scope)
    store.save_approval_package(package)

    with pytest.raises(ValueError, match="approval package"):
        store.save_verification_run(make_verification_run(scope, package_id="missing"))
    with pytest.raises(ValueError, match="approval package"):
        store.save_verification_run(make_verification_run(scope, payload_hash="b" * 64))
    with pytest.raises(ValueError, match="approval package"):
        store.save_verification_run(make_verification_run(scope, case_memory_id="other-case"))


def test_verification_retry_rejects_a_different_run_id_and_accepts_naive_timestamps(store):
    scope = make_scope()
    save_research_case(store, scope)
    store.save_approval_package(make_approval_package(scope))
    run = make_verification_run(
        scope,
        created_at=datetime(2026, 7, 1, 10, 30),
        verified_at=datetime(2026, 7, 1, 10, 31),
    )

    first = store.save_verification_run(run)
    retried = store.save_verification_run(run)

    assert retried.run_id == first.run_id
    assert retried.created_at.tzinfo is timezone.utc
    with pytest.raises(ValueError, match="verification event conflict"):
        store.save_verification_run(make_verification_run(scope, run_id="run-other"))


@pytest.mark.parametrize(
    ("table_name", "schema", "insert"),
    [
        (
            "approval_packages",
            """
            CREATE TABLE approval_packages (
                package_id TEXT PRIMARY KEY, case_memory_id TEXT NOT NULL,
                team_id TEXT NOT NULL, project_id TEXT NOT NULL, repository TEXT NOT NULL,
                branch TEXT NOT NULL, experiment_environment TEXT NOT NULL,
                requested_by TEXT NOT NULL, payload_json TEXT NOT NULL,
                payload_hash TEXT NOT NULL, risk_level TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, expires_at TEXT NOT NULL
            )
            """,
            """
            INSERT INTO approval_packages VALUES (
                'legacy-package', 'case-1', 'team-a', 'project-a', 'org/repository',
                'main', 'staging', 'researcher', '{}', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                'low', 'pending', '2026-07-01T09:30:00Z', '2026-07-02T09:30:00Z'
            )
            """,
        ),
        (
            "verification_runs",
            """
            CREATE TABLE verification_runs (
                run_id TEXT PRIMARY KEY, package_id TEXT NOT NULL, case_memory_id TEXT NOT NULL,
                team_id TEXT NOT NULL, project_id TEXT NOT NULL, repository TEXT NOT NULL,
                branch TEXT NOT NULL, experiment_environment TEXT NOT NULL, payload_hash TEXT NOT NULL,
                event_key TEXT NOT NULL, receipt_json TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, verified_at TEXT
            )
            """,
            """
            INSERT INTO verification_runs VALUES (
                'legacy-run', 'legacy-package', 'case-1', 'team-a', 'project-a', 'org/repository',
                'main', 'staging', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', '{}', 'passed',
                '2026-07-01T10:30:00Z', '2026-07-01T10:31:00Z'
            )
            """,
        ),
    ],
)
def test_initialization_rejects_nonempty_pre_receipt_d1_tables(
    tmp_path, table_name, schema, insert
):
    path = tmp_path / f"legacy-{table_name}.db"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(schema)
        connection.execute(insert)
        connection.commit()

    with pytest.raises(RuntimeError, match="legacy D1 receipt migration required"):
        ResearchStore(path)

    with closing(sqlite3.connect(path)) as connection:
        columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table_name})")}
        count = connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
    assert "receipt_token" not in columns
    assert "receipt_id" not in columns
    assert count == 1


def test_finalize_approval_decision_expires_at_the_decision_time_boundary(store):
    scope = make_scope()
    save_research_case(store, scope)
    expires_at = datetime(2026, 7, 1, 10, tzinfo=timezone.utc)
    package = make_approval_package(scope, expires_at=expires_at)
    store.save_approval_package(package)

    with pytest.raises(ValueError, match="expired"):
        store.finalize_approval_decision(
            package.package_id,
            scope,
            ApprovalDecision.APPROVED,
            "reviewer",
            "too late",
            expires_at,
        )

    assert store.get_approval_package(package.package_id, scope).status is ApprovalStatus.EXPIRED


def test_finalize_verification_receipt_is_atomic_and_retries_by_semantic_content(store):
    scope = make_scope()
    save_research_case(store, scope)
    package = make_approval_package(scope)
    store.save_approval_package(package)
    store.finalize_approval_decision(
        package.package_id,
        scope,
        ApprovalDecision.APPROVED,
        "reviewer",
        "approved",
        datetime(2026, 7, 1, 10, tzinfo=timezone.utc),
    )
    run = make_verification_run(scope)

    saved = store.finalize_verification_receipt(
        run, datetime(2026, 7, 1, 11, tzinfo=timezone.utc)
    )
    retried = store.finalize_verification_receipt(
        make_verification_run(
            scope,
            run_id="retry-run-id",
            created_at=datetime(2026, 7, 2, 10, tzinfo=timezone.utc),
            verified_at=datetime(2026, 7, 2, 10, 1, tzinfo=timezone.utc),
        ),
        datetime(2026, 7, 3, 11, tzinfo=timezone.utc),
    )

    assert saved.run_id == run.run_id
    assert retried == saved
    assert store.list_verification_runs(package.package_id, scope) == [saved]


def test_finalize_verification_receipt_handles_insert_conflict_from_a_racing_writer(
    store, monkeypatch
):
    scope = make_scope()
    save_research_case(store, scope)
    package = make_approval_package(scope)
    store.save_approval_package(package)
    now = datetime(2026, 7, 1, 10, tzinfo=timezone.utc)
    store.finalize_approval_decision(
        package.package_id, scope, ApprovalDecision.APPROVED, "reviewer", "approved", now
    )
    contender = ResearchStore(store.path)
    primary_run = make_verification_run(scope, run_id="primary-run")
    contender_run = make_verification_run(
        scope,
        run_id="contender-run",
        created_at=datetime(2026, 7, 1, 12, tzinfo=timezone.utc),
        verified_at=datetime(2026, 7, 1, 12, 1, tzinfo=timezone.utc),
    )
    original_connect = ResearchStore._connect
    injected = False

    class RacingCursor:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class RacingConnection:
        def __init__(self, connection):
            self._connection = connection

        def __enter__(self):
            self._connection.__enter__()
            return self

        def __exit__(self, *args):
            return self._connection.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self._connection, name)

        def close(self):
            self._connection.close()

        def execute(self, statement, parameters=()):
            nonlocal injected
            if (
                not injected
                and "SELECT * FROM verification_runs WHERE event_key" in statement
            ):
                cursor = self._connection.execute(statement, parameters)
                row = cursor.fetchone()
                injected = True
                contender.finalize_verification_receipt(
                    contender_run, datetime(2026, 7, 1, 11, tzinfo=timezone.utc)
                )
                return RacingCursor(row)
            return self._connection.execute(statement, parameters)

    def racing_connect(instance):
        connection = original_connect(instance)
        return RacingConnection(connection) if instance is store else connection

    monkeypatch.setattr(ResearchStore, "_connect", racing_connect)

    result = store.finalize_verification_receipt(
        primary_run, datetime(2026, 7, 1, 11, tzinfo=timezone.utc)
    )

    assert result.run_id == contender_run.run_id
    assert store.list_verification_runs(package.package_id, scope) == [result]
