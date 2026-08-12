from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

from core.constants import EventType, MemoryType, Scene
from core.models import MemoryCandidate, MemoryEvent

_SENSITIVE_TOKENS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "access_key",
    "credential",
    "authorization",
)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|password|passwd|secret|token|access[_-]?key|authorization|credential)\b\s*[:=]\s*['\"]?[^'\"\s,;]+"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_LONG_SECRET_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9_-]{24,}(?![A-Za-z0-9])")


_FAQ_PATTERNS = [
    re.compile(
        r"(?:(?:问题|Q)[:：]\s*(?P<question>.+?))(?:(?:\r?\n)|[。；;])\s*(?:(?:答案|A|解决方案|解决办法|处理方式)[:：]\s*(?P<answer>.+))",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"(?:(?P<question>[^。！？?]*?(?:怎么|如何|为何|为什么|怎样|报错|失败|无法|不能)[^。！？?]*?))(?:(?:。|！|\?|？))?\s*(?:(?P<answer>[^。！？?]*?(?:先|然后|再|最后|可以|建议|检查|重试|修复|解决)[^。！？?]*))",
        re.IGNORECASE | re.DOTALL,
    ),
]

_GUIDE_HINTS = (
    "步骤",
    "教程",
    "指南",
    "配置",
    "设置",
    "安装",
    "部署",
    "初始化",
    "运行",
    "导出",
    "合并",
)

_TEMPLATE_DOMAIN_RULES = [
    (
        "batch_export",
        (
            "batch export",
            "batch_export",
            "批量导出",
            "导出",
            "export",
            "save as",
        ),
    ),
    (
        "merge_files",
        (
            "merge files",
            "merge",
            "combine",
            "合并文件",
            "合并",
            "拼接",
        ),
    ),
    (
        "desktop_config",
        (
            "desktop config",
            "desktop",
            "桌面",
            "配置",
            "settings",
            "setup desktop",
        ),
    ),
    (
        "software_setup",
        (
            "software setup",
            "setup",
            "install",
            "安装",
            "部署",
            "初始化",
            "environment setup",
        ),
    ),
]


def _slugify(text: str) -> str:
    token = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "_", text.strip().lower())
    token = re.sub(r"_+", "_", token).strip("_")
    return token or "knowledge"


def _is_sensitive_key(key: Any) -> bool:
    normalized = str(key).strip().lower().replace("-", "_")
    return any(token in normalized for token in _SENSITIVE_TOKENS)


def _redact_sensitive_text(text: str) -> str:
    text = _SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}=<redacted>", text)
    text = _BEARER_RE.sub("Bearer <redacted>", text)
    return _LONG_SECRET_RE.sub("<redacted>", text)


def _stable_candidate_id(user_id: str, memory_type: MemoryType, key: str) -> str:
    """Return a deterministic candidate id for idempotent extraction output."""
    payload = f"{user_id}\x1f{memory_type.value}\x1f{key}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:32]


def _text_fragments(payload: Any) -> list[str]:
    if payload is None:
        return []
    if isinstance(payload, str):
        text = _redact_sensitive_text(payload.strip())
        return [text] if text else []
    if isinstance(payload, (int, float, bool)):
        return [str(payload)]
    if isinstance(payload, dict):
        fragments: list[str] = []
        for key, value in payload.items():
            if key in {"event_id", "raw_event_id", "timestamp", "created_at", "updated_at"} or _is_sensitive_key(key):
                continue
            fragments.extend(_text_fragments(value))
        return fragments
    if isinstance(payload, (list, tuple, set)):
        fragments: list[str] = []
        items = sorted(payload, key=lambda item: str(item)) if isinstance(payload, set) else payload
        for item in items:
            fragments.extend(_text_fragments(item))
        return fragments
    return [str(payload)]


def _flatten_event_text(event: MemoryEvent) -> str:
    parts = [
        event.content or "",
        event.tool_name or "",
        *(_text_fragments(event.input)),
        *(_text_fragments(event.output)),
        *(_text_fragments(event.metadata)),
    ]
    return _redact_sensitive_text("\n".join(part for part in parts if part and str(part).strip()))


def _normalize_for_template(text: str) -> str:
    value = text.lower()
    value = re.sub(r"https?://\S+", "<url>", value)
    value = re.sub(r"[a-zA-Z]:\\[^\s]+", "<path>", value)
    value = re.sub(r"/[^\s]+", "<path>", value)
    value = re.sub(
        r"\b[\w.-]*\d[\w.-]*\.(?:csv|xlsx|xls|txt|json|jsonl|docx|pdf|pptx|md|zip)\b",
        "<file>",
        value,
    )
    value = re.sub(r"\b[\w.-]*\d[\w.-]*\b", "<var>", value)
    value = re.sub(r"\b[0-9]+\b", "<num>", value)
    value = re.sub(r"\b[0-9a-f]{8,}\b", "<id>", value)
    value = re.sub(r"['\"][^'\"]+['\"]", "<text>", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _summarize_mapping(payload: dict[str, Any] | None, *, max_items: int = 4) -> str:
    if not payload:
        return ""
    fragments: list[str] = []
    for key, value in payload.items():
        if _is_sensitive_key(key):
            continue
        if value is None:
            continue
        if isinstance(value, dict):
            continue
        if isinstance(value, (list, tuple, set)):
            normalized = ", ".join(str(item) for item in value if item is not None)
        else:
            normalized = _redact_sensitive_text(str(value))
        if normalized:
            fragments.append(f"{key}={normalized}")
        if len(fragments) >= max_items:
            break
    return "; ".join(fragments)


def _make_candidate(
    *,
    user_id: str,
    memory_type: MemoryType,
    key: str,
    content: str,
    scenario: Scene | str,
    confidence: float,
    source: str,
    source_events: list[str],
    source_summaries: list[str],
    tags: list[str],
    metadata: dict[str, Any] | None = None,
) -> MemoryCandidate:
    safe_content = _redact_sensitive_text(content)
    safe_summaries = [_redact_sensitive_text(summary) for summary in source_summaries]
    return MemoryCandidate(
        candidate_id=_stable_candidate_id(user_id, memory_type, key),
        user_id=user_id,
        memory_type=memory_type,
        key=key,
        content=safe_content,
        scenario=scenario,
        confidence=max(0.5, min(confidence, 1.0)),
        source=source,
        source_events=source_events,
        source_summaries=safe_summaries,
        tags=tags,
        metadata=metadata or {},
    )


def _ensure_event_context(candidate: MemoryCandidate, event: MemoryEvent, *, source: str) -> MemoryCandidate:
    candidate.user_id = event.user_id
    candidate.scenario = event.scenario
    candidate.source = source
    if event.event_id not in candidate.source_events:
        candidate.source_events.append(event.event_id)
    if not candidate.source_summaries and event.content:
        candidate.source_summaries = [event.content]
    return candidate


def _extract_tool_intent(event: MemoryEvent) -> str:
    fragments = " ".join(
        part.lower()
        for part in (
            event.tool_name or "",
            event.content or "",
            _summarize_mapping(event.input),
            _summarize_mapping(event.output),
        )
        if part
    )

    for intent, keywords in _TEMPLATE_DOMAIN_RULES:
        if any(keyword in fragments for keyword in keywords):
            return intent
    return "tool_case"


def _completeness_score(*, has_input: bool, has_output: bool, has_content: bool, has_metadata: bool) -> float:
    score = 0.5
    if has_input:
        score += 0.15
    if has_output:
        score += 0.2
    if has_content:
        score += 0.1
    if has_metadata:
        score += 0.05
    return min(score, 0.95)


def _extract_faq_candidates(event: MemoryEvent, text: str) -> list[MemoryCandidate]:
    candidates: list[MemoryCandidate] = []
    seen: set[str] = set()

    for pattern in _FAQ_PATTERNS:
        for match in pattern.finditer(text):
            question = (match.groupdict().get("question") or "").strip()
            answer = (match.groupdict().get("answer") or "").strip()
            if not question or not answer:
                continue
            signature = _normalize_for_template(f"{question} :: {answer}")
            if signature in seen:
                continue
            seen.add(signature)
            topic = _slugify(question[:24])
            confidence = 0.88
            if len(question) > 18:
                confidence += 0.04
            if len(answer) > 18:
                confidence += 0.04
            candidates.append(
                _make_candidate(
                    user_id=event.user_id,
                    memory_type=MemoryType.KNOWLEDGE,
                    key=f"knowledge.faq.{topic}",
                    content=f"问题：{question}；解决：{answer}",
                    scenario=event.scenario,
                    confidence=confidence,
                    source="knowledge_conversation",
                    source_events=[event.event_id],
                    source_summaries=[question, answer],
                    tags=["faq", "solution"],
                    metadata={
                        "topic": topic,
                        "question": question,
                        "answer": answer,
                        "kind": "faq_solution",
                    },
                )
            )

    return candidates


def _extract_guide_candidates(event: MemoryEvent, text: str) -> list[MemoryCandidate]:
    lowered = text.lower()
    guide_triggers = ("怎么", "如何", "步骤", "配置", "安装", "设置", "导出", "合并", "教程", "指南")
    if not any(trigger in lowered for trigger in guide_triggers) and not any(hint in text for hint in _GUIDE_HINTS):
        return []

    steps = [
        line.strip(" -•\t")
        for line in re.split(r"[\n\r；;。]+", text)
        if line.strip()
    ]
    step_like = [step for step in steps if re.match(r"^(?:先|然后|再|最后|步骤|\d+[).、-])", step)]
    content = "；".join(step_like[:5] if step_like else steps[:3])
    if not content:
        content = text.strip()
    topic_seed = step_like[0] if step_like else steps[0]
    topic = _slugify(topic_seed[:24])
    confidence = 0.72
    if step_like:
        confidence += min(0.15, 0.03 * len(step_like))
    if len(steps) >= 3:
        confidence += 0.05

    return [
        _make_candidate(
            user_id=event.user_id,
            memory_type=MemoryType.KNOWLEDGE,
            key=f"knowledge.guide.{topic}",
            content=f"操作指南：{content}",
            scenario=event.scenario,
            confidence=confidence,
            source="knowledge_conversation",
            source_events=[event.event_id],
            source_summaries=[content],
            tags=["guide", "howto"],
            metadata={
                "topic": topic,
                "step_count": len(step_like) if step_like else len(steps),
                "kind": "operation_guide",
            },
        )
    ]


def _extract_system_guide(event: MemoryEvent) -> list[MemoryCandidate]:
    fragments = " ".join(
        part.lower()
        for part in (
            event.content or "",
            _summarize_mapping(event.input),
            _summarize_mapping(event.output),
            _summarize_mapping(event.metadata),
        )
        if part
    )
    if not any(keyword in fragments for keyword in ("desktop", "桌面", "config", "配置", "settings", "安装", "setup", "software")):
        return []

    topic = "system_setup"
    if "desktop" in fragments or "桌面" in fragments:
        topic = "desktop_config"
    elif "安装" in fragments or "setup" in fragments or "software" in fragments:
        topic = "software_setup"

    content = event.content.strip() if event.content else ""
    if not content:
        content = "系统操作指南"

    confidence = 0.74
    if event.input:
        confidence += 0.08
    if event.output:
        confidence += 0.08
    if event.metadata:
        confidence += 0.04

    return [
        _make_candidate(
            user_id=event.user_id,
            memory_type=MemoryType.KNOWLEDGE,
            key=f"knowledge.system.{topic}",
            content=f"系统操作指南：{content}",
            scenario=event.scenario,
            confidence=confidence,
            source="knowledge_conversation",
            source_events=[event.event_id],
            source_summaries=[content],
            tags=["system", topic],
            metadata={
                "topic": topic,
                "kind": "system_guide",
            },
        )
    ]


def _template_signature(event: MemoryEvent) -> tuple[str, str, str]:
    text = _flatten_event_text(event)
    normalized = _normalize_for_template(text)
    domain = "generic"
    for name, keywords in _TEMPLATE_DOMAIN_RULES:
        if any(keyword in normalized for keyword in keywords):
            domain = name
            break
    structure = []
    if event.tool_name:
        structure.append(f"tool:{event.tool_name.strip().lower()}")
    if event.input:
        structure.append("input:" + ",".join(sorted(str(key).lower() for key in event.input.keys())))
    if event.output:
        structure.append("output:" + ",".join(sorted(str(key).lower() for key in event.output.keys())))
    if event.metadata:
        structure.append("meta:" + ",".join(sorted(str(key).lower() for key in event.metadata.keys())))
    structure_key = "|".join(structure) if structure else "plain"
    text_key = re.sub(r"\s+", " ", normalized)
    return domain, structure_key, text_key


def _template_content(domain: str, events: list[MemoryEvent]) -> str:
    representative = events[0]
    sample = representative.content or representative.tool_name or domain
    if domain == "batch_export":
        return f"批量导出模板：{sample}"
    if domain == "merge_files":
        return f"合并文件模板：{sample}"
    if domain == "desktop_config":
        return f"桌面配置模板：{sample}"
    if domain == "software_setup":
        return f"软件安装/配置模板：{sample}"
    return f"通用模板：{sample}"


def _template_confidence(events: list[MemoryEvent]) -> float:
    if not events:
        return 0.5
    count = len(events)
    rich_events = 0
    for event in events:
        if event.input:
            rich_events += 1
        if event.output:
            rich_events += 1
        if event.content:
            rich_events += 1
        if event.metadata:
            rich_events += 1
    richness = min(1.0, rich_events / (count * 4))
    confidence = 0.55
    confidence += min(0.18, 0.06 * max(0, count - 1))
    confidence += 0.18 * richness
    return min(confidence, 0.95)


def _dedupe_candidates(candidates: list[MemoryCandidate]) -> list[MemoryCandidate]:
    deduped: list[MemoryCandidate] = []
    seen: set[tuple[str, str, str]] = set()
    for candidate in candidates:
        signature = (candidate.user_id, candidate.memory_type.value, candidate.key)
        if signature in seen:
            continue
        seen.add(signature)
        deduped.append(candidate)
    return deduped


class KnowledgeExtractor:
    @staticmethod
    def extract_from_tool_result(event: MemoryEvent) -> list[MemoryCandidate]:
        text = _flatten_event_text(event)
        intent = _extract_tool_intent(event)
        input_summary = _summarize_mapping(event.input)
        output_summary = _summarize_mapping(event.output)
        metadata_summary = _summarize_mapping(event.metadata)

        has_input = bool(event.input)
        has_output = bool(event.output)
        has_content = bool(event.content and event.content.strip())
        has_metadata = bool(event.metadata)
        confidence = _completeness_score(
            has_input=has_input,
            has_output=has_output,
            has_content=has_content,
            has_metadata=has_metadata,
        )

        tool_name = _slugify(event.tool_name or "tool")
        key = f"knowledge.tool_case.{intent}.{tool_name}"
        content_bits = [f"工具用例：{event.tool_name or 'tool'}"]
        if input_summary:
            content_bits.append(f"输入 {input_summary}")
        if output_summary:
            content_bits.append(f"输出 {output_summary}")
        elif text.strip():
            content_bits.append(f"结果 {text.strip()[:120]}")

        candidates = [
            _make_candidate(
                user_id=event.user_id,
                memory_type=MemoryType.KNOWLEDGE,
                key=key,
                content="；".join(content_bits),
                scenario=event.scenario,
                confidence=confidence,
                source="knowledge_tool_result",
                source_events=[event.event_id],
                source_summaries=[summary for summary in (input_summary, output_summary, metadata_summary, event.content or "") if summary],
                tags=["tool_case", intent],
                metadata={
                    "intent": intent,
                    "tool_name": event.tool_name,
                    "input_summary": input_summary,
                    "output_summary": output_summary,
                    "completeness": confidence,
                },
            )
        ]

        if event.success is False or any(term in text.lower() for term in ("error", "exception", "failed", "失败", "报错", "异常")):
            detail = event.content or output_summary or text.strip()
            if detail:
                candidates.append(
                    _make_candidate(
                        user_id=event.user_id,
                        memory_type=MemoryType.KNOWLEDGE,
                        key=f"knowledge.issue.{intent}.{tool_name}",
                        content=f"问题诊断：{detail}",
                        scenario=event.scenario,
                        confidence=max(0.62, confidence - 0.08),
                        source="knowledge_tool_result",
                        source_events=[event.event_id],
                        source_summaries=[detail],
                        tags=["issue", intent],
                        metadata={
                            "intent": intent,
                            "tool_name": event.tool_name,
                            "kind": "problem_diagnosis",
                        },
                    )
                )

        return _dedupe_candidates([_ensure_event_context(candidate, event, source="knowledge_tool_result") for candidate in candidates])

    @staticmethod
    def extract_from_conversation(event: MemoryEvent) -> list[MemoryCandidate]:
        text = _flatten_event_text(event)
        candidates: list[MemoryCandidate] = []
        candidates.extend(_extract_faq_candidates(event, text))
        candidates.extend(_extract_guide_candidates(event, text))
        candidates.extend(_extract_system_guide(event))
        return _dedupe_candidates(candidates)

    @staticmethod
    def extract_templates(events: list[MemoryEvent]) -> list[MemoryCandidate]:
        # Templates are personal memories.  Grouping across users would merge
        # evidence from different tenants and leak a second user's activity.
        groups: dict[tuple[str, str, str, str], list[MemoryEvent]] = defaultdict(list)
        for event in events:
            domain, structure_key, text_key = _template_signature(event)
            groups[(event.user_id, domain, structure_key, text_key)].append(event)

        candidates: list[MemoryCandidate] = []
        for (user_id, domain, structure_key, text_key), group in sorted(groups.items()):
            if len(group) < 2:
                continue
            ordered_group = sorted(group, key=lambda event: (event.timestamp, event.event_id))
            representative = ordered_group[0]
            confidence = _template_confidence(ordered_group)
            template_key = (
                f"template.{domain}.{_slugify(structure_key)}."
                f"{hashlib.sha256(text_key.encode('utf-8')).hexdigest()[:12]}"
            )
            candidates.append(
                _make_candidate(
                    user_id=user_id,
                    memory_type=MemoryType.TEMPLATE,
                    key=template_key,
                    content=_template_content(domain, ordered_group),
                    scenario=representative.scenario if all(event.scenario == representative.scenario for event in ordered_group) else Scene.GLOBAL,
                    confidence=confidence,
                    source="knowledge_template",
                    source_events=[event.event_id for event in ordered_group],
                    source_summaries=[event.content or _flatten_event_text(event)[:160] for event in ordered_group if event.content or _flatten_event_text(event)],
                    tags=["template", domain],
                    metadata={
                        "domain": domain,
                        "structure_key": structure_key,
                        "count": len(ordered_group),
                        "kind": "reusable_template",
                        "normalized_text": text_key,
                    },
                )
            )

        return _dedupe_candidates(candidates)


def extract_knowledge(events: list[MemoryEvent]) -> list[MemoryCandidate]:
    """Expose the existing knowledge extractor through the API's legacy entry point."""

    candidates: list[MemoryCandidate] = []
    for event in events:
        if event.event_type is EventType.TOOL_RESULT:
            candidates.extend(KnowledgeExtractor.extract_from_tool_result(event))
        else:
            candidates.extend(KnowledgeExtractor.extract_from_conversation(event))
    candidates.extend(KnowledgeExtractor.extract_templates(events))
    return _dedupe_candidates(candidates)
