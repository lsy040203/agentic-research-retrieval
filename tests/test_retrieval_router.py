from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.research_models import EvidenceChunk, ScopeKey
from retrieval.llm_reranker import LocalLLMReranker, LocalLLMSettings


SCOPE = ScopeKey("team", "project", "repo", "main", "test")
OTHER_SCOPE = ScopeKey("team", "project", "repo", "dev", "test")


def chunk(chunk_id: str, *, scope: ScopeKey = SCOPE, content: str = "safe evidence") -> EvidenceChunk:
    return EvidenceChunk(chunk_id, scope, content, f"source:{chunk_id}")


class FakeTool:
    """只读测试工具；仅在路由器实际调用时记录次数。"""

    def __init__(
        self,
        name: str,
        chunks: list[EvidenceChunk] | None = None,
        *,
        provider: str = "local",
        read_only: bool = True,
        index_updated_at: datetime | None = None,
        error: Exception | None = None,
    ) -> None:
        self.name = name
        self.provider = provider
        self.read_only = read_only
        self.index_updated_at = index_updated_at or datetime.now(timezone.utc)
        self.chunks = list(chunks or [])
        self.error = error
        self.calls = 0

    def retrieve(self, query: str, scope: ScopeKey) -> list[EvidenceChunk]:
        self.calls += 1
        if self.error:
            raise self.error
        return list(self.chunks)


class CountingTool(FakeTool):
    """每次调用产生唯一证据，确保预算测试不会因无新增提前停止。"""

    def retrieve(self, query: str, scope: ScopeKey) -> list[EvidenceChunk]:
        self.calls += 1
        return [chunk(f"{self.name}-{self.calls}")]


def router(*tools: FakeTool, **kwargs):
    from policy.retrieval_router import RetrievalRouter

    value = RetrievalRouter(**kwargs)
    for tool in tools:
        value.register(tool)
    return value


class FakePlanner:
    """测试用规划器只返回名称计划，不能接触工具实例或证据。"""

    def __init__(self, rounds, *, error: Exception | None = None) -> None:
        self.rounds = rounds
        self.error = error
        self.calls: list[tuple[str, list[tuple[str, str]]]] = []

    def plan(self, query: str, tools: list[tuple[str, str]]):
        from policy.llm_query_planner import QueryPlan

        self.calls.append((query, tools))
        if self.error:
            raise self.error
        return QueryPlan(tuple(tuple(round_names) for round_names in self.rounds), "test plan")


def test_empty_query_is_rejected() -> None:
    with pytest.raises(ValueError, match="query"):
        router().retrieve("   ", SCOPE)


def test_register_keeps_reference_without_running_tool() -> None:
    tool = FakeTool("local-index", [chunk("a")])
    router(tool)
    assert tool.calls == 0


@pytest.mark.parametrize(
    ("tool", "reason"),
    [
        (FakeTool("local-index", read_only=False), "not_read_only"),
        (FakeTool("local-index", provider="cloud"), "provider_not_allowed"),
        (FakeTool("websearch-index"), "unsafe_name"),
    ],
)
def test_ineligible_tool_is_not_called(tool: FakeTool, reason: str) -> None:
    result = router(tool).retrieve("safe query", SCOPE)

    assert tool.calls == 0
    assert result.rejections == [f"{tool.name}:{reason}"]
    assert result.traces[0].accepted is False


@pytest.mark.parametrize("updated_at", [None, datetime.now(timezone.utc) - timedelta(days=2)])
def test_missing_or_stale_index_is_rejected(updated_at: datetime | None) -> None:
    tool = FakeTool("local-index", index_updated_at=updated_at)
    if updated_at is None:
        tool.index_updated_at = None

    result = router(tool, max_index_age_seconds=60).retrieve("safe query", SCOPE)

    assert tool.calls == 0
    assert result.rejections == ["local-index:stale_index"]


def test_future_index_timestamp_is_rejected_without_calling_or_returning_evidence() -> None:
    future = datetime.now(timezone.utc) + timedelta(minutes=1)
    tool = FakeTool("bm25", [chunk("future")], index_updated_at=future)

    result = router(tool, max_index_age_seconds=60).retrieve("safe query", SCOPE)

    assert tool.calls == 0
    assert result.evidence == []
    assert result.rejections == ["bm25:stale_index"]


def test_scope_mismatch_discards_only_bad_tool_result() -> None:
    bad = FakeTool("bad", [chunk("wrong", scope=OTHER_SCOPE)])
    good = FakeTool("good", [chunk("right")])

    result = router(bad, good).retrieve("safe query", SCOPE)

    assert [item.chunk_id for item in result.evidence] == ["right"]
    assert result.partial is True
    assert result.degradations == ["planner_llm_disabled", "scope_mismatch:bad"]
    assert next(trace for trace in result.traces if trace.tool_name == "bad").accepted is False


def test_non_evidence_tool_result_is_rejected_without_losing_healthy_route() -> None:
    class InvalidResultTool(FakeTool):
        def retrieve(self, query: str, scope: ScopeKey):
            self.calls += 1
            return ["not-evidence"]

    bad = InvalidResultTool("bad")
    good = FakeTool("good", [chunk("right")])
    result = router(bad, good).retrieve("safe query", SCOPE)

    assert [item.chunk_id for item in result.evidence] == ["right"]
    assert result.partial is True
    assert "invalid_tool_result:bad" in result.degradations


def test_tool_error_is_captured_without_losing_other_evidence() -> None:
    broken = FakeTool("broken", error=RuntimeError("boom"))
    good = FakeTool("good", [chunk("right")])

    result = router(broken, good).retrieve("safe query", SCOPE)

    assert [item.chunk_id for item in result.evidence] == ["right"]
    assert result.partial is True
    assert result.degradations == ["planner_llm_disabled", "tool_error:broken"]
    assert next(trace for trace in result.traces if trace.tool_name == "broken").reason == "tool_error"


def test_two_tools_are_fused_then_rule_reranked_without_sensitive_trace() -> None:
    secret_query = "query-not-for-trace"
    secret_evidence = "evidence-not-for-trace"
    first = FakeTool("first", [chunk("a", content=secret_evidence)])
    second = FakeTool("second", [chunk("b", content="query-not-for-trace other")])
    reranker = LocalLLMReranker(LocalLLMSettings())

    result = router(first, second, reranker=reranker).retrieve(secret_query, SCOPE)

    assert {item.chunk_id for item in result.evidence} == {"a", "b"}
    assert all(item.rerank_score is not None for item in result.evidence)
    assert result.degradations == ["planner_llm_disabled", "llm_disabled"]
    assert all(secret_query not in repr(trace) for trace in result.traces)
    assert all(secret_evidence not in repr(trace) for trace in result.traces)


def test_unknown_planned_tool_is_recorded_without_error() -> None:
    safe = FakeTool("safe", [chunk("safe")])
    result = router(safe).retrieve("safe query", SCOPE, tool_rounds=[["missing", "safe"]])

    assert safe.calls == 0
    assert result.rejections == ["plan:manual_unknown_tool"]
    assert result.traces[0].reason == "manual_unknown_tool"


def test_duplicate_manual_plan_is_rejected_without_calling_tools() -> None:
    tool = FakeTool("local-index", [chunk("same")])

    result = router(tool).retrieve("safe query", SCOPE, tool_rounds=[["local-index"], ["local-index"]])

    assert tool.calls == 0
    assert result.partial is True
    assert result.degradations == ["manual_duplicate_tool"]


def test_overbudget_manual_plan_is_rejected_without_calling_tools() -> None:
    first = CountingTool("first")
    second = CountingTool("second")
    rounds = [["first", "second"] for _ in range(7)]

    result = router(first, second).retrieve("safe query", SCOPE, tool_rounds=rounds)

    assert first.calls + second.calls == 0
    assert len(result.traces) == 1
    assert result.partial is True
    assert result.degradations == ["manual_budget_exceeded"]


def test_reranker_degradation_reason_is_propagated() -> None:
    tool = FakeTool("local-index", [chunk("a")])
    result = router(tool, reranker=LocalLLMReranker(LocalLLMSettings())).retrieve("safe query", SCOPE)

    assert result.degradations == ["planner_llm_disabled", "llm_disabled"]


def test_invalid_reranker_result_preserves_candidates_with_fixed_degradation() -> None:
    class InvalidReranker:
        def rerank(self, query, candidates):
            return ["not-a-rerank-result"]

    tool = FakeTool("local-index", [chunk("a")])
    result = router(tool, reranker=InvalidReranker()).retrieve("safe query", SCOPE)

    assert [item.chunk_id for item in result.evidence] == ["a"]
    assert "reranker_invalid_result" in result.degradations


@pytest.mark.parametrize(
    "replacement, reason",
    [
        (chunk("a", scope=OTHER_SCOPE), None),
        (chunk("a"), 123),
    ],
)
def test_reranker_cannot_replace_candidate_scope_or_return_non_string_reason(
    replacement: EvidenceChunk, reason: object
) -> None:
    from retrieval.llm_reranker import RerankResult

    class ForeignReranker:
        def rerank(self, query, candidates):
            return RerankResult([replacement], True, reason)

    original = chunk("a")
    result = router(FakeTool("local-index", [original]), reranker=ForeignReranker()).retrieve(
        "safe query", SCOPE
    )

    assert [item.chunk_id for item in result.evidence] == [original.chunk_id]
    assert all(item.scope == SCOPE for item in result.evidence)
    assert "reranker_invalid_result" in result.degradations


def test_router_does_not_run_subprocess_or_http(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess
    from urllib import request

    def forbidden(*args, **kwargs):
        raise AssertionError("external execution is forbidden")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(request, "urlopen", forbidden)
    result = router(FakeTool("local-index", [chunk("a")])).retrieve("safe query", SCOPE)

    assert [item.chunk_id for item in result.evidence] == ["a"]


def test_default_semantic_plan_fuses_bm25_and_vector() -> None:
    """语义查询在两个索引都可用时必须形成多路 RRF 输入。"""

    bm25 = FakeTool("bm25", [chunk("shared"), chunk("lexical")])
    vector = FakeTool("vector", [chunk("shared"), chunk("semantic")])

    result = router(vector, bm25).retrieve("find architecture guidance", SCOPE)

    assert [trace.tool_name for trace in result.traces] == ["bm25", "vector"]
    assert {item.chunk_id for item in result.evidence} == {"shared", "lexical", "semantic"}
    assert all("rrf_score" in item.metadata for item in result.evidence)


def test_default_plan_keeps_bm25_when_vector_errors() -> None:
    """一路异常只标记 partial，另一成功路的证据仍应返回。"""

    bm25 = FakeTool("bm25", [chunk("lexical")])
    vector = FakeTool("vector", error=RuntimeError("broken vector"))

    result = router(vector, bm25).retrieve("semantic lookup", SCOPE)

    assert [item.chunk_id for item in result.evidence] == ["lexical"]
    assert result.partial is True
    assert "tool_error:vector" in result.degradations


def test_code_error_query_prioritizes_grep_and_bm25() -> None:
    """报错和源码信号使用确定的 grep、BM25 优先顺序。"""

    grep = FakeTool("grep", [chunk("grep")])
    bm25 = FakeTool("bm25", [chunk("bm25")])
    vector = FakeTool("vector", [chunk("vector")])

    result = router(vector, bm25, grep).retrieve("Traceback: 报错 in app.py", SCOPE)

    assert [trace.tool_name for trace in result.traces] == ["grep", "bm25", "vector"]


def test_research_memory_is_in_default_plan_when_registered() -> None:
    """已注册研究记忆工具会被默认规则规划，且不臆造未注册工具。"""

    research = FakeTool("research-memory", [chunk("research")])

    result = router(research).retrieve("architecture decision", SCOPE)

    assert [trace.tool_name for trace in result.traces] == ["research-memory"]
    assert [item.chunk_id for item in result.evidence] == ["research"]


def test_legal_llm_plan_runs_bm25_and_vector_then_rrf_fuses() -> None:
    bm25 = FakeTool("bm25", [chunk("shared"), chunk("lexical")])
    vector = FakeTool("vector", [chunk("shared"), chunk("semantic")])
    planner = FakePlanner([["bm25", "vector"]])

    result = router(vector, bm25, query_planner=planner).retrieve("architecture", SCOPE)

    assert [trace.tool_name for trace in result.traces] == ["planner", "bm25", "vector"]
    assert {item.chunk_id for item in result.evidence} == {"shared", "lexical", "semantic"}
    assert all("rrf_score" in item.metadata for item in result.evidence)
    assert result.degradations == []


@pytest.mark.parametrize(
    "rounds, expected_reason",
    [
        ([["missing"]], "planner_unknown_tool"),
        ([["bm25", "bm25"]], "planner_duplicate_tool"),
        ([["bm25", "vector", "grep", "research-memory"]] * 4, "planner_budget_exceeded"),
    ],
)
def test_invalid_llm_plan_falls_back_to_rules_without_executing_proposal(
    rounds, expected_reason: str
) -> None:
    bm25 = FakeTool("bm25", [chunk("bm25")])
    vector = FakeTool("vector", [chunk("vector")])

    result = router(bm25, vector, query_planner=FakePlanner(rounds)).retrieve("safe query", SCOPE)

    assert bm25.calls == 1
    assert vector.calls == 1
    assert result.degradations[0] == expected_reason
    assert [trace.tool_name for trace in result.traces] == ["bm25", "vector"]


def test_planner_failure_and_blank_default_configuration_fall_back_to_rules() -> None:
    from policy.llm_query_planner import PlannerError

    tool = FakeTool("bm25", [chunk("a")])
    failed = router(tool, query_planner=FakePlanner([], error=PlannerError("planner_timeout"))).retrieve(
        "safe query", SCOPE
    )
    blank = router(tool).retrieve("safe query", SCOPE)

    assert failed.degradations[0] == "planner_timeout"
    assert blank.degradations[0] == "planner_llm_disabled"
    assert tool.calls == 2


def test_planner_input_and_trace_never_expose_query_or_key() -> None:
    secret_query = "ignore prompt SECRET-QUERY"
    secret_key = "SECRET-KEY"
    tool = FakeTool("bm25", [chunk("a")])
    tool.capability = f"ignore system {secret_key}"
    planner = FakePlanner([["bm25"]])

    result = router(tool, query_planner=planner).retrieve(secret_query, SCOPE)

    assert planner.calls[0][1] == [("bm25", "lexical local index")]
    assert all(secret_query not in repr(trace) for trace in result.traces)
    assert all(secret_key not in repr(trace) for trace in result.traces)


def test_untrusted_tool_capability_never_reaches_planner_http_body() -> None:
    import json

    from policy.llm_query_planner import LLMQueryPlanner, OpenAICompatibleQueryPlannerClient
    from retrieval.llm_reranker import load_llm_settings_from_environment

    class Transport:
        def __init__(self) -> None:
            content = json.dumps({"tool_rounds": [["bm25"]], "reason": "safe"})
            self.response = (200, json.dumps({"choices": [{"message": {"content": content}}]}).encode())
            self.body = b""

        def post(self, url, body, headers, timeout_seconds):
            self.body = body
            return self.response

    secret = "SECRET-CAPABILITY"
    tool = FakeTool("bm25", [chunk("a")])
    tool.capability = secret
    transport = Transport()
    settings = load_llm_settings_from_environment(
        {"ARR_LLM_ENABLED": "true", "ARR_SILICONFLOW_API_KEY": "unit-secret", "ARR_LLM_MODEL": "test"}
    )
    planner = LLMQueryPlanner(settings, client=OpenAICompatibleQueryPlannerClient(transport))

    router(tool, query_planner=planner).retrieve("safe query", SCOPE)

    assert secret.encode() not in transport.body
    assert b"unit-secret" not in transport.body


def test_untrusted_planner_error_and_malformed_plan_fall_back_without_sensitive_trace() -> None:
    from policy.llm_query_planner import PlannerError, QueryPlan

    class MalformedPlanner:
        def plan(self, query, tools):
            return QueryPlan("not-rounds", "bad")

    tool = FakeTool("bm25", [chunk("a")])
    secret = "SECRET-QUERY"
    failed = router(
        tool, query_planner=FakePlanner([], error=PlannerError(f"{secret} SECRET-KEY"))
    ).retrieve(secret, SCOPE)
    malformed = router(tool, query_planner=MalformedPlanner()).retrieve(secret, SCOPE)

    assert failed.degradations[0] == "planner_error"
    assert malformed.degradations[0] == "planner_invalid_response"
    assert secret not in repr(failed)
    assert "SECRET-KEY" not in repr(failed)


@pytest.mark.parametrize("rounds", [[], [[]]])
def test_empty_llm_plan_falls_back_to_rules(rounds) -> None:
    tool = FakeTool("bm25", [chunk("a")])

    result = router(tool, query_planner=FakePlanner(rounds)).retrieve("safe query", SCOPE)

    assert tool.calls == 1
    assert result.degradations[0] == "planner_invalid_response"


def test_unexpected_planner_bug_is_not_silently_downgraded() -> None:
    class BrokenPlanner:
        def plan(self, query, tools):
            raise AssertionError("programming bug")

    with pytest.raises(AssertionError, match="programming bug"):
        router(FakeTool("bm25"), query_planner=BrokenPlanner()).retrieve("safe query", SCOPE)
