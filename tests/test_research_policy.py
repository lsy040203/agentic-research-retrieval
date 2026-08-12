"""Tests for published research-memory retrieval policy."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.constants import ResearchMemoryKind, ResearchMemoryStatus
from core.research_models import ResearchMemory, ScopeKey
from policy.research_policy import ResearchPolicy


def make_scope(**overrides: str) -> ScopeKey:
    values = {
        "team_id": "team-a",
        "project_id": "project-a",
        "repository": "org/repository",
        "branch": "main",
        "experiment_environment": "cuda-12",
    }
    values.update(overrides)
    return ScopeKey(**values)


def make_memory(
    scope: ScopeKey, memory_id: str, **overrides: object
) -> ResearchMemory:
    values: dict[str, object] = {
        "memory_id": memory_id,
        "scope": scope,
        "kind": ResearchMemoryKind.KNOWLEDGE,
        "title": "Observed result",
        "content": "The retrieval threshold improved precision.",
        "source_refs": ["doi:10.1000/example"],
        "confidence": 0.8,
        "applicability": {},
        "status": ResearchMemoryStatus.PUBLISHED,
        "created_at": datetime(2026, 7, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 7, 2, tzinfo=timezone.utc),
        "related_memory_ids": [],
    }
    values.update(overrides)
    return ResearchMemory(**values)  # type: ignore[arg-type]


def test_filter_and_rank_rejects_revoked_candidate_and_environment_mismatch() -> None:
    scope = make_scope()
    published = make_memory(scope, "published")
    revoked = make_memory(scope, "revoked", status=ResearchMemoryStatus.REVOKED)
    candidate = make_memory(scope, "candidate", status=ResearchMemoryStatus.CANDIDATE)
    incompatible = make_memory(
        scope,
        "incompatible",
        applicability={"experiment_environments": ["cpu"]},
    )

    result = ResearchPolicy().filter_and_rank(
        scope, [revoked, candidate, incompatible, published]
    )

    assert result == [published]


@pytest.mark.parametrize(
    ("scope_field", "foreign_value"),
    [
        ("team_id", "team-b"),
        ("project_id", "project-b"),
        ("repository", "other/repository"),
        ("branch", "release"),
        ("experiment_environment", "cpu"),
    ],
)
def test_filter_and_rank_rejects_memory_from_any_foreign_scope_dimension(
    scope_field: str, foreign_value: str
) -> None:
    scope = make_scope()
    foreign_scope = make_scope(**{scope_field: foreign_value})
    foreign_memory = make_memory(foreign_scope, "foreign")

    assert ResearchPolicy().filter_and_rank(scope, [foreign_memory]) == []


def test_filter_and_rank_keeps_missing_applicability_and_resolves_conflicts() -> None:
    scope = make_scope()
    unrestricted = make_memory(scope, "unrestricted")
    older = make_memory(
        scope,
        "older",
        confidence=0.9,
        applicability={"conflict_key": "threshold"},
        updated_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    winner = make_memory(
        scope,
        "winner",
        confidence=0.9,
        applicability={"conflict_key": "threshold"},
        updated_at=datetime(2026, 7, 2, tzinfo=timezone.utc),
    )
    lower_confidence = make_memory(
        scope,
        "lower-confidence",
        confidence=0.8,
        applicability={"conflict_key": "threshold"},
        updated_at=datetime(2026, 7, 3, tzinfo=timezone.utc),
    )
    id_tiebreaker = make_memory(
        scope,
        "alpha",
        confidence=0.9,
        applicability={"conflict_key": "other"},
        updated_at=datetime(2026, 7, 2, tzinfo=timezone.utc),
    )
    same_score = make_memory(
        scope,
        "zulu",
        confidence=0.9,
        applicability={"conflict_key": "other"},
        updated_at=datetime(2026, 7, 2, tzinfo=timezone.utc),
    )

    result = ResearchPolicy().filter_and_rank(
        scope,
        [older, winner, lower_confidence, same_score, unrestricted, id_tiebreaker],
    )

    assert [memory.memory_id for memory in result] == ["unrestricted", "winner", "alpha"]


def test_retrieval_confidence_is_bounded_and_deterministic() -> None:
    scope = make_scope()
    memory = make_memory(
        scope,
        "scored",
        source_refs=["doi:10.1000/example", "run://42"],
        applicability={"experiment_environments": ["cuda-12"]},
    )
    policy = ResearchPolicy()

    first = policy.retrieval_confidence(scope, memory)
    second = policy.retrieval_confidence(scope, memory)

    assert 0.0 <= first <= 1.0
    assert first == second


def test_filter_and_rank_preserves_empty_and_whitespace_conflict_keys() -> None:
    scope = make_scope()
    missing = make_memory(scope, "missing")
    empty = make_memory(scope, "empty", applicability={"conflict_key": ""})
    empty_second = make_memory(scope, "empty-second", applicability={"conflict_key": ""})
    whitespace = make_memory(scope, "whitespace", applicability={"conflict_key": "  "})
    whitespace_second = make_memory(
        scope, "whitespace-second", applicability={"conflict_key": "  "}
    )

    result = ResearchPolicy().filter_and_rank(
        scope, [missing, empty, empty_second, whitespace, whitespace_second]
    )

    assert result == [missing, empty, empty_second, whitespace, whitespace_second]


def test_is_usable_and_retrieval_confidence_do_not_mutate_memory() -> None:
    scope = make_scope()
    memory = make_memory(
        scope,
        "immutable",
        source_refs=["doi:10.1000/example"],
        applicability={
            "experiment_environments": ["cuda-12"],
            "conflict_key": "threshold",
        },
    )
    source_refs_before = list(memory.source_refs)
    applicability_before = dict(memory.applicability)

    policy = ResearchPolicy()

    assert policy.is_usable(scope, memory) is True
    policy.retrieval_confidence(scope, memory, now=datetime(2026, 7, 3, tzinfo=timezone.utc))
    policy.filter_and_rank(scope, [memory])
    assert memory.source_refs == source_refs_before
    assert memory.applicability == applicability_before


def test_retrieval_confidence_uses_supplied_utc_now_for_exact_weighted_score() -> None:
    scope = make_scope()
    now = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)
    memory = make_memory(
        scope,
        "weighted",
        source_refs=["doi:10.1000/example"],
        applicability={"experiment_environments": ["cuda-12"]},
        updated_at=datetime(2025, 12, 31, 8, tzinfo=timezone.utc),
    )
    policy = ResearchPolicy()

    first = policy.retrieval_confidence(scope, memory, now=now)
    second = policy.retrieval_confidence(scope, memory, now=now)

    freshness = 1.0 - (201 - 183) / 365
    expected = 0.35 * 0.6 + 0.25 * 1.0 + 0.20 * 1.0 + 0.10 * freshness + 0.10 * 0.25
    assert first == expected
    assert second == expected
    assert 0.0 <= first <= 1.0
