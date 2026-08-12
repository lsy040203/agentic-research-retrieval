"""HybridRetriever 的 RRF 融合行为测试。"""

from __future__ import annotations

from math import isclose

import pytest

from core.research_models import EvidenceChunk, ScopeKey
from retrieval.hybrid_retriever import HybridRetriever


def _scope() -> ScopeKey:
    """构造所有测试共用且可比较的检索范围。"""

    return ScopeKey(
        team_id="team",
        project_id="project",
        repository="repository",
        branch="main",
        experiment_environment="test",
    )


def _chunk(
    chunk_id: str,
    *,
    source_ref: str = "source",
    locator: str | None = "line:1",
    content: str = "evidence",
    scope: ScopeKey | None = None,
    metadata: dict[str, object] | None = None,
) -> EvidenceChunk:
    """创建可按需变化的证据块测试数据。"""

    return EvidenceChunk(
        chunk_id=chunk_id,
        scope=_scope() if scope is None else scope,
        content=content,
        source_ref=source_ref,
        locator=locator,
        metadata={} if metadata is None else metadata,
    )


def test_fuse_sums_rrf_scores_from_two_candidate_lists() -> None:
    """同一证据在两路的第一、第二名分数应累加。"""

    shared = _chunk("shared")
    result = HybridRetriever().fuse([[shared], [_chunk("other"), shared]])

    assert [chunk.chunk_id for chunk in result] == ["shared", "other"]
    assert isclose(result[0].metadata["rrf_score"], 1 / 61 + 1 / 62)


def test_fuse_deduplicates_identical_chunk_ids() -> None:
    """身份完全相同的同 ID 证据仅应保留一个输出副本。"""

    result = HybridRetriever().fuse([[_chunk("same")], [_chunk("same")]])

    assert len(result) == 1
    assert result[0].chunk_id == "same"


@pytest.mark.parametrize(
    ("first_kwargs", "second_kwargs"),
    [
        ({"source_ref": "first"}, {"source_ref": "second"}),
        ({"locator": "line:1"}, {"locator": "line:2"}),
        (
            {},
            {
                "scope": ScopeKey(
                    team_id="other-team",
                    project_id="project",
                    repository="repository",
                    branch="main",
                    experiment_environment="test",
                )
            },
        ),
    ],
    ids=["source-ref", "locator", "scope"],
)
def test_fuse_rejects_conflicting_identity_for_a_chunk_id(
    first_kwargs: dict[str, object], second_kwargs: dict[str, object]
) -> None:
    """相同 ID 的来源、定位符或范围不一致时必须拒绝融合。"""

    with pytest.raises(ValueError, match="chunk_id"):
        HybridRetriever().fuse(
            [[_chunk("same", **first_kwargs)], [_chunk("same", **second_kwargs)]]
        )


def test_fuse_returns_empty_list_for_empty_input() -> None:
    """没有候选路由时返回空列表。"""

    assert HybridRetriever().fuse([]) == []


def test_fuse_does_not_mutate_input_chunk_or_metadata() -> None:
    """融合结果必须使用新对象和新 metadata，不能写入输入。"""

    original_metadata: dict[str, object] = {"retriever": "grep"}
    original = _chunk("immutable", metadata=original_metadata)

    result = HybridRetriever().fuse([[original], [_chunk("other")]])

    assert original.metadata == {"retriever": "grep"}
    assert result[0] is not original
    assert result[0].metadata is not original.metadata
    assert result[0].metadata == {"retriever": "grep", "rrf_score": 1 / 61}


def test_fuse_uses_single_route_as_a_scoreless_deduplicated_sort() -> None:
    """唯一非空候选路由不写 RRF，且不能被原始 rank 影响排序。"""

    duplicate = _chunk("z", source_ref="b", locator="line:2")
    result = HybridRetriever().fuse(
        [
            [],
            [
                duplicate,
                duplicate,
                _chunk("y", source_ref="a", locator="line:3"),
                _chunk("x", source_ref="a", locator=None),
            ],
        ]
    )

    assert [chunk.chunk_id for chunk in result] == ["x", "y", "z"]
    assert all("rrf_score" not in chunk.metadata for chunk in result)


def test_fuse_removes_an_inherited_rrf_score_from_a_single_route_copy() -> None:
    """单路输出不得继承旧融合结果的 RRF 分数，且不能修改输入。"""

    original = _chunk(
        "stale-score", metadata={"retriever": "grep", "rrf_score": 0.75}
    )

    result = HybridRetriever().fuse([[original]])

    assert original.metadata == {"retriever": "grep", "rrf_score": 0.75}
    assert result[0].metadata == {"retriever": "grep"}
    assert "rrf_score" not in result[0].metadata


def test_fuse_counts_only_a_duplicate_chunks_first_rank_within_each_route() -> None:
    """同一路的重复候选只使用第一次出现的 rank 参与多路 RRF。"""

    shared = _chunk("shared")
    result = HybridRetriever().fuse([[shared, shared], [shared]])

    assert isclose(result[0].metadata["rrf_score"], 2 / 61)


def test_fuse_rejects_conflicting_duplicate_chunk_within_a_route() -> None:
    """同一路的重复 ID 若身份不一致，也不能静默跳过。"""

    with pytest.raises(ValueError, match="chunk_id"):
        HybridRetriever().fuse(
            [[_chunk("same", source_ref="first"), _chunk("same", source_ref="second")]]
        )


def test_fuse_uses_required_stable_tie_breaking_order() -> None:
    """同分结果按来源、定位符和 ID 的确定性顺序返回。"""

    result = HybridRetriever().fuse(
        [
            [_chunk("z", source_ref="b", locator="line:2")],
            [_chunk("y", source_ref="a", locator="line:3")],
            [_chunk("x", source_ref="a", locator=None)],
        ]
    )

    assert [chunk.chunk_id for chunk in result] == ["x", "y", "z"]
