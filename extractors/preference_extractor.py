from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import replace
from typing import Any, Iterable

from core.constants import EventType, MemoryType, Scene
from core.models import MemoryCandidate, MemoryEvent


_FORMAT_ALIASES = {
    "md": "markdown",
    "markdown": "markdown",
    "pdf": "pdf",
    "word": "word",
    "doc": "word",
    "docx": "word",
    "excel": "excel",
    "xls": "excel",
    "xlsx": "excel",
    "csv": "csv",
    "json": "json",
    "jsonl": "jsonl",
    "ppt": "powerpoint",
    "pptx": "powerpoint",
}

_STYLE_ALIASES = {
    "简洁": "concise",
    "详细": "detailed",
    "正式": "formal",
    "口语化": "conversational",
    "分点": "bullets",
    "结构化": "structured",
    "先结论后分析": "conclusion_first",
}

_LANGUAGE_ALIASES = {
    "中文": "zh",
    "英文": "en",
    "中英双语": "bilingual",
    "chinese": "zh",
    "english": "en",
}

_TOOL_ALIASES = {
    "python": "python",
    "bash": "bash",
    "powershell": "powershell",
    "git": "git",
    "sqlite": "sqlite",
    "curl": "curl",
    "rg": "rg",
    "ripgrep": "rg",
}

_MAX_EXPLICIT_TEXT_CHARS = 12000
_FORMAT_VALUE_RE = re.compile(
    r"(?<![0-9A-Za-z])(?P<value>Markdown|MD|PDF|Word|DOCX|Excel|XLSX|CSV|JSONL|JSON|PPTX)(?![0-9A-Za-z])",
    re.IGNORECASE,
)
_LANGUAGE_VALUE_RE = re.compile(
    r"(?P<value>中文|英文|中英双语|English|Chinese)",
    re.IGNORECASE,
)
_STYLE_VALUE_RE = re.compile(r"(?P<value>简洁|详细|正式|口语化|分点|结构化|先结论后分析)")
_PREFERENCE_CUE_RE = re.compile(
    r"(以后|今后|下次|之后|默认|每次|始终|统一|偏好|喜欢|更喜欢|习惯|倾向|请|麻烦|希望|导出|保存|生成|输出|回复|回答|格式|用|使用|采用)",
    re.IGNORECASE,
)
_CLAUSE_SPLIT_RE = re.compile(r"[\n\r。！？；;.!?]+")

_PREFERENCE_PATTERNS: list[dict[str, Any]] = [
    {
        "key": "output_format",
        "pattern": re.compile(
            r"(?:以后|今后|下次|之后)(?:都|请|麻烦|默认)?(?:用|使用|采用)\s*(?P<value>Markdown|MD|PDF|Word|DOCX|Excel|XLSX|CSV|JSONL|JSON|PPTX)",
            re.IGNORECASE,
        ),
        "summary": "偏好以后使用 {value}",
    },
    {
        "key": "output_format",
        "pattern": re.compile(
            r"(?:导出|保存|生成)(?:为|成)?\s*(?P<value>Markdown|MD|PDF|Word|DOCX|Excel|XLSX|CSV|JSONL|JSON|PPTX)",
            re.IGNORECASE,
        ),
        "summary": "偏好输出为 {value}",
    },
    {
        "key": "response_style",
        "pattern": re.compile(
            r"(?:我|用户)?(?:喜欢|偏好|更喜欢|习惯于|倾向于)\s*(?P<value>简洁|详细|正式|口语化|分点|结构化|先结论后分析)",
        ),
        "summary": "偏好回答风格为 {value}",
    },
    {
        "key": "output_format",
        "pattern": re.compile(
            r"(?:我|用户)?(?:喜欢|偏好|更喜欢|习惯于|倾向于)\s*(?P<value>Markdown|MD|PDF|Word|DOCX|Excel|XLSX|CSV|JSONL|JSON|PPTX)",
            re.IGNORECASE,
        ),
        "summary": "偏好输出为 {value}",
    },
    {
        "key": "language",
        "pattern": re.compile(
            r"(?:我|用户)?(?:喜欢|偏好|更喜欢|习惯于|倾向于)\s*(?P<value>中文|英文|中英双语|English|Chinese)",
            re.IGNORECASE,
        ),
        "summary": "偏好使用 {value}",
    },
    {
        "key": "tool",
        "pattern": re.compile(
            r"(?:我|用户)?(?:喜欢|偏好|更喜欢|习惯于|倾向于)\s*(?P<value>python|bash|powershell|git|sqlite|curl|rg|ripgrep)",
            re.IGNORECASE,
        ),
        "summary": "偏好优先使用 {value}",
    },
    {
        "key": "avoidance",
        "pattern": re.compile(
            r"(?:我|用户)?(?:不喜欢|不要|不想|避免|别)\s*(?P<value>冗长|啰嗦|表格|代码块|废话|推测)",
        ),
        "summary": "偏好避免 {value}",
    },
    {
        "key": "language",
        "pattern": re.compile(
            r"(?:以后|今后|下次)(?:都|请|默认)?(?:用|使用|说|回复|回答)\s*(?P<value>中文|英文|中英双语|English|Chinese)",
            re.IGNORECASE,
        ),
        "summary": "偏好使用 {value}",
    },
    {
        "key": "tool",
        "pattern": re.compile(
            r"(?:请|麻烦)?(?:优先|尽量|默认)(?:使用|用)\s*(?P<value>python|bash|powershell|git|sqlite|curl|rg|ripgrep)",
            re.IGNORECASE,
        ),
        "summary": "偏好优先使用 {value}",
    },
    {
        "key": "workflow",
        "pattern": re.compile(
            r"(?:以后|今后|下次)(?:都|请|默认)?(?P<value>先给结论后分析|先给结论|先列清单|先说风险|先给方案)",
        ),
        "summary": "偏好工作流 {value}",
    },
    {
        "key": "response_length",
        "pattern": re.compile(
            r"(?:回答|回复)(?:请|尽量)?(?P<value>简短|简洁|详细|长一点|少一点)",
        ),
        "summary": "偏好回复长度 {value}",
    },
    {
        "key": "presentation",
        "pattern": re.compile(
            r"(?:请|麻烦)?(?:始终|一律|统一|每次都)?(?P<value>分点|结构化|表格式|清单式|逐步)",
        ),
        "summary": "偏好表达方式 {value}",
    },
    {
        "key": "behavior",
        "pattern": re.compile(
            r"(?:每次|以后|今后|默认)(?:都|请)?(?:先|优先)?(?P<value>确认|给草稿|给方案|给清单|给结论)",
        ),
        "summary": "偏好行为 {value}",
    },
    {
        "key": "language",
        "pattern": re.compile(
            r"(?:请|麻烦)?(?:全部|统一|默认)?(?:用|使用|写成)\s*(?P<value>中文|英文|中英双语)",
        ),
        "summary": "偏好语言 {value}",
    },
]


def _slugify(value: str) -> str:
    token = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "_", value.strip().lower())
    token = token.strip("_")
    return token or "preference"


def _stable_candidate_id(user_id: str, memory_type: MemoryType, key: str) -> str:
    payload = f"{user_id}\x1f{memory_type.value}\x1f{key}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:32]


def _normalize_value(key: str, value: str) -> tuple[str, str]:
    raw_value = value.strip()
    if key == "output_format":
        normalized = _FORMAT_ALIASES.get(raw_value.lower(), raw_value.lower())
        return normalized, normalized.upper() if normalized != "markdown" else "Markdown"
    if key == "response_style":
        normalized = _STYLE_ALIASES.get(raw_value, raw_value.lower())
        return normalized, raw_value
    if key == "language":
        normalized = _LANGUAGE_ALIASES.get(raw_value.lower(), _LANGUAGE_ALIASES.get(raw_value, raw_value.lower()))
        return normalized, raw_value
    if key == "tool":
        normalized = _TOOL_ALIASES.get(raw_value.lower(), raw_value.lower())
        return normalized, raw_value
    if key in {"workflow", "response_length", "presentation", "behavior", "avoidance"}:
        return _slugify(raw_value), raw_value
    return _slugify(raw_value), raw_value


def _canonical_structured_key(key: str) -> str:
    normalized = key.replace("preferred_", "").replace("default_", "")
    if "format" in normalized:
        return "output_format"
    if "language" in normalized or "locale" in normalized:
        return "language"
    if "style" in normalized or "tone" in normalized:
        return "response_style"
    if "tool" in normalized:
        return "tool"
    if "length" in normalized:
        return "response_length"
    if "workflow" in normalized or "mode" in normalized:
        return "workflow"
    if "parameter" in normalized or "option" in normalized:
        return "parameter"
    return normalized


def _candidate_for(
    *,
    user_id: str,
    scenario: Scene | str,
    key: str,
    normalized_value: str,
    display_value: str,
    content: str,
    source: str,
    source_events: list[str] | None = None,
    source_summaries: list[str] | None = None,
    tags: list[str] | None = None,
    confidence: float = 0.9,
    metadata: dict[str, Any] | None = None,
) -> MemoryCandidate:
    candidate = MemoryCandidate(
        candidate_id=_stable_candidate_id(user_id, MemoryType.PREFERENCE, f"preference.{key}.{normalized_value}"),
        user_id=user_id,
        memory_type=MemoryType.PREFERENCE,
        key=f"preference.{key}.{normalized_value}",
        content=content,
        scenario=scenario,
        confidence=confidence,
        source=source,
        source_events=source_events or [],
        source_summaries=source_summaries or [],
        tags=tags or [],
        metadata=metadata or {},
    )
    candidate.metadata.setdefault("normalized_value", normalized_value)
    candidate.metadata.setdefault("display_value", display_value)
    return candidate


def _iter_text_fragments(payload: Any) -> Iterable[str]:
    if payload is None:
        return []
    if isinstance(payload, str):
        text = payload.strip()
        return [text] if text else []
    if isinstance(payload, (int, float, bool)):
        return [str(payload)]
    if isinstance(payload, dict):
        fragments: list[str] = []
        for key, value in payload.items():
            if key in {"event_id", "raw_event_id", "timestamp", "created_at", "updated_at"}:
                continue
            if isinstance(value, (str, int, float, bool)):
                fragments.extend(_iter_text_fragments(value))
            elif isinstance(value, (dict, list, tuple)):
                fragments.extend(_iter_text_fragments(value))
        return fragments
    if isinstance(payload, (list, tuple, set)):
        fragments: list[str] = []
        items = sorted(payload, key=lambda item: str(item)) if isinstance(payload, set) else payload
        for item in items:
            fragments.extend(_iter_text_fragments(item))
        return fragments
    return [str(payload)]


def _structured_pref_candidates(event: MemoryEvent) -> list[MemoryCandidate]:
    candidates: list[MemoryCandidate] = []
    payload_sources = [event.input, event.output, event.metadata]
    seen: set[tuple[str, str]] = set()

    for payload in payload_sources:
        if not isinstance(payload, dict):
            continue
        for raw_key, raw_value in payload.items():
            if raw_value is None:
                continue
            key = str(raw_key).strip().lower()
            if not isinstance(raw_value, (str, int, float, bool)):
                continue
            value_text = str(raw_value).strip()
            if not value_text:
                continue
            if not any(
                token in key
                for token in (
                    "format",
                    "style",
                    "tone",
                    "language",
                    "locale",
                    "tool",
                    "parameter",
                    "preference",
                    "preference",
                    "mode",
                    "workflow",
                    "response",
                    "length",
                )
            ):
                continue
            canonical_key = _canonical_structured_key(key)
            normalized, display_value = _normalize_value(canonical_key, value_text)
            pair = (canonical_key, normalized)
            if pair in seen:
                continue
            seen.add(pair)
            human_key = canonical_key.replace("_", " ")
            content = f"偏好{human_key}为 {display_value}"
            candidates.append(
                _candidate_for(
                    user_id=event.user_id,
                    scenario=event.scenario,
                    key=canonical_key,
                    normalized_value=normalized,
                    display_value=display_value,
                    content=content,
                    source="tool_result",
                    source_events=[event.event_id],
                    source_summaries=[content],
                    tags=["explicit", "structured"],
                    confidence=0.86,
                    metadata={
                        "match_type": "structured_field",
                        "field": canonical_key,
                        "field_value": value_text,
                    },
                )
            )
    return candidates


def _extract_from_text(content: str, *, source: str = "conversation") -> list[MemoryCandidate]:
    text = content or ""
    if not text.strip():
        return []
    if len(text) > _MAX_EXPLICIT_TEXT_CHARS:
        text = text[:_MAX_EXPLICIT_TEXT_CHARS]

    results: list[MemoryCandidate] = []
    seen: set[tuple[str, str]] = set()

    for spec in _PREFERENCE_PATTERNS:
        for match in spec["pattern"].finditer(text):
            raw_value = match.group("value").strip()
            if not raw_value:
                continue
            normalized_value, display_value = _normalize_value(spec["key"], raw_value)
            pair = (spec["key"], normalized_value)
            if pair in seen:
                continue
            seen.add(pair)
            summary = spec["summary"].format(value=display_value)
            confidence = 0.95 if source == "conversation" else 0.9
            results.append(
                _candidate_for(
                    user_id="",
                    scenario=Scene.GLOBAL,
                    key=spec["key"],
                    normalized_value=normalized_value,
                    display_value=display_value,
                    content=summary,
                    source=source,
                    source_summaries=[text],
                    tags=["explicit", spec["key"]],
                    confidence=confidence,
                    metadata={
                        "match_type": "pattern",
                        "pattern": spec["pattern"].pattern,
                        "matched_text": match.group(0),
                    },
                )
            )

    for candidate in _extract_flexible_preferences(text, source=source):
        pair = (candidate.metadata.get("field", ""), candidate.metadata.get("normalized_value", ""))
        if pair in seen:
            continue
        seen.add(pair)
        results.append(candidate)

    return results


def _extract_flexible_preferences(text: str, *, source: str) -> list[MemoryCandidate]:
    """Extract natural-language preferences when modifiers appear between cues.

    Rule-based patterns above are intentionally precise.  Real user utterances
    often insert task words between the temporal cue and the value, e.g.
    "以后导出都用 PDF 格式".  This fallback keeps the extraction deterministic
    while allowing those modifiers.
    """
    results: list[MemoryCandidate] = []
    seen: set[tuple[str, str]] = set()

    for raw_clause in _CLAUSE_SPLIT_RE.split(text):
        clause = raw_clause.strip()
        if not clause or len(clause) > 240:
            continue
        if not _PREFERENCE_CUE_RE.search(clause):
            continue

        for key, value_re, summary in (
            ("output_format", _FORMAT_VALUE_RE, "偏好输出为 {value}"),
            ("language", _LANGUAGE_VALUE_RE, "偏好使用 {value}"),
            ("response_style", _STYLE_VALUE_RE, "偏好回答风格为 {value}"),
        ):
            for match in value_re.finditer(clause):
                raw_value = match.group("value").strip()
                normalized_value, display_value = _normalize_value(key, raw_value)
                pair = (key, normalized_value)
                if pair in seen:
                    continue
                seen.add(pair)
                confidence = 0.9 if source == "conversation" else 0.84
                results.append(
                    _candidate_for(
                        user_id="",
                        scenario=Scene.GLOBAL,
                        key=key,
                        normalized_value=normalized_value,
                        display_value=display_value,
                        content=summary.format(value=display_value),
                        source=source,
                        source_summaries=[clause],
                        tags=["explicit", key, "flexible"],
                        confidence=confidence,
                        metadata={
                            "match_type": "flexible_clause",
                            "field": key,
                            "matched_text": clause,
                        },
                    )
                )
    return results


def _rebind_candidates(
    candidates: list[MemoryCandidate],
    *,
    event: MemoryEvent,
    source: str,
) -> list[MemoryCandidate]:
    rebound: list[MemoryCandidate] = []
    for candidate in candidates:
        rebound.append(
            replace(
                candidate,
                candidate_id=_stable_candidate_id(event.user_id, candidate.memory_type, candidate.key),
                user_id=event.user_id,
                scenario=event.scenario,
                source=source,
                source_events=(candidate.source_events or []) + [event.event_id],
                source_summaries=(candidate.source_summaries or []) or [event.content or event.source],
            )
        )
    return rebound


class PreferenceExtractor:
    @staticmethod
    def extract_from_conversation(event: MemoryEvent) -> list[MemoryCandidate]:
        pieces = [event.content, *(_iter_text_fragments(event.input)), *(_iter_text_fragments(event.output))]
        text = "\n".join(piece for piece in pieces if piece)
        candidates = _extract_from_text(text, source="conversation")
        candidates.extend(_structured_pref_candidates(event))
        return _dedupe_candidates(_rebind_candidates(candidates, event=event, source="conversation"))

    @staticmethod
    def extract_from_tool_result(event: MemoryEvent) -> list[MemoryCandidate]:
        pieces = [
            event.content,
            *(_iter_text_fragments(event.output)),
            *(_iter_text_fragments(event.metadata)),
        ]
        text = "\n".join(piece for piece in pieces if piece)
        candidates = _extract_from_text(text, source="tool_result")
        candidates.extend(_structured_pref_candidates(event))
        return _dedupe_candidates(_rebind_candidates(candidates, event=event, source="tool_result"))

    @staticmethod
    def extract_explicit_preference(content: str) -> list[MemoryCandidate]:
        return _extract_from_text(content, source="conversation")

    @staticmethod
    def extract_implicit_preference(events: list[MemoryEvent]) -> list[MemoryCandidate]:
        by_user: dict[str, list[MemoryEvent]] = defaultdict(list)
        for event in events:
            by_user[event.user_id].append(event)

        candidates: list[MemoryCandidate] = []
        for user_id, user_events in by_user.items():
            candidates.extend(_extract_implicit_for_user(user_id, user_events))
        return _dedupe_candidates(candidates)


def extract_preferences(events: list[MemoryEvent]) -> list[MemoryCandidate]:
    """Expose the existing preference extractor through the API's legacy entry point."""

    candidates: list[MemoryCandidate] = []
    for event in events:
        if event.event_type is EventType.TOOL_RESULT:
            candidates.extend(PreferenceExtractor.extract_from_tool_result(event))
        else:
            candidates.extend(PreferenceExtractor.extract_from_conversation(event))
    candidates.extend(PreferenceExtractor.extract_implicit_preference(events))
    return _dedupe_candidates(candidates)


def _dedupe_candidates(candidates: list[MemoryCandidate]) -> list[MemoryCandidate]:
    unique: list[MemoryCandidate] = []
    seen: set[tuple[str, str, str]] = set()
    for candidate in candidates:
        signature = (candidate.user_id, candidate.memory_type.value, candidate.key)
        if signature in seen:
            continue
        seen.add(signature)
        unique.append(candidate)
    return unique


def _normalize_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return str(value).strip()


def _collect_scalar_params(payload: Any, prefix: str = "") -> list[tuple[str, str]]:
    collected: list[tuple[str, str]] = []
    if not isinstance(payload, dict):
        return collected
    for raw_key, raw_value in payload.items():
        key = f"{prefix}{raw_key}".strip(".")
        if raw_value is None:
            continue
        if isinstance(raw_value, dict):
            collected.extend(_collect_scalar_params(raw_value, prefix=f"{key}."))
        elif isinstance(raw_value, (list, tuple, set)):
            values = sorted(raw_value, key=lambda item: str(item)) if isinstance(raw_value, set) else raw_value
            joined = ", ".join(_normalize_scalar(item) for item in values if item is not None)
            if joined:
                collected.append((key.lower(), joined))
        elif isinstance(raw_value, (str, int, float, bool)):
            text = _normalize_scalar(raw_value)
            if text:
                collected.append((key.lower(), text))
    return collected


def _action_signature(event: MemoryEvent) -> str:
    if event.content and event.content.strip():
        return re.sub(r"\s+", " ", event.content.strip().lower())
    if event.tool_name:
        return f"tool:{event.tool_name.strip().lower()}"
    if isinstance(event.metadata, dict):
        for key in ("action", "intent", "behavior", "operation"):
            value = event.metadata.get(key)
            if isinstance(value, str) and value.strip():
                return f"{key}:{re.sub(r'\s+', ' ', value.strip().lower())}"
    return f"{event.event_type.value}:{event.source.strip().lower()}"


def _extract_implicit_for_user(user_id: str, events: list[MemoryEvent]) -> list[MemoryCandidate]:
    if not events:
        return []

    ordered_events = sorted(events, key=lambda event: event.timestamp)
    results: list[MemoryCandidate] = []

    action_groups: dict[str, list[MemoryEvent]] = defaultdict(list)
    tool_groups: dict[str, list[MemoryEvent]] = defaultdict(list)
    parameter_groups: dict[tuple[str, str], list[MemoryEvent]] = defaultdict(list)

    for event in ordered_events:
        action_groups[_action_signature(event)].append(event)
        if event.tool_name:
            tool_groups[event.tool_name.strip().lower()].append(event)
        for key, value in _collect_scalar_params(event.input):
            if any(token in key for token in ("format", "language", "style", "mode", "length", "temperature", "top_p", "prompt", "model", "tool")):
                parameter_groups[(key, value)].append(event)

    for signature, group in action_groups.items():
        if len(group) < 3:
            continue
        results.append(
            _candidate_for(
                user_id=user_id,
                scenario=_shared_scenario(group),
                key="workflow." + _slugify(signature),
                normalized_value=_slugify(signature),
                display_value=signature,
                content=f"用户连续 {len(group)} 次重复相同操作：{group[0].content or group[0].source}",
                source="implicit_action",
                source_events=[event.event_id for event in group],
                source_summaries=[event.content or event.source for event in group],
                tags=["implicit", "workflow"],
                confidence=min(0.55 + 0.1 * (len(group) - 3), 0.95),
                metadata={
                    "heuristic": "repeated_action",
                    "count": len(group),
                    "signature": signature,
                },
            )
        )

    for tool_name, group in tool_groups.items():
        if len(group) < 3:
            continue
        display_tool = tool_name
        results.append(
            _candidate_for(
                user_id=user_id,
                scenario=_shared_scenario(group),
                key="tool." + _slugify(tool_name),
                normalized_value=_slugify(tool_name),
                display_value=display_tool,
                content=f"用户连续 {len(group)} 次使用工具 {display_tool}",
                source="implicit_tool",
                source_events=[event.event_id for event in group],
                source_summaries=[event.content or event.tool_name or event.source for event in group],
                tags=["implicit", "tool"],
                confidence=min(0.55 + 0.1 * (len(group) - 3), 0.95),
                metadata={
                    "heuristic": "repeated_tool",
                    "count": len(group),
                    "tool_name": tool_name,
                },
            )
        )

    for (param_name, param_value), group in parameter_groups.items():
        if len(group) < 3:
            continue
        results.append(
            _candidate_for(
                user_id=user_id,
                scenario=_shared_scenario(group),
                key=f"parameter.{_slugify(param_name)}",
                normalized_value=_slugify(param_value),
                display_value=param_value,
                content=f"用户多次使用参数 {param_name}={param_value}",
                source="implicit_parameter",
                source_events=[event.event_id for event in group],
                source_summaries=[event.content or event.source for event in group],
                tags=["implicit", "parameter"],
                confidence=min(0.55 + 0.1 * (len(group) - 3), 0.95),
                metadata={
                    "heuristic": "repeated_parameter",
                    "count": len(group),
                    "parameter_name": param_name,
                    "parameter_value": param_value,
                },
            )
        )

    return results


def _shared_scenario(events: list[MemoryEvent]) -> Scene:
    scenario = events[0].scenario
    if all(event.scenario == scenario for event in events):
        return scenario
    return Scene.GLOBAL
