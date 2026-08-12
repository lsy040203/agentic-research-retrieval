"""Embedding 上游的最小稳定适配边界。"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Protocol


@dataclass(frozen=True)
class EmbeddingResult:
    """一次查询嵌入及其明确的模型标识，不绑定任何 SDK。"""

    values: tuple[float, ...]
    model_id: str

    def __post_init__(self) -> None:
        """在进入适配边界时拒绝无效向量并标准化可接受数值。"""

        if not isinstance(self.values, tuple) or not self.values:
            raise ValueError("values must be a non-empty tuple")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in self.values):
            raise ValueError("values must contain only finite numbers")
        try:
            normalized_values = tuple(float(value) for value in self.values)
        except (OverflowError, TypeError, ValueError) as error:
            raise ValueError("values must contain only finite numbers") from error
        if not all(math.isfinite(value) for value in normalized_values):
            raise ValueError("values must contain only finite numbers")
        if not isinstance(self.model_id, str) or not self.model_id.strip():
            raise ValueError("model_id must be a non-empty string")
        object.__setattr__(self, "values", normalized_values)
        object.__setattr__(self, "model_id", self.model_id.strip())


class EmbeddingProvider(Protocol):
    """本地 embedding 适配器必须提供的只读查询接口。"""

    def embed_query(self, query: str) -> EmbeddingResult:
        """为已验证的查询返回向量与产生该向量的模型标识。"""
