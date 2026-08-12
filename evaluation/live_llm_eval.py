"""Offline-only contracts for manually invoked live LLM evaluation.

This module deliberately owns no settings, networking, or environment access.
Callers inject planner and reranker clients, allowing pytest to remain offline.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, Sequence

from core.research_models import EvidenceChunk, ScopeKey
from policy.llm_query_planner import PlannerError, QueryPlan
from retrieval.llm_reranker import RerankResult, RuleReranker


_SCOPE_FIELDS = frozenset(
    {"team_id", "project_id", "repository", "branch", "experiment_environment"}
)
_MODES = frozenset({"rule", "llm", "hybrid"})


class LiveEvaluationValidationError(ValueError):
    """Raised when a local live-evaluation input violates its safety contract."""


@dataclass
class LiveCallBudget:
    limit: int = 20
    used: int = 0

    def __post_init__(self) -> None:
        if type(self.limit) is not int or not 1 <= self.limit <= 20:
            raise LiveEvaluationValidationError("live call limit must be an integer from 1 to 20")
        if type(self.used) is not int or not 0 <= self.used <= self.limit:
            raise LiveEvaluationValidationError("used live calls must be within the configured limit")

    def try_consume(self, stage: Literal["planner", "reranker"]) -> bool:
        if stage not in {"planner", "reranker"}:
            raise LiveEvaluationValidationError("unknown live evaluation stage")
        if self.used >= self.limit:
            return False
        self.used += 1
        return True


@dataclass(frozen=True)
class LiveEvalCase:
    case_id: str
    query: str
    scope: ScopeKey
    allowed_tools: tuple[str, ...]
    expected_tools: tuple[str, ...]
    candidate_ids: tuple[str, ...]
    relevant_ids: tuple[str, ...]
    scenario_kind: str
    expected_first_relevant_rank: int | None = None


class PlannerProtocol(Protocol):
    def plan(self, query: str, tools: Sequence[tuple[str, str]]) -> QueryPlan: ...


class RerankerProtocol(Protocol):
    def rerank(self, query: str, candidates: Sequence[EvidenceChunk]) -> RerankResult: ...


@dataclass(frozen=True)
class LiveCaseResult:
    accepted_tools: tuple[str, ...]
    ranked_ids: tuple[str, ...]
    scope_leak_count: int
    passed: bool
    degradation: str | None
    planner_calls: int
    reranker_calls: int
    candidates_truncated: bool


@dataclass(frozen=True)
class LiveEvaluationReport:
    case_id: str
    prompt_version: str
    mode: str
    accepted_tools: tuple[str, ...]
    planner_calls: int = 0
    reranker_calls: int = 0
    scope_leak_count: int = 0
    passed: bool = True
    degradation: str | None = None
    candidates_truncated: bool = False

    @classmethod
    def from_case(
        cls,
        *,
        case_id: str,
        prompt_version: str,
        mode: str,
        query: str,
        evidence: Sequence[str],
        api_key: str | None,
        accepted_tools: Sequence[str],
        degradation: str | None,
        candidates_truncated: bool = False,
    ) -> "LiveEvaluationReport":
        """Create a safe report without retaining untrusted text or credentials."""
        del query, evidence, api_key
        return cls(
            case_id,
            prompt_version,
            mode,
            tuple(accepted_tools),
            degradation=degradation,
            candidates_truncated=candidates_truncated,
        )

    def to_json(self) -> str:
        return json.dumps(
            {
                "case_id": self.case_id,
                "prompt_version": self.prompt_version,
                "mode": self.mode,
                "accepted_tools": list(self.accepted_tools),
                "planner_calls": self.planner_calls,
                "reranker_calls": self.reranker_calls,
                "scope_leak_count": self.scope_leak_count,
                "passed": self.passed,
                "degradation": self.degradation,
                "candidates_truncated": self.candidates_truncated,
            },
            ensure_ascii=False,
            sort_keys=True,
        )


def load_live_eval_dataset(path: str | Path) -> list[LiveEvalCase]:
    """Load a small, local, synthetic benchmark without external side effects."""
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LiveEvaluationValidationError("live evaluation dataset cannot be parsed") from error
    if not isinstance(document, dict) or set(document) != {"dataset_version", "cases"}:
        raise LiveEvaluationValidationError("dataset must contain only dataset_version and cases")
    if not isinstance(document["dataset_version"], str) or not document["dataset_version"].strip():
        raise LiveEvaluationValidationError("dataset_version must be non-blank")
    cases = document["cases"]
    if not isinstance(cases, list) or len(cases) > 20:
        raise LiveEvaluationValidationError("dataset must contain at most 20 cases")
    case_ids: set[str] = set()
    return [_parse_case(item, index, case_ids) for index, item in enumerate(cases)]


def run_live_case(
    case: LiveEvalCase,
    *,
    mode: Literal["rule", "llm", "hybrid"],
    budget: LiveCallBudget,
    planner: PlannerProtocol,
    reranker: RerankerProtocol,
    candidates: Sequence[EvidenceChunk] | None = None,
) -> LiveCaseResult:
    """Evaluate one case, using deterministic rules for every unsafe LLM outcome."""
    if mode not in _MODES:
        raise LiveEvaluationValidationError("unsupported evaluation mode")
    baseline = list(candidates) if candidates is not None else _synthetic_candidates(case)
    fallback = RuleReranker().rerank(case.query, baseline)
    candidates_truncated = len(fallback) < len(baseline)
    scope_leak_count = sum(candidate.scope != case.scope for candidate in baseline)
    accepted_tools = case.expected_tools
    degradation: str | None = None
    planner_calls = reranker_calls = 0
    ranked = fallback

    if mode != "rule":
        if not budget.try_consume("planner"):
            degradation = "planner_budget_exhausted"
        else:
            planner_calls = 1
            try:
                proposal = planner.plan(case.query, [(tool, "registered") for tool in case.allowed_tools])
                proposed_tools = _valid_tools(proposal, case.allowed_tools)
            except TimeoutError:
                proposed_tools = None
                degradation = "planner_timeout"
            except (PlannerError, ValueError, RuntimeError):
                proposed_tools = None
                degradation = "planner_error"
            if proposed_tools is None:
                degradation = degradation or "planner_invalid_result"
            else:
                accepted_tools = proposed_tools
                if not budget.try_consume("reranker"):
                    degradation = "reranker_budget_exhausted"
                else:
                    reranker_calls = 1
                    try:
                        result = reranker.rerank(case.query, baseline)
                        if isinstance(result, RerankResult) and isinstance(result.evidence, list):
                            scope_leak_count += sum(
                                isinstance(candidate, EvidenceChunk) and candidate.scope != case.scope
                                for candidate in result.evidence
                            )
                        if not _valid_rerank_result(result, baseline, case.scope):
                            degradation = "reranker_invalid_result"
                        else:
                            ranked = result.evidence
                            if result.degradation_reason:
                                degradation = result.degradation_reason
                    except TimeoutError:
                        degradation = "reranker_timeout"
                    except (ValueError, RuntimeError):
                        degradation = "reranker_error"

    ranked_ids = tuple(candidate.chunk_id for candidate in ranked)
    passed = (
        scope_leak_count == 0
        and set(case.expected_tools) == set(accepted_tools)
        and set(case.relevant_ids).issubset(ranked_ids)
        and _ranking_meets_expectation(case, ranked_ids)
        and degradation is None
    )
    return LiveCaseResult(
        accepted_tools,
        ranked_ids,
        scope_leak_count,
        passed,
        degradation,
        planner_calls,
        reranker_calls,
        candidates_truncated,
    )


def _parse_case(item: object, index: int, case_ids: set[str]) -> LiveEvalCase:
    if not isinstance(item, dict):
        raise LiveEvaluationValidationError(f"case {index} must be an object")
    required = {
        "case_id", "query", "scope", "allowed_tools", "expected_tools", "candidate_ids",
        "relevant_ids", "scenario_kind", "expected_first_relevant_rank",
    }
    if set(item) != required:
        raise LiveEvaluationValidationError(f"case {index} has an invalid shape")
    case_id = _non_blank(item["case_id"], "case_id")
    if case_id in case_ids:
        raise LiveEvaluationValidationError("case_id must be unique")
    case_ids.add(case_id)
    scope_value = item["scope"]
    if not isinstance(scope_value, dict) or set(scope_value) != _SCOPE_FIELDS:
        raise LiveEvaluationValidationError("scope must contain all five dimensions")
    try:
        scope = ScopeKey(**{name: _non_blank(scope_value[name], name) for name in _SCOPE_FIELDS})
    except (TypeError, ValueError) as error:
        raise LiveEvaluationValidationError("scope is invalid") from error
    allowed = _string_tuple(item["allowed_tools"], "allowed_tools", required=True)
    expected = _string_tuple(item["expected_tools"], "expected_tools", required=False)
    candidates = _string_tuple(item["candidate_ids"], "candidate_ids", required=True)
    relevant = _string_tuple(item["relevant_ids"], "relevant_ids", required=False)
    if not set(expected).issubset(allowed) or not set(relevant).issubset(candidates):
        raise LiveEvaluationValidationError("expected tools and relevant IDs must be allowed candidates")
    query = _non_blank(item["query"], "query")
    if not RuleReranker._tokens(query):
        raise LiveEvaluationValidationError("query must contain a rule-reranker token")
    expected_rank = item["expected_first_relevant_rank"]
    if relevant:
        if type(expected_rank) is not int or not 1 <= expected_rank <= len(candidates):
            raise LiveEvaluationValidationError("expected_first_relevant_rank is invalid")
    elif expected_rank is not None:
        raise LiveEvaluationValidationError("non-relevant cases must use a null expected rank")
    return LiveEvalCase(
        case_id,
        query,
        scope,
        allowed,
        expected,
        candidates,
        relevant,
        _non_blank(item["scenario_kind"], "scenario_kind"),
        expected_rank,
    )


def _non_blank(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LiveEvaluationValidationError(f"{name} must be non-blank")
    return value.strip()


def _string_tuple(value: object, name: str, *, required: bool) -> tuple[str, ...]:
    if not isinstance(value, list) or (required and not value):
        raise LiveEvaluationValidationError(f"{name} must be a valid list")
    values = tuple(_non_blank(item, name) for item in value)
    if len(set(values)) != len(values):
        raise LiveEvaluationValidationError(f"{name} must not contain duplicates")
    return values


def _synthetic_candidates(case: LiveEvalCase) -> list[EvidenceChunk]:
    return [EvidenceChunk(candidate_id, case.scope, "synthetic evidence", "live-eval") for candidate_id in case.candidate_ids]


def _valid_tools(proposal: object, allowed_tools: Sequence[str]) -> tuple[str, ...] | None:
    if not isinstance(proposal, QueryPlan):
        return None
    names = tuple(name for round_names in proposal.tool_rounds for name in round_names)
    if not names or len(names) != len(set(names)) or any(name not in allowed_tools for name in names):
        return None
    return names


def _valid_rerank_result(
    result: object, baseline: Sequence[EvidenceChunk], scope: ScopeKey
) -> bool:
    if not isinstance(result, RerankResult) or not isinstance(result.evidence, list):
        return False
    baseline_by_id = {candidate.chunk_id: candidate for candidate in baseline}
    if len(baseline_by_id) != len(baseline) or len(result.evidence) != len(baseline):
        return False
    returned_ids = [candidate.chunk_id for candidate in result.evidence if isinstance(candidate, EvidenceChunk)]
    return (
        len(returned_ids) == len(result.evidence)
        and len(set(returned_ids)) == len(returned_ids)
        and set(returned_ids) == set(baseline_by_id)
        and all(candidate.scope == scope and baseline_by_id[candidate.chunk_id].scope == candidate.scope for candidate in result.evidence)
    )


def _ranking_meets_expectation(case: LiveEvalCase, ranked_ids: Sequence[str]) -> bool:
    """Require the first relevant evidence to meet the case's maximum accepted rank."""
    if case.expected_first_relevant_rank is None:
        return not case.relevant_ids
    first_relevant_rank = next(
        (index for index, candidate_id in enumerate(ranked_ids, 1) if candidate_id in case.relevant_ids),
        None,
    )
    return first_relevant_rank is not None and first_relevant_rank <= case.expected_first_relevant_rank
