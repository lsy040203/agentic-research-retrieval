"""只读证据检索的安全路由器。"""

from __future__ import annotations

from collections.abc import Sequence as SequenceABC
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from dataclasses import replace
import re
from time import perf_counter_ns
from typing import Protocol, Sequence

from core.research_models import EvidenceChunk, ScopeKey
from policy.llm_query_planner import LLMQueryPlanner, PlannerError, QueryPlan
from retrieval.hybrid_retriever import HybridRetriever
from retrieval.llm_reranker import LocalLLMSettings, RerankResult


_FORBIDDEN_IDENTIFIERS = (
    "remote",
    "websearch",
    "shell",
    "command",
    "write",
    "exec",
)
_MAX_ROUNDS = 6
_MAX_PLANNED_CALLS = 12
_DEFAULT_TOOL_ORDER = ("research-memory", "grep", "bm25", "vector")
_CODE_ERROR_PATTERN = re.compile(r"error|traceback|报错|异常|\.py", re.IGNORECASE)
_SAFE_CAPABILITIES = {
    "research-memory": "published local research memory",
    "grep": "local source text search",
    "bm25": "lexical local index",
    "vector": "semantic local index",
}
_DEFAULT_CAPABILITY = "registered local read-only retrieval"


class RuleQueryPlanner:
    """纯规则地将查询映射为已注册的只读检索工具顺序。"""

    def plan(self, query: str, registered_names: Sequence[str]) -> list[str]:
        """不臆造工具名；源码或报错信号优先文本定位与词法检索。"""

        available = {name for name in registered_names if isinstance(name, str)}
        preferred = _DEFAULT_TOOL_ORDER
        if _CODE_ERROR_PATTERN.search(query):
            preferred = ("grep", "bm25", "research-memory", "vector")

        planned = [name for name in preferred if name in available]
        # 兼容既有自定义本地工具，同时保持注册顺序不影响默认行为。
        planned.extend(sorted(available.difference(preferred), key=str.casefold))
        return planned


# 兼容 C1 前已公开的名称；新的默认回退实现使用更准确的命名。
QueryAnalyzer = RuleQueryPlanner


class ReadOnlyRetrievalTool(Protocol):
    """受路由器调度的只读检索工具契约。"""

    name: str
    provider: str
    read_only: bool
    index_updated_at: datetime | None

    def retrieve(self, query: str, scope: ScopeKey) -> list[EvidenceChunk]:
        """在指定隔离范围内返回候选证据，不得产生外部副作用。"""


class EvidenceReranker(Protocol):
    """兼容本地 LLM 精排器的最小接口。"""

    def rerank(
        self, query: str, candidates: Sequence[EvidenceChunk]
    ) -> RerankResult:
        """返回精排证据及可审计的降级原因。"""


class QueryPlanner(Protocol):
    """只建议工具轮次；不能取得工具实例、证据或作用域。"""

    def plan(self, query: str, tools: Sequence[tuple[str, str]]) -> QueryPlan:
        """返回已注册工具名称组成的建议计划。"""


@dataclass(frozen=True)
class ToolTrace:
    """单次工具调度的非敏感审计记录。"""

    round_index: int
    tool_name: str
    accepted: bool
    candidate_count: int
    duration_ms: float
    reason: str


@dataclass
class RouterResult:
    """只读检索的证据、局部状态和审计信息。"""

    evidence: list[EvidenceChunk]
    partial: bool
    traces: list[ToolTrace]
    rejections: list[str]
    degradations: list[str]


class RetrievalRouter:
    """仅路由已注册、可验证且新鲜的本地只读检索工具。"""

    def __init__(
        self,
        allowed_providers: set[str] | None = None,
        max_index_age_seconds: float = 86400,
        hybrid_retriever: HybridRetriever | None = None,
        reranker: EvidenceReranker | None = None,
        query_analyzer: RuleQueryPlanner | None = None,
        query_planner: QueryPlanner | None = None,
    ) -> None:
        if max_index_age_seconds < 0:
            raise ValueError("max_index_age_seconds must not be negative")
        providers = allowed_providers if allowed_providers is not None else {"local"}
        self._allowed_providers = {provider.casefold() for provider in providers}
        self._max_index_age_seconds = float(max_index_age_seconds)
        self._hybrid_retriever = hybrid_retriever or HybridRetriever()
        self._reranker = reranker
        self._query_analyzer = query_analyzer or RuleQueryPlanner()
        # 默认关闭的设置不会产生网络访问，并会确定性退回规则规划。
        self._query_planner = query_planner or LLMQueryPlanner(LocalLLMSettings())
        self._tools: dict[str, ReadOnlyRetrievalTool] = {}

    def register(self, tool: ReadOnlyRetrievalTool) -> None:
        """保存工具引用；注册本身不执行任何检索。"""

        self._tools[tool.name] = tool

    def retrieve(
        self,
        query: str,
        scope: ScopeKey,
        *,
        tool_rounds: Sequence[Sequence[str]] | None = None,
    ) -> RouterResult:
        """按受限轮次收集同范围证据，并融合后交给可选精排器。"""

        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must not be blank")

        # 显式轮次只为旧调用方保留；默认先请求 LLM 建议，失败才使用规则规划。
        rounds = tool_rounds
        traces: list[ToolTrace] = []
        rejections: list[str] = []
        degradations: list[str] = []
        if rounds is None:
            try:
                proposal = self._query_planner.plan(query, self._tool_descriptions())
                proposal_rejection = self._proposed_plan_rejection(proposal.tool_rounds)
                if proposal_rejection is not None:
                    raise PlannerError(proposal_rejection)
                rounds = proposal.tool_rounds
                # 不记录 LLM 的自由文本 reason，防止它回显查询或敏感值。
                traces.append(ToolTrace(-1, "planner", True, 0, 0.0, "planner_accepted"))
            except PlannerError as error:
                rounds = [self._query_analyzer.plan(query, tuple(self._tools))]
                degradations.append(error.reason)
        else:
            manual_rejection = self._manual_plan_rejection(rounds)
            if manual_rejection is not None:
                self._reject(traces, rejections, -1, "plan", manual_rejection)
                return RouterResult([], True, traces, rejections, [manual_rejection])
        candidate_lists: list[list[EvidenceChunk]] = []
        seen_chunk_ids: set[str] = set()
        partial = False
        planned_calls = 0
        budget_exhausted = False

        for round_index, names in enumerate(rounds):
            if round_index >= _MAX_ROUNDS:
                budget_exhausted = True
                break

            round_added_evidence = False
            for name in names:
                if planned_calls >= _MAX_PLANNED_CALLS:
                    budget_exhausted = True
                    break
                planned_calls += 1

                tool = self._tools.get(name)
                if tool is None:
                    self._reject(
                        traces, rejections, round_index, name, "not_registered"
                    )
                    continue

                rejection = self._admission_rejection(tool)
                if rejection is not None:
                    self._reject(
                        traces, rejections, round_index, tool.name, rejection
                    )
                    continue

                started = perf_counter_ns()
                try:
                    # 工具返回畸形的非可迭代结果也属于该工具失败，不能中断其余路由。
                    candidates = list(tool.retrieve(query, scope))
                except Exception:
                    duration_ms = (perf_counter_ns() - started) / 1_000_000
                    traces.append(
                        ToolTrace(
                            round_index,
                            tool.name,
                            False,
                            0,
                            duration_ms,
                            "tool_error",
                        )
                    )
                    partial = True
                    degradations.append(f"tool_error:{tool.name}")
                    continue

                duration_ms = (perf_counter_ns() - started) / 1_000_000
                if not all(isinstance(candidate, EvidenceChunk) for candidate in candidates):
                    traces.append(
                        ToolTrace(
                            round_index,
                            tool.name,
                            False,
                            0,
                            duration_ms,
                            "invalid_tool_result",
                        )
                    )
                    partial = True
                    degradations.append(f"invalid_tool_result:{tool.name}")
                    continue
                if any(candidate.scope != scope for candidate in candidates):
                    traces.append(
                        ToolTrace(
                            round_index,
                            tool.name,
                            False,
                            len(candidates),
                            duration_ms,
                            "scope_mismatch",
                        )
                    )
                    rejections.append(f"{tool.name}:scope_mismatch")
                    partial = True
                    degradations.append(f"scope_mismatch:{tool.name}")
                    continue

                # reason 是固定摘要；审计记录不保存查询或证据正文。
                traces.append(
                    ToolTrace(
                        round_index,
                        tool.name,
                        True,
                        len(candidates),
                        duration_ms,
                        "accepted",
                    )
                )
                candidate_lists.append(candidates)
                if any(candidate.chunk_id not in seen_chunk_ids for candidate in candidates):
                    round_added_evidence = True
                    seen_chunk_ids.update(candidate.chunk_id for candidate in candidates)

            if budget_exhausted:
                break
            if not round_added_evidence:
                partial = True
                degradations.append("no_new_evidence")
                break

        if budget_exhausted:
            partial = True
            degradations.append("budget_exhausted")

        evidence = self._fuse(candidate_lists, degradations)
        evidence = self._rerank(query, scope, evidence, degradations)
        return RouterResult(evidence, partial, traces, rejections, degradations)

    def _admission_rejection(self, tool: ReadOnlyRetrievalTool) -> str | None:
        # 准入仅依赖工具实际公开属性，畸形工具不能借由注册名称绕过检查。
        try:
            read_only = tool.read_only
        except Exception:
            return "not_read_only"
        if read_only is not True:
            return "not_read_only"
        try:
            provider_value = tool.provider
        except Exception:
            return "provider_not_allowed"
        if not isinstance(provider_value, str):
            return "provider_not_allowed"
        provider = provider_value.casefold()
        if provider not in self._allowed_providers:
            return "provider_not_allowed"
        try:
            tool_name = tool.name
        except Exception:
            return "unsafe_name"
        if not isinstance(tool_name, str):
            return "unsafe_name"
        identifier = f"{tool_name} {provider_value}".casefold()
        if any(term in identifier for term in _FORBIDDEN_IDENTIFIERS):
            return "unsafe_name"
        try:
            updated_at = tool.index_updated_at
        except Exception:
            return "stale_index"
        if updated_at is None:
            return "stale_index"
        try:
            normalized = (
                updated_at.replace(tzinfo=timezone.utc)
                if updated_at.tzinfo is None
                else updated_at.astimezone(timezone.utc)
            )
            age_seconds = (datetime.now(timezone.utc) - normalized).total_seconds()
        except (AttributeError, OverflowError, ValueError):
            return "stale_index"
        # 未来时间戳同样不可信，不能借由负年龄绕过索引新鲜度门禁。
        return (
            "stale_index"
            if age_seconds < 0 or age_seconds > self._max_index_age_seconds
            else None
        )

    def _tool_descriptions(self) -> list[tuple[str, str]]:
        """只向规划器暴露名称和能力摘要，绝不传递工具对象或检索内容。"""

        descriptions: list[tuple[str, str]] = []
        for name in self._tools:
            descriptions.append((name, _SAFE_CAPABILITIES.get(name, _DEFAULT_CAPABILITY)))
        return descriptions

    def _manual_plan_rejection(self, rounds: Sequence[Sequence[str]]) -> str | None:
        """显式计划同样先通过预算与唯一性检查，避免部分执行恶意计划。"""

        if len(rounds) > _MAX_ROUNDS:
            return "manual_budget_exceeded"
        names: list[str] = []
        for round_names in rounds:
            if not isinstance(round_names, SequenceABC) or isinstance(round_names, (str, bytes)):
                return "manual_invalid_plan"
            names.extend(round_names)
        if len(names) > _MAX_PLANNED_CALLS:
            return "manual_budget_exceeded"
        if any(not isinstance(name, str) or not name.strip() for name in names):
            return "manual_invalid_plan"
        if len(names) != len(set(names)):
            return "manual_duplicate_tool"
        if any(name not in self._tools for name in names):
            return "manual_unknown_tool"
        return None

    def _proposed_plan_rejection(self, rounds: Sequence[Sequence[str]]) -> str | None:
        """Router 重复验证 LLM 输出，确保替换规划器也不能绕开安全边界。"""

        if not isinstance(rounds, SequenceABC) or isinstance(rounds, (str, bytes)):
            return "planner_invalid_response"
        if not rounds:
            return "planner_invalid_response"
        if len(rounds) > _MAX_ROUNDS:
            return "planner_budget_exceeded"
        names: list[object] = []
        for round_names in rounds:
            if not isinstance(round_names, SequenceABC) or isinstance(round_names, (str, bytes)):
                return "planner_invalid_response"
            if not round_names:
                return "planner_invalid_response"
            names.extend(round_names)
        if len(names) > _MAX_PLANNED_CALLS:
            return "planner_budget_exceeded"
        if any(not isinstance(name, str) or not name.strip() for name in names):
            return "planner_invalid_response"
        if len(names) != len(set(names)):
            return "planner_duplicate_tool"
        if any(name not in self._tools for name in names):
            return "planner_unknown_tool"
        return None

    @staticmethod
    def _reject(
        traces: list[ToolTrace],
        rejections: list[str],
        round_index: int,
        tool_name: str,
        reason: str,
    ) -> None:
        traces.append(ToolTrace(round_index, tool_name, False, 0, 0.0, reason))
        rejections.append(f"{tool_name}:{reason}")

    def _fuse(
        self,
        candidate_lists: list[list[EvidenceChunk]],
        degradations: list[str],
    ) -> list[EvidenceChunk]:
        try:
            return self._hybrid_retriever.fuse(candidate_lists)
        except ValueError:
            degradations.append("hybrid_error")
            return []

    def _rerank(
        self,
        query: str,
        scope: ScopeKey,
        candidates: list[EvidenceChunk],
        degradations: list[str],
    ) -> list[EvidenceChunk]:
        if self._reranker is None:
            return candidates
        # 在调用不可信精排器前冻结候选身份，并保留可安全回退的副本。
        identities = {
            candidate.chunk_id: self._evidence_identity(candidate) for candidate in candidates
        }
        fallback = [replace(candidate, metadata=deepcopy(candidate.metadata)) for candidate in candidates]
        try:
            result = self._reranker.rerank(query, candidates)
        except Exception:
            degradations.append("reranker_error")
            return fallback
        if not self._valid_rerank_result(result, identities, scope):
            degradations.append("reranker_invalid_result")
            return fallback
        if result.degradation_reason:
            degradations.append(result.degradation_reason)
        return list(result.evidence)

    @staticmethod
    def _evidence_identity(candidate: EvidenceChunk) -> tuple[ScopeKey, str, str | None, str]:
        """精排只能改变排名字段，不能替换证据的来源、范围或正文身份。"""

        return (candidate.scope, candidate.source_ref, candidate.locator, candidate.content)

    @classmethod
    def _valid_rerank_result(
        cls,
        result: object,
        identities: dict[str, tuple[ScopeKey, str, str | None, str]],
        scope: ScopeKey,
    ) -> bool:
        if (
            not isinstance(result, RerankResult)
            or not isinstance(result.evidence, list)
            or not (isinstance(result.degradation_reason, str) or result.degradation_reason is None)
            or len(result.evidence) != len(identities)
        ):
            return False
        received: set[str] = set()
        for candidate in result.evidence:
            if not isinstance(candidate, EvidenceChunk) or candidate.chunk_id in received:
                return False
            expected = identities.get(candidate.chunk_id)
            if expected is None or candidate.scope != scope:
                return False
            if cls._evidence_identity(candidate) != expected:
                return False
            received.add(candidate.chunk_id)
        return received == set(identities)
