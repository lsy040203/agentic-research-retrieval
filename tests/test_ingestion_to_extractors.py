from __future__ import annotations

from ingestion.adapter import raw_event_to_memory_event
from ingestion.collector import create_raw_event
from extractors.environment_extractor import EnvironmentExtractor
from extractors.knowledge_extractor import KnowledgeExtractor
from extractors.preference_extractor import PreferenceExtractor
from extractors.tool_extractor import ToolExtractor


def _memory_event(payload: dict):
    return raw_event_to_memory_event(create_raw_event(payload))


def test_ingestion_conversation_event_feeds_preference_extractor() -> None:
    event = _memory_event(
        {
            "event_id": "raw-pref-1",
            "user_id": "user-a",
            "session_id": "session-a",
            "task_id": "task-a",
            "event_type": "conversation",
            "scenario": "office",
            "timestamp": "2026-06-27T10:00:00+08:00",
            "content": "以后都用 Markdown 输出，回答尽量简洁。",
        }
    )

    candidates = PreferenceExtractor.extract_from_conversation(event)
    keys = {candidate.key for candidate in candidates}

    assert "preference.output_format.markdown" in keys
    assert all(candidate.user_id == "user-a" for candidate in candidates)
    assert all("raw-pref-1" in candidate.source_events for candidate in candidates)


def test_ingestion_tool_events_feed_knowledge_and_tool_extractors() -> None:
    call_event = _memory_event(
        {
            "event_id": "raw-tool-call-1",
            "user_id": "user-a",
            "session_id": "session-a",
            "task_id": "task-a",
            "event_type": "tool_call",
            "scenario": "office",
            "timestamp": "2026-06-27T10:01:00+08:00",
            "tool_name": "wps_export",
            "input": {"file": "report.docx", "format": "pdf"},
            "call_id": "call-1",
        }
    )
    result_event = _memory_event(
        {
            "event_id": "raw-tool-result-1",
            "user_id": "user-a",
            "session_id": "session-a",
            "task_id": "task-a",
            "event_type": "tool_result",
            "scenario": "office",
            "timestamp": "2026-06-27T10:01:03+08:00",
            "tool_name": "wps_export",
            "output": {"file": "report.pdf", "status": "success"},
            "success": True,
            "duration_ms": 3000,
            "call_id": "call-1",
        }
    )

    knowledge = KnowledgeExtractor.extract_from_tool_result(result_event)
    tool_patterns = ToolExtractor.extract_tool_pattern([call_event, result_event])

    assert knowledge
    assert any(candidate.key == "knowledge.tool_case.batch_export.wps_export" for candidate in knowledge)
    assert len(tool_patterns) == 1
    assert tool_patterns[0].metadata["success_rate"] == 1.0
    assert tool_patterns[0].metadata["success_count"] == 1


def test_ingestion_system_context_event_feeds_environment_extractor() -> None:
    event = _memory_event(
        {
            "event_id": "raw-env-1",
            "user_id": "user-a",
            "session_id": "session-a",
            "task_id": "task-a",
            "event_type": "system_context",
            "scenario": "system",
            "timestamp": "2026-06-27T10:02:00+08:00",
            "downloads": "C:\\Users\\alice\\Downloads",
            "documents": "C:\\Users\\alice\\Documents",
            "locale": "zh_CN",
            "installed_software": ["WPS Office", "Python"],
            "os_version": "Kylin Desktop V11",
        }
    )

    candidates = EnvironmentExtractor.extract_from_tool_output(event.metadata)
    keys = {candidate.key for candidate in candidates}

    assert "environment.path.downloads" in keys
    assert "environment.path.documents" in keys
    assert "environment.locale.language" in keys
    assert "environment.locale.region" in keys
    assert any(candidate.key.startswith("environment.software.") for candidate in candidates)
    assert any(candidate.metadata.get("path") == "~\\Downloads" for candidate in candidates)
