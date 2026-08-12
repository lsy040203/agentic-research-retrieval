"""将持久化 BM25 索引适配为只读本地检索工具。"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import math
from pathlib import Path

from core.research_models import EvidenceChunk, ScopeKey
from retrieval.bm25_index import BM25Index, BM25IndexError


class BM25Retriever:
    """仅从本地 BM25 索引返回同 Scope 的证据副本。"""

    name = "bm25"
    provider = "local"
    read_only = True

    def __init__(
        self, index: BM25Index | str | Path, *, max_index_age_seconds: float | None = 86400
    ) -> None:
        if (
            max_index_age_seconds is not None
            and (
                isinstance(max_index_age_seconds, bool)
                or not isinstance(max_index_age_seconds, (int, float))
                or not math.isfinite(max_index_age_seconds)
                or max_index_age_seconds < 0
            )
        ):
            raise ValueError("max_index_age_seconds must not be negative")
        self._index = index if isinstance(index, BM25Index) else BM25Index(index)
        self._max_index_age_seconds = (
            None if max_index_age_seconds is None else float(max_index_age_seconds)
        )
        # Router 在首次 retrieve 前也会预检此字段，因此从已有索引加载最新时间。
        built_at = self._index.latest_built_at()
        self.index_updated_at: datetime | None = (
            None if built_at is None or self._is_expired(built_at) else built_at
        )

    def retrieve(self, query: str, scope: ScopeKey) -> list[EvidenceChunk]:
        """检索评分证据；索引异常或 Scope 问题一律安全降级为空列表。"""

        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must not be blank")
        if not isinstance(scope, ScopeKey):
            return []
        try:
            loaded = self._index.load(scope)
            self.index_updated_at = None if self._is_expired(loaded.built_at) else loaded.built_at
            if self._is_expired(loaded.built_at):
                return []
            # 新鲜度校验与评分严格复用同一份已校验内存快照。
            matches = self._index.query_loaded(loaded, query)
        except (BM25IndexError, OSError, TypeError, ValueError, ArithmeticError):
            return []

        results: list[EvidenceChunk] = []
        for chunk, score in matches:
            if not math.isfinite(score) or score < 0:
                continue
            metadata = dict(chunk.metadata)
            metadata.update({"retriever": "bm25", "bm25_score": float(score), "index_version": 1})
            # replace 与新 metadata 共同保证不写入原索引恢复出的对象。
            results.append(replace(chunk, metadata=metadata))
        return results

    def _is_expired(self, built_at: datetime) -> bool:
        """按可选最大年龄拒绝旧索引，避免本地缓存返回过期内容。"""

        now = datetime.now(timezone.utc)
        # 未来时间不能伪装为新鲜索引，也不能交给 Router 准入。
        if built_at > now:
            return True
        if self._max_index_age_seconds is None:
            return False
        return (now - built_at).total_seconds() > self._max_index_age_seconds
