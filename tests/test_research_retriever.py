"""隔离作用域研究记忆检索器的测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.constants import ResearchMemoryKind, ResearchMemoryStatus
from core.research_models import ResearchMemory, ScopeKey
from memory.research_store import ResearchStore
from policy.retrieval_router import RetrievalRouter
from retrieval.research_retriever import ResearchEvidenceRetriever, ResearchRetriever


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


def make_memory(scope: ScopeKey, memory_id: str, **overrides: object) -> ResearchMemory:
    values = {
        "memory_id": memory_id,
        "scope": scope,
        "kind": ResearchMemoryKind.KNOWLEDGE,
        "title": "Observed result",
        "content": "The retrieval threshold improved precision.",
        "source_refs": ["run://42"],
        "confidence": 0.9,
        "applicability": {"experiment_environments": [scope.experiment_environment]},
        "status": ResearchMemoryStatus.PUBLISHED,
        "created_at": datetime(2026, 7, 1, 9, 30, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 7, 1, 10, 30, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return ResearchMemory(**values)


def test_retrieve_returns_only_policy_approved_published_memories(tmp_path):
    scope = make_scope()
    store = ResearchStore(tmp_path / "research.db")
    published = make_memory(scope, "published-id")
    store.save(published)
    store.save(
        make_memory(
            scope,
            "candidate-id",
            status=ResearchMemoryStatus.CANDIDATE,
        )
    )
    store.save(
        make_memory(
            scope,
            "revoked-id",
            status=ResearchMemoryStatus.REVOKED,
        )
    )
    store.save(make_memory(make_scope(branch="feature/other"), "foreign-id"))

    result = ResearchRetriever(store).retrieve(scope)

    assert result == [published]


def test_retrieve_only_uses_list_published_and_delegates_to_policy():
    scope = make_scope()
    candidate = make_memory(scope, "published-id")

    class StoreSpy:
        def __init__(self) -> None:
            self.scopes: list[ScopeKey] = []

        def list_published(self, received_scope: ScopeKey) -> list[ResearchMemory]:
            self.scopes.append(received_scope)
            return [candidate]

        def __getattr__(self, name: str):
            raise AssertionError(f"unexpected store method access: {name}")

    class PolicySpy:
        def __init__(self) -> None:
            self.calls: list[tuple[ScopeKey, list[ResearchMemory]]] = []

        def filter_and_rank(
            self, received_scope: ScopeKey, candidates: list[ResearchMemory]
        ) -> list[ResearchMemory]:
            self.calls.append((received_scope, candidates))
            return candidates

    store = StoreSpy()
    policy = PolicySpy()

    result = ResearchRetriever(store, policy).retrieve(scope)

    assert result == [candidate]
    assert store.scopes == [scope]
    assert policy.calls == [(scope, [candidate])]


def test_retrieve_preserves_an_explicit_falsey_policy():
    scope = make_scope()
    candidate = make_memory(scope, "published-id")

    class StoreSpy:
        def list_published(self, received_scope: ScopeKey) -> list[ResearchMemory]:
            assert received_scope == scope
            return [candidate]

    class FalseyPolicy:
        def __bool__(self) -> bool:
            return False

        def filter_and_rank(
            self, received_scope: ScopeKey, candidates: list[ResearchMemory]
        ) -> list[ResearchMemory]:
            assert received_scope == scope
            return []

    assert ResearchRetriever(StoreSpy(), FalseyPolicy()).retrieve(scope) == []


class EvidenceStore:
    """供研究证据检索测试使用的只读内存存储。"""

    def __init__(self, memories: list[ResearchMemory], index_updated_at: object = None) -> None:
        self.memories = memories
        self.index_updated_at = index_updated_at
        self.scopes: list[ScopeKey] = []

    def list_published(self, scope: ScopeKey) -> list[ResearchMemory]:
        self.scopes.append(scope)
        return list(self.memories)


class PassThroughPolicy:
    """记录筛选调用，并可模拟上游政策返回的候选项。"""

    def __init__(self, result: list[ResearchMemory] | None = None) -> None:
        self.result = result
        self.calls: list[tuple[ScopeKey, list[ResearchMemory]]] = []

    def filter_and_rank(
        self, scope: ScopeKey, memories: list[ResearchMemory]
    ) -> list[ResearchMemory]:
        self.calls.append((scope, memories))
        return list(memories if self.result is None else self.result)


def test_evidence_retriever_only_emits_matching_usable_published_memories() -> None:
    scope = make_scope()
    matched = make_memory(scope, "published", title="Precision study", content="retrieval notes")
    candidate = make_memory(scope, "candidate", title="Precision candidate", status=ResearchMemoryStatus.CANDIDATE)
    revoked = make_memory(scope, "revoked", title="Precision revoked", status=ResearchMemoryStatus.REVOKED)
    wrong_environment = make_memory(
        scope,
        "wrong-environment",
        title="Precision environment",
        applicability={"experiment_environments": ["cpu"]},
    )
    policy = PassThroughPolicy()
    store = EvidenceStore([matched, candidate, revoked, wrong_environment])

    result = ResearchEvidenceRetriever(store, policy).retrieve("PRECISION", scope)

    assert [chunk.chunk_id for chunk in result] == ["research:published"]
    assert store.scopes == [scope]
    assert policy.calls == [(scope, [matched, candidate, revoked, wrong_environment])]


def test_evidence_retriever_ranks_title_matches_before_content_then_confidence() -> None:
    scope = make_scope()
    title_low = make_memory(scope, "z-title", title="Threshold guide", content="unrelated", confidence=0.1)
    title_high = make_memory(scope, "a-title", title="threshold result", content="unrelated", confidence=0.8)
    content_high = make_memory(scope, "content", title="Other", content="Threshold result", confidence=0.99)

    result = ResearchEvidenceRetriever(
        EvidenceStore([content_high, title_low, title_high]), PassThroughPolicy()
    ).retrieve("THRESHOLD", scope)

    assert [chunk.chunk_id for chunk in result] == [
        "research:a-title",
        "research:z-title",
        "research:content",
    ]


def test_evidence_retriever_ranks_multi_token_coverage_before_confidence() -> None:
    scope = make_scope()
    title_two_tokens = make_memory(
        scope, "title-two", title="Alpha beta finding", content="unrelated", confidence=0.1
    )
    title_one_token = make_memory(
        scope, "title-one", title="Alpha finding", content="beta is in the body", confidence=0.99
    )

    result = ResearchEvidenceRetriever(
        EvidenceStore([title_one_token, title_two_tokens]), PassThroughPolicy()
    ).retrieve("alpha beta", scope)

    assert [chunk.chunk_id for chunk in result] == [
        "research:title-two",
        "research:title-one",
    ]


def test_evidence_retriever_returns_no_evidence_for_unmatched_query() -> None:
    scope = make_scope()
    memory = make_memory(scope, "published")

    retriever = ResearchEvidenceRetriever(EvidenceStore([memory]), PassThroughPolicy())

    assert retriever.retrieve("absent-token", scope) == []
    with pytest.raises(ValueError, match="query"):
        retriever.retrieve("   ", scope)


def test_router_records_research_memory_dependency_failure_without_content() -> None:
    scope = make_scope()
    timestamp = datetime.now(timezone.utc) - timedelta(seconds=1)

    class BrokenPolicy:
        def filter_and_rank(self, scope: ScopeKey, memories: list[ResearchMemory]) -> list[ResearchMemory]:
            raise RuntimeError("policy unavailable")

    router = RetrievalRouter(max_index_age_seconds=60)
    router.register(
        ResearchEvidenceRetriever(EvidenceStore([make_memory(scope, "published")], timestamp), BrokenPolicy())
    )

    result = router.retrieve("retrieval", scope)

    assert result.evidence == []
    assert result.partial is True
    assert "tool_error:research-memory" in result.degradations
    assert result.traces[0].reason == "tool_error"


def test_router_records_research_memory_store_failure_without_content() -> None:
    scope = make_scope()
    timestamp = datetime.now(timezone.utc) - timedelta(seconds=1)

    class BrokenStore(EvidenceStore):
        def list_published(self, scope: ScopeKey) -> list[ResearchMemory]:
            raise RuntimeError("store unavailable")

    router = RetrievalRouter(max_index_age_seconds=60)
    router.register(ResearchEvidenceRetriever(BrokenStore([], timestamp)))

    result = router.retrieve("retrieval", scope)

    assert result.evidence == []
    assert result.partial is True
    assert "tool_error:research-memory" in result.degradations
    assert result.traces[0].reason == "tool_error"


def test_evidence_retriever_enforces_scope_and_copies_traceable_metadata() -> None:
    scope = make_scope()
    foreign = make_memory(make_scope(branch="feature/other"), "foreign", title="retrieval")
    matched = make_memory(
        scope,
        "local",
        title="Retrieval evidence",
        source_refs=["run://42"],
        applicability={"locator": "report.md#L12", "nested": {"labels": ["stable"]}},
    )
    policy = PassThroughPolicy([foreign, matched])
    retriever = ResearchEvidenceRetriever(EvidenceStore([matched]), policy)

    result = retriever.retrieve("retrieval", scope)

    assert len(result) == 1
    chunk = result[0]
    assert (chunk.chunk_id, chunk.source_ref, chunk.locator) == (
        "research:local",
        "research_memory:local",
        "report.md#L12",
    )
    assert chunk.content == "Retrieval evidence\n\nThe retrieval threshold improved precision."
    assert chunk.metadata == {
        "retriever": "research_memory",
        "memory_id": "local",
        "kind": "knowledge",
        "confidence": 0.9,
        "source_refs": ["run://42"],
        "applicability": {"locator": "report.md#L12", "nested": {"labels": ["stable"]}},
    }
    chunk.metadata["applicability"]["nested"]["labels"].append("changed")
    assert matched.applicability == {"locator": "report.md#L12", "nested": {"labels": ["stable"]}}
    assert matched.source_refs == ["run://42"]


def test_evidence_retriever_exposes_router_contract_and_safe_store_freshness() -> None:
    scope = make_scope()
    timestamp = datetime.now(timezone.utc) - timedelta(seconds=1)
    retriever = ResearchEvidenceRetriever(EvidenceStore([], timestamp))

    assert (retriever.name, retriever.provider, retriever.read_only) == (
        "research-memory",
        "local",
        True,
    )
    assert retriever.index_updated_at == timestamp
    assert ResearchEvidenceRetriever(EvidenceStore([], "not-a-time")).index_updated_at is None


def test_router_accepts_fresh_real_research_store_and_rejects_empty_or_future_scope(tmp_path) -> None:
    scope = make_scope()
    now = datetime.now(timezone.utc)
    store = ResearchStore(tmp_path / "research.db")
    store.save(
        make_memory(
            scope,
            "fresh",
            title="Retrieval finding",
            updated_at=now - timedelta(seconds=1),
        )
    )
    router = RetrievalRouter(max_index_age_seconds=60)
    router.register(ResearchEvidenceRetriever(store, freshness_scope=scope))

    accepted = router.retrieve("retrieval", scope)

    assert [chunk.chunk_id for chunk in accepted.evidence] == ["research:fresh"]
    assert accepted.rejections == []

    empty_router = RetrievalRouter(max_index_age_seconds=60)
    empty_router.register(
        ResearchEvidenceRetriever(store, freshness_scope=make_scope(branch="empty"))
    )
    assert empty_router.retrieve("retrieval", make_scope(branch="empty")).rejections == [
        "research-memory:stale_index"
    ]

    future_scope = make_scope(branch="future")
    store.save(
        make_memory(
            future_scope,
            "future",
            title="Retrieval future",
            updated_at=now + timedelta(days=1),
        )
    )
    future_router = RetrievalRouter(max_index_age_seconds=60)
    future_router.register(ResearchEvidenceRetriever(store, freshness_scope=future_scope))
    assert future_router.retrieve("retrieval", future_scope).rejections == [
        "research-memory:stale_index"
    ]
