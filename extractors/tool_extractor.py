from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from typing import Any

from core.constants import EventType, MemoryType, Scene
from core.models import MemoryCandidate, MemoryEvent


_CORRELATION_KEYS = ("tool_call_id", "call_id", "invocation_id", "request_id", "trace_id", "run_id")
_DURATION_KEYS = ("duration_ms", "latency_ms", "elapsed_ms", "response_time_ms", "execution_time_ms")
_SENSITIVE_TOKENS = ("password", "passwd", "secret", "token", "api_key", "access_key", "credential", "authorization")
_SUCCESS_STATUSES = {"ok", "success", "succeeded", "completed", "complete", "done", "passed"}
_FAILURE_STATUSES = {"error", "failed", "failure", "exception", "timeout", "timed_out", "cancelled", "canceled"}


@dataclass(frozen=True, slots=True)
class _ToolInvocation:
    user_id: str
    tool_name: str
    scenario: Scene
    event_ids: tuple[str, ...]
    success: bool | None
    duration_ms: float | None
    failure_reason: str | None
    parameters: tuple[tuple[str, str], ...]


def _slugify(value: str) -> str:
    token = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "_", value.strip().lower())
    return token.strip("_") or "tool"


def _stable_candidate_id(user_id: str, memory_type: MemoryType, key: str) -> str:
    payload = f"{user_id}\x1f{memory_type.value}\x1f{key}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:32]


def _normalise_tool_name(event: MemoryEvent) -> str:
    return (event.tool_name or "unknown_tool").strip().lower() or "unknown_tool"


def _is_tool_event(event: MemoryEvent) -> bool:
    return event.event_type in {EventType.TOOL_CALL, EventType.TOOL_RESULT} or bool(event.tool_name)


def _group_key(event: MemoryEvent) -> tuple[str, str, str, str]:
    return event.user_id, event.session_id, event.task_id, _normalise_tool_name(event)


def _correlation_id(event: MemoryEvent) -> str | None:
    for payload in (event.metadata, event.output, event.input, event.raw_event or {}):
        if not isinstance(payload, dict):
            continue
        for key in _CORRELATION_KEYS:
            value = payload.get(key)
            if value is not None and str(value).strip():
                return f"{key}:{str(value).strip()}"
    return None


def _as_duration(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value >= 0:
        return float(value)
    if isinstance(value, str):
        try:
            parsed = float(value.strip())
        except ValueError:
            return None
        return parsed if parsed >= 0 else None
    return None


def _duration_ms(event: MemoryEvent) -> float | None:
    for payload in (event.metadata, event.output, event.input):
        if not isinstance(payload, dict):
            continue
        for key in _DURATION_KEYS:
            duration = _as_duration(payload.get(key))
            if duration is not None:
                return duration
    return None


def _terminal_success(event: MemoryEvent) -> bool | None:
    if event.success is not None:
        return bool(event.success)

    text_values: list[str] = []
    for payload in (event.output, event.metadata):
        if not isinstance(payload, dict):
            continue
        for key in ("status", "state", "result", "outcome"):
            value = payload.get(key)
            if isinstance(value, str):
                text_values.append(value.lower().strip())
        if any(key in payload and payload[key] not in (None, "", False) for key in ("error", "exception", "stderr", "error_type")):
            return False

    text_values.extend(re.findall(r"[a-z_]+", (event.content or "").lower()))
    if any(value in _FAILURE_STATUSES for value in text_values):
        return False
    if any(value in _SUCCESS_STATUSES for value in text_values):
        return True
    return None


def _normalise_failure_reason(value: str) -> str:
    text = re.sub(r"[A-Za-z]:\\[^\s,;]+|/[^\s,;]+", "<path>", value.strip().lower())
    text = re.sub(r"\b\d+\b", "<num>", text)
    text = re.sub(r"\s+", " ", text)
    return text[:160] or "unknown_failure"


def _failure_reason(event: MemoryEvent) -> str:
    for payload in (event.output, event.metadata, event.raw_event or {}):
        if not isinstance(payload, dict):
            continue
        for key in ("error_type", "error_code", "error", "exception", "stderr", "message", "reason"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return _normalise_failure_reason(f"{key}: {value}")
    if event.content and event.content.strip():
        return _normalise_failure_reason(event.content)
    return "unknown_failure"


def _collect_parameters(payload: Any, prefix: str = "") -> list[tuple[str, str]]:
    if not isinstance(payload, dict):
        return []

    parameters: list[tuple[str, str]] = []
    for raw_key, value in sorted(payload.items(), key=lambda item: str(item[0])):
        key = f"{prefix}.{raw_key}".strip(".").lower()
        if any(token in key for token in _SENSITIVE_TOKENS):
            continue
        if value is None:
            continue
        if isinstance(value, dict):
            parameters.extend(_collect_parameters(value, key))
            continue
        if isinstance(value, set):
            value = sorted(value, key=str)
        if isinstance(value, (list, tuple)):
            rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        elif isinstance(value, (str, int, float, bool)):
            rendered = str(value).strip()
        else:
            continue
        if rendered:
            parameters.append((key, rendered[:120]))
    return parameters


def _event_scenario(events: list[MemoryEvent]) -> Scene:
    scenario = events[0].scenario
    return scenario if all(event.scenario == scenario for event in events) else Scene.SYSTEM


def _build_invocations(events: list[MemoryEvent]) -> list[_ToolInvocation]:
    grouped: dict[tuple[str, str, str, str], list[MemoryEvent]] = defaultdict(list)
    for event in events:
        if _is_tool_event(event):
            grouped[_group_key(event)].append(event)

    invocations: list[_ToolInvocation] = []
    for (_, _, _, tool_name), group in sorted(grouped.items()):
        ordered = sorted(group, key=lambda event: (event.timestamp, event.event_id))
        pending_by_id: dict[str, deque[MemoryEvent]] = defaultdict(deque)
        pending_fifo: deque[MemoryEvent] = deque()

        for event in ordered:
            if event.event_type is EventType.TOOL_CALL:
                pending_fifo.append(event)
                correlation = _correlation_id(event)
                if correlation:
                    pending_by_id[correlation].append(event)
                continue

            if event.event_type is not EventType.TOOL_RESULT:
                continue

            correlation = _correlation_id(event)
            call_event: MemoryEvent | None = None
            if correlation and pending_by_id[correlation]:
                call_event = pending_by_id[correlation].popleft()
                pending_fifo.remove(call_event)
            elif pending_fifo:
                call_event = pending_fifo.popleft()
                call_correlation = _correlation_id(call_event)
                if call_correlation and pending_by_id[call_correlation]:
                    pending_by_id[call_correlation].remove(call_event)

            duration = _duration_ms(event)
            if duration is None and call_event is not None:
                duration = _duration_ms(call_event)
            if duration is None and call_event is not None:
                elapsed = (event.timestamp - call_event.timestamp).total_seconds() * 1000
                duration = elapsed if elapsed >= 0 else None

            success = _terminal_success(event)
            invocations.append(
                _ToolInvocation(
                    user_id=event.user_id,
                    tool_name=tool_name,
                    scenario=_event_scenario([call_event, event] if call_event else [event]),
                    event_ids=tuple(event_id for event_id in ((call_event.event_id if call_event else None), event.event_id) if event_id),
                    success=success,
                    duration_ms=duration,
                    failure_reason=_failure_reason(event) if success is False else None,
                    parameters=tuple(_collect_parameters(call_event.input if call_event else event.input)),
                )
            )

        for call_event in pending_fifo:
            invocations.append(
                _ToolInvocation(
                    user_id=call_event.user_id,
                    tool_name=tool_name,
                    scenario=call_event.scenario,
                    event_ids=(call_event.event_id,),
                    success=None,
                    duration_ms=_duration_ms(call_event),
                    failure_reason=None,
                    parameters=tuple(_collect_parameters(call_event.input)),
                )
            )
    return invocations


def _success_rate(invocations: list[_ToolInvocation]) -> float:
    completed = [item for item in invocations if item.success is not None]
    if not completed:
        return 0.0
    return sum(item.success is True for item in completed) / len(completed)


class ToolExtractor:
    @staticmethod
    def calculate_tool_success_rate(tool_name: str, events: list[MemoryEvent]) -> float:
        """Return success_count / completed_invocation_count for one tool.

        Calls without a terminal result are excluded rather than being silently
        counted as failures; their number is reported by ``extract_tool_pattern``.
        """
        normalized_tool = tool_name.strip().lower()
        invocations = [item for item in _build_invocations(events) if item.tool_name == normalized_tool]
        return _success_rate(invocations)

    @staticmethod
    def extract_tool_pattern(events: list[MemoryEvent]) -> list[MemoryCandidate]:
        by_user_tool: dict[tuple[str, str], list[_ToolInvocation]] = defaultdict(list)
        for invocation in _build_invocations(events):
            by_user_tool[(invocation.user_id, invocation.tool_name)].append(invocation)

        candidates: list[MemoryCandidate] = []
        for (user_id, tool_name), invocations in sorted(by_user_tool.items()):
            completed = [item for item in invocations if item.success is not None]
            successes = sum(item.success is True for item in completed)
            failures = sum(item.success is False for item in completed)
            unknown = len(invocations) - len(completed)
            durations = [item.duration_ms for item in invocations if item.duration_ms is not None]
            failure_counts = Counter(item.failure_reason for item in invocations if item.failure_reason)
            parameter_counts = Counter(item.parameters for item in invocations if item.parameters)
            rate = _success_rate(invocations)
            completeness = len(completed) / len(invocations) if invocations else 0.0
            common_failures = [
                {"reason": reason, "count": count}
                for reason, count in failure_counts.most_common(5)
            ]
            common_parameters = [
                {"parameters": dict(parameters), "count": count}
                for parameters, count in parameter_counts.most_common(5)
            ]
            scenarios = [item.scenario for item in invocations]
            scenario = scenarios[0] if all(item == scenarios[0] for item in scenarios) else Scene.SYSTEM
            key = f"tool.pattern.{_slugify(tool_name)}"
            content_parts = [
                f"工具 {tool_name}：成功率 {rate:.2%}（{successes}/{len(completed)}）",
                f"数据完整度 {completeness:.2%}（已完成 {len(completed)}，未完成 {unknown}）",
            ]
            if durations:
                content_parts.append(f"平均响应 {sum(durations) / len(durations):.2f}ms")
            if common_failures:
                content_parts.append(f"常见失败 {common_failures[0]['reason']}（{common_failures[0]['count']}）")

            event_ids = sorted({event_id for item in invocations for event_id in item.event_ids})
            candidates.append(
                MemoryCandidate(
                    candidate_id=_stable_candidate_id(user_id, MemoryType.TOOL, key),
                    user_id=user_id,
                    memory_type=MemoryType.TOOL,
                    key=key,
                    content="；".join(content_parts),
                    scenario=scenario,
                    confidence=round(min(0.98, 0.55 + 0.45 * completeness), 4),
                    source="tool_pattern",
                    source_events=event_ids,
                    source_summaries=[f"{tool_name}: {item.success}" for item in invocations],
                    tags=["tool", "pattern", _slugify(tool_name)],
                    metadata={
                        "tool_name": tool_name,
                        "success_count": successes,
                        "failure_count": failures,
                        "total_count": len(completed),
                        "observed_count": len(invocations),
                        "unknown_count": unknown,
                        "success_rate": round(rate, 4),
                        "data_completeness": round(completeness, 4),
                        "average_response_ms": round(sum(durations) / len(durations), 4) if durations else None,
                        "duration_sample_count": len(durations),
                        "common_failure_reasons": common_failures,
                        "common_parameter_combinations": common_parameters,
                    },
                )
            )
        return candidates
