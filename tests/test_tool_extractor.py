from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.constants import EventType, MemoryType, Scene
from core.models import MemoryEvent
from extractors.tool_extractor import ToolExtractor


def _event(
    event_id: str,
    *,
    user_id: str = "alice",
    session_id: str = "session-1",
    task_id: str = "task-1",
    event_type: EventType,
    tool_name: str = "backup",
    input_payload: dict | None = None,
    output_payload: dict | None = None,
    metadata: dict | None = None,
    success: bool | None = None,
    offset_seconds: int = 0,
) -> MemoryEvent:
    return MemoryEvent(
        event_id=event_id,
        raw_event_id=f"raw-{event_id}",
        user_id=user_id,
        session_id=session_id,
        task_id=task_id,
        event_type=event_type,
        scenario=Scene.SYSTEM,
        source="tool",
        tool_name=tool_name,
        input=input_payload or {},
        output=output_payload or {},
        metadata=metadata or {},
        success=success,
        timestamp=datetime(2026, 6, 25, tzinfo=timezone.utc) + timedelta(seconds=offset_seconds),
    )


def test_tool_pattern_statistics_are_exact_and_user_isolated() -> None:
    events = [
        _event(
            "alice-call-1",
            event_type=EventType.TOOL_CALL,
            input_payload={"mode": "full", "target": "docs", "api_key": "must-not-leak"},
            metadata={"call_id": "a-1"},
            offset_seconds=1,
        ),
        _event(
            "alice-result-1",
            event_type=EventType.TOOL_RESULT,
            output_payload={"status": "ok"},
            metadata={"call_id": "a-1", "duration_ms": 120},
            success=True,
            offset_seconds=2,
        ),
        _event(
            "alice-call-2",
            event_type=EventType.TOOL_CALL,
            input_payload={"mode": "incremental", "target": "docs"},
            metadata={"call_id": "a-2"},
            offset_seconds=3,
        ),
        _event(
            "alice-result-2",
            event_type=EventType.TOOL_RESULT,
            output_payload={"error_type": "timeout", "message": "remote timeout after 30 seconds"},
            metadata={"call_id": "a-2", "duration_ms": 300},
            success=False,
            offset_seconds=4,
        ),
        _event(
            "alice-call-3",
            event_type=EventType.TOOL_CALL,
            input_payload={"mode": "full", "target": "documents"},
            metadata={"call_id": "a-3"},
            offset_seconds=5,
        ),
        _event(
            "bob-call-1",
            user_id="bob",
            event_type=EventType.TOOL_CALL,
            input_payload={"mode": "full"},
            metadata={"call_id": "b-1"},
            offset_seconds=1,
        ),
        _event(
            "bob-result-1",
            user_id="bob",
            event_type=EventType.TOOL_RESULT,
            output_payload={"status": "ok"},
            metadata={"call_id": "b-1", "duration_ms": 80},
            success=True,
            offset_seconds=2,
        ),
    ]

    candidates = ToolExtractor.extract_tool_pattern(list(reversed(events)))
    assert {candidate.user_id for candidate in candidates} == {"alice", "bob"}

    alice = next(candidate for candidate in candidates if candidate.user_id == "alice")
    assert alice.memory_type is MemoryType.TOOL
    assert alice.metadata["success_count"] == 1
    assert alice.metadata["failure_count"] == 1
    assert alice.metadata["total_count"] == 2
    assert alice.metadata["observed_count"] == 3
    assert alice.metadata["unknown_count"] == 1
    assert alice.metadata["success_rate"] == 0.5
    assert alice.metadata["average_response_ms"] == 210.0
    assert alice.metadata["common_failure_reasons"][0]["reason"].startswith("error_type: timeout")
    assert "api_key" not in str(alice.metadata["common_parameter_combinations"])
    assert all(event_id.startswith("alice") for event_id in alice.source_events)

    assert ToolExtractor.calculate_tool_success_rate("backup", events) == 2 / 3


def test_tool_success_rate_pairs_calls_and_results_by_time_and_correlation() -> None:
    events = [
        _event(
            "result-2",
            event_type=EventType.TOOL_RESULT,
            metadata={"call_id": "two", "duration_ms": 20},
            output_payload={"status": "failed", "error": "network error"},
            offset_seconds=4,
        ),
        _event(
            "call-1",
            event_type=EventType.TOOL_CALL,
            metadata={"call_id": "one"},
            input_payload={"format": "json"},
            offset_seconds=1,
        ),
        _event(
            "result-1",
            event_type=EventType.TOOL_RESULT,
            metadata={"call_id": "one", "duration_ms": 10},
            output_payload={"status": "success"},
            offset_seconds=3,
        ),
        _event(
            "call-2",
            event_type=EventType.TOOL_CALL,
            metadata={"call_id": "two"},
            input_payload={"format": "csv"},
            offset_seconds=2,
        ),
    ]

    assert ToolExtractor.calculate_tool_success_rate("BACKUP", events) == 0.5
    candidate = ToolExtractor.extract_tool_pattern(events)[0]
    assert candidate.metadata["total_count"] == 2
    assert candidate.metadata["unknown_count"] == 0
    assert candidate.metadata["duration_sample_count"] == 2


def test_tool_success_rate_preserves_unknown_outcomes() -> None:
    events = [
        _event("call-only", event_type=EventType.TOOL_CALL, input_payload={"mode": "dry-run"}),
    ]

    candidate = ToolExtractor.extract_tool_pattern(events)[0]
    assert ToolExtractor.calculate_tool_success_rate("backup", events) == 0.0
    assert candidate.metadata["total_count"] == 0
    assert candidate.metadata["unknown_count"] == 1
    assert candidate.metadata["data_completeness"] == 0.0


def test_tool_pattern_infers_terminal_status_and_elapsed_duration() -> None:
    events = [
        _event(
            "sync-call",
            event_type=EventType.TOOL_CALL,
            tool_name="sync",
            input_payload={"options": {"retry": True}, "secret": "must-not-leak"},
            offset_seconds=10,
        ),
        _event(
            "sync-result",
            event_type=EventType.TOOL_RESULT,
            tool_name="sync",
            output_payload={"status": "completed"},
            offset_seconds=12,
        ),
        _event(
            "sync-orphan-failure",
            event_type=EventType.TOOL_RESULT,
            tool_name="sync",
            output_payload={"status": "error", "error": "permission denied"},
            offset_seconds=14,
        ),
    ]

    candidate = ToolExtractor.extract_tool_pattern(events)[0]
    assert candidate.metadata["success_count"] == 1
    assert candidate.metadata["failure_count"] == 1
    assert candidate.metadata["success_rate"] == 0.5
    assert candidate.metadata["average_response_ms"] == 2000.0
    assert candidate.metadata["common_failure_reasons"][0]["reason"].startswith("error: permission denied")
    assert candidate.metadata["common_parameter_combinations"][0]["parameters"] == {"options.retry": "True"}
