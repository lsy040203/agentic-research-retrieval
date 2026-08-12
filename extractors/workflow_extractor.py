from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any, Iterable

from core.constants import EventType, MemoryType, Scene
from core.models import MemoryCandidate, MemoryEvent


_WORKFLOW_TEXT_MARKERS = (
    "workflow",
    "pipeline",
    "sequence",
    "step",
    "steps",
    "then",
    "after",
    "before",
    "next",
    "follow",
    "run",
    "execute",
    "process",
    "tool chain",
    "dependency",
    "dependent",
    "flow",
    "步骤",
    "流程",
    "然后",
    "接着",
    "再",
    "最后",
    "依赖",
    "串联",
    "串行",
    "顺序",
    "执行",
    "运行",
    "调用",
    "工具",
    "导出",
    "合并",
    "安装",
    "配置",
)

_TRANSITION_MARKERS = (
    "then",
    "after",
    "before",
    "next",
    "based on",
    "using",
    "with",
    "result of",
    "from",
    "then use",
    "然后",
    "接着",
    "再",
    "之后",
    "基于",
    "使用",
    "基于上一步",
    "利用",
    "再用",
    "根据",
)

_FILE_RE = re.compile(
    r"\b[\w.-]+\.(?:csv|tsv|xlsx|xls|json|jsonl|txt|log|md|pdf|docx|zip|tar|gz|py|sh|sql)\b",
    re.IGNORECASE,
)
_PATH_RE = re.compile(r"(?:[A-Za-z]:\\|/)[^\s,;]+")
_STEP_RE = re.compile(r"^(?:\d+[.)、-]|step\s*\d+|步骤\s*\d+)", re.IGNORECASE)


def _slugify(text: str) -> str:
    token = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "_", text.strip().lower())
    token = re.sub(r"_+", "_", token).strip("_")
    return token or "workflow"


def _stable_candidate_id(user_id: str, memory_type: MemoryType, key: str) -> str:
    payload = f"{user_id}\x1f{memory_type.value}\x1f{key}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:32]


def _normalize_text(value: Any) -> str:
    text = str(value).strip().lower()
    return re.sub(r"\s+", " ", text)


def _iter_text_fragments(payload: Any) -> list[str]:
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
            fragments.extend(_iter_text_fragments(value))
        return fragments
    if isinstance(payload, (list, tuple, set)):
        fragments: list[str] = []
        items = sorted(payload, key=lambda item: str(item)) if isinstance(payload, set) else payload
        for item in items:
            fragments.extend(_iter_text_fragments(item))
        return fragments
    return [str(payload)]


def _event_text(event: MemoryEvent) -> str:
    parts = [
        event.content or "",
        event.tool_name or "",
        *(_iter_text_fragments(event.input)),
        *(_iter_text_fragments(event.output)),
        *(_iter_text_fragments(event.metadata)),
    ]
    return "\n".join(part for part in parts if part and str(part).strip())


def _is_tool_event(event: MemoryEvent) -> bool:
    return event.event_type in {EventType.TOOL_CALL, EventType.TOOL_RESULT} or bool(event.tool_name)


def _is_workflow_relevant(event: MemoryEvent) -> bool:
    if _is_tool_event(event):
        return True

    text = _normalize_text(_event_text(event))
    if not text:
        return False

    if any(marker in text for marker in _WORKFLOW_TEXT_MARKERS):
        return True

    if _STEP_RE.search(text):
        return True

    return False


def _extract_artifacts(event: MemoryEvent) -> set[str]:
    artifacts: set[str] = set()
    text = _event_text(event)
    for match in _FILE_RE.finditer(text):
        artifacts.add(match.group(0).lower())
    for match in _PATH_RE.finditer(text):
        artifacts.add(match.group(0).lower())

    for payload in (event.input, event.output, event.metadata):
        if not isinstance(payload, dict):
            continue
        for key, value in payload.items():
            if value is None:
                continue
            key_text = str(key).strip().lower()
            if key_text in {"source", "target", "file", "path", "input", "output", "depends_on", "previous", "next", "artifact", "artifacts"}:
                for fragment in _iter_text_fragments(value):
                    fragment = fragment.strip().lower()
                    if fragment:
                        artifacts.add(fragment)
    return artifacts


def _group_key(event: MemoryEvent) -> tuple[str, str, str]:
    """Keep simultaneous tasks in one session isolated from each other."""
    return event.user_id, event.session_id, event.task_id


def _group_events(events: list[MemoryEvent]) -> list[tuple[tuple[str, str, str], list[MemoryEvent]]]:
    grouped: dict[tuple[str, str, str], list[MemoryEvent]] = defaultdict(list)
    for event in events:
        key = _group_key(event)
        grouped[key].append(event)
    return [
        (key, sorted(group, key=lambda event: (event.timestamp, event.event_id)))
        for key, group in sorted(grouped.items())
    ]


def _segment_scenario(events: list[MemoryEvent]) -> Scene:
    scenario = events[0].scenario
    if all(event.scenario == scenario for event in events):
        return scenario
    return Scene.GLOBAL


def _boundary_signature(events: list[MemoryEvent], boundaries: list[tuple[int, int]]) -> str:
    parts: list[str] = []
    for start, end in boundaries:
        segment = events[start : end + 1]
        tool_names = [event.tool_name or event.event_type.value for event in segment if _is_tool_event(event)]
        if tool_names:
            parts.append("->".join(_slugify(name) for name in tool_names))
    return "__".join(parts) if parts else "workflow"


def _workflow_boundaries_with_indices(events: list[MemoryEvent]) -> list[tuple[int, int]]:
    relevant = [index for index, event in enumerate(events) if _is_workflow_relevant(event)]
    if not relevant:
        return []

    boundaries: list[tuple[int, int]] = []
    start = prev = relevant[0]
    for index in relevant[1:]:
        if index - prev <= 2:
            prev = index
            continue
        boundaries.append((start, prev))
        start = prev = index
    boundaries.append((start, prev))
    return boundaries


def _tool_groups(segment: list[MemoryEvent]) -> list[list[MemoryEvent]]:
    groups: list[list[MemoryEvent]] = []
    for event in segment:
        if not _is_tool_event(event):
            continue
        if groups and _same_tool_group(groups[-1][-1], event):
            groups[-1].append(event)
            continue
        groups.append([event])
    return groups


def _same_tool_group(left: MemoryEvent, right: MemoryEvent) -> bool:
    left_tool = (left.tool_name or "").strip().lower()
    right_tool = (right.tool_name or "").strip().lower()
    if not left_tool or not right_tool:
        return False
    if left_tool != right_tool:
        return False
    if left.event_type == right.event_type:
        return True
    return {left.event_type, right.event_type} <= {EventType.TOOL_CALL, EventType.TOOL_RESULT}


def _step_label(group: list[MemoryEvent]) -> str:
    representative = group[0]
    if representative.tool_name:
        return representative.tool_name.strip().lower()
    if representative.content:
        return re.sub(r"\s+", " ", representative.content.strip().lower())[:40]
    return representative.event_type.value


def _group_artifacts(group: list[MemoryEvent]) -> set[str]:
    artifacts: set[str] = set()
    for event in group:
        artifacts |= _extract_artifacts(event)
    return artifacts


def _group_transition_markers(group: list[MemoryEvent]) -> bool:
    text = " ".join(_normalize_text(_event_text(event)) for event in group)
    return any(marker in text for marker in _TRANSITION_MARKERS)


def _dependency_edges(groups: list[list[MemoryEvent]]) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    if len(groups) < 2:
        return edges

    group_artifacts = [_group_artifacts(group) for group in groups]
    group_texts = [" ".join(_normalize_text(_event_text(event)) for event in group) for group in groups]

    for index in range(len(groups) - 1):
        left_group = groups[index]
        right_group = groups[index + 1]
        overlap = sorted(group_artifacts[index] & group_artifacts[index + 1])
        score = 0.0
        evidence: list[str] = []

        if overlap:
            score += 0.7
            evidence.extend(overlap[:4])

        left_tool = _step_label(left_group)
        right_tool = _step_label(right_group)
        if left_tool and left_tool in group_texts[index + 1]:
            score += 0.1
            evidence.append(left_tool)

        if any(marker in group_texts[index + 1] for marker in _TRANSITION_MARKERS):
            score += 0.15
            evidence.append("transition")

        if any(token in group_texts[index + 1] for token in ("source", "target", "input", "output", "result", "from", "use")):
            score += 0.1
            evidence.append("field_reference")

        if _group_transition_markers(right_group):
            score += 0.05

        if score >= 0.5:
            edges.append(
                {
                    "from": left_group[0].event_id,
                    "to": right_group[0].event_id,
                    "from_tool": left_tool,
                    "to_tool": right_tool,
                    "score": min(score, 1.0),
                    "evidence": evidence,
                }
            )
    return edges


def _workflow_content(prefix: str, groups: list[list[MemoryEvent]], dependencies: list[dict[str, Any]]) -> str:
    steps = [f"{index + 1}. {_step_label(group)}" for index, group in enumerate(groups)]
    if dependencies:
        edges = " -> ".join(edge["from_tool"] + "→" + edge["to_tool"] for edge in dependencies if edge.get("from_tool") and edge.get("to_tool"))
        if edges:
            return f"{prefix}: " + " | ".join(steps) + f" | deps: {edges}"
    return f"{prefix}: " + " | ".join(steps)


def _workflow_confidence(group_count: int, dependency_count: int, complex_flow: bool) -> float:
    confidence = 0.62
    confidence += min(0.12, 0.04 * max(0, group_count - 2))
    confidence += min(0.12, 0.06 * dependency_count)
    if complex_flow:
        confidence += 0.08
    return min(confidence, 0.95)


def _workflow_reproduction_rate(groups: list[list[MemoryEvent]], dependencies: list[dict[str, Any]]) -> tuple[float, dict[str, float]]:
    """Score whether a workflow contains enough evidence to replay safely.

    A workflow is reproducible only when its ordered tool steps, adjacent data
    dependencies, and terminal tool results are all present.  This is an
    evidence score, not a fabricated constant, and is intentionally surfaced
    to downstream storage and evaluation code.
    """
    if not groups:
        return 0.0, {"step_coverage": 0.0, "dependency_coverage": 0.0, "result_coverage": 0.0}

    step_coverage = sum(bool(_step_label(group)) for group in groups) / len(groups)
    expected_dependencies = max(0, len(groups) - 1)
    dependency_coverage = 1.0 if expected_dependencies == 0 else min(1.0, len(dependencies) / expected_dependencies)
    result_coverage = sum(
        any(event.event_type is EventType.TOOL_RESULT and event.success is not False for event in group)
        for group in groups
    ) / len(groups)
    rate = 0.35 * step_coverage + 0.45 * dependency_coverage + 0.20 * result_coverage
    return round(min(rate, 1.0), 4), {
        "step_coverage": round(step_coverage, 4),
        "dependency_coverage": round(dependency_coverage, 4),
        "result_coverage": round(result_coverage, 4),
    }


def _workflow_candidate(
    *,
    pattern: str,
    prefix: str,
    events: list[MemoryEvent],
    groups: list[list[MemoryEvent]],
    dependencies: list[dict[str, Any]],
) -> MemoryCandidate:
    step_labels = [_step_label(group) for group in groups]
    source_event_ids = [event.event_id for event in events]
    source_summaries = [event.content or event.tool_name or event.event_type.value for event in events]
    reproduction_rate, reconstruction_evidence = _workflow_reproduction_rate(groups, dependencies)
    confidence = _workflow_confidence(len(groups), len(dependencies), pattern != "tool_sequence")
    signature = "__".join(_slugify(label) for label in step_labels)
    boundary = (0, len(events) - 1) if events else (0, 0)

    return MemoryCandidate(
        candidate_id=_stable_candidate_id(events[0].user_id, MemoryType.WORKFLOW, f"workflow.{pattern}.{signature}"),
        user_id=events[0].user_id,
        memory_type=MemoryType.WORKFLOW,
        key=f"workflow.{pattern}.{signature}",
        content=_workflow_content(prefix, groups, dependencies),
        scenario=_segment_scenario(events),
        confidence=confidence,
        source=f"workflow_{pattern}",
        source_events=source_event_ids,
        source_summaries=source_summaries,
        tags=["workflow", pattern] + (["dependency"] if dependencies else []),
        metadata={
            "pattern": pattern,
            "step_count": len(groups),
            "tool_names": step_labels,
            "dependencies": dependencies,
            "reproduction_rate": reproduction_rate,
            "reconstruction_evidence": reconstruction_evidence,
            "occurrence_count": 1,
            "task_ids": [events[0].task_id],
            "boundary": boundary,
            "group_event_counts": [len(group) for group in groups],
            "workflow_signature": signature,
        },
    )


def _extract_candidates_from_segment(segment: list[MemoryEvent], *, mode: str) -> list[MemoryCandidate]:
    groups = _tool_groups(segment)
    if len(groups) < 2:
        return []

    dependencies = _dependency_edges(groups)
    has_conversation_glue = any(not _is_tool_event(event) for event in segment)
    distinct_tools = len({label for label in (_step_label(group) for group in groups)})

    if mode == "tool_sequence":
        candidate = _workflow_candidate(
                pattern="tool_sequence",
                prefix="Tool sequence",
                events=segment,
                groups=groups,
                dependencies=dependencies,
        )
        return [candidate] if candidate.metadata["reproduction_rate"] >= 0.8 else []

    complex_flow = distinct_tools >= 2 or dependencies or has_conversation_glue or len(groups) >= 3
    if not complex_flow:
        return []

    candidate = _workflow_candidate(
            pattern="multi_step",
            prefix="Complex workflow",
            events=segment,
            groups=groups,
            dependencies=dependencies,
    )
    return [candidate] if candidate.metadata["reproduction_rate"] >= 0.8 else []


def _dedupe_candidates(candidates: list[MemoryCandidate]) -> list[MemoryCandidate]:
    unique: dict[tuple[str, str, str], MemoryCandidate] = {}
    for candidate in candidates:
        signature = (candidate.user_id, candidate.memory_type.value, candidate.key)
        existing = unique.get(signature)
        if existing is None:
            unique[signature] = candidate
            continue

        merged_events = sorted(set(existing.source_events) | set(candidate.source_events))
        merged_summaries = list(dict.fromkeys(existing.source_summaries + candidate.source_summaries))
        old_count = int(existing.metadata.get("occurrence_count", 1))
        new_count = int(candidate.metadata.get("occurrence_count", 1))
        weighted_rate = (
            float(existing.metadata.get("reproduction_rate", 0.0)) * old_count
            + float(candidate.metadata.get("reproduction_rate", 0.0)) * new_count
        ) / (old_count + new_count)
        metadata = dict(existing.metadata)
        metadata["occurrence_count"] = old_count + new_count
        metadata["reproduction_rate"] = round(weighted_rate, 4)
        metadata["task_ids"] = sorted(
            set(existing.metadata.get("task_ids", [])) | set(candidate.metadata.get("task_ids", []))
        )
        unique[signature] = MemoryCandidate(
            candidate_id=existing.candidate_id,
            user_id=existing.user_id,
            memory_type=existing.memory_type,
            key=existing.key,
            content=existing.content,
            scenario=existing.scenario,
            confidence=max(existing.confidence, candidate.confidence),
            source=existing.source,
            source_events=merged_events,
            source_summaries=merged_summaries,
            tags=list(dict.fromkeys(existing.tags + candidate.tags)),
            metadata=metadata,
            created_at=existing.created_at,
        )
    return list(unique.values())


class WorkflowExtractor:
    @staticmethod
    def detect_workflow_boundary(events: list[MemoryEvent]) -> list[tuple[int, int]]:
        return _workflow_boundaries_with_indices(events)

    @staticmethod
    def extract_tool_sequence(events: list[MemoryEvent]) -> list[MemoryCandidate]:
        candidates: list[MemoryCandidate] = []
        for _, group_events in _group_events(events):
            for start, end in _workflow_boundaries_with_indices(group_events):
                segment = group_events[start : end + 1]
                candidates.extend(_extract_candidates_from_segment(segment, mode="tool_sequence"))
        return _dedupe_candidates(candidates)

    @staticmethod
    def extract_multi_step_workflow(session_events: list[MemoryEvent]) -> list[MemoryCandidate]:
        candidates: list[MemoryCandidate] = []
        for _, group_events in _group_events(session_events):
            for start, end in _workflow_boundaries_with_indices(group_events):
                segment = group_events[start : end + 1]
                candidates.extend(_extract_candidates_from_segment(segment, mode="multi_step"))
        return _dedupe_candidates(candidates)


def extract_workflows(events: list[MemoryEvent]) -> list[MemoryCandidate]:
    """Expose the existing workflow extractor through the API's legacy entry point."""

    return _dedupe_candidates(
        WorkflowExtractor.extract_tool_sequence(events)
        + WorkflowExtractor.extract_multi_step_workflow(events)
    )
