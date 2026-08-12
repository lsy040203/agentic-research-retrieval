from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from core.constants import EventType, MemoryType, Scene
from core.models import MemoryCandidate, MemoryEvent, MemoryRecord
from extractors import knowledge_extractor as knowledge_mod
from extractors import preference_extractor as preference_mod
from extractors.knowledge_extractor import KnowledgeExtractor
from extractors.preference_extractor import PreferenceExtractor
from memory.store import init_db, save_memory


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


def test_preference_explicit_from_conversation() -> None:
    content = "以后都用 Markdown 输出，回答尽量简洁，先给结论后分析。"

    raw_candidates = PreferenceExtractor.extract_explicit_preference(content)
    assert raw_candidates
    assert any(candidate.key == "preference.output_format.markdown" for candidate in raw_candidates)
    assert all(candidate.user_id == "" for candidate in raw_candidates)

    event = _event("evt-1", content=content)
    candidates = PreferenceExtractor.extract_from_conversation(event)

    keys = {candidate.key for candidate in candidates}
    assert "preference.output_format.markdown" in keys
    assert "preference.response_length.简洁" in keys
    assert all(candidate.user_id == event.user_id for candidate in candidates)
    assert all(event.event_id in candidate.source_events for candidate in candidates)
    assert all(0.5 <= candidate.confidence <= 1.0 for candidate in candidates)


def test_preference_implicit_from_frequency() -> None:
    events = [
        _event(
            "evt-2",
            event_type=EventType.TOOL_CALL,
            source="workflow",
            tool_name="bash",
            input_payload={"format": "markdown"},
        ),
        _event(
            "evt-3",
            event_type=EventType.TOOL_CALL,
            source="workflow",
            tool_name="bash",
            input_payload={"format": "markdown"},
        ),
        _event(
            "evt-4",
            event_type=EventType.TOOL_CALL,
            source="workflow",
            tool_name="bash",
            input_payload={"format": "markdown"},
        ),
    ]

    candidates = PreferenceExtractor.extract_implicit_preference(events)

    assert any(candidate.key.startswith("preference.tool.bash") for candidate in candidates)
    assert any(candidate.key.startswith("preference.parameter.format") for candidate in candidates)
    assert all(candidate.memory_type is MemoryType.PREFERENCE for candidate in candidates)
    assert all(candidate.user_id == "user-1" for candidate in candidates)
    assert all(len(candidate.source_events) >= 3 for candidate in candidates)
    assert all(0.5 <= candidate.confidence <= 1.0 for candidate in candidates)


def test_preference_confidence_score() -> None:
    explicit_event = _event(
        "evt-5",
        content="以后都用 PDF 输出，回答尽量简洁。",
    )
    implicit_events = [
        _event("evt-6", event_type=EventType.TOOL_CALL, source="workflow", tool_name="bash", input_payload={"format": "pdf"}),
        _event("evt-7", event_type=EventType.TOOL_CALL, source="workflow", tool_name="bash", input_payload={"format": "pdf"}),
        _event("evt-8", event_type=EventType.TOOL_CALL, source="workflow", tool_name="bash", input_payload={"format": "pdf"}),
    ]

    explicit_candidate = PreferenceExtractor.extract_from_conversation(explicit_event)[0]
    implicit_candidate = PreferenceExtractor.extract_implicit_preference(implicit_events)[0]

    assert explicit_candidate.confidence > implicit_candidate.confidence
    assert 0.9 <= explicit_candidate.confidence <= 1.0
    assert 0.5 <= implicit_candidate.confidence <= 1.0


def test_knowledge_from_tool_result() -> None:
    complete_event = _event(
        "evt-9",
        event_type=EventType.TOOL_RESULT,
        source="tool",
        tool_name="etl",
        content="批量导出完成。",
        input_payload={"operation": "batch export", "source": "orders.csv", "format": "xlsx"},
        output_payload={"status": "ok", "rows": 1200, "file": "orders_20260623.xlsx"},
        metadata={"mode": "batch"},
    )
    partial_event = _event(
        "evt-10",
        event_type=EventType.TOOL_RESULT,
        source="tool",
        tool_name="etl",
        content="批量导出处理中。",
        input_payload={"operation": "batch export"},
        output_payload={},
        metadata={},
    )

    complete_candidates = KnowledgeExtractor.extract_from_tool_result(complete_event)
    partial_candidates = KnowledgeExtractor.extract_from_tool_result(partial_event)

    assert len(complete_candidates) >= 1
    candidate = complete_candidates[0]
    assert candidate.memory_type is MemoryType.KNOWLEDGE
    assert candidate.user_id == complete_event.user_id
    assert candidate.key.startswith("knowledge.tool_case.batch_export.")
    assert "输入" in candidate.content
    assert "输出" in candidate.content
    assert complete_event.event_id in candidate.source_events
    assert candidate.confidence >= 0.8
    assert candidate.confidence > partial_candidates[0].confidence

    faq_event = _event(
        "evt-11",
        content="问题：导出失败怎么办？\n解决方案：先检查文件是否被占用，再重试。\n桌面配置：1. 打开 settings；2. 关闭自动更新；3. 重启。",
    )
    faq_candidates = KnowledgeExtractor.extract_from_conversation(faq_event)
    faq_keys = {item.key for item in faq_candidates}
    assert any(key.startswith("knowledge.faq.") for key in faq_keys)
    assert any(key.startswith("knowledge.system.desktop_config") or key.startswith("knowledge.guide.") for key in faq_keys)


def test_knowledge_template_extraction() -> None:
    events = [
        _event(
            "evt-12",
            event_type=EventType.TOOL_CALL,
            source="workflow",
            tool_name="exporter",
            content="批量导出订单到 Excel",
            input_payload={"operation": "batch export", "source": "orders_001.csv", "format": "xlsx"},
            output_payload={"file": "orders_001.xlsx", "status": "ok"},
        ),
        _event(
            "evt-13",
            event_type=EventType.TOOL_CALL,
            source="workflow",
            tool_name="exporter",
            content="批量导出订单到 Excel",
            input_payload={"operation": "batch export", "source": "orders_002.csv", "format": "xlsx"},
            output_payload={"file": "orders_002.xlsx", "status": "ok"},
        ),
    ]

    candidates = KnowledgeExtractor.extract_templates(events)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.memory_type is MemoryType.TEMPLATE
    assert candidate.key.startswith("template.batch_export.")
    assert set(candidate.source_events) == {"evt-12", "evt-13"}
    assert candidate.confidence >= 0.6


def test_knowledge_deduplication() -> None:
    events = [
        _event(
            "evt-14",
            event_type=EventType.TOOL_CALL,
            source="workflow",
            tool_name="exporter",
            content="批量导出订单到 Excel",
            input_payload={"operation": "batch export", "source": "orders_001.csv", "format": "xlsx"},
            output_payload={"file": "orders_001.xlsx", "status": "ok"},
        ),
        _event(
            "evt-15",
            event_type=EventType.TOOL_CALL,
            source="workflow",
            tool_name="exporter",
            content="批量导出订单到 Excel",
            input_payload={"operation": "batch export", "source": "orders_002.csv", "format": "xlsx"},
            output_payload={"file": "orders_002.xlsx", "status": "ok"},
        ),
        _event(
            "evt-16",
            event_type=EventType.TOOL_CALL,
            source="workflow",
            tool_name="exporter",
            content="批量导出订单到 Excel",
            input_payload={"operation": "batch export", "source": "orders_003.csv", "format": "xlsx"},
            output_payload={"file": "orders_003.xlsx", "status": "ok"},
        ),
    ]

    candidates = KnowledgeExtractor.extract_templates(events)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.key.startswith("template.batch_export.")
    assert set(candidate.source_events) == {"evt-14", "evt-15", "evt-16"}
    assert candidate.confidence >= 0.65


def test_core_models_and_storage_round_trip(tmp_path: Path) -> None:
    event = MemoryEvent(
        event_id="evt-model-1",
        raw_event_id="raw-model-1",
        user_id="user-model",
        session_id="session-model",
        task_id="task-model",
        event_type=EventType.CONVERSATION,
        scenario=Scene.GLOBAL,
        source="conversation",
        content="模型测试",
        metadata={"tuple": (1, 2)},
        timestamp=datetime(2026, 6, 23, tzinfo=timezone.utc),
    )

    event_dict = event.to_dict()
    assert event_dict["event_type"] == "conversation"
    assert event_dict["scenario"] == "global"
    assert event_dict["timestamp"].startswith("2026-06-23T00:00:00")

    candidate = MemoryCandidate(
        candidate_id="cand-model-1",
        user_id="user-candidate",
        memory_type=MemoryType.KNOWLEDGE,
        key="knowledge.example",
        content="候选内容",
        scenario=Scene.GLOBAL,
        source_events=["evt-a", "evt-b"],
        source_summaries=["summary-1"],
        tags=["tag-a", "tag-b"],
        metadata={"tuple": (1, 2), "flags": [True, False]},
        created_at=datetime(2026, 6, 23, 1, tzinfo=timezone.utc),
    )

    candidate_dict = candidate.to_dict()
    assert candidate_dict["memory_type"] == "knowledge"
    assert candidate_dict["user_id"] == "user-candidate"
    assert "evt-a" in candidate_dict["source_events"]
    assert candidate_dict["metadata"]["tuple"] == (1, 2)

    db_path = tmp_path / "memories.sqlite3"
    init_db(str(db_path))
    persisted = save_memory(str(db_path), candidate)
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT user_id, memory_type, key, content FROM memories"
        ).fetchone()
    assert row == ("user-candidate", "knowledge", "knowledge.example", "候选内容")
    assert persisted.memory_id


def test_preference_internal_helper_branches() -> None:
    assert preference_mod._normalize_value("response_style", "简洁") == ("concise", "简洁")
    assert preference_mod._normalize_value("language", "English") == ("en", "English")
    assert preference_mod._normalize_value("tool", "ripgrep") == ("rg", "ripgrep")
    assert preference_mod._normalize_value("workflow", "先给方案")[1] == "先给方案"
    assert preference_mod._normalize_value("unknown", "自定义值")[0] == "自定义值"

    assert preference_mod._canonical_structured_key("preferred_format") == "output_format"
    assert preference_mod._canonical_structured_key("default_language") == "language"
    assert preference_mod._canonical_structured_key("response_tone") == "response_style"
    assert preference_mod._canonical_structured_key("preferred_tool") == "tool"
    assert preference_mod._canonical_structured_key("response_length") == "response_length"
    assert preference_mod._canonical_structured_key("workflow_mode") == "workflow"
    assert preference_mod._canonical_structured_key("parameter_option") == "parameter"
    assert preference_mod._canonical_structured_key("custom") == "custom"

    assert preference_mod._iter_text_fragments(None) == []
    assert preference_mod._iter_text_fragments("  abc  ") == ["abc"]
    assert preference_mod._iter_text_fragments(3) == ["3"]
    fragments = preference_mod._iter_text_fragments(
        {
            "event_id": "skip",
            "nested": {"x": "y"},
            "items": [1, 2],
            "flags": (True, False),
        }
    )
    assert "y" in fragments
    assert "1" in fragments and "2" in fragments
    assert "True" in fragments and "False" in fragments
    assert {"alpha", "beta"} == set(preference_mod._iter_text_fragments({"alpha", "beta"}))

    explicit = preference_mod._extract_from_text("")
    assert explicit == []
    explicit = preference_mod._extract_from_text(
        "以后都用 Markdown 输出，之后也用 Markdown 输出，偏好中文，优先使用 git，不喜欢冗长，今后先列清单，回答详细，始终分点。"
    )
    assert len(explicit) >= 6
    assert any(item.key.startswith("preference.output_format") for item in explicit)
    assert any(item.key.startswith("preference.language") for item in explicit)
    assert any(item.key.startswith("preference.tool") for item in explicit)
    assert any(item.key.startswith("preference.workflow") for item in explicit)

    event = _event(
        "evt-pref-internal",
        content="偏好测试",
        input_payload={
            "preferred_format": "PDF",
            "default_language": "中文",
            "response_tone": "正式",
            "preferred_tool": "git",
            "response_length": "long",
            "workflow_mode": "setup",
            "parameter_option": "fast",
        },
        output_payload={"preferred_format": "PDF", "default_language": "中文"},
        metadata={"preferred_format": "PDF", "response_style": "简洁"},
    )
    structured = preference_mod._structured_pref_candidates(event)
    structured_keys = {item.key for item in structured}
    assert "preference.output_format.pdf" in structured_keys
    assert "preference.language.zh" in structured_keys
    assert "preference.response_style.formal" in structured_keys
    assert "preference.tool.git" in structured_keys
    assert "preference.response_length.long" in structured_keys
    assert "preference.workflow.setup" in structured_keys
    assert "preference.parameter.fast" in structured_keys

    rebound = preference_mod._rebind_candidates(explicit[:1], event=event, source="conversation")
    assert rebound[0].user_id == event.user_id
    assert rebound[0].source == "conversation"
    assert event.event_id in rebound[0].source_events

    deduped = preference_mod._dedupe_candidates(
        [
            MemoryCandidate(candidate_id="cand-pref-a", user_id="u", memory_type=MemoryType.PREFERENCE, key="preference.test.x", content="a"),
            MemoryCandidate(candidate_id="cand-pref-b", user_id="u", memory_type=MemoryType.PREFERENCE, key="preference.test.x", content="b"),
        ]
    )
    assert len(deduped) == 1


def test_preference_implicit_internal_branches() -> None:
    assert preference_mod._normalize_scalar(True) == "true"
    assert preference_mod._normalize_scalar(3) == "3"
    assert preference_mod._normalize_scalar(3.14) == "3.14"
    assert preference_mod._normalize_scalar("  hi  ") == "hi"

    params = preference_mod._collect_scalar_params(
        {
            "skip": None,
            "nested": {
                "temperature": 0.2,
                "top_p": 0.9,
                "flags": [True, False],
                "model": "gpt-4",
            },
            "tags": ("alpha", "beta"),
        }
    )
    params_map = dict(params)
    assert params_map["nested.temperature"] == "0.2"
    assert params_map["nested.top_p"] == "0.9"
    assert params_map["nested.flags"] == "true, false"
    assert params_map["nested.model"] == "gpt-4"
    assert params_map["tags"] == "alpha, beta"

    content_event = _event("evt-sig-1", content="  重复 操作  ")
    tool_event = _event("evt-sig-2", content="", tool_name="Bash")
    meta_event = _event("evt-sig-3", content="", tool_name=None, metadata={"action": "Open File"})
    fallback_event = _event("evt-sig-4", content="", tool_name=None, metadata={})
    assert preference_mod._action_signature(content_event) == "重复 操作"
    assert preference_mod._action_signature(tool_event) == "tool:bash"
    assert preference_mod._action_signature(meta_event).startswith("action:open file")
    assert preference_mod._action_signature(fallback_event) == "conversation:conversation"

    mixed_scenario_events = [
        _event(
            "evt-imp-1",
            event_type=EventType.TOOL_CALL,
            scenario=Scene.CODING,
            source="workflow",
            tool_name="bash",
            input_payload={"format": "markdown", "nested": {"temperature": 0.2}},
        ),
        _event(
            "evt-imp-2",
            event_type=EventType.TOOL_CALL,
            scenario=Scene.CODING,
            source="workflow",
            tool_name="bash",
            input_payload={"format": "markdown", "nested": {"temperature": 0.2}},
        ),
        _event(
            "evt-imp-3",
            event_type=EventType.TOOL_CALL,
            scenario=Scene.CODING,
            source="workflow",
            tool_name="bash",
            input_payload={"format": "markdown", "nested": {"temperature": 0.2}},
        ),
        _event(
            "evt-imp-4",
            event_type=EventType.TOOL_CALL,
            scenario=Scene.GLOBAL,
            source="workflow",
            tool_name="git",
            input_payload={"format": "markdown"},
        ),
        _event(
            "evt-imp-5",
            event_type=EventType.TOOL_CALL,
            scenario=Scene.GLOBAL,
            source="workflow",
            tool_name="git",
            input_payload={"format": "markdown"},
        ),
        _event(
            "evt-imp-6",
            event_type=EventType.TOOL_CALL,
            scenario=Scene.GLOBAL,
            source="workflow",
            tool_name="git",
            input_payload={"format": "markdown"},
        ),
    ]

    implicit = preference_mod.PreferenceExtractor.extract_implicit_preference(mixed_scenario_events)
    keys = {candidate.key for candidate in implicit}
    assert any(key.startswith("preference.tool.bash") for key in keys)
    assert any(key.startswith("preference.tool.git") for key in keys)
    assert any(key.startswith("preference.parameter.format") for key in keys)
    assert any(candidate.scenario is Scene.GLOBAL for candidate in implicit)
    assert any(candidate.scenario is Scene.CODING for candidate in implicit)
    assert all(candidate.user_id == "user-1" for candidate in implicit)

    tool_group = [event for event in mixed_scenario_events if event.tool_name == "bash"]
    results = preference_mod._extract_implicit_for_user("user-x", tool_group)
    assert results


def test_knowledge_internal_helper_branches() -> None:
    assert knowledge_mod._text_fragments(None) == []
    assert knowledge_mod._text_fragments("  abc  ") == ["abc"]
    assert knowledge_mod._text_fragments(5) == ["5"]
    fragments = knowledge_mod._text_fragments(
        {
            "event_id": "skip",
            "nested": {"x": "y"},
            "items": [1, 2],
            "set_items": {"alpha", "beta"},
        }
    )
    assert "y" in fragments
    assert "1" in fragments and "2" in fragments
    assert {"alpha", "beta"}.issubset(set(fragments))

    normalized = knowledge_mod._normalize_for_template(
        r"Visit https://example.com orders_001.xlsx C:\tmp\app_2.log /var/log/app.txt 42 deadbeef 'quoted text'"
    )
    assert "<url>" in normalized
    assert "<file>" in normalized
    assert "<path>" in normalized
    assert "<id>" in normalized
    assert "<text>" in normalized

    summary = knowledge_mod._summarize_mapping(
        {"a": 1, "b": [2, 3], "c": None, "d": {"skip": 1}, "e": "z"},
        max_items=3,
    )
    assert summary == "a=1; b=2, 3; e=z"

    batch_event = _event(
        "evt-know-intent-1",
        event_type=EventType.TOOL_RESULT,
        source="tool",
        tool_name="etl",
        content="batch export",
        input_payload={"operation": "batch export"},
    )
    merge_event = _event(
        "evt-know-intent-2",
        event_type=EventType.TOOL_RESULT,
        source="tool",
        tool_name="merge",
        content="merge files",
        input_payload={"operation": "merge files"},
    )
    desktop_event = _event(
        "evt-know-intent-3",
        event_type=EventType.TOOL_RESULT,
        source="tool",
        tool_name="desktop",
        content="desktop config",
    )
    software_event = _event(
        "evt-know-intent-4",
        event_type=EventType.TOOL_RESULT,
        source="tool",
        tool_name="installer",
        content="software setup",
    )
    generic_event = _event("evt-know-intent-5", event_type=EventType.TOOL_RESULT, source="tool", tool_name="other", content="misc")
    assert knowledge_mod._extract_tool_intent(batch_event) == "batch_export"
    assert knowledge_mod._extract_tool_intent(merge_event) == "merge_files"
    assert knowledge_mod._extract_tool_intent(desktop_event) == "desktop_config"
    assert knowledge_mod._extract_tool_intent(software_event) == "software_setup"
    assert knowledge_mod._extract_tool_intent(generic_event) == "tool_case"

    assert knowledge_mod._completeness_score(
        has_input=True, has_output=True, has_content=True, has_metadata=True
    ) > knowledge_mod._completeness_score(
        has_input=True, has_output=False, has_content=False, has_metadata=False
    )

    faq_candidates = knowledge_mod._extract_faq_candidates(
        _event("evt-faq-1", content="Q: How to export?\nA: Use batch export.")
    , "Q: How to export?\nA: Use batch export.")
    assert len(faq_candidates) == 1

    guide_candidates = knowledge_mod._extract_guide_candidates(
        _event("evt-guide-1", content="步骤1: 打开软件；然后配置；最后运行。"),
        "步骤1: 打开软件；然后配置；最后运行。",
    )
    assert guide_candidates

    system_candidates = knowledge_mod._extract_system_guide(
        _event("evt-system-1", content="系统配置说明")
    )
    assert system_candidates
    assert system_candidates[0].key.startswith("knowledge.system.system_setup")


def test_knowledge_template_variants_and_dedup() -> None:
    events = [
        _event(
            "evt-temp-1",
            event_type=EventType.TOOL_CALL,
            scenario=Scene.GLOBAL,
            source="workflow",
            tool_name="exporter",
            content="批量导出订单到 Excel",
            input_payload={"operation": "batch export", "source": "orders_001.csv", "format": "xlsx"},
            output_payload={"file": "orders_001.xlsx", "status": "ok"},
        ),
        _event(
            "evt-temp-1b",
            event_type=EventType.TOOL_CALL,
            scenario=Scene.GLOBAL,
            source="workflow",
            tool_name="exporter",
            content="批量导出订单到 Excel",
            input_payload={"operation": "batch export", "source": "orders_002.csv", "format": "xlsx"},
            output_payload={"file": "orders_002.xlsx", "status": "ok"},
        ),
        _event(
            "evt-temp-2",
            event_type=EventType.TOOL_CALL,
            scenario=Scene.GLOBAL,
            source="workflow",
            tool_name="merger",
            content="合并文件",
            input_payload={"operation": "merge files", "source": "left_001.csv", "target": "right_001.csv"},
            output_payload={"file": "merged_001.csv"},
        ),
        _event(
            "evt-temp-2b",
            event_type=EventType.TOOL_CALL,
            scenario=Scene.CODING,
            source="workflow",
            tool_name="merger",
            content="合并文件",
            input_payload={"operation": "merge files", "source": "left_002.csv", "target": "right_002.csv"},
            output_payload={"file": "merged_002.csv"},
        ),
        _event(
            "evt-temp-3",
            event_type=EventType.TOOL_CALL,
            scenario=Scene.SYSTEM,
            source="workflow",
            tool_name="desktop",
            content="桌面配置",
            input_payload={"mode": "desktop config", "setting": "dark"},
            output_payload={"status": "ok"},
        ),
        _event(
            "evt-temp-3b",
            event_type=EventType.TOOL_CALL,
            scenario=Scene.SYSTEM,
            source="workflow",
            tool_name="desktop",
            content="桌面配置",
            input_payload={"mode": "desktop config", "setting": "dark"},
            output_payload={"status": "ok"},
        ),
        _event(
            "evt-temp-4",
            event_type=EventType.TOOL_CALL,
            scenario=Scene.CODING,
            source="workflow",
            tool_name="installer",
            content="软件安装",
            input_payload={"mode": "software setup", "package": "app.exe"},
            output_payload={"status": "ok"},
        ),
        _event(
            "evt-temp-4b",
            event_type=EventType.TOOL_CALL,
            scenario=Scene.CODING,
            source="workflow",
            tool_name="installer",
            content="软件安装",
            input_payload={"mode": "software setup", "package": "app.exe"},
            output_payload={"status": "ok"},
        ),
        _event(
            "evt-temp-5",
            event_type=EventType.TOOL_CALL,
            scenario=Scene.GLOBAL,
            source="workflow",
            tool_name="misc",
            content="通用流程",
            input_payload={"step": "1"},
            output_payload={"result": "done"},
        ),
        _event(
            "evt-temp-5b",
            event_type=EventType.TOOL_CALL,
            scenario=Scene.GLOBAL,
            source="workflow",
            tool_name="misc",
            content="通用流程",
            input_payload={"step": "1"},
            output_payload={"result": "done"},
        ),
    ]

    candidates = knowledge_mod.KnowledgeExtractor.extract_templates(events)
    keys = {candidate.key for candidate in candidates}
    assert any(key.startswith("template.batch_export.") for key in keys)
    assert any(key.startswith("template.merge_files.") for key in keys)
    assert any(key.startswith("template.desktop_config.") for key in keys)
    assert any(key.startswith("template.software_setup.") for key in keys)
    assert any(key.startswith("template.generic.") for key in keys)
    assert any(candidate.scenario is Scene.GLOBAL for candidate in candidates)
    assert any(candidate.scenario is Scene.SYSTEM for candidate in candidates)
    assert any(candidate.scenario is Scene.CODING for candidate in candidates)

    deduped = knowledge_mod._dedupe_candidates(
        [
            MemoryCandidate(candidate_id="cand-template-a", user_id="u", memory_type=MemoryType.TEMPLATE, key="template.generic.x", content="a"),
            MemoryCandidate(candidate_id="cand-template-b", user_id="u", memory_type=MemoryType.TEMPLATE, key="template.generic.x", content="b"),
        ]
    )
    assert len(deduped) == 1


def test_knowledge_templates_are_isolated_per_user_and_have_stable_keys() -> None:
    events = [
        _event(
            "alice-1",
            user_id="alice",
            event_type=EventType.TOOL_RESULT,
            source="tool",
            tool_name="exporter",
            content="批量导出订单到 Excel",
            input_payload={"operation": "batch export", "source": "orders_001.csv"},
            output_payload={"file": "orders_001.xlsx"},
        ),
        _event(
            "alice-2",
            user_id="alice",
            event_type=EventType.TOOL_RESULT,
            source="tool",
            tool_name="exporter",
            content="批量导出订单到 Excel",
            input_payload={"operation": "batch export", "source": "orders_002.csv"},
            output_payload={"file": "orders_002.xlsx"},
        ),
        _event(
            "bob-1",
            user_id="bob",
            event_type=EventType.TOOL_RESULT,
            source="tool",
            tool_name="exporter",
            content="批量导出订单到 Excel",
            input_payload={"operation": "batch export", "source": "orders_003.csv"},
            output_payload={"file": "orders_003.xlsx"},
        ),
        _event(
            "bob-2",
            user_id="bob",
            event_type=EventType.TOOL_RESULT,
            source="tool",
            tool_name="exporter",
            content="批量导出订单到 Excel",
            input_payload={"operation": "batch export", "source": "orders_004.csv"},
            output_payload={"file": "orders_004.xlsx"},
        ),
    ]

    candidates = KnowledgeExtractor.extract_templates(list(reversed(events)))
    assert {candidate.user_id for candidate in candidates} == {"alice", "bob"}
    assert all(all(event_id.startswith(candidate.user_id) for event_id in candidate.source_events) for candidate in candidates)

    repeated = KnowledgeExtractor.extract_templates(events)
    assert {(candidate.user_id, candidate.key, candidate.candidate_id) for candidate in candidates} == {
        (candidate.user_id, candidate.key, candidate.candidate_id) for candidate in repeated
    }


def test_preference_explicit_allows_modifiers_between_cue_and_format() -> None:
    event = _event(
        "evt-pref-flex-1",
        content="以后导出都用 PDF 格式；下次生成报告默认保存成 Markdown。",
    )

    candidates = PreferenceExtractor.extract_from_conversation(event)
    keys = {candidate.key for candidate in candidates}

    assert "preference.output_format.pdf" in keys
    assert "preference.output_format.markdown" in keys


def test_preference_ignores_empty_and_truncates_extreme_noise() -> None:
    assert PreferenceExtractor.extract_explicit_preference("") == []
    noisy = "x" * 15000 + " 以后都用 PDF 输出"
    assert PreferenceExtractor.extract_explicit_preference(noisy) == []


def test_knowledge_tool_result_redacts_sensitive_values() -> None:
    event = _event(
        "evt-secret-1",
        event_type=EventType.TOOL_RESULT,
        source="tool",
        tool_name="deploy",
        output_payload={
            "status": "success",
            "api_key": "sk-live-1234567890abcdef",
            "message": "done with password=super-secret token=abcdef1234567890abcdef123456",
        },
        metadata={"authorization": "Bearer abcdef1234567890abcdef123456"},
    )

    candidates = KnowledgeExtractor.extract_from_tool_result(event)
    rendered = str([candidate.to_dict() for candidate in candidates])

    assert "sk-live" not in rendered
    assert "super-secret" not in rendered
    assert "abcdef1234567890abcdef123456" not in rendered
    assert "<redacted>" in rendered
