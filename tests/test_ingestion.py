"""
Ingestion 层单元测试（collector + adapter + validator + cleaner）

测试用例（12+）：
  ✓ test_load_jsonl_normal()
  ✓ test_load_jsonl_empty_file()
  ✓ test_load_jsonl_corrupt_line()
  ✓ test_create_raw_event()
  ✓ test_adapter_conversation()
  ✓ test_adapter_tool_call()
  ✓ test_adapter_tool_result()
  ✓ test_validator_pass()
  ✓ test_validator_fail_missing_user_id()
  ✓ test_validator_fail_invalid_type()
  ✓ test_cleaner_deduplicate()
  ✓ test_cleaner_normalize()
"""

import json
import os
import tempfile
from datetime import datetime, timedelta

import pytest

from core.constants import EventType, Scene
from core.models import MemoryEvent, RawEvent


# ════════════════════════════════════════════════════════════════════
# 帮助函数
# ════════════════════════════════════════════════════════════════════


def make_event(**kw) -> MemoryEvent:
    """快速构建 MemoryEvent，仅填入必要的默认值。"""
    d = dict(
        event_id="e1",
        raw_event_id="e1",
        user_id="u1",
        session_id="s1",
        task_id="t1",
        event_type=EventType.CONVERSATION,
        scenario=Scene.OFFICE,
        source="conversation",
        actor="user",
        content="hello",
        tool_name="",
        input={},
        output={},
        success=None,
        metadata={},
        timestamp=datetime.now(),
        raw_event=None,
    )
    d.update(kw)
    return MemoryEvent(**d)


def make_raw_event(**kw) -> RawEvent:
    """快速构建 RawEvent。"""
    d = dict(
        event_id="e1",
        user_id="u1",
        session_id="s1",
        task_id="t1",
        event_type=EventType.CONVERSATION,
        scenario=Scene.OFFICE,
        timestamp=datetime.now(),
        payload={},
    )
    d.update(kw)
    return RawEvent(**d)


# ════════════════════════════════════════════════════════════════════
# collector — load_jsonl
# ════════════════════════════════════════════════════════════════════


class TestLoadJsonl:
    """load_jsonl() 测试"""

    def test_load_jsonl_normal(self):
        """正常 JSONL 文件加载。"""
        lines = [
            json.dumps({"event_id": "e1", "user_id": "u1", "event_type": "conversation"}),
            json.dumps({"event_id": "e2", "user_id": "u1", "event_type": "tool_call"}),
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            f.writelines(line + "\n" for line in lines)
            path = f.name

        try:
            from ingestion.collector import load_jsonl

            result = load_jsonl(path)
            assert len(result) == 2
            assert result[0]["event_id"] == "e1"
            assert result[1]["event_id"] == "e2"
        finally:
            os.unlink(path)

    def test_load_jsonl_empty_file(self):
        """空文件返回空列表。"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            f.write("")
            path = f.name

        try:
            from ingestion.collector import load_jsonl

            result = load_jsonl(path)
            assert result == []
        finally:
            os.unlink(path)

    def test_load_jsonl_only_blanks(self):
        """仅有空行的文件返回空列表。"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            f.write("\n\n\n")
            path = f.name

        try:
            from ingestion.collector import load_jsonl

            result = load_jsonl(path)
            assert result == []
        finally:
            os.unlink(path)

    def test_load_jsonl_corrupt_line(self):
        """损坏的 JSON 行抛出 ValueError。"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            f.write('{"event_id": "e1"}\n')
            f.write("this is not json\n")
            path = f.name

        try:
            from ingestion.collector import load_jsonl

            with pytest.raises(ValueError, match="Invalid JSON at line 2"):
                load_jsonl(path)
        finally:
            os.unlink(path)

    def test_load_jsonl_not_dict(self):
        """JSON 值不是 dict 类型时抛出 ValueError。"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            f.write('"just a string"\n')
            path = f.name

        try:
            from ingestion.collector import load_jsonl

            with pytest.raises(ValueError, match="not a JSON object"):
                load_jsonl(path)
        finally:
            os.unlink(path)

    def test_load_jsonl_file_not_found(self):
        """不存在的文件抛 FileNotFoundError。"""
        from ingestion.collector import load_jsonl

        with pytest.raises(FileNotFoundError):
            load_jsonl("/nonexistent/path/file.jsonl")


# ════════════════════════════════════════════════════════════════════
# collector — validate_raw_payload
# ════════════════════════════════════════════════════════════════════


class TestValidateRawPayload:
    """validate_raw_payload() 测试"""

    def test_validate_pass(self):
        from ingestion.collector import validate_raw_payload

        assert validate_raw_payload({"event_id": "e1", "user_id": "u1", "event_type": "conversation"}) is True

    def test_validate_not_dict(self):
        from ingestion.collector import validate_raw_payload

        assert validate_raw_payload("not-a-dict") is False
        assert validate_raw_payload(123) is False
        assert validate_raw_payload(None) is False

    def test_validate_missing_field(self):
        from ingestion.collector import validate_raw_payload

        assert validate_raw_payload({"user_id": "u1", "event_type": "conversation"}) is False
        assert validate_raw_payload({"event_id": "e1", "event_type": "conversation"}) is False
        assert validate_raw_payload({"event_id": "e1", "user_id": "u1"}) is False

    def test_validate_empty_field(self):
        from ingestion.collector import validate_raw_payload

        assert validate_raw_payload({"event_id": "", "user_id": "u1", "event_type": "conversation"}) is False

    def test_validate_invalid_event_type(self):
        from ingestion.collector import validate_raw_payload

        assert validate_raw_payload({"event_id": "e1", "user_id": "u1", "event_type": "invalid_type"}) is False


# ════════════════════════════════════════════════════════════════════
# collector — create_raw_event
# ════════════════════════════════════════════════════════════════════


class TestCreateRawEvent:
    """create_raw_event() 测试"""

    def test_create_basic(self):
        from ingestion.collector import create_raw_event

        data = {
            "event_id": "evt_001",
            "user_id": "u001",
            "session_id": "s001",
            "task_id": "t001",
            "event_type": "conversation",
            "scenario": "office",
            "timestamp": "2025-06-01T10:00:00",
            "content": "hello world",
            "actor": "user",
        }
        raw = create_raw_event(data)
        assert raw.event_id == "evt_001"
        assert raw.user_id == "u001"
        assert raw.session_id == "s001"
        assert raw.task_id == "t001"
        assert raw.event_type == EventType.CONVERSATION
        assert raw.scenario == Scene.OFFICE
        assert raw.timestamp == datetime(2025, 6, 1, 10, 0, 0)
        assert raw.payload == {"content": "hello world", "actor": "user"}

    def test_create_raw_event(self):
        """符合排期表命名要求的别名测试。"""
        self.test_create_basic()

    def test_default_scenario_unknown(self):
        from ingestion.collector import create_raw_event

        raw = create_raw_event({"event_id": "e1", "user_id": "u1", "event_type": "conversation"})
        assert raw.scenario == Scene.UNKNOWN

    def test_invalid_scenario_fallback(self):
        from ingestion.collector import create_raw_event

        raw = create_raw_event({"event_id": "e1", "user_id": "u1", "event_type": "conversation", "scenario": "bogus"})
        assert raw.scenario == Scene.UNKNOWN

    def test_missing_timestamp_default(self):
        from ingestion.collector import create_raw_event

        raw = create_raw_event({"event_id": "e1", "user_id": "u1", "event_type": "conversation"})
        assert isinstance(raw.timestamp, datetime)

    def test_invalid_timestamp_raises(self):
        from ingestion.collector import create_raw_event

        with pytest.raises(ValueError, match="Invalid timestamp"):
            create_raw_event({"event_id": "e1", "user_id": "u1", "event_type": "conversation", "timestamp": "not-a-date"})

    def test_invalid_event_type_raises(self):
        from ingestion.collector import create_raw_event

        with pytest.raises(ValueError, match="Invalid or missing event_type"):
            create_raw_event({"event_id": "e1", "user_id": "u1", "event_type": "bad_type"})

    def test_event_id_uniqueness_across_calls(self):
        """不同调用产生不同 event_id 的 RawEvent。"""
        from ingestion.collector import create_raw_event

        e1 = create_raw_event({"event_id": "a", "user_id": "u1", "event_type": "conversation"})
        e2 = create_raw_event({"event_id": "b", "user_id": "u1", "event_type": "conversation"})
        assert e1.event_id != e2.event_id

    def test_payload_separation(self):
        """已知字段从 payload 中分离，其余归入 payload。"""
        from ingestion.collector import create_raw_event

        data = {"event_id": "e1", "user_id": "u1", "event_type": "conversation", "custom_field": "val", "nested": {"k": 1}}
        raw = create_raw_event(data)
        assert "custom_field" in raw.payload
        assert raw.payload["custom_field"] == "val"
        assert raw.payload["nested"] == {"k": 1}
        # 已知字段不在 payload
        assert "event_id" not in raw.payload
        assert "user_id" not in raw.payload


# ════════════════════════════════════════════════════════════════════
# adapter — map_source
# ════════════════════════════════════════════════════════════════════


class TestMapSource:
    """map_source() 测试"""

    def test_source_conversation(self):
        from ingestion.adapter import map_source

        assert map_source(EventType.CONVERSATION) == "conversation"

    def test_source_tool_call(self):
        from ingestion.adapter import map_source

        assert map_source(EventType.TOOL_CALL) == "tool_call"

    def test_source_tool_result(self):
        from ingestion.adapter import map_source

        assert map_source(EventType.TOOL_RESULT) == "tool_result"

    def test_source_all_types(self):
        from ingestion.adapter import map_source

        for et in EventType:
            result = map_source(et)
            assert isinstance(result, str) and len(result) > 0


# ════════════════════════════════════════════════════════════════════
# adapter — raw_event_to_memory_event
# ════════════════════════════════════════════════════════════════════


class TestRawEventToMemoryEvent:
    """raw_event_to_memory_event() 测试"""

    def test_adapter_conversation(self):
        from ingestion.adapter import raw_event_to_memory_event

        raw = make_raw_event(
            event_id="evt_c1",
            event_type=EventType.CONVERSATION,
            payload={"content": "以后用 PDF", "actor": "user"},
        )
        mem = raw_event_to_memory_event(raw)
        assert mem.event_id == "evt_c1"
        assert mem.raw_event_id == "evt_c1"
        assert mem.source == "conversation"
        assert mem.actor == "user"
        assert mem.content == "以后用 PDF"
        assert mem.raw_event is raw

    def test_adapter_tool_call(self):
        from ingestion.adapter import raw_event_to_memory_event

        raw = make_raw_event(
            event_id="evt_tc1",
            event_type=EventType.TOOL_CALL,
            payload={"tool_name": "wps_export", "input": {"file": "test.docx"}},
        )
        mem = raw_event_to_memory_event(raw)
        assert mem.source == "tool_call"
        assert mem.actor == "agent"
        assert mem.tool_name == "wps_export"
        assert mem.input == {"file": "test.docx"}
        assert mem.raw_event_id == "evt_tc1"

    def test_adapter_tool_result(self):
        from ingestion.adapter import raw_event_to_memory_event

        raw = make_raw_event(
            event_id="evt_tr1",
            event_type=EventType.TOOL_RESULT,
            payload={"tool_name": "wps_export", "output": {"file": "test.pdf"}, "success": True},
        )
        mem = raw_event_to_memory_event(raw)
        assert mem.source == "tool_result"
        assert mem.actor == "tool"
        assert mem.tool_name == "wps_export"
        assert mem.output == {"file": "test.pdf"}
        assert mem.success is True

    def test_adapter_user_behavior(self):
        from ingestion.adapter import raw_event_to_memory_event

        raw = make_raw_event(
            event_id="evt_ub1",
            event_type=EventType.USER_BEHAVIOR,
            payload={"action": "open_file", "file": "doc.pdf", "app": "wps"},
        )
        mem = raw_event_to_memory_event(raw)
        assert mem.source == "user_behavior"
        assert mem.actor == "user"
        assert mem.metadata["action"] == "open_file"

    def test_adapter_system_context(self):
        from ingestion.adapter import raw_event_to_memory_event

        raw = make_raw_event(
            event_id="evt_sc1",
            event_type=EventType.SYSTEM_CONTEXT,
            payload={"os": "Windows 11", "version": "3.2.1"},
        )
        mem = raw_event_to_memory_event(raw)
        assert mem.source == "system_context"
        assert mem.actor == "system"
        assert mem.metadata["os"] == "Windows 11"
        assert mem.content is None


    def test_conversation_actor_not_overridden_by_payload(self):
        """排期表规约：CONVERSATION actor 必须为 'user'，payload 的 actor 不覆写。"""
        from ingestion.adapter import raw_event_to_memory_event

        raw = make_raw_event(
            event_type=EventType.CONVERSATION,
            payload={"content": "hi", "actor": "agent"},
        )
        mem = raw_event_to_memory_event(raw)
        assert mem.actor == "user", f"should be 'user', got '{mem.actor}'"

    def test_adapt_events_batch(self):
        """adapt_events 批量转换保持顺序。"""
        from ingestion.adapter import adapt_events

        raws = [
            make_raw_event(event_id="a", event_type=EventType.CONVERSATION, payload={"content": "hi"}),
            make_raw_event(event_id="b", event_type=EventType.TOOL_CALL, payload={"tool_name": "calc"}),
            make_raw_event(event_id="c", event_type=EventType.TOOL_RESULT, payload={"success": True}),
        ]
        mems = adapt_events(raws)
        assert len(mems) == 3
        assert [m.event_id for m in mems] == ["a", "b", "c"]

    def test_all_fields_mapped(self):
        """所有 RawEvent 字段映射到 MemoryEvent。"""
        from ingestion.adapter import raw_event_to_memory_event

        ts = datetime(2025, 6, 1, 10, 0, 0)
        raw = RawEvent(
            event_id="evt_full",
            user_id="u_full",
            session_id="s_full",
            task_id="t_full",
            event_type=EventType.TOOL_CALL,
            scenario=Scene.CODING,
            timestamp=ts,
            payload={"tool_name": "git", "input": {"cmd": "push"}},
        )
        mem = raw_event_to_memory_event(raw)
        assert mem.event_id == "evt_full"
        assert mem.user_id == "u_full"
        assert mem.session_id == "s_full"
        assert mem.task_id == "t_full"
        assert mem.event_type == EventType.TOOL_CALL
        assert mem.scenario == Scene.CODING
        assert mem.timestamp == ts
        assert mem.raw_event is raw

    # ── 补充分支覆盖 ────────────────────────────────────────────

    def test_tool_name_from_nested_dict(self):
        """tool_name 从嵌套 dict 提取。"""
        from ingestion.adapter import raw_event_to_memory_event

        raw = make_raw_event(
            event_type=EventType.TOOL_CALL,
            payload={"function": {"name": "wps_export"}, "input": {}},
        )
        mem = raw_event_to_memory_event(raw)
        assert mem.tool_name == "wps_export"

    def test_success_from_string(self):
        """success 从字符串解析。"""
        from ingestion.adapter import raw_event_to_memory_event

        raw = make_raw_event(
            event_type=EventType.TOOL_RESULT,
            payload={"tool_name": "calc", "output": {}, "success": "true"},
        )
        mem = raw_event_to_memory_event(raw)
        assert mem.success is True

        raw = make_raw_event(
            event_type=EventType.TOOL_RESULT,
            payload={"tool_name": "calc", "output": {}, "success": "false"},
        )
        mem = raw_event_to_memory_event(raw)
        assert mem.success is False

    def test_adapter_user_config(self):
        from ingestion.adapter import raw_event_to_memory_event

        raw = make_raw_event(
            event_type=EventType.USER_CONFIG,
            payload={"config": {"theme": "dark", "lang": "zh"}},
        )
        mem = raw_event_to_memory_event(raw)
        assert mem.source == "user_config"
        assert mem.actor == "user"

    def test_adapter_task_plan(self):
        from ingestion.adapter import raw_event_to_memory_event

        raw = make_raw_event(
            event_type=EventType.TASK_PLAN,
            payload={"plan": "先读数据，再导出"},
        )
        mem = raw_event_to_memory_event(raw)
        assert mem.source == "task_plan"
        assert mem.actor == "agent"
        assert "先读数据" in (mem.content or "")

    def test_adapter_task_summary(self):
        from ingestion.adapter import raw_event_to_memory_event

        raw = make_raw_event(
            event_type=EventType.TASK_SUMMARY,
            payload={"summary": "任务完成"},
        )
        mem = raw_event_to_memory_event(raw)
        assert mem.source == "task_summary"
        assert mem.actor == "system"
        assert mem.content == "任务完成"

    def test_adapter_document_import(self):
        from ingestion.adapter import raw_event_to_memory_event

        raw = make_raw_event(
            event_type=EventType.DOCUMENT_IMPORT,
            payload={"content": "imported doc text", "filename": "readme.md"},
        )
        mem = raw_event_to_memory_event(raw)
        assert mem.source == "document_import"
        assert mem.actor == "system"
        assert mem.content == "imported doc text"

    def test_adapter_user_feedback(self):
        from ingestion.adapter import raw_event_to_memory_event

        raw = make_raw_event(
            event_type=EventType.USER_FEEDBACK,
            payload={"content": "这个答案不对"},
        )
        mem = raw_event_to_memory_event(raw)
        assert mem.source == "user_feedback"
        assert mem.actor == "user"
        assert mem.content == "这个答案不对"


# ════════════════════════════════════════════════════════════════════
# validator — validate_memory_event
# ════════════════════════════════════════════════════════════════════


class TestValidateMemoryEvent:
    """validate_memory_event() 测试"""

    def test_validator_pass(self):
        from ingestion.validator import validate_memory_event

        valid, errs = validate_memory_event(make_event())
        assert valid is True
        assert errs == []

    def test_validator_fail_missing_user_id(self):
        from ingestion.validator import validate_memory_event

        valid, errs = validate_memory_event(make_event(user_id=""))
        assert valid is False
        assert any("user_id" in e for e in errs)

    def test_validator_fail_invalid_type(self):
        from ingestion.validator import validate_memory_event

        e = make_event()
        e.event_type = "not_an_enum"  # type: ignore
        valid, errs = validate_memory_event(e)
        assert valid is False
        assert any("event_type" in e for e in errs)

    def test_fail_invalid_scenario(self):
        from ingestion.validator import validate_memory_event

        e = make_event()
        e.scenario = "bogus_scene"  # type: ignore
        valid, errs = validate_memory_event(e)
        assert valid is False
        assert any("scenario" in e for e in errs)

    def test_fail_empty_source(self):
        from ingestion.validator import validate_memory_event

        valid, errs = validate_memory_event(make_event(source=""))
        assert valid is False
        assert any("source" in e for e in errs)

    def test_fail_future_timestamp(self):
        from ingestion.validator import validate_memory_event

        future = datetime.now() + timedelta(seconds=10)
        valid, errs = validate_memory_event(make_event(timestamp=future))
        assert valid is False
        assert any("timestamp" in e.lower() for e in errs)

    def test_timestamp_five_sec_tolerance(self):
        """5 秒内的未来时间戳应通过。"""
        from ingestion.validator import validate_memory_event

        future = datetime.now() + timedelta(seconds=3)
        valid, errs = validate_memory_event(make_event(timestamp=future))
        assert valid is True

    def test_multi_error_accumulation(self):
        """多个校验失败应累积所有错误。"""
        from ingestion.validator import validate_memory_event

        e = make_event(user_id="", source="")
        valid, errs = validate_memory_event(e)
        assert valid is False
        assert len(errs) >= 2


class TestValidateToolResultEvent:
    """validate_tool_result_event() 测试"""

    def test_pass_with_success(self):
        from ingestion.validator import validate_tool_result_event

        valid, errs = validate_tool_result_event(
            make_event(event_type=EventType.TOOL_RESULT, success=True)
        )
        assert valid is True

    def test_fail_missing_success(self):
        from ingestion.validator import validate_tool_result_event

        valid, errs = validate_tool_result_event(
            make_event(event_type=EventType.TOOL_RESULT, success=None)
        )
        assert valid is False
        assert any("success" in e for e in errs)


class TestValidateConversationEvent:
    """validate_conversation_event() 测试"""

    def test_pass_with_content(self):
        from ingestion.validator import validate_conversation_event

        valid, errs = validate_conversation_event(make_event(content="hello"))
        assert valid is True

    def test_fail_empty_content(self):
        from ingestion.validator import validate_conversation_event

        valid, errs = validate_conversation_event(make_event(content=""))
        assert valid is False
        assert any("content" in e for e in errs)

    def test_fail_blank_content(self):
        from ingestion.validator import validate_conversation_event

        valid, errs = validate_conversation_event(make_event(content="   "))
        assert valid is False


# ════════════════════════════════════════════════════════════════════
# validator — compute_event_confidence / is_low_quality
# ════════════════════════════════════════════════════════════════════


class TestEventConfidence:
    """compute_event_confidence() / is_low_quality() 测试"""

    def test_high_quality(self):
        from ingestion.validator import compute_event_confidence, is_low_quality

        e = make_event(content="以后导出文件都用 PDF 格式")
        assert compute_event_confidence(e) == 1.0
        assert is_low_quality(e) is False

    def test_short_content(self):
        from ingestion.validator import compute_event_confidence

        assert compute_event_confidence(make_event(content="好")) == 0.65

    def test_fuzzy_content(self):
        from ingestion.validator import compute_event_confidence

        assert compute_event_confidence(make_event(content="你随便吧")) == 0.75

    def test_tool_result_empty_output_fail(self):
        from ingestion.validator import compute_event_confidence, is_low_quality

        e = make_event(event_type=EventType.TOOL_RESULT, output={}, success=False)
        assert is_low_quality(e) is True

    def test_tool_result_empty_output_but_success(self):
        from ingestion.validator import compute_event_confidence, is_low_quality

        e = make_event(event_type=EventType.TOOL_RESULT, output={}, success=True)
        assert is_low_quality(e) is False

    def test_tool_call_no_input(self):
        from ingestion.validator import compute_event_confidence

        e = make_event(event_type=EventType.TOOL_CALL, input={})
        assert compute_event_confidence(e) == 0.75

    def test_user_behavior_empty_metadata(self):
        from ingestion.validator import compute_event_confidence

        e = make_event(event_type=EventType.USER_BEHAVIOR, metadata={"click": ""})
        assert compute_event_confidence(e) == 0.70

    def test_missing_actor(self):
        from ingestion.validator import compute_event_confidence

        e = make_event(actor=None, content="long enough text")
        assert compute_event_confidence(e) == 0.90

    def test_long_content_penalty(self):
        from ingestion.validator import compute_event_confidence

        e = make_event(content="x" * 6000)
        assert compute_event_confidence(e) == 0.85

    def test_custom_threshold(self):
        from ingestion.validator import is_low_quality

        e = make_event(content="好", actor=None)  # score = 0.55
        assert is_low_quality(e, threshold=0.6) is True
        assert is_low_quality(e, threshold=0.5) is False


# ════════════════════════════════════════════════════════════════════
# cleaner — normalize_content
# ════════════════════════════════════════════════════════════════════


class TestNormalizeContent:
    """normalize_content() 测试"""

    def test_cleaner_normalize(self):
        from ingestion.cleaner import normalize_content

        assert normalize_content("  hello   world  ") == "hello world"
        assert normalize_content("\n\nhello\n\n\nworld\n\n") == "hello\n\nworld"
        assert normalize_content("") == ""
        assert normalize_content("  ") == ""
        assert normalize_content("hello\x00world") == "helloworld"

    def test_trim_trailing_whitespace(self):
        from ingestion.cleaner import normalize_content

        assert normalize_content("  abc  ") == "abc"
        assert normalize_content("\tabc\t") == "abc"

    def test_multi_newline_collapse(self):
        from ingestion.cleaner import normalize_content

        assert normalize_content("a\n\n\nb") == "a\n\nb"
        assert normalize_content("a\n\n\n\nb") == "a\n\nb"

    def test_tab_collapse(self):
        from ingestion.cleaner import normalize_content

        assert normalize_content("a\t\tb") == "a b"

    def test_control_char_removal(self):
        from ingestion.cleaner import normalize_content

        assert normalize_content("a\x01b\x02c") == "abc"
        assert normalize_content("hello\x7fworld") == "helloworld"


# ════════════════════════════════════════════════════════════════════
# cleaner — deduplicate_events
# ════════════════════════════════════════════════════════════════════


class TestDeduplicateEvents:
    """deduplicate_events() 测试"""

    def test_cleaner_deduplicate(self):
        from ingestion.cleaner import deduplicate_events

        events = [
            make_event(event_id="e1", content="first"),
            make_event(event_id="e2", content="second"),
            make_event(event_id="e1", content="duplicate"),
            make_event(event_id="e3", content="third"),
        ]
        result = deduplicate_events(events)
        assert len(result) == 3
        assert [e.event_id for e in result] == ["e1", "e2", "e3"]
        assert result[0].content == "first"  # 首次出现保留

    def test_all_unique(self):
        from ingestion.cleaner import deduplicate_events

        events = [make_event(event_id=f"e{i}") for i in range(5)]
        assert len(deduplicate_events(events)) == 5

    def test_empty_list(self):
        from ingestion.cleaner import deduplicate_events

        assert deduplicate_events([]) == []

    def test_all_duplicates(self):
        from ingestion.cleaner import deduplicate_events

        events = [make_event(event_id="e1") for _ in range(10)]
        assert len(deduplicate_events(events)) == 1


# ════════════════════════════════════════════════════════════════════
# cleaner — remove_noise
# ════════════════════════════════════════════════════════════════════


class TestRemoveNoise:
    """remove_noise() 测试"""

    def test_blank_content_conversation_removed(self):
        from ingestion.cleaner import remove_noise

        result = remove_noise([
            make_event(event_id="e1", content="valid"),
            make_event(event_id="e2", content="   "),
            make_event(event_id="e3", content=""),
        ])
        assert len(result) == 1
        assert result[0].event_id == "e1"

    def test_tool_result_blank_content_kept(self):
        """非 CONVERSATION/USER_FEEDBACK 的空白 content 保留。"""
        from ingestion.cleaner import remove_noise

        result = remove_noise([
            make_event(event_id="e1", event_type=EventType.TOOL_RESULT, content="", tool_name="calc", success=True),
        ])
        assert len(result) == 1

    def test_optional_fields_filled(self):
        from ingestion.cleaner import remove_noise

        result = remove_noise([make_event(event_id="e1", actor=None, tool_name=None)])
        assert result[0].actor == "user"
        assert result[0].tool_name == ""

    def test_missing_tool_name_flagged(self):
        from ingestion.cleaner import remove_noise

        result = remove_noise([make_event(event_id="e1", event_type=EventType.TOOL_CALL, tool_name="")])
        flags = result[0].metadata.get("abnormal_flags", [])
        assert "missing_tool_name" in flags

    def test_long_content_flagged(self):
        from ingestion.cleaner import remove_noise

        result = remove_noise([make_event(event_id="e1", content="x" * 20000)])
        flags = result[0].metadata.get("abnormal_flags", [])
        assert "content_too_long" in flags
