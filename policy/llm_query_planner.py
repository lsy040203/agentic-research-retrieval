"""受限 LLM 工具规划：模型只能建议，路由器始终负责执行前校验。"""

from __future__ import annotations

from dataclasses import dataclass
import json
from collections.abc import Sequence as SequenceABC
from typing import Protocol, Sequence
from urllib.error import URLError

from policy.llm_prompts import build_router_payload
from retrieval.llm_reranker import (
    HttpTransport,
    LocalLLMReranker,
    LocalLLMSettings,
    SILICONFLOW_BASE_URL,
    UrllibHttpTransport,
)


_MAX_ROUNDS = 6
_MAX_PLANNED_CALLS = 12
_PLANNER_REASONS = frozenset(
    {
        "planner_error",
        "planner_unsafe_settings",
        "planner_timeout",
        "planner_http_error",
        "planner_invalid_response",
        "planner_budget_exceeded",
        "planner_duplicate_tool",
        "planner_unknown_tool",
        "planner_llm_disabled",
        "planner_llm_missing_base_url",
        "planner_llm_missing_model",
        "planner_llm_missing_api_key",
        "planner_llm_invalid_base_url",
        "planner_llm_untrusted_credentials",
    }
)


class PlannerError(ValueError):
    """可安全记录的规划失败；reason 不包含查询、工具描述或凭据。"""

    def __init__(self, reason: str) -> None:
        self.reason = reason if reason in _PLANNER_REASONS else "planner_error"
        super().__init__(self.reason)


@dataclass(frozen=True)
class QueryPlan:
    """已通过协议校验但尚未通过工具安全门禁的建议计划。"""

    tool_rounds: tuple[tuple[str, ...], ...]
    reason: str


class QueryPlannerClient(Protocol):
    def plan(
        self,
        query: str,
        tools: Sequence[tuple[str, str]],
        settings: LocalLLMSettings,
    ) -> QueryPlan:
        """返回 LLM 的结构化计划，不接触证据或工具实现。"""


class OpenAICompatibleQueryPlannerClient:
    """以与精排器相同的受限 SiliconFlow 协议请求工具计划。"""

    def __init__(self, transport: HttpTransport | None = None) -> None:
        self._transport = transport

    def plan(
        self,
        query: str,
        tools: Sequence[tuple[str, str]],
        settings: LocalLLMSettings,
    ) -> QueryPlan:
        if LocalLLMReranker._settings_degradation_reason(settings) is not None:
            raise PlannerError("planner_unsafe_settings")
        payload = build_router_payload(query, tools, settings.model)
        headers = {"Content-Type": "application/json"}
        if settings.api_key:
            headers["Authorization"] = f"Bearer {settings.api_key}"
        transport = self._transport or UrllibHttpTransport()
        try:
            status, response_body = transport.post(
                f"{SILICONFLOW_BASE_URL}/chat/completions",
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers,
                float(settings.timeout_seconds),
            )
        except TimeoutError:
            raise PlannerError("planner_timeout") from None
        except (URLError, RuntimeError):
            raise PlannerError("planner_error") from None
        if not 200 <= status < 300:
            raise PlannerError("planner_http_error")
        return self._parse(response_body)

    @staticmethod
    def _parse(response_body: bytes) -> QueryPlan:
        try:
            payload = json.loads(response_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise PlannerError("planner_invalid_response") from None
        if not isinstance(payload, dict):
            raise PlannerError("planner_invalid_response")
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise PlannerError("planner_invalid_response")
        message = choices[0].get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise PlannerError("planner_invalid_response")
        try:
            payload = json.loads(message["content"])
        except json.JSONDecodeError:
            raise PlannerError("planner_invalid_response") from None
        if not isinstance(payload, dict):
            raise PlannerError("planner_invalid_response")
        rounds, reason = payload.get("tool_rounds"), payload.get("reason")
        if (
            not isinstance(rounds, list)
            or not rounds
            or not isinstance(reason, str)
            or not reason.strip()
        ):
            raise PlannerError("planner_invalid_response")
        normalized: list[tuple[str, ...]] = []
        for round_names in rounds:
            if not isinstance(round_names, list) or not round_names:
                raise PlannerError("planner_invalid_response")
            if any(not isinstance(name, str) or not name.strip() for name in round_names):
                raise PlannerError("planner_invalid_response")
            normalized.append(tuple(round_names))
        # LLM 自由文本不得进入审计对象，避免其回显输入或凭据。
        return QueryPlan(tuple(normalized), "planner_accepted")


class LLMQueryPlanner:
    """在调用工具前验证 LLM 提案的名称唯一性、注册性与预算。"""

    def __init__(
        self,
        settings: LocalLLMSettings,
        client: QueryPlannerClient | None = None,
        transport: HttpTransport | None = None,
    ) -> None:
        self._settings = settings
        self._client = client
        self._transport = transport

    def plan(self, query: str, tools: Sequence[tuple[str, str]]) -> QueryPlan:
        configuration_reason = LocalLLMReranker._settings_degradation_reason(self._settings)
        if configuration_reason is not None:
            raise PlannerError(f"planner_{configuration_reason}")
        client = self._client or OpenAICompatibleQueryPlannerClient(self._transport)
        try:
            proposal = client.plan(query, tools, self._settings)
        except PlannerError:
            raise
        except TimeoutError:
            raise PlannerError("planner_timeout") from None
        except (ValueError, URLError, RuntimeError):
            raise PlannerError("planner_invalid_response") from None
        self._validate(proposal, {name for name, _ in tools})
        return proposal

    @staticmethod
    def _validate(proposal: QueryPlan, registered_names: set[str]) -> None:
        if not isinstance(proposal, QueryPlan):
            raise PlannerError("planner_invalid_response")
        rounds = proposal.tool_rounds
        if not isinstance(rounds, SequenceABC) or isinstance(rounds, (str, bytes)):
            raise PlannerError("planner_invalid_response")
        if len(rounds) > _MAX_ROUNDS:
            raise PlannerError("planner_budget_exceeded")
        names: list[object] = []
        for round_names in rounds:
            if not isinstance(round_names, SequenceABC) or isinstance(round_names, (str, bytes)):
                raise PlannerError("planner_invalid_response")
            names.extend(round_names)
        if len(names) > _MAX_PLANNED_CALLS:
            raise PlannerError("planner_budget_exceeded")
        if any(not isinstance(name, str) or not name.strip() for name in names):
            raise PlannerError("planner_invalid_response")
        if len(names) != len(set(names)):
            raise PlannerError("planner_duplicate_tool")
        if any(name not in registered_names for name in names):
            raise PlannerError("planner_unknown_tool")
