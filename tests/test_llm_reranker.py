"""本地 LLM 与规则精排的行为契约测试。"""

from __future__ import annotations

from io import BytesIO
import json
from math import inf, isclose, nan
from urllib.error import HTTPError

import pytest

from core.research_models import EvidenceChunk, ScopeKey
from retrieval.llm_reranker import (
    LLMRank,
    LocalLLMReranker,
    LocalLLMSettings,
    OpenAICompatibleLocalLLMClient,
    RuleReranker,
    SILICONFLOW_BASE_URL,
    UrllibHttpTransport,
    load_llm_settings_from_environment,
)


def _scope() -> ScopeKey:
    return ScopeKey("team", "project", "repo", "main", "test")


def _chunk(
    chunk_id: str,
    *,
    content: str = "alpha beta",
    source_ref: str = "source",
    locator: str | None = "line:1",
    metadata: dict[str, object] | None = None,
) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id,
        scope=_scope(),
        content=content,
        source_ref=source_ref,
        locator=locator,
        metadata={} if metadata is None else metadata,
    )


def _enabled_settings(api_key: str = "unit-secret", model: str = "test-model") -> LocalLLMSettings:
    """经显式环境映射构造可用于离线成功路径的受信任配置。"""

    return load_llm_settings_from_environment(
        {
            "ARR_LLM_ENABLED": "true",
            "ARR_SILICONFLOW_API_KEY": api_key,
            "ARR_LLM_MODEL": model,
        }
    )


class _Client:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[list[EvidenceChunk]] = []

    def rerank(self, query: str, candidates: list[EvidenceChunk], settings: LocalLLMSettings):
        self.calls.append(candidates)
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


class _FakeTransport:
    """记录本地适配器请求的离线传输桩，避免测试访问真实网络。"""

    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[tuple[str, bytes, dict[str, str], float]] = []

    def post(
        self, url: str, body: bytes, headers: dict[str, str], timeout_seconds: float
    ) -> tuple[int, bytes]:
        self.calls.append((url, body, headers, timeout_seconds))
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


def test_openai_compatible_client_posts_to_allowed_siliconflow_endpoint_and_parses_ranks() -> None:
    transport = _FakeTransport(
        (200, b'{"results":[{"chunk_id":"one","score":0.9,"reason":"relevant"}]}')
    )
    settings = _enabled_settings()

    ranks = OpenAICompatibleLocalLLMClient(transport).rerank(
        "alpha", [_chunk("one", content="alpha source", source_ref="doc", locator="p:1")], settings
    )

    assert ranks == [LLMRank("one", 0.9, "relevant")]
    url, body, headers, timeout_seconds = transport.calls[0]
    assert url == f"{SILICONFLOW_BASE_URL}/chat/completions"
    assert headers == {"Content-Type": "application/json", "Authorization": "Bearer unit-secret"}
    assert timeout_seconds == 5.0
    payload = json.loads(body)
    assert payload["model"] == "test-model"
    assert set(payload) == {"model", "messages"}
    assert payload["messages"][0]["role"] == "system"
    assert "strict JSON" in payload["messages"][0]["content"]
    assert json.loads(payload["messages"][1]["content"]) == {
        "query": "alpha",
        "candidates": [{"chunk_id": "one", "content": "alpha source"}],
    }


def test_openai_compatible_client_parses_strict_json_from_chat_completion_content() -> None:
    response = {
        "choices": [
            {
                "message": {
                    "content": '{"results":[{"chunk_id":"one","score":0.9,"reason":"relevant"}]}'
                }
            }
        ]
    }

    ranks = OpenAICompatibleLocalLLMClient._parse_ranks(json.dumps(response).encode("utf-8"))

    assert ranks == [LLMRank("one", 0.9, "relevant")]


@pytest.mark.parametrize(
    "response",
    [
        {"choices": []},
        {"choices": [{"message": {"content": None}}]},
        {"choices": [{"message": {"content": "not JSON"}}]},
    ],
)
def test_openai_compatible_client_rejects_invalid_chat_completion_content(
    response: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        OpenAICompatibleLocalLLMClient._parse_ranks(json.dumps(response).encode("utf-8"))


def test_openai_compatible_client_refuses_remote_url_before_transport() -> None:
    transport = _FakeTransport((200, b"{}"))
    settings = LocalLLMSettings(
        enabled=True, base_url="https://example.com", api_key="unit-secret", model="test-model"
    )

    with pytest.raises(ValueError, match="unsafe"):
        OpenAICompatibleLocalLLMClient(transport).rerank("alpha", [_chunk("one")], settings)

    assert transport.calls == []


@pytest.mark.parametrize(
    "base_url",
    [
        "http://api.siliconflow.cn/v1",
        "https://localhost/v1",
        "https://api.siliconflow.cn/v1?target=other",
        "https://api.siliconflow.cn/v1#other",
        "https://unit-secret@api.siliconflow.cn/v1",
        "https://api.siliconflow.cn:443/v1",
        "https://api.siliconflow.cn/v1/other",
    ],
)
def test_non_allowlisted_endpoint_is_rejected_before_transport(base_url: str) -> None:
    """仅受限 SiliconFlow HTTPS 根端点允许建立出站请求。"""

    transport = _FakeTransport((200, b"{}"))
    settings = LocalLLMSettings(
        enabled=True, base_url=base_url, api_key="unit-secret", model="test-model"
    )

    with pytest.raises(ValueError, match="unsafe"):
        OpenAICompatibleLocalLLMClient(transport).rerank("alpha", [_chunk("one")], settings)

    assert transport.calls == []


def test_environment_mapping_loads_settings_without_exposing_key() -> None:
    """显式环境映射是唯一的凭据来源，配置失败信息不回显其值。"""

    secret = "unit-secret"
    settings = load_llm_settings_from_environment(
        {
            "ARR_LLM_ENABLED": "true",
            "ARR_SILICONFLOW_API_KEY": secret,
            "ARR_LLM_MODEL": "test-model",
        }
    )
    transport = _FakeTransport((500, b"failed"))
    result = LocalLLMReranker(settings, transport=transport).rerank("alpha", [_chunk("one")])

    assert settings.enabled is True
    assert settings.base_url == SILICONFLOW_BASE_URL
    assert settings.api_key == secret
    assert settings.model == "test-model"
    assert secret not in repr(settings)
    assert secret not in str(RuntimeError(settings))
    assert secret not in (result.degradation_reason or "")
    assert secret not in (result.evidence[0].rerank_reason or "")
    assert secret not in transport.calls[0][1].decode("utf-8")

    rejected = LocalLLMSettings(
        enabled=True, base_url="https://example.com", api_key=secret, model="test-model"
    )
    with pytest.raises(ValueError) as error:
        OpenAICompatibleLocalLLMClient(_FakeTransport((200, b"{}"))).rerank(
            "alpha", [_chunk("one")], rejected
        )
    assert secret not in str(error.value)


def test_environment_mapping_defaults_to_disabled_empty_settings() -> None:
    """未声明环境变量时保持关闭且不隐式读取进程环境。"""

    assert load_llm_settings_from_environment({}) == LocalLLMSettings()


@pytest.mark.parametrize("enabled", ["", "false"])
def test_disabled_environment_mapping_discards_credentials_and_never_calls_transport(
    enabled: str,
) -> None:
    """禁用状态不读取也不保留映射中的密钥、模型或其他 LLM 设置。"""

    transport = _FakeTransport((200, b"{}"))
    settings = load_llm_settings_from_environment(
        {
            "ARR_LLM_ENABLED": enabled,
            "ARR_SILICONFLOW_API_KEY": "unit-secret",
            "ARR_LLM_MODEL": "test-model",
        }
    )

    result = LocalLLMReranker(settings, transport=transport).rerank("alpha", [_chunk("one")])

    assert settings == LocalLLMSettings()
    assert result.degradation_reason == "llm_disabled"
    assert transport.calls == []


def test_manually_constructed_credentials_are_rejected_before_transport() -> None:
    """只有环境加载器产生的凭据允许进入 transport 认证头。"""

    transport = _FakeTransport((200, b'{"results":[{"chunk_id":"one","score":0.9,"reason":"ok"}]}'))
    settings = LocalLLMSettings(
        enabled=True, base_url=SILICONFLOW_BASE_URL, api_key="unit-secret", model="test-model"
    )

    result = LocalLLMReranker(settings, transport=transport).rerank("alpha", [_chunk("one")])

    assert result.degradation_reason == "llm_untrusted_credentials"
    assert transport.calls == []


def test_mapping_loaded_credentials_reach_fake_transport_authentication() -> None:
    """显式映射加载的测试凭据能到达 Fake transport 的认证头。"""

    transport = _FakeTransport((200, b'{"results":[{"chunk_id":"one","score":0.9,"reason":"ok"}]}'))

    result = LocalLLMReranker(_enabled_settings(), transport=transport).rerank(
        "alpha", [_chunk("one")]
    )

    assert result.used_llm is True
    assert transport.calls[0][2]["Authorization"] == "Bearer unit-secret"


def test_urllib_transport_stops_at_redirect_without_requesting_location() -> None:
    class _RedirectingOpener:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def open(self, request: object, timeout: float) -> object:
            self.calls.append(request.full_url)  # type: ignore[attr-defined]
            raise HTTPError(
                request.full_url,  # type: ignore[attr-defined]
                302,
                "Found",
                {"Location": "https://example.com"},
                BytesIO(b"redirect"),
            )

    opener = _RedirectingOpener()
    status, body = UrllibHttpTransport(opener=opener).post(
        f"{SILICONFLOW_BASE_URL}/chat/completions", b"{}", {"Content-Type": "application/json"}, 5.0
    )

    assert status == 302
    assert body == b"redirect"
    assert opener.calls == [f"{SILICONFLOW_BASE_URL}/chat/completions"]


def test_redirect_response_falls_back_without_leaking_key() -> None:
    transport = _FakeTransport((302, b"redirect"))
    settings = _enabled_settings()

    result = LocalLLMReranker(settings, transport=transport).rerank("alpha", [_chunk("one")])

    assert result.degradation_reason == "llm_error"
    assert len(transport.calls) == 1
    assert "unit-secret" not in (result.evidence[0].rerank_reason or "")


def test_unexpected_client_programming_error_is_not_silently_converted_to_degradation() -> None:
    class _BuggyClient:
        def rerank(self, query: str, candidates: object, settings: LocalLLMSettings) -> object:
            raise TypeError("programmer bug")

    settings = _enabled_settings()

    with pytest.raises(TypeError, match="programmer bug"):
        LocalLLMReranker(settings, client=_BuggyClient()).rerank("alpha", [_chunk("one")])


@pytest.mark.parametrize(
    "base_url", ["https://api.siliconflow.cn/v1?target=other", "https://api.siliconflow.cn/v1#other"]
)
def test_allowlisted_url_with_query_or_fragment_falls_back_before_transport(base_url: str) -> None:
    transport = _FakeTransport((200, b"{}"))
    settings = LocalLLMSettings(
        enabled=True, base_url=base_url, api_key="unit-secret", model="test-model"
    )

    result = LocalLLMReranker(settings, transport=transport).rerank("alpha", [_chunk("one")])

    assert result.degradation_reason == "llm_invalid_base_url"
    assert transport.calls == []


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        ((500, b"server failed"), "llm_error"),
        (TimeoutError("unit-secret"), "llm_timeout"),
        ((200, b"not json"), "llm_invalid_response"),
    ],
)
def test_default_http_adapter_falls_back_without_leaking_key(
    response: object, reason: str
) -> None:
    transport = _FakeTransport(response)
    settings = _enabled_settings()

    result = LocalLLMReranker(settings, transport=transport).rerank("alpha", [_chunk("one")])

    assert result.used_llm is False
    assert result.degradation_reason == reason
    assert transport.calls
    assert "unit-secret" not in (result.evidence[0].rerank_reason or "")
    assert "unit-secret" not in (result.degradation_reason or "")


def test_disabled_default_http_adapter_does_not_send_request() -> None:
    transport = _FakeTransport((200, b"{}"))

    result = LocalLLMReranker(LocalLLMSettings(), transport=transport).rerank("alpha", [_chunk("one")])

    assert result.degradation_reason == "llm_disabled"
    assert transport.calls == []


def test_non_allowlisted_default_http_adapter_does_not_send_request() -> None:
    transport = _FakeTransport((200, b"{}"))
    settings = LocalLLMSettings(
        enabled=True, base_url="https://example.com", api_key="unit-secret", model="test-model"
    )

    result = LocalLLMReranker(settings, transport=transport).rerank("alpha", [_chunk("one")])

    assert result.degradation_reason == "llm_invalid_base_url"
    assert transport.calls == []


def test_rule_reranker_calculates_formula_and_reason() -> None:
    chunk = _chunk("one", content="alpha gamma", metadata={"rrf_score": 2.0})

    result = RuleReranker().rerank("alpha beta", [chunk])

    assert isclose(result[0].rerank_score or 0, 0.7 * 0.5 + 0.2 + 0.1)
    assert "coverage=" in (result[0].rerank_reason or "")
    assert "rrf=" in (result[0].rerank_reason or "")
    assert "completeness=" in (result[0].rerank_reason or "")


def test_rule_reranker_uses_casefold_and_zero_for_missing_rrf() -> None:
    result = RuleReranker().rerank("STRASSE", [_chunk("one", content="Straße")])

    assert isclose(result[0].rerank_score or 0, 0.8)


@pytest.mark.parametrize("query", ["", "   ", "---"])
def test_rule_reranker_rejects_empty_or_unusable_query(query: str) -> None:
    with pytest.raises(ValueError):
        RuleReranker().rerank(query, [_chunk("one")])


def test_rule_reranker_limits_candidates_and_preserves_input() -> None:
    original = _chunk("original", metadata={"note": "keep"})
    candidates = [original] + [_chunk(str(index)) for index in range(1, 22)]

    result = RuleReranker().rerank("alpha", candidates)

    assert len(result) == 20
    assert original.rerank_score is None
    assert original.metadata == {"note": "keep"}
    assert result[0] is not original
    assert result[0].metadata is not original.metadata


def test_rule_reranker_uses_required_stable_tie_breaking_order() -> None:
    candidates = [
        _chunk("z", source_ref="b", locator="line:1"),
        _chunk("y", source_ref="a", locator="line:3"),
        _chunk("x", source_ref="a", locator="line:1"),
    ]

    result = RuleReranker().rerank("alpha", candidates)

    assert [chunk.chunk_id for chunk in result] == ["x", "y", "z"]


@pytest.mark.parametrize(
    ("source_ref", "locator", "content", "expected_completeness"),
    [
        ("source", "line:1", "alpha", "1.0000"),
        ("", "line:1", "alpha", "0.6667"),
        ("source", None, "alpha", "0.6667"),
        ("source", "line:1", "", "0.6667"),
        ("", None, "alpha", "0.3333"),
        ("", "line:1", "", "0.3333"),
        ("source", None, "", "0.3333"),
        ("", None, "", "0.0000"),
    ],
)
def test_rule_reranker_calculates_each_completeness_combination(
    source_ref: str, locator: str | None, content: str, expected_completeness: str
) -> None:
    result = RuleReranker().rerank(
        "alpha", [_chunk("one", source_ref=source_ref, locator=locator, content=content)]
    )

    assert f"completeness={expected_completeness}" in (result[0].rerank_reason or "")


def test_disabled_llm_never_calls_client() -> None:
    client = _Client([])
    result = LocalLLMReranker(LocalLLMSettings(), client=client).rerank("alpha", [_chunk("one")])

    assert result.used_llm is False
    assert result.degradation_reason == "llm_disabled"
    assert client.calls == []


def test_enabled_llm_accepts_allowlisted_siliconflow_url() -> None:
    client = _Client([LLMRank("one", 0.9, "relevant")])
    settings = _enabled_settings()

    result = LocalLLMReranker(settings, client=client).rerank("alpha", [_chunk("one")])

    assert result.used_llm is True


def test_enabled_llm_rejects_remote_url() -> None:
    client = _Client([])
    settings = LocalLLMSettings(
        enabled=True, base_url="https://example.com", api_key="unit-secret", model="test-model"
    )

    result = LocalLLMReranker(settings, client=client).rerank("alpha", [_chunk("one")])

    assert result.degradation_reason == "llm_invalid_base_url"
    assert client.calls == []


@pytest.mark.parametrize("base_url", ["https://api.siliconflow.cn:not-a-port/v1", "https://api.siliconflow.cn:443/v1"])
def test_enabled_llm_rejects_malformed_or_ported_urls_without_calling_client(base_url: str) -> None:
    client = _Client([LLMRank("one", 0.9, "unused")])
    settings = LocalLLMSettings(enabled=True, base_url=base_url, api_key="unit-secret", model="test-model")

    result = LocalLLMReranker(settings, client=client).rerank("alpha", [_chunk("one")])

    assert result.used_llm is False
    assert result.degradation_reason == "llm_invalid_base_url"
    assert client.calls == []


@pytest.mark.parametrize(
    "settings",
    [
        LocalLLMSettings(enabled=True, model="local"),
        LocalLLMSettings(enabled=True, base_url=SILICONFLOW_BASE_URL),
    ],
)
def test_enabled_llm_degrades_when_required_setting_is_missing(settings: LocalLLMSettings) -> None:
    result = LocalLLMReranker(settings).rerank("alpha", [_chunk("one")])

    assert result.degradation_reason in {"llm_missing_base_url", "llm_missing_model"}


def test_successful_llm_response_sorts_and_copies_candidates() -> None:
    first, second = _chunk("one"), _chunk("two")
    client = _Client([LLMRank("one", 0.2, "low"), LLMRank("two", 0.9, "high secret")])
    settings = _enabled_settings(api_key="secret")

    result = LocalLLMReranker(settings, client=client).rerank("alpha", [first, second])

    assert result.used_llm is True
    assert [item.chunk_id for item in result.evidence] == ["two", "one"]
    assert result.evidence[0] is not second
    assert result.evidence[0].rerank_reason == "high [REDACTED]"
    assert "secret" not in (result.evidence[0].rerank_reason or "")


def test_llm_client_receives_independent_candidate_and_metadata_copies() -> None:
    class _MutatingClient(_Client):
        def rerank(self, query: str, candidates: list[EvidenceChunk], settings: LocalLLMSettings):
            candidates[0].metadata["tampered"] = True
            candidates[0].metadata["nested"]["value"] = "changed"
            candidates[0].metadata["items"].append("changed")
            return super().rerank(query, candidates, settings)

    original = _chunk(
        "one",
        metadata={"source": "original", "nested": {"value": "original"}, "items": ["original"]},
    )
    caller_candidates = [original]
    client = _MutatingClient([LLMRank("one", 0.9, "safe")])
    settings = _enabled_settings()

    result = LocalLLMReranker(settings, client=client).rerank("alpha", caller_candidates)

    assert result.used_llm is True
    assert original.metadata == {
        "source": "original",
        "nested": {"value": "original"},
        "items": ["original"],
    }
    assert caller_candidates == [original]
    assert client.calls[0][0] is not original
    assert client.calls[0][0].metadata is not original.metadata


@pytest.mark.parametrize(
    "response,reason",
    [
        (TimeoutError(), "llm_timeout"),
        (RuntimeError("boom"), "llm_error"),
        ([LLMRank("unknown", 0.5, "x")], "llm_invalid_response"),
        ([LLMRank("one", 0.5, "x"), LLMRank("one", 0.4, "y")], "llm_invalid_response"),
        ([LLMRank("one", 0.5, "x")], "llm_invalid_response"),
        ([LLMRank("one", nan, "x"), LLMRank("two", 0.5, "y")], "llm_invalid_response"),
        ([LLMRank("one", 0.5, ""), LLMRank("two", 0.5, "y")], "llm_invalid_response"),
    ],
)
def test_invalid_or_failed_llm_response_degrades(response: object, reason: str) -> None:
    settings = _enabled_settings(api_key="secret")
    result = LocalLLMReranker(settings, client=_Client(response)).rerank("alpha", [_chunk("one"), _chunk("two")])

    assert result.used_llm is False
    assert result.degradation_reason == reason
    assert all("secret" not in (item.rerank_reason or "") for item in result.evidence)


def test_enabled_llm_sends_at_most_twenty_candidates() -> None:
    client = _Client([LLMRank(str(index), 0.5, "ok") for index in range(20)])
    settings = _enabled_settings()

    LocalLLMReranker(settings, client=client).rerank("alpha", [_chunk(str(index)) for index in range(21)])

    assert len(client.calls[0]) == 20


def test_enabled_llm_deduplicates_identical_chunk_ids_before_calling_client() -> None:
    first = _chunk("one", content="first occurrence")
    duplicate = _chunk("one", content="first occurrence")
    second = _chunk("two")
    client = _Client([LLMRank("one", 0.9, "first"), LLMRank("two", 0.2, "second")])
    settings = _enabled_settings()

    result = LocalLLMReranker(settings, client=client).rerank("alpha", [first, duplicate, second])

    assert result.used_llm is True
    assert [chunk.chunk_id for chunk in client.calls[0]] == ["one", "two"]
    assert [chunk.chunk_id for chunk in result.evidence] == ["one", "two"]


def test_enabled_llm_rejects_conflicting_duplicate_identity_without_calling_client() -> None:
    first = _chunk("same", content="first")
    conflict = _chunk("same", content="different")
    client = _Client([LLMRank("same", 0.9, "unused")])
    settings = _enabled_settings()

    result = LocalLLMReranker(settings, client=client).rerank("alpha", [first, conflict])

    assert result.used_llm is False
    assert result.degradation_reason == "llm_conflicting_candidate_identity"
    assert client.calls == []


def test_rule_reranker_treats_negative_and_non_finite_rrf_as_zero() -> None:
    negative_one = _chunk("negative-one", metadata={"rrf_score": -1.0})
    negative_two = _chunk("negative-two", metadata={"rrf_score": -2.0})
    not_a_number = _chunk("nan")
    infinity = _chunk("infinity")
    # EvidenceChunk 会拒绝非 JSON 元数据，因此在构造后模拟外部输入被污染的情况。
    not_a_number.metadata["rrf_score"] = nan
    infinity.metadata["rrf_score"] = inf

    result = RuleReranker().rerank("alpha", [negative_one, negative_two, not_a_number, infinity])

    assert all("rrf=0.0000" in (chunk.rerank_reason or "") for chunk in result)
    assert all(0 <= (chunk.rerank_score or 0) <= 1 for chunk in result)


def test_settings_rejects_non_positive_timeout() -> None:
    with pytest.raises(ValueError, match="timeout"):
        LocalLLMSettings(timeout_seconds=0)
