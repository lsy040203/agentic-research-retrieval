"""在隔离作用域内检索已发布研究记忆。"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import math
import re
from typing import Protocol

from core.constants import ResearchMemoryStatus
from core.research_models import EvidenceChunk, ResearchMemory, ScopeKey
from policy.research_policy import ResearchPolicy


_TOKEN_PATTERN = re.compile(r"\w+", re.UNICODE)


class ResearchMemoryStore(Protocol):
    """提供已发布研究记忆读取能力的存储契约。"""

    def list_published(self, scope: ScopeKey) -> list[ResearchMemory]:
        """返回指定作用域内的已发布研究记忆。"""


class ResearchMemoryPolicy(Protocol):
    """提供研究记忆过滤与排序能力的策略契约。"""

    def filter_and_rank(
        self, scope: ScopeKey, memories: list[ResearchMemory]
    ) -> list[ResearchMemory]:
        """过滤并排序给定作用域内的候选记忆。"""


class ResearchRetriever:
    """在指定作用域内检索经策略审核的已发布研究记忆。"""

    def __init__(
        self, store: ResearchMemoryStore, policy: ResearchMemoryPolicy | None = None
    ) -> None:
        """使用给定存储和可选策略创建无副作用的检索器。"""
        self._store = store
        self._policy = ResearchPolicy() if policy is None else policy

    def retrieve(self, scope: ScopeKey) -> list[ResearchMemory]:
        """返回当前作用域内经策略过滤和排序的研究记忆。"""
        # 只通过存储层读取当前作用域的已发布候选，避免直接访问 SQLite。
        candidates = self._store.list_published(scope)
        # 将候选交由纯策略层过滤和排序，检索器本身不修改记忆。
        return self._policy.filter_and_rank(scope, candidates)


class ResearchEvidenceRetriever:
    """将可用研究记忆安全转换为可由路由器消费的本地证据。"""

    name = "research-memory"
    provider = "local"
    read_only = True

    def __init__(
        self,
        store: ResearchMemoryStore,
        policy: ResearchMemoryPolicy | None = None,
        *,
        freshness_scope: ScopeKey | None = None,
    ) -> None:
        self._store = store
        self._policy = ResearchPolicy() if policy is None else policy
        self._freshness_scope = freshness_scope

    @property
    def index_updated_at(self) -> datetime | None:
        """只转交存储层声明的有效更新时间，未知时拒绝伪造时间。"""

        try:
            if self._freshness_scope is not None:
                get_updated_at = getattr(self._store, "get_updated_at", None)
                updated_at = (
                    get_updated_at(self._freshness_scope)
                    if callable(get_updated_at)
                    else None
                )
            else:
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
        """返回同 Scope 且与查询词相交的已发布研究记忆证据。"""

        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must not be blank")
        if type(scope) is not ScopeKey:
            return []
        if self._freshness_scope is not None and scope != self._freshness_scope:
            return []
        query_tokens = set(self._tokenize(query))
        if not query_tokens:
            return []
        candidates = self._store.list_published(scope)
        approved = self._policy.filter_and_rank(scope, candidates)
        memories = list(approved)

        ranked: list[tuple[int, int, float, str, EvidenceChunk]] = []
        for memory in memories:
            chunk = self._to_evidence(memory, scope, query_tokens)
            if chunk is None:
                continue
            title_matches = len(query_tokens.intersection(self._tokenize(memory.title)))
            content_matches = len(query_tokens.intersection(self._tokenize(memory.content)))
            ranked.append(
                (title_matches, content_matches, float(memory.confidence), memory.memory_id, chunk)
            )

        # 按标题、正文的查询词覆盖数排序，再用既有置信度和 ID 消除并列。
        ranked.sort(key=lambda item: (-item[0], -item[1], -item[2], item[3]))
        return [item[4] for item in ranked]

    @staticmethod
    def _tokenize(value: str) -> list[str]:
        """使用纯 Python、大小写无关的 BM25 同型分词规则。"""

        return [token.casefold() for token in _TOKEN_PATTERN.findall(value)]

    def _to_evidence(
        self, memory: object, scope: ScopeKey, query_tokens: set[str]
    ) -> EvidenceChunk | None:
        """验证上游对象并构造独立副本，任何畸形输入均不传播。"""

        if not self._is_usable_memory(memory, scope):
            return None
        title_tokens = set(self._tokenize(memory.title))
        content_tokens = set(self._tokenize(memory.content))
        if not query_tokens.intersection(title_tokens | content_tokens):
            return None
        try:
            applicability = deepcopy(memory.applicability)
            source_refs = deepcopy(memory.source_refs)
            locator = applicability.get("locator")
            if not isinstance(locator, str) or not locator.strip():
                locator = None
            return EvidenceChunk(
                chunk_id=f"research:{memory.memory_id}",
                scope=scope,
                content=f"{memory.title}\n\n{memory.content}",
                source_ref=f"research_memory:{memory.memory_id}",
                locator=locator,
                metadata={
                    "retriever": "research_memory",
                    "memory_id": memory.memory_id,
                    "kind": memory.kind.value,
                    "confidence": float(memory.confidence),
                    "source_refs": source_refs,
                    "applicability": applicability,
                },
            )
        except Exception:
            return None

    @staticmethod
    def _is_usable_memory(memory: object, scope: ScopeKey) -> bool:
        """在政策结果之外复核 Scope、发布状态和环境，防止伪造记忆泄漏。"""

        if not isinstance(memory, ResearchMemory) or type(memory.scope) is not ScopeKey:
            return False
        if memory.scope != scope or memory.status is not ResearchMemoryStatus.PUBLISHED:
            return False
        if not all(
            isinstance(value, str) and value.strip()
            for value in (memory.memory_id, memory.title, memory.content)
        ):
            return False
        if (
            isinstance(memory.confidence, bool)
            or not isinstance(memory.confidence, (int, float))
            or not math.isfinite(float(memory.confidence))
            or not isinstance(memory.applicability, dict)
            or not isinstance(memory.source_refs, list)
        ):
            return False
        environments = memory.applicability.get("experiment_environments")
        return environments is None or (
            isinstance(environments, list)
            and scope.experiment_environment in environments
        )
