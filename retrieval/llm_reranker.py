"""规则优先、可选本地 LLM 的证据精排器。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
import json
import math
import re
from typing import Mapping, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from core.research_models import EvidenceChunk
from policy.llm_prompts import build_rerank_payload


_TOKEN_PATTERN = re.compile(r"\w+", re.UNICODE)
_MAX_CANDIDATES = 20
SILICONFLOW_BASE_URL = "https://api.siliconflow.cn/v1"
SILICONFLOW_API_KEY_ENV = "ARR_SILICONFLOW_API_KEY"
LLM_MODEL_ENV = "ARR_LLM_MODEL"
LLM_ENABLED_ENV = "ARR_LLM_ENABLED"
LLM_TIMEOUT_SECONDS_ENV = "ARR_LLM_TIMEOUT_SECONDS"
_ENV_SETTINGS_PROVENANCE = object()


@dataclass(frozen=True)
class LocalLLMSettings:
    """本地 LLM 精排的连接设置；默认关闭，避免意外网络调用。"""

    enabled: bool = False
    base_url: str | None = None
    api_key: str | None = field(default=None, repr=False)
    model: str | None = None
    timeout_seconds: float = 5.0
    _credential_provenance: object | None = field(
        default=None, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(float(self.timeout_seconds))
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be a positive finite number")


def load_llm_settings_from_environment(environ: Mapping[str, str]) -> LocalLLMSettings:
    """从显式环境映射加载设置，默认关闭且绝不读取进程环境。"""

    enabled_value = environ.get(LLM_ENABLED_ENV, "")
    enabled = isinstance(enabled_value, str) and enabled_value.casefold() in {"1", "true", "yes"}
    if not enabled:
        return LocalLLMSettings()
    timeout_value = environ.get(LLM_TIMEOUT_SECONDS_ENV, "")
    timeout_seconds = 5.0
    if isinstance(timeout_value, str) and timeout_value.strip():
        try:
            timeout_seconds = float(timeout_value)
        except ValueError:
            timeout_seconds = 5.0
    settings = LocalLLMSettings(
        enabled=enabled,
        base_url=SILICONFLOW_BASE_URL,
        api_key=environ.get(SILICONFLOW_API_KEY_ENV) or None,
        model=environ.get(LLM_MODEL_ENV) or None,
        timeout_seconds=timeout_seconds,
    )
    object.__setattr__(settings, "_credential_provenance", _ENV_SETTINGS_PROVENANCE)
    return settings


def is_allowed_llm_endpoint(base_url: str | None) -> bool:
    """仅接受规范化后精确等于 SiliconFlow HTTPS v1 的无凭据端点。"""

    if not isinstance(base_url, str):
        return False
    try:
        parsed = urlparse(base_url)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.casefold() == "https"
        and parsed.hostname == "api.siliconflow.cn"
        and parsed.path in {"/v1", "/v1/"}
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
        and parsed.username is None
        and parsed.password is None
        and port is None
    )


@dataclass(frozen=True)
class LLMRank:
    """本地 LLM 为一个证据块给出的精排结论。"""

    chunk_id: str
    score: float
    reason: str


class HttpTransport(Protocol):
    """本地 OpenAI 兼容服务的最小 HTTP 传输接口，便于离线注入测试替身。"""

    def post(
        self, url: str, body: bytes, headers: dict[str, str], timeout_seconds: float
    ) -> tuple[int, bytes]:
        """向指定地址发送 JSON POST，并返回状态码与原始响应体。"""


class _NoRedirectHandler(HTTPRedirectHandler):
    """禁止 urllib 自动跳转，避免认证头被重定向到非本地地址。"""

    def redirect_request(
        self,
        request: Request,
        file_pointer: object,
        code: int,
        message: str,
        headers: object,
        new_url: str,
    ) -> None:
        """始终拒绝 3xx 跳转，让调用方将其视为非成功响应。"""

        return None


class UrllibHttpTransport:
    """基于标准库 urllib 的 HTTP 传输实现，不依赖第三方 SDK。"""

    def __init__(self, opener: object | None = None) -> None:
        # 默认 opener 显式禁用重定向，测试可注入离线 opener 验证边界。
        self._opener = opener or build_opener(_NoRedirectHandler())

    def post(
        self, url: str, body: bytes, headers: dict[str, str], timeout_seconds: float
    ) -> tuple[int, bytes]:
        """执行一次 HTTP 请求；调用方统一将异常转换为安全降级。"""

        request = Request(url, data=body, headers=headers, method="POST")
        try:
            with self._opener.open(request, timeout=timeout_seconds) as response:  # type: ignore[union-attr]
                return int(response.status), response.read()
        except HTTPError as exc:
            # HTTPError 同时带有状态码，保留给适配器转成非敏感失败信号。
            return int(exc.code), exc.read()
        except URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise TimeoutError from None
            raise RuntimeError("local HTTP request failed") from None


class OpenAICompatibleLocalLLMClient:
    """将精排请求适配为本地 OpenAI 兼容 chat/completions HTTP 调用。"""

    def __init__(self, transport: HttpTransport | None = None) -> None:
        self._transport = transport

    def rerank(
        self,
        query: str,
        candidates: Sequence[EvidenceChunk],
        settings: LocalLLMSettings,
    ) -> Sequence[LLMRank]:
        """提交最多二十条证据，并只接受约定的严格 JSON 精排结果。"""

        # adapter 可被独立使用，必须自行执行与精排器相同的出站安全检查。
        if LocalLLMReranker._settings_degradation_reason(settings) is not None:
            raise ValueError("unsafe local LLM settings")
        endpoint = f"{SILICONFLOW_BASE_URL}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if settings.api_key:
            headers["Authorization"] = f"Bearer {settings.api_key}"
        payload = build_rerank_payload(query, candidates[:_MAX_CANDIDATES], settings.model)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        transport = self._transport or UrllibHttpTransport()
        status, response_body = transport.post(
            endpoint, body, headers, float(settings.timeout_seconds)
        )
        if not 200 <= status < 300:
            raise RuntimeError("local HTTP response was unsuccessful")
        return self._parse_ranks(response_body)

    @staticmethod
    def _parse_ranks(response_body: bytes) -> Sequence[LLMRank]:
        """验证响应结构和字段类型，避免将模型自由文本当作可信精排结果。"""

        try:
            payload = json.loads(response_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid local LLM JSON response") from exc
        if not isinstance(payload, dict):
            raise ValueError("invalid local LLM response structure")

        results = payload.get("results")
        if not isinstance(results, list):
            choices = payload.get("choices")
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                raise ValueError("invalid local LLM response structure")
            message = choices[0].get("message")
            if not isinstance(message, dict) or not isinstance(message.get("content"), str):
                raise ValueError("invalid local LLM response structure")
            try:
                content = json.loads(message["content"])
            except json.JSONDecodeError as exc:
                raise ValueError("invalid local LLM JSON response") from exc
            if not isinstance(content, dict) or not isinstance(content.get("results"), list):
                raise ValueError("invalid local LLM response structure")
            results = content["results"]

        ranks: list[LLMRank] = []
        for item in results:
            if not isinstance(item, dict):
                raise ValueError("invalid local LLM result")
            chunk_id, score, reason = item.get("chunk_id"), item.get("score"), item.get("reason")
            if (
                not isinstance(chunk_id, str)
                or not chunk_id
                or isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not math.isfinite(float(score))
                or not 0 <= float(score) <= 1
                or not isinstance(reason, str)
                or not reason.strip()
            ):
                raise ValueError("invalid local LLM result fields")
            ranks.append(LLMRank(chunk_id=chunk_id, score=float(score), reason=reason))
        return ranks


class LocalLLMClient(Protocol):
    """仅描述本地客户端接口，不绑定任何 HTTP 或第三方 SDK。"""

    def rerank(
        self,
        query: str,
        candidates: Sequence[EvidenceChunk],
        settings: LocalLLMSettings,
    ) -> Sequence[LLMRank]:
        """为候选证据返回逐块且唯一的 LLM 排序结果。"""


@dataclass
class RerankResult:
    """精排结果以及是否由 LLM 成功产生的可审计状态。"""

    evidence: list[EvidenceChunk]
    used_llm: bool
    degradation_reason: str | None


class RuleReranker:
    """无外部依赖的确定性规则精排器。"""

    def rerank(self, query: str, candidates: Sequence[EvidenceChunk]) -> list[EvidenceChunk]:
        """基于词覆盖、RRF 和证据完整度重排前二十个候选。"""

        query_tokens = self._tokens(query)
        if not query_tokens:
            raise ValueError("query must contain at least one token")

        limited = list(candidates[:_MAX_CANDIDATES])
        rrf_scores = [self._finite_rrf(candidate) for candidate in limited]
        maximum_rrf = max(rrf_scores, default=0.0)

        ranked: list[EvidenceChunk] = []
        for candidate, rrf_score in zip(limited, rrf_scores):
            coverage = len(query_tokens & self._tokens(candidate.content)) / len(query_tokens)
            normalized_rrf = 0.0 if maximum_rrf == 0 else rrf_score / maximum_rrf
            completeness = sum(
                (
                    bool(candidate.source_ref),
                    bool(candidate.locator),
                    bool(candidate.content and candidate.content.strip()),
                )
            ) / 3
            score = min(1.0, max(0.0, 0.70 * coverage + 0.20 * normalized_rrf + 0.10 * completeness))
            metadata = self._copy_metadata(candidate.metadata)
            reason = (
                "规则重排："
                f"coverage={coverage:.4f}; rrf={normalized_rrf:.4f}; "
                f"completeness={completeness:.4f}"
            )
            ranked.append(
                replace(
                    candidate,
                    metadata=metadata,
                    rerank_score=score,
                    rerank_reason=reason,
                )
            )

        return sorted(ranked, key=self._stable_key)

    @staticmethod
    def _tokens(value: str) -> set[str]:
        """仅用正则与 casefold 获取用于匹配的词集合。"""

        if not isinstance(value, str):
            return set()
        return {token.casefold() for token in _TOKEN_PATTERN.findall(value)}

    @staticmethod
    def _finite_rrf(candidate: EvidenceChunk) -> float:
        """只接受有限 RRF 值；缺失或非法值按零处理。"""

        value = candidate.metadata.get("rrf_score", 0.0)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return 0.0
        score = float(value)
        return score if math.isfinite(score) and score >= 0 else 0.0

    @staticmethod
    def _copy_metadata(metadata: dict[str, object]) -> dict[str, object]:
        """深复制元数据，并移除无法重新构造 EvidenceChunk 的非有限 RRF。"""

        copied = deepcopy(metadata)
        value = copied.get("rrf_score")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if not math.isfinite(float(value)):
                copied.pop("rrf_score", None)
        return copied

    @staticmethod
    def _stable_key(candidate: EvidenceChunk) -> tuple[float, str, str, str]:
        return (
            -(candidate.rerank_score or 0.0),
            candidate.source_ref,
            candidate.locator or "",
            candidate.chunk_id,
        )


class LocalLLMReranker:
    """在安全且完整配置下使用本地 LLM，否则确定性降级到规则排序。"""

    def __init__(
        self,
        settings: LocalLLMSettings,
        client: LocalLLMClient | None = None,
        rule_reranker: RuleReranker | None = None,
        transport: HttpTransport | None = None,
    ) -> None:
        self._settings = settings
        self._client = client
        self._transport = transport
        self._rule_reranker = rule_reranker or RuleReranker()

    def rerank(self, query: str, candidates: Sequence[EvidenceChunk]) -> RerankResult:
        """精排候选；本地 LLM 出现任何不可信状态时均安全回退。"""

        limited = list(candidates[:_MAX_CANDIDATES])
        configuration_reason = self._settings_degradation_reason(self._settings)
        if configuration_reason is not None:
            return self._fallback(query, limited, configuration_reason)
        client = self._client
        if client is None:
            # 仅在开关、地址和模型均已通过校验后构造默认 HTTP 客户端。
            client = OpenAICompatibleLocalLLMClient(self._transport)

        llm_candidates = self._deduplicate_candidates(candidates)
        if llm_candidates is None:
            return self._fallback(query, limited, "llm_conflicting_candidate_identity")
        llm_candidates = llm_candidates[:_MAX_CANDIDATES]
        client_candidates = [
            replace(candidate, metadata=RuleReranker._copy_metadata(candidate.metadata))
            for candidate in llm_candidates
        ]

        try:
            ranks = client.rerank(query, client_candidates, self._settings)
        except TimeoutError:
            return self._fallback(query, limited, "llm_timeout")
        except ValueError:
            # 适配器用 ValueError 表示 JSON 或字段结构不符合精排协议。
            return self._fallback(query, limited, "llm_invalid_response")
        except (URLError, RuntimeError):
            return self._fallback(query, limited, "llm_error")

        if not self._valid_ranks(ranks, llm_candidates):
            return self._fallback(query, limited, "llm_invalid_response")

        rank_by_id = {rank.chunk_id: rank for rank in ranks}
        evidence = [
            self._with_llm_rank(candidate, rank_by_id[candidate.chunk_id])
            for candidate in llm_candidates
        ]
        return RerankResult(
            evidence=sorted(evidence, key=RuleReranker._stable_key),
            used_llm=True,
            degradation_reason=None,
        )

    def _fallback(
        self, query: str, candidates: Sequence[EvidenceChunk], reason: str
    ) -> RerankResult:
        return RerankResult(
            evidence=self._rule_reranker.rerank(query, candidates),
            used_llm=False,
            degradation_reason=reason,
        )

    @classmethod
    def _settings_degradation_reason(cls, settings: LocalLLMSettings) -> str | None:
        """集中校验本地 LLM 设置，确保所有出站路径遵循同一安全边界。"""

        if not settings.enabled:
            return "llm_disabled"
        if not settings.base_url:
            return "llm_missing_base_url"
        if not settings.model:
            return "llm_missing_model"
        if not settings.api_key:
            return "llm_missing_api_key"
        if not is_allowed_llm_endpoint(settings.base_url):
            return "llm_invalid_base_url"
        if settings._credential_provenance is not _ENV_SETTINGS_PROVENANCE:
            return "llm_untrusted_credentials"
        return None

    @staticmethod
    def _deduplicate_candidates(
        candidates: Sequence[EvidenceChunk],
    ) -> list[EvidenceChunk] | None:
        """按 ID 稳定去重；同 ID 身份冲突时拒绝向 LLM 发送证据。"""

        unique: dict[str, EvidenceChunk] = {}
        for candidate in candidates:
            existing = unique.get(candidate.chunk_id)
            if existing is None:
                unique[candidate.chunk_id] = candidate
                continue
            if (
                existing.scope,
                existing.source_ref,
                existing.locator,
                existing.content,
            ) != (
                candidate.scope,
                candidate.source_ref,
                candidate.locator,
                candidate.content,
            ):
                return None
        return list(unique.values())

    @staticmethod
    def _valid_ranks(ranks: object, candidates: Sequence[EvidenceChunk]) -> bool:
        if not isinstance(ranks, Sequence) or isinstance(ranks, (str, bytes)):
            return False
        candidate_ids = [candidate.chunk_id for candidate in candidates]
        if len(set(candidate_ids)) != len(candidate_ids) or len(ranks) != len(candidate_ids):
            return False
        received_ids: set[str] = set()
        for rank in ranks:
            if not isinstance(rank, LLMRank) or rank.chunk_id in received_ids:
                return False
            if rank.chunk_id not in candidate_ids:
                return False
            if isinstance(rank.score, bool) or not isinstance(rank.score, (int, float)):
                return False
            if not math.isfinite(float(rank.score)) or not 0 <= float(rank.score) <= 1:
                return False
            if not isinstance(rank.reason, str) or not rank.reason.strip():
                return False
            received_ids.add(rank.chunk_id)
        return received_ids == set(candidate_ids)

    def _with_llm_rank(self, candidate: EvidenceChunk, rank: LLMRank) -> EvidenceChunk:
        """复制 LLM 结果，并确保配置中的密钥不会进入可见理由。"""

        reason = rank.reason
        if self._settings.api_key:
            reason = reason.replace(self._settings.api_key, "[REDACTED]")
        score = float(rank.score)
        metadata = RuleReranker._copy_metadata(candidate.metadata)
        return replace(
            candidate,
            metadata=metadata,
            rerank_score=score,
            rerank_reason=reason,
        )
