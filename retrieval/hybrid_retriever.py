"""以倒数排名融合多路只读证据候选。"""

from __future__ import annotations

from dataclasses import replace
import math
from typing import Sequence

from core.research_models import EvidenceChunk


def retrieve(
    user_id: str,
    query: str,
    scenario: object,
    top_k: int,
    memory_types: list[object],
    include_statuses: list[object],
    debug: bool,
) -> list[object]:
    """Provide the legacy route's empty-result fallback until retrieval is wired."""

    del user_id, query, scenario, top_k, memory_types, include_statuses, debug
    return []


class HybridRetriever:
    """使用固定常数的 RRF 将多个候选列表融合为确定性结果。"""

    _RRF_K = 60

    def fuse(
        self, candidate_lists: Sequence[Sequence[EvidenceChunk]]
    ) -> list[EvidenceChunk]:
        """融合候选列表；仅多路结果副本记录有限的 RRF 分数。"""

        has_multiple_routes = sum(bool(candidates) for candidates in candidate_lists) > 1
        fused: dict[str, tuple[EvidenceChunk, float]] = {}
        for candidates in candidate_lists:
            route_chunks: dict[str, EvidenceChunk] = {}
            for rank, chunk in enumerate(candidates, start=1):
                route_chunk = route_chunks.get(chunk.chunk_id)
                if route_chunk is not None:
                    if not self._has_matching_identity(route_chunk, chunk):
                        raise ValueError(
                            f"conflicting evidence identity for chunk_id {chunk.chunk_id!r}"
                        )
                    # 同一路重复候选不改变其首次出现时的排名贡献。
                    continue
                route_chunks[chunk.chunk_id] = chunk

                existing = fused.get(chunk.chunk_id)
                if existing is None:
                    fused[chunk.chunk_id] = (
                        chunk,
                        1 / (self._RRF_K + rank) if has_multiple_routes else 0.0,
                    )
                    continue

                existing_chunk, existing_score = existing
                if not self._has_matching_identity(existing_chunk, chunk):
                    raise ValueError(
                        f"conflicting evidence identity for chunk_id {chunk.chunk_id!r}"
                    )
                if has_multiple_routes:
                    fused[chunk.chunk_id] = (
                        existing_chunk,
                        existing_score + 1 / (self._RRF_K + rank),
                    )

        results: list[EvidenceChunk] = []
        for chunk, rrf_score in fused.values():
            metadata = dict(chunk.metadata)
            if has_multiple_routes:
                if not math.isfinite(rrf_score):
                    raise ValueError("rrf_score must be finite")
                metadata["rrf_score"] = rrf_score
            else:
                metadata.pop("rrf_score", None)
            results.append(replace(chunk, metadata=metadata))

        if not has_multiple_routes:
            return sorted(
                results,
                key=lambda chunk: (chunk.source_ref, chunk.locator or "", chunk.chunk_id),
            )

        return sorted(
            results,
            key=lambda chunk: (
                -float(chunk.metadata["rrf_score"]),
                chunk.source_ref,
                chunk.locator or "",
                chunk.chunk_id,
            ),
        )

    @staticmethod
    def _has_matching_identity(first: EvidenceChunk, second: EvidenceChunk) -> bool:
        """确认共享 ID 的候选确实描述同一段可追溯证据。"""

        return (
            first.source_ref == second.source_ref
            and first.locator == second.locator
            and first.scope == second.scope
            and first.content == second.content
        )
