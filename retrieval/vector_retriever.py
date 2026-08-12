"""将本地向量上游安全适配为可路由的证据检索工具。"""

from __future__ import annotations

from datetime import datetime, timezone
import math

from core.research_models import EvidenceChunk, ScopeKey
from embeddings.embedding_service import EmbeddingProvider, EmbeddingResult
from vector_store.vector_service import VectorHit, VectorStore


class VectorRetriever:
    """校验 embedding 与索引返回值，并仅产出同范围的证据副本。"""

    name = "vector"
    provider = "local"
    read_only = True

    def __init__(
        self, provider: EmbeddingProvider, store: VectorStore, *, limit: int = 10
    ) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        self._provider = provider
        self._store = store
        self._limit = limit

    @property
    def index_updated_at(self) -> datetime | None:
        """仅转交索引公开的新鲜度；缺失或异常时拒绝路由而非伪造时间。"""

        try:
            updated_at = getattr(self._store, "index_updated_at", None)
        except Exception:
            return None
        if not isinstance(updated_at, datetime):
            return None
        try:
            normalized = (
                updated_at.replace(tzinfo=timezone.utc)
                if updated_at.tzinfo is None
                else updated_at.astimezone(timezone.utc)
            )
            if normalized > datetime.now(timezone.utc):
                return None
        except (AttributeError, OverflowError, TypeError, ValueError):
            return None
        return normalized

    def retrieve(self, query: str, scope: ScopeKey) -> list[EvidenceChunk]:
        """安全降级：任一上游错误、无效值或隔离不匹配均返回空列表。"""

        if not isinstance(query, str) or not query.strip() or not isinstance(scope, ScopeKey):
            return []
        try:
            embedding = self._provider.embed_query(query)
        except Exception:
            return []
        if not self._is_valid_embedding(embedding):
            return []

        try:
            hits = self._store.search(embedding, scope, self._limit)
            candidates = list(hits)
        except Exception:
            return []

        validated: list[VectorHit] = []
        identities: dict[str, tuple[object, ...]] = {}
        for hit in candidates:
            if not self._is_valid_hit(hit, scope, embedding.model_id):
                return []
            identity = (hit.scope, hit.source_ref, hit.locator, hit.content, hit.embedding_model_id)
            existing = identities.get(hit.chunk_id)
            if existing is not None and existing != identity:
                # 相同 chunk_id 指向不同身份时无法安全归因，丢弃整个批次。
                return []
            if existing is None:
                identities[hit.chunk_id] = identity
                validated.append(hit)

        validated.sort(
            key=lambda hit: (-float(hit.score), hit.source_ref, hit.locator or "", hit.chunk_id)
        )
        return [
            EvidenceChunk(
                chunk_id=hit.chunk_id,
                scope=hit.scope,
                content=hit.content,
                source_ref=hit.source_ref,
                locator=hit.locator,
                vector_score=float(hit.score),
                metadata={
                    "retriever": "vector",
                    "embedding_model_id": hit.embedding_model_id,
                    "vector_score": float(hit.score),
                },
            )
            for hit in validated
        ]

    @staticmethod
    def _is_valid_embedding(value: object) -> bool:
        """检查向量为非空有限 float 元组，并带有非空模型标识。"""

        if not isinstance(value, EmbeddingResult):
            return False
        if not isinstance(value.values, tuple) or not value.values:
            return False
        if not isinstance(value.model_id, str) or not value.model_id.strip():
            return False
        return all(type(dimension) is float and math.isfinite(dimension) for dimension in value.values)

    @staticmethod
    def _is_valid_hit(hit: object, scope: ScopeKey, model_id: str) -> bool:
        """只接受可归因、同 Scope、同模型且分数受限的索引命中。"""

        if not isinstance(hit, VectorHit) or not isinstance(hit.scope, ScopeKey):
            return False
        if hit.scope != scope or hit.embedding_model_id != model_id:
            return False
        if not all(
            isinstance(value, str) and value.strip()
            for value in (hit.chunk_id, hit.source_ref, hit.content, hit.embedding_model_id)
        ):
            return False
        if hit.locator is not None and (not isinstance(hit.locator, str) or not hit.locator.strip()):
            return False
        return (
            not isinstance(hit.score, bool)
            and isinstance(hit.score, (int, float))
            and math.isfinite(float(hit.score))
            and 0.0 <= float(hit.score) <= 1.0
        )
