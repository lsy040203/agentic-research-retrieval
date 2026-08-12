"""VectorRetriever 的离线契约测试，不访问真实 embedding 或向量索引。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo

import pytest

from core.research_models import ScopeKey
from embeddings.embedding_service import EmbeddingResult
from retrieval.vector_retriever import VectorRetriever
from vector_store.vector_service import VectorHit


SCOPE = ScopeKey("team", "project", "repo", "main", "test")
OTHER_SCOPE = ScopeKey("team", "project", "repo", "dev", "test")
UNSET = object()


class FakeProvider:
    """记录调用参数的离线 embedding 提供者。"""

    def __init__(self, result: object = UNSET, error: Exception | None = None) -> None:
        self.result = EmbeddingResult((0.1, 0.2), "kylin-v1") if result is UNSET else result
        self.error = error
        self.calls: list[str] = []

    def embed_query(self, query: str) -> object:
        self.calls.append(query)
        if self.error is not None:
            raise self.error
        return self.result


class FakeStore:
    """记录查询向量、Scope 与 limit 的离线向量索引。"""

    def __init__(
        self,
        hits: object = (),
        error: Exception | None = None,
        index_updated_at: object | None = None,
    ) -> None:
        self.hits = hits
        self.error = error
        self.index_updated_at = index_updated_at
        self.calls: list[tuple[EmbeddingResult, ScopeKey, int]] = []

    def search(self, vector: EmbeddingResult, scope: ScopeKey, limit: int) -> object:
        self.calls.append((vector, scope, limit))
        if self.error is not None:
            raise self.error
        return self.hits


def hit(
    chunk_id: str,
    *,
    score: float = 0.5,
    scope: ScopeKey = SCOPE,
    model_id: str = "kylin-v1",
    source_ref: str | None = None,
    locator: str | None = None,
    content: str = "safe evidence",
) -> VectorHit:
    return VectorHit(
        chunk_id=chunk_id,
        scope=scope,
        source_ref=source_ref or f"source:{chunk_id}",
        locator=locator,
        content=content,
        score=score,
        embedding_model_id=model_id,
    )


def unsafe_embedding(values: object, model_id: object) -> EmbeddingResult:
    """绕过 DTO 构造器，模拟违反已发布契约的失陷上游。"""

    value = object.__new__(EmbeddingResult)
    object.__setattr__(value, "values", values)
    object.__setattr__(value, "model_id", model_id)
    return value


def unsafe_hit(**values: object) -> VectorHit:
    """绕过 DTO 构造器，验证 Retriever 对失陷上游的二次防御。"""

    value = object.__new__(VectorHit)
    defaults = {
        "chunk_id": "forged",
        "scope": SCOPE,
        "source_ref": "source:forged",
        "locator": None,
        "content": "safe evidence",
        "score": 0.5,
        "embedding_model_id": "kylin-v1",
    }
    defaults.update(values)
    for name, item in defaults.items():
        object.__setattr__(value, name, item)
    return value


class ForgedScope:
    """以宽松相等性模拟试图绕过 Scope 隔离的非领域对象。"""

    def __eq__(self, other: object) -> bool:
        return True

    def __ne__(self, other: object) -> bool:
        return False


class ExplodingTimezone(tzinfo):
    """在规范化时抛错的时区，用于验证不安全时间戳被拒绝。"""

    def utcoffset(self, value: datetime | None) -> timedelta:
        raise ValueError("invalid timezone")

    def dst(self, value: datetime | None) -> timedelta:
        raise ValueError("invalid timezone")


class MalformedOffsetTimezone(tzinfo):
    """返回非法 UTC 偏移量，模拟 datetime 规范化时的 TypeError。"""

    def utcoffset(self, value: datetime | None):
        return 1

    def dst(self, value: datetime | None):
        return None


def test_retrieves_valid_hits_and_passes_embedding_and_scope_to_store() -> None:
    provider = FakeProvider()
    store = FakeStore([hit("a", score=0.8)])
    retriever = VectorRetriever(provider, store, limit=7)

    result = retriever.retrieve("how to test", SCOPE)

    assert provider.calls == ["how to test"]
    assert store.calls == [(EmbeddingResult((0.1, 0.2), "kylin-v1"), SCOPE, 7)]
    assert len(result) == 1
    assert result[0].chunk_id == "a"
    assert result[0].vector_score == 0.8
    assert result[0].metadata == {
        "retriever": "vector",
        "embedding_model_id": "kylin-v1",
        "vector_score": 0.8,
    }


@pytest.mark.parametrize("hits", [[hit("model", model_id="other")], [hit("scope", scope=OTHER_SCOPE)]])
def test_rejects_unmatched_hits(hits: list[VectorHit]) -> None:
    assert VectorRetriever(FakeProvider(), FakeStore(hits)).retrieve("query", SCOPE) == []


@pytest.mark.parametrize(
    "result",
    [
        unsafe_embedding((), "kylin-v1"),
        unsafe_embedding((0.1, float("inf")), "kylin-v1"),
        unsafe_embedding((0.1,), " "),
        object(),
        None,
        (),
    ],
)
def test_invalid_provider_result_never_queries_store(result: object) -> None:
    store = FakeStore([hit("unused")])
    assert VectorRetriever(FakeProvider(result), store).retrieve("query", SCOPE) == []
    assert store.calls == []


@pytest.mark.parametrize(
    "factory",
    [
        lambda: EmbeddingResult((), "kylin-v1"),
        lambda: EmbeddingResult((0.1, True), "kylin-v1"),
        lambda: EmbeddingResult((0.1, float("inf")), "kylin-v1"),
        lambda: EmbeddingResult((10**10000,), "kylin-v1"),
        lambda: EmbeddingResult((0.1,), " "),
        lambda: VectorHit("a", ForgedScope(), "source", None, "content", 0.5, "kylin-v1"),
        lambda: VectorHit(" ", SCOPE, "source", None, "content", 0.5, "kylin-v1"),
        lambda: VectorHit("a", SCOPE, "source", 1, "content", 0.5, "kylin-v1"),
        lambda: VectorHit("a", SCOPE, "source", None, "content", float("nan"), "kylin-v1"),
        lambda: VectorHit("a", SCOPE, "source", None, "content", 10**10000, "kylin-v1"),
    ],
)
def test_dto_construction_rejects_invalid_boundary_values(factory) -> None:
    with pytest.raises((TypeError, ValueError)):
        factory()


def test_retriever_rejects_forged_scope_even_when_it_claims_equality() -> None:
    forged = unsafe_hit(scope=ForgedScope())
    assert VectorRetriever(FakeProvider(), FakeStore([forged])).retrieve("query", SCOPE) == []


@pytest.mark.parametrize("provider_error,store_error", [(RuntimeError("x"), None), (None, RuntimeError("x"))])
def test_provider_or_store_error_safely_degrades(provider_error, store_error) -> None:
    provider = FakeProvider(error=provider_error)
    store = FakeStore([hit("a")], error=store_error)
    assert VectorRetriever(provider, store).retrieve("query", SCOPE) == []


def test_blank_query_is_rejected_without_provider_call() -> None:
    provider = FakeProvider()
    assert VectorRetriever(provider, FakeStore()).retrieve("  ", SCOPE) == []
    assert provider.calls == []


def test_stably_sorts_and_creates_independent_evidence_metadata() -> None:
    upstream = hit("z", score=0.8, source_ref="source-b", locator="2")
    store = FakeStore([upstream, hit("a", score=0.8, source_ref="source-a", locator="9"), hit("low", score=0.1)])

    result = VectorRetriever(FakeProvider(), store).retrieve("query", SCOPE)

    assert [item.chunk_id for item in result] == ["a", "z", "low"]
    result[0].metadata["changed"] = True
    assert not hasattr(upstream, "metadata")
    assert result[1].metadata == {
        "retriever": "vector",
        "embedding_model_id": "kylin-v1",
        "vector_score": 0.8,
    }


def test_conflicting_duplicate_chunk_identity_rejects_entire_batch() -> None:
    assert VectorRetriever(FakeProvider(), FakeStore([hit("same"), hit("same", source_ref="other")])).retrieve("query", SCOPE) == []


def test_router_contract_and_freshness_are_derived_only_from_store() -> None:
    timestamp = datetime.now(timezone.utc) - timedelta(seconds=1)
    retriever = VectorRetriever(FakeProvider(), FakeStore(index_updated_at=timestamp))
    unknown = VectorRetriever(FakeProvider(), object())

    assert (retriever.name, retriever.provider, retriever.read_only) == ("vector", "local", True)
    assert retriever.index_updated_at == timestamp
    assert unknown.index_updated_at is None


@pytest.mark.parametrize(
    "timestamp",
    [
        datetime.now(timezone.utc) + timedelta(days=1),
        "not a datetime",
        datetime(2020, 1, 1, tzinfo=ExplodingTimezone()),
        datetime(2020, 1, 1, tzinfo=MalformedOffsetTimezone()),
    ],
)
def test_router_freshness_rejects_future_or_unusable_store_timestamp(timestamp: object) -> None:
    retriever = VectorRetriever(FakeProvider(), FakeStore(index_updated_at=timestamp))
    assert retriever.index_updated_at is None
