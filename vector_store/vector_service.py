"""向量索引上游的最小稳定适配边界。"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Protocol, Sequence

from core.research_models import ScopeKey
from embeddings.embedding_service import EmbeddingResult


@dataclass(frozen=True)
class VectorHit:
    """向量索引返回的原始命中；校验责任由检索边界承担。"""

    chunk_id: str
    scope: ScopeKey
    source_ref: str
    locator: str | None
    content: str
    score: float
    embedding_model_id: str

    def __post_init__(self) -> None:
        """拒绝不能安全归因或不能安全排序的索引原始命中。"""

        if not isinstance(self.scope, ScopeKey):
            raise TypeError("scope must be a ScopeKey")
        for name in ("chunk_id", "source_ref", "content", "embedding_model_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
            object.__setattr__(self, name, value.strip())
        if self.locator is not None:
            if not isinstance(self.locator, str) or not self.locator.strip():
                raise ValueError("locator must be a non-empty string or None")
            object.__setattr__(self, "locator", self.locator.strip())
        if isinstance(self.score, bool) or not isinstance(self.score, (int, float)):
            raise ValueError("score must be a finite number in [0, 1]")
        try:
            normalized_score = float(self.score)
        except (OverflowError, TypeError, ValueError) as error:
            raise ValueError("score must be a finite number in [0, 1]") from error
        if not math.isfinite(normalized_score) or not 0.0 <= normalized_score <= 1.0:
            raise ValueError("score must be a finite number in [0, 1]")
        object.__setattr__(self, "score", normalized_score)


class VectorStore(Protocol):
    """本地向量索引只需公开的查询接口。"""

    def search(
        self, vector: EmbeddingResult, scope: ScopeKey, limit: int
    ) -> Sequence[VectorHit]:
        """在指定隔离范围中查询与向量匹配的命中。"""

