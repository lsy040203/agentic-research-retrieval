"""BM25 离线索引与检索器的安全行为测试。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
import math

import pytest

from core.research_models import EvidenceChunk, ScopeKey
from retrieval.bm25_index import BM25Index
from retrieval.bm25_retriever import BM25Retriever


def _scope(**changes: str) -> ScopeKey:
    """创建五维隔离测试范围。"""

    values = {
        "team_id": "team",
        "project_id": "project",
        "repository": "org/repository",
        "branch": "main",
        "experiment_environment": "test",
    }
    values.update(changes)
    return ScopeKey(**values)


def _chunk(chunk_id: str, content: str, *, scope: ScopeKey | None = None, **kwargs: object) -> EvidenceChunk:
    """创建可追溯的本地证据块。"""

    return EvidenceChunk(
        chunk_id=chunk_id,
        scope=scope or _scope(),
        content=content,
        source_ref=str(kwargs.pop("source_ref", f"source:{chunk_id}")),
        locator=kwargs.pop("locator", "line:1"),
        metadata=kwargs.pop("metadata", {}),
        **kwargs,
    )


def test_exact_term_is_ranked_first_and_index_has_required_metadata(tmp_path) -> None:
    """精确稀有术语应优先，索引应记录标准化参数。"""

    scope = _scope()
    index = BM25Index(tmp_path)
    index.build(scope, [_chunk("broad", "alpha beta gamma"), _chunk("exact", "quasar quasar")])

    results = BM25Retriever(index).retrieve("QUASAR", scope)
    payload = json.loads(index.index_path(scope).read_text(encoding="utf-8"))

    assert [result.chunk_id for result in results] == ["exact"]
    assert payload["schema"] == "arr.bm25_index"
    assert payload["version"] == 1
    assert payload["scope"] == {
        "team_id": "team", "project_id": "project", "repository": "org/repository",
        "branch": "main", "experiment_environment": "test",
    }
    assert payload["k1"] == 1.5
    assert payload["b"] == 0.75
    assert payload["document_count"] == 2
    assert payload["avg_doc_length"] > 0
    assert payload["postings"]["quasar"]


def test_high_frequency_term_uses_non_negative_idf(tmp_path) -> None:
    """出现在全部文档中的术语仍必须得到非负稳定 IDF 分数。"""

    scope = _scope()
    index = BM25Index(tmp_path)
    index.build(scope, [_chunk("a", "common one"), _chunk("b", "common two")])

    scores = index.query(scope, "common")

    assert scores and all(score >= 0 for _, score in scores)


def test_scope_isolation_covers_all_five_dimensions(tmp_path) -> None:
    """任一 ScopeKey 维度不同都不得命中另一个索引。"""

    scope = _scope()
    index = BM25Index(tmp_path)
    index.build(scope, [_chunk("only-here", "isolated term", scope=scope)])
    retriever = BM25Retriever(index)

    for field in ("team_id", "project_id", "repository", "branch", "experiment_environment"):
        assert retriever.retrieve("isolated", _scope(**{field: "other"})) == []


def test_rebuild_is_deterministic_and_atomic(tmp_path) -> None:
    """相同输入的重建 JSON 稳定，完成后目录中不遗留临时文件。"""

    scope = _scope()
    index = BM25Index(tmp_path)
    chunks = [_chunk("z", "alpha beta", source_ref="z"), _chunk("a", "beta alpha", source_ref="a")]
    index.build(scope, chunks)
    first = json.loads(index.index_path(scope).read_text(encoding="utf-8"))
    index.build(scope, chunks)
    second = json.loads(index.index_path(scope).read_text(encoding="utf-8"))

    assert first["documents"] == second["documents"]
    assert first["postings"] == second["postings"]
    assert list(tmp_path.glob("*.tmp")) == []


def test_retriever_rejects_empty_query(tmp_path) -> None:
    """空白查询不能进入本地检索。"""

    with pytest.raises(ValueError, match="query"):
        BM25Retriever(BM25Index(tmp_path)).retrieve("  ", _scope())


@pytest.mark.parametrize("invalid_payload", ["{not json", json.dumps({"schema": "wrong", "version": 1})])
def test_corrupt_or_incompatible_index_degrades_to_empty(tmp_path, invalid_payload: str) -> None:
    """损坏或不兼容索引应静默降级，绝不返回原文。"""

    scope = _scope()
    index = BM25Index(tmp_path)
    index.index_path(scope).write_text(invalid_payload, encoding="utf-8")

    assert BM25Retriever(index).retrieve("secret", scope) == []


def test_results_are_copies_with_bm25_metadata_and_index_freshness(tmp_path) -> None:
    """结果不修改输入，且带有 Router 所需的新鲜时间和审计字段。"""

    scope = _scope()
    original = _chunk("original", "needle haystack", metadata={"origin": "input"})
    index = BM25Index(tmp_path)
    index.build(scope, [original])
    retriever = BM25Retriever(index)

    result = retriever.retrieve("needle", scope)[0]

    assert original.metadata == {"origin": "input"}
    assert result is not original
    assert result.metadata["retriever"] == "bm25"
    assert result.metadata["bm25_score"] >= 0
    assert result.metadata["index_version"] == 1
    assert retriever.index_updated_at is not None
    assert retriever.index_updated_at.tzinfo is not None
    assert retriever.index_updated_at <= datetime.now(timezone.utc)


def test_expired_index_degrades_to_empty(tmp_path) -> None:
    """超过检索器允许年龄的索引不能返回旧证据。"""

    scope = _scope()
    index = BM25Index(tmp_path)
    index.build(scope, [_chunk("old", "expired needle")])
    payload = json.loads(index.index_path(scope).read_text(encoding="utf-8"))
    payload["built_at"] = "2000-01-01T00:00:00+00:00"
    index.index_path(scope).write_text(json.dumps(payload), encoding="utf-8")

    assert BM25Retriever(index, max_index_age_seconds=60).retrieve("needle", scope) == []


@pytest.mark.parametrize(
    "tamper",
    [
        lambda payload: payload["postings"].update({"forged": [{"chunk_id": "a", "term_frequency": 1}]}),
        lambda payload: payload["postings"]["alpha"].append({"chunk_id": "b", "term_frequency": 1}),
        lambda payload: payload["postings"]["alpha"][0].update({"term_frequency": 99}),
    ],
    ids=["forged-term", "forged-document-posting", "forged-term-frequency"],
)
def test_forged_valid_json_postings_degrade_without_returning_evidence(tmp_path, tamper) -> None:
    """与正文重新计算不一致的合法 JSON 倒排表必须被拒绝。"""

    scope = _scope()
    index = BM25Index(tmp_path)
    index.build(scope, [_chunk("a", "alpha"), _chunk("b", "beta")])
    payload = json.loads(index.index_path(scope).read_text(encoding="utf-8"))
    tamper(payload)
    index.index_path(scope).write_text(json.dumps(payload), encoding="utf-8")

    assert BM25Retriever(index).retrieve("forged alpha", scope) == []


@pytest.mark.parametrize("value", [True, "60", math.nan, math.inf, -math.inf])
def test_invalid_max_index_age_is_rejected(tmp_path, value: object) -> None:
    """索引年龄配置只允许有限的非负数值。"""

    with pytest.raises(ValueError, match="max_index_age_seconds"):
        BM25Retriever(BM25Index(tmp_path), max_index_age_seconds=value)  # type: ignore[arg-type]


def test_future_index_is_unavailable_to_retriever_and_router(tmp_path) -> None:
    """未来 built_at 不得被视为新鲜，也不能通过 Router 的准入检查。"""

    from policy.retrieval_router import RetrievalRouter

    scope = _scope()
    index = BM25Index(tmp_path)
    index.build(scope, [_chunk("future", "future needle")])
    payload = json.loads(index.index_path(scope).read_text(encoding="utf-8"))
    payload["built_at"] = "2999-01-01T00:00:00+00:00"
    index.index_path(scope).write_text(json.dumps(payload), encoding="utf-8")
    retriever = BM25Retriever(index)
    router = RetrievalRouter(max_index_age_seconds=60)
    router.register(retriever)

    assert retriever.index_updated_at is None
    assert retriever.retrieve("needle", scope) == []
    assert router.retrieve("needle", scope).rejections == ["bm25:stale_index"]


def test_retrieve_scores_the_same_snapshot_used_for_freshness(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """原子替换发生在旧实现的第二次 load 时也不能改变已验证快照的结果。"""

    scope = _scope()
    index = BM25Index(tmp_path)
    index.build(scope, [_chunk("initial", "snapshot needle")])
    original_load = index.load
    load_calls = 0

    def load_with_future_replacement(received_scope: ScopeKey):
        nonlocal load_calls
        load_calls += 1
        if load_calls == 2:
            index.build(scope, [_chunk("future", "replacement needle")])
            payload = json.loads(index.index_path(scope).read_text(encoding="utf-8"))
            payload["built_at"] = "2999-01-01T00:00:00+00:00"
            index.index_path(scope).write_text(json.dumps(payload), encoding="utf-8")
        return original_load(received_scope)

    monkeypatch.setattr(index, "load", load_with_future_replacement)

    results = BM25Retriever(index).retrieve("needle", scope)

    assert [chunk.chunk_id for chunk in results] == ["initial"]
    assert load_calls == 1
