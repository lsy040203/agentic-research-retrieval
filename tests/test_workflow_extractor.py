from __future__ import annotations

from datetime import datetime, timezone

from core.constants import EventType, MemoryType, Scene
from core.models import MemoryEvent
from extractors import workflow_extractor as workflow_mod
from extractors.workflow_extractor import WorkflowExtractor


def _event(
    event_id: str,
    *,
    user_id: str = "user-1",
    session_id: str = "session-1",
    task_id: str = "task-1",
    event_type: EventType = EventType.CONVERSATION,
    scenario: Scene = Scene.GLOBAL,
    source: str = "conversation",
    content: str | None = None,
    tool_name: str | None = None,
    input_payload: dict | None = None,
    output_payload: dict | None = None,
    metadata: dict | None = None,
    success: bool | None = True,
    timestamp: datetime | None = None,
) -> MemoryEvent:
    return MemoryEvent(
        event_id=event_id,
        raw_event_id=f"raw-{event_id}",
        user_id=user_id,
        session_id=session_id,
        task_id=task_id,
        event_type=event_type,
        scenario=scenario,
        source=source,
        content=content,
        tool_name=tool_name,
        input=input_payload or {},
        output=output_payload or {},
        metadata=metadata or {},
        success=success,
        timestamp=timestamp or datetime(2026, 6, 23, tzinfo=timezone.utc),
        raw_event={"event_id": event_id},
    )


def test_detect_workflow_boundary_splits_separate_clusters() -> None:
    events = [
        _event("evt-0", content="just chatting"),
        _event(
            "evt-1",
            event_type=EventType.TOOL_CALL,
            source="tool",
            tool_name="download",
            input_payload={"url": "https://example.com/a.csv"},
            output_payload={"file": "a.csv"},
        ),
        _event(
            "evt-2",
            event_type=EventType.TOOL_RESULT,
            source="tool",
            tool_name="download",
            output_payload={"file": "a.csv"},
        ),
        _event("evt-3", content="small talk"),
        _event("evt-4", content="still small talk"),
        _event(
            "evt-5",
            event_type=EventType.TOOL_CALL,
            source="tool",
            tool_name="export",
            input_payload={"source": "a.csv"},
            output_payload={"file": "report.xlsx"},
        ),
        _event(
            "evt-6",
            event_type=EventType.TOOL_RESULT,
            source="tool",
            tool_name="export",
            output_payload={"file": "report.xlsx"},
        ),
    ]

    assert WorkflowExtractor.detect_workflow_boundary(events) == [(1, 2), (5, 6)]


def test_extract_tool_sequence_recognizes_continuous_tool_chain() -> None:
    events = [
        _event("evt-0", content="please handle this in order"),
        _event(
            "evt-1",
            event_type=EventType.TOOL_CALL,
            source="tool",
            tool_name="download",
            input_payload={"url": "https://example.com/raw.csv"},
            output_payload={"file": "raw.csv"},
        ),
        _event(
            "evt-2",
            event_type=EventType.TOOL_RESULT,
            source="tool",
            tool_name="download",
            output_payload={"file": "raw.csv"},
        ),
        _event("evt-3", content="use raw.csv next"),
        _event(
            "evt-4",
            event_type=EventType.TOOL_CALL,
            source="tool",
            tool_name="transform",
            input_payload={"source": "raw.csv"},
            output_payload={"file": "processed.csv"},
        ),
        _event(
            "evt-5",
            event_type=EventType.TOOL_RESULT,
            source="tool",
            tool_name="transform",
            output_payload={"file": "processed.csv"},
        ),
        _event("evt-6", content="then export the output"),
        _event(
            "evt-7",
            event_type=EventType.TOOL_CALL,
            source="tool",
            tool_name="export",
            input_payload={"input": "processed.csv"},
            output_payload={"file": "report.xlsx"},
        ),
        _event(
            "evt-8",
            event_type=EventType.TOOL_RESULT,
            source="tool",
            tool_name="export",
            output_payload={"file": "report.xlsx"},
        ),
    ]

    candidates = WorkflowExtractor.extract_tool_sequence(events)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.memory_type is MemoryType.WORKFLOW
    assert candidate.scenario is Scene.GLOBAL
    assert candidate.key.startswith("workflow.tool_sequence.")
    assert candidate.content.startswith("Tool sequence")
    assert candidate.metadata["pattern"] == "tool_sequence"
    assert candidate.metadata["step_count"] == 3
    assert candidate.metadata["tool_names"] == ["download", "transform", "export"]
    assert candidate.metadata["reproduction_rate"] >= 0.8
    assert len(candidate.metadata["dependencies"]) == 2
    assert candidate.metadata["dependencies"][0]["from_tool"] == "download"
    assert candidate.metadata["dependencies"][0]["to_tool"] == "transform"
    assert candidate.metadata["dependencies"][1]["from_tool"] == "transform"
    assert candidate.metadata["dependencies"][1]["to_tool"] == "export"


def test_extract_multi_step_workflow_recognizes_dependencies() -> None:
    events = [
        _event("evt-0", content="step by step, use the previous result"),
        _event(
            "evt-1",
            event_type=EventType.TOOL_CALL,
            source="tool",
            tool_name="search",
            input_payload={"query": "dataset"},
            output_payload={"file": "dataset.csv"},
        ),
        _event(
            "evt-2",
            event_type=EventType.TOOL_RESULT,
            source="tool",
            tool_name="search",
            output_payload={"file": "dataset.csv"},
        ),
        _event("evt-3", content="then use dataset.csv for the next step"),
        _event(
            "evt-4",
            event_type=EventType.TOOL_CALL,
            source="tool",
            tool_name="analyse",
            input_payload={"input": "dataset.csv"},
            output_payload={"file": "analysis.json"},
        ),
        _event(
            "evt-5",
            event_type=EventType.TOOL_RESULT,
            source="tool",
            tool_name="analyse",
            output_payload={"file": "analysis.json"},
        ),
        _event("evt-6", content="after that, export the report"),
        _event(
            "evt-7",
            event_type=EventType.TOOL_CALL,
            source="tool",
            tool_name="export",
            input_payload={"input": "analysis.json"},
            output_payload={"file": "report.xlsx"},
        ),
        _event(
            "evt-8",
            event_type=EventType.TOOL_RESULT,
            source="tool",
            tool_name="export",
            output_payload={"file": "report.xlsx"},
        ),
    ]

    candidates = WorkflowExtractor.extract_multi_step_workflow(events)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.memory_type is MemoryType.WORKFLOW
    assert candidate.scenario is Scene.GLOBAL
    assert candidate.key.startswith("workflow.multi_step.")
    assert candidate.content.startswith("Complex workflow")
    assert candidate.metadata["pattern"] == "multi_step"
    assert candidate.metadata["step_count"] == 3
    assert candidate.metadata["reproduction_rate"] >= 0.8
    assert len(candidate.metadata["dependencies"]) == 2
    assert candidate.metadata["dependencies"][0]["evidence"]
    assert candidate.metadata["dependencies"][1]["evidence"]


def test_extract_tool_sequence_deduplicates_repeated_workflows() -> None:
    workflow_one = [
        _event(
            "evt-a1",
            session_id="session-a",
            event_type=EventType.TOOL_CALL,
            source="tool",
            tool_name="download",
            input_payload={"url": "https://example.com/raw.csv"},
            output_payload={"file": "raw.csv"},
        ),
        _event(
            "evt-a2",
            session_id="session-a",
            event_type=EventType.TOOL_RESULT,
            source="tool",
            tool_name="download",
            output_payload={"file": "raw.csv"},
        ),
        _event("evt-a3", session_id="session-a", content="use raw.csv next"),
        _event(
            "evt-a4",
            session_id="session-a",
            event_type=EventType.TOOL_CALL,
            source="tool",
            tool_name="transform",
            input_payload={"source": "raw.csv"},
            output_payload={"file": "processed.csv"},
        ),
        _event(
            "evt-a5",
            session_id="session-a",
            event_type=EventType.TOOL_RESULT,
            source="tool",
            tool_name="transform",
            output_payload={"file": "processed.csv"},
        ),
    ]
    workflow_two = [
        _event(
            "evt-b1",
            session_id="session-b",
            event_type=EventType.TOOL_CALL,
            source="tool",
            tool_name="download",
            input_payload={"url": "https://example.com/raw.csv"},
            output_payload={"file": "raw.csv"},
        ),
        _event(
            "evt-b2",
            session_id="session-b",
            event_type=EventType.TOOL_RESULT,
            source="tool",
            tool_name="download",
            output_payload={"file": "raw.csv"},
        ),
        _event("evt-b3", session_id="session-b", content="use raw.csv next"),
        _event(
            "evt-b4",
            session_id="session-b",
            event_type=EventType.TOOL_CALL,
            source="tool",
            tool_name="transform",
            input_payload={"source": "raw.csv"},
            output_payload={"file": "processed.csv"},
        ),
        _event(
            "evt-b5",
            session_id="session-b",
            event_type=EventType.TOOL_RESULT,
            source="tool",
            tool_name="transform",
            output_payload={"file": "processed.csv"},
        ),
    ]

    candidates = WorkflowExtractor.extract_tool_sequence(workflow_one + workflow_two)

    assert len(candidates) == 1
    assert candidates[0].metadata["workflow_signature"] == "download__transform"
    assert candidates[0].metadata["occurrence_count"] == 2
    assert set(candidates[0].source_events) == {event.event_id for event in workflow_one + workflow_two}


def test_workflow_extraction_orders_events_and_isolates_concurrent_tasks() -> None:
    base = datetime(2026, 6, 23, tzinfo=timezone.utc)
    task_one = [
        _event(
            "one-download-call",
            task_id="task-one",
            event_type=EventType.TOOL_CALL,
            source="tool",
            tool_name="download",
            output_payload={"file": "one.csv"},
            timestamp=base.replace(second=1),
        ),
        _event(
            "one-download-result",
            task_id="task-one",
            event_type=EventType.TOOL_RESULT,
            source="tool",
            tool_name="download",
            output_payload={"file": "one.csv"},
            timestamp=base.replace(second=2),
        ),
        _event(
            "one-export-call",
            task_id="task-one",
            event_type=EventType.TOOL_CALL,
            source="tool",
            tool_name="export",
            input_payload={"source": "one.csv"},
            output_payload={"file": "one.xlsx"},
            timestamp=base.replace(second=3),
        ),
        _event(
            "one-export-result",
            task_id="task-one",
            event_type=EventType.TOOL_RESULT,
            source="tool",
            tool_name="export",
            output_payload={"file": "one.xlsx"},
            timestamp=base.replace(second=4),
        ),
    ]
    task_two = [
        _event(
            "two-search-call",
            task_id="task-two",
            event_type=EventType.TOOL_CALL,
            source="tool",
            tool_name="search",
            output_payload={"file": "two.csv"},
            timestamp=base.replace(second=1),
        ),
        _event(
            "two-search-result",
            task_id="task-two",
            event_type=EventType.TOOL_RESULT,
            source="tool",
            tool_name="search",
            output_payload={"file": "two.csv"},
            timestamp=base.replace(second=2),
        ),
        _event(
            "two-analyse-call",
            task_id="task-two",
            event_type=EventType.TOOL_CALL,
            source="tool",
            tool_name="analyse",
            input_payload={"source": "two.csv"},
            output_payload={"file": "two.json"},
            timestamp=base.replace(second=3),
        ),
        _event(
            "two-analyse-result",
            task_id="task-two",
            event_type=EventType.TOOL_RESULT,
            source="tool",
            tool_name="analyse",
            output_payload={"file": "two.json"},
            timestamp=base.replace(second=4),
        ),
    ]

    candidates = WorkflowExtractor.extract_tool_sequence(list(reversed(task_one + task_two)))

    assert len(candidates) == 2
    by_steps = {tuple(candidate.metadata["tool_names"]): candidate for candidate in candidates}
    assert set(by_steps) == {("download", "export"), ("search", "analyse")}
    assert set(by_steps[("download", "export")].source_events) == {event.event_id for event in task_one}
    assert set(by_steps[("search", "analyse")].source_events) == {event.event_id for event in task_two}
    assert all(candidate.metadata["reproduction_rate"] >= 0.8 for candidate in candidates)


def test_workflow_requires_real_reconstruction_evidence() -> None:
    events = [
        _event("call-1", event_type=EventType.TOOL_CALL, source="tool", tool_name="first"),
        _event("result-1", event_type=EventType.TOOL_RESULT, source="tool", tool_name="first"),
        _event("call-2", event_type=EventType.TOOL_CALL, source="tool", tool_name="second"),
        _event("result-2", event_type=EventType.TOOL_RESULT, source="tool", tool_name="second"),
    ]

    assert WorkflowExtractor.extract_tool_sequence(events) == []


def test_workflow_internal_helper_branches_and_empty_paths() -> None:
    assert workflow_mod._iter_text_fragments(None) == []
    assert workflow_mod._iter_text_fragments("  abc  ") == ["abc"]
    assert workflow_mod._iter_text_fragments(5) == ["5"]

    fragments = workflow_mod._iter_text_fragments(
        {
            "skip": "value",
            "nested": {"x": "y"},
            "items": [1, 2],
            "flags": {True, False},
        }
    )
    assert "y" in fragments
    assert "1" in fragments and "2" in fragments
    assert "True" in fragments and "False" in fragments

    relevant = _event("evt-relevant", content="step 1 then next")
    irrelevant = _event("evt-irrelevant", content="plain chat")
    assert workflow_mod._is_workflow_relevant(relevant)
    assert not workflow_mod._is_workflow_relevant(irrelevant)

    tool_call = _event("evt-tool-call", event_type=EventType.TOOL_CALL, source="tool", tool_name="Bash")
    tool_result = _event("evt-tool-result", event_type=EventType.TOOL_RESULT, source="tool", tool_name="Bash")
    tool_other = _event("evt-tool-other", event_type=EventType.TOOL_CALL, source="tool", tool_name="Cat")
    assert workflow_mod._same_tool_group(tool_call, tool_result)
    assert not workflow_mod._same_tool_group(tool_call, tool_other)

    assert workflow_mod._step_label([tool_call]) == "bash"
    assert workflow_mod._step_label([_event("evt-label", content="  Use the output  ")]) == "use the output"
    assert workflow_mod._step_label([_event("evt-fallback", content=None, tool_name=None)]).startswith("conversation")

    assert workflow_mod._group_transition_markers([_event("evt-trans", content="then use it")])
    assert not workflow_mod._group_transition_markers([_event("evt-notrans", content="just text")])

    simple_events = [
        _event("evt-b0", event_type=EventType.TOOL_CALL, source="tool", tool_name="download", output_payload={"file": "a.csv"}),
        _event("evt-b1", event_type=EventType.TOOL_RESULT, source="tool", tool_name="download", output_payload={"file": "a.csv"}),
        _event("evt-b2", content="after that"),
        _event("evt-b3", event_type=EventType.TOOL_CALL, source="tool", tool_name="export", input_payload={"source": "a.csv"}),
    ]
    assert workflow_mod._boundary_signature(simple_events, [(0, 1), (3, 3)]).startswith("download")
    assert workflow_mod._workflow_content("Prefix", [[tool_call, tool_result], [tool_other]], []) == "Prefix: 1. bash | 2. cat"
    assert "deps:" in workflow_mod._workflow_content(
        "Prefix",
        [[tool_call], [tool_other]],
        [{"from_tool": "bash", "to_tool": "cat"}],
    )
    assert workflow_mod._workflow_confidence(2, 0, False) < workflow_mod._workflow_confidence(4, 2, True)

    dependency_left = _event(
        "evt-left",
        event_type=EventType.TOOL_CALL,
        source="tool",
        tool_name="download",
        output_payload={"file": "a.csv"},
    )
    dependency_right = _event(
        "evt-right",
        event_type=EventType.TOOL_CALL,
        source="tool",
        tool_name="transform",
        input_payload={"source": "a.csv"},
    )
    edges = workflow_mod._dependency_edges([[dependency_left], [dependency_right]])
    assert edges and edges[0]["from_tool"] == "download" and edges[0]["to_tool"] == "transform"

    assert WorkflowExtractor.detect_workflow_boundary([]) == []
    assert WorkflowExtractor.extract_tool_sequence([tool_call, tool_result]) == []
    assert WorkflowExtractor.extract_multi_step_workflow([tool_call, tool_result]) == []
