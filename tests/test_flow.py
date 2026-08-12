"""
集成测试 — 端到端流程验证

场景： Collector → Adapter → Cleaner → Validator → Store

覆盖：
  ✓ 正常端到端流程（JSONL → RawEvent → MemoryEvent → 清洗 → 校验 → 入库）
  ✓ 数据完整性（字段无损、类型正确、raw_event_id 关联保留）
  ✓ 事务完整性（批量失败回滚）
  ✓ 异常路径（JSONL 损坏、空文件、低质事件拦截）
  ✓ 短/中/长三层记忆流转（ShortTermMemory → MidTermMemory → Promotion → LongTermMemory）
"""

import os
import tempfile
import shutil
import json
from datetime import datetime

import pytest

from core.config import DEMO_DATA_PATH
from core.constants import EventType, MemoryType, Scene
from core.models import MemoryEvent, MemoryRecord


# ── 全局临时数据库 fixture ──────────────────────────────────────────


@pytest.fixture(scope="function")
def db_path():
    """所有测试共享一个临时数据库目录。"""
    tmpdir = tempfile.mkdtemp()
    db = os.path.join(tmpdir, "integration.db")
    from memory.store import init_db
    init_db(db)
    yield db
    shutil.rmtree(tmpdir, ignore_errors=True)


# ════════════════════════════════════════════════════════════════════
# 1. 正常端到端流程
# ════════════════════════════════════════════════════════════════════


class TestEndToEndFlow:
    """Collector → Adapter → Cleaner → Validator → Store"""

    def test_end_to_end_flow(self, db_path):
        """
        完整集成流程：
        1. 加载 office_demo_events.jsonl → RawEvent
        2. RawEvent → MemoryEvent
        3. 清洗去重
        4. 校验
        5. 存储
        6. 验证
        """
        # 1. 加载
        from ingestion.collector import load_jsonl, create_raw_event, validate_raw_payload

        raw_dicts = load_jsonl(str(DEMO_DATA_PATH))
        assert len(raw_dicts) == 6, f"expected 6 lines, got {len(raw_dicts)}"

        raw_events = [create_raw_event(d) for d in raw_dicts if validate_raw_payload(d)]
        assert len(raw_events) == 6

        # 2. 转换为 MemoryEvent
        from ingestion.adapter import raw_event_to_memory_event

        memory_events = [raw_event_to_memory_event(r) for r in raw_events]
        assert len(memory_events) == 6
        # 验证 raw_event_id 关联保留
        for m in memory_events:
            assert m.raw_event_id == m.event_id

        # 3. 清洗去重
        from ingestion.cleaner import remove_noise, deduplicate_events

        cleaned = remove_noise(memory_events)
        cleaned = deduplicate_events(cleaned)
        assert len(cleaned) <= 6

        # 4. 校验
        from ingestion.validator import validate_memory_event

        for event in cleaned:
            valid, errs = validate_memory_event(event)
            assert valid, f"{event.event_id} failed: {errs}"

        # 5. 存储
        from memory.store import insert_event_batch, query_events_by_session, count_events

        count = insert_event_batch(db_path, cleaned)
        assert count == len(cleaned), f"expected {len(cleaned)}, got {count}"

        # 6. 验证
        total = count_events(db_path, "u001")
        assert total == count, f"count mismatch: {total} != {count}"

        s1_events = query_events_by_session(db_path, "sess_demo_001")
        assert len(s1_events) == 6

        # 按 event_id 索引
        by_id = {e.event_id: e for e in s1_events}

        # 字段完整性验证 —— evt_001 (conversation)
        e1 = by_id["evt_001"]
        assert e1.event_type == EventType.CONVERSATION
        assert e1.actor == "user"
        assert e1.source == "conversation"
        assert e1.content == "以后导出文件都用 PDF 格式"

        # evt_002 (tool_call)
        e2 = by_id["evt_002"]
        assert e2.event_type == EventType.TOOL_CALL
        assert e2.actor == "agent"
        assert e2.tool_name == "wps_export"
        assert e2.input.get("file") == "月报.docx"

        # evt_003 (tool_result)
        e3 = by_id["evt_003"]
        assert e3.event_type == EventType.TOOL_RESULT
        assert e3.actor == "tool"
        assert e3.tool_name == "wps_export"
        assert e3.success is True
        assert e3.output.get("file") == "月报.pdf"

        # evt_004 (user_behavior)
        e4 = by_id["evt_004"]
        assert e4.event_type == EventType.USER_BEHAVIOR
        assert e4.actor == "user"
        assert "action" in e4.metadata

        # evt_005 (user_config)
        e5 = by_id["evt_005"]
        assert e5.event_type == EventType.USER_CONFIG
        assert e5.actor == "user"

        # evt_006 (system_context)
        e6 = by_id["evt_006"]
        assert e6.event_type == EventType.SYSTEM_CONTEXT
        assert e6.actor == "system"

        # 时间戳保留为 datetime
        assert isinstance(e1.timestamp, datetime)
        assert e1.timestamp.isoformat() == "2025-06-20T09:00:00"


# ════════════════════════════════════════════════════════════════════
# 2. 数据完整性保证
# ════════════════════════════════════════════════════════════════════


class TestDataIntegrity:
    """数据完整性专项测试"""

    def test_no_data_loss(self, db_path):
        """所有事件无损经过完整 pipeline。"""
        from ingestion.collector import load_jsonl, create_raw_event, validate_raw_payload
        from ingestion.adapter import raw_event_to_memory_event
        from ingestion.cleaner import remove_noise, deduplicate_events
        from ingestion.validator import validate_memory_event
        from memory.store import insert_event_batch, query_events_by_session

        raw_dicts = load_jsonl(str(DEMO_DATA_PATH))
        raw_events = [create_raw_event(d) for d in raw_dicts if validate_raw_payload(d)]
        memory_events = [raw_event_to_memory_event(r) for r in raw_events]
        cleaned = deduplicate_events(remove_noise(memory_events))

        for e in cleaned:
            valid, errs = validate_memory_event(e)
            assert valid

        insert_event_batch(db_path, cleaned)
        stored = query_events_by_session(db_path, "sess_demo_001")
        stored_ids = {e.event_id for e in stored}

        # 所有原始 event_id 都在存储中找到
        for raw in raw_events:
            assert raw.event_id in stored_ids, f"Missing: {raw.event_id}"

    def test_fields_round_trip(self, db_path):
        """所有结构化字段入库后无损还原。"""
        from memory.store import insert_event, query_events_by_session
        from datetime import timedelta

        ts = datetime(2025, 7, 1, 12, 0, 0)
        orig = MemoryEvent(
            event_id="rt_001",
            raw_event_id="rt_raw_001",
            user_id="u_rt",
            session_id="s_rt",
            task_id="t_rt",
            event_type=EventType.TOOL_CALL,
            scenario=Scene.CODING,
            source="tool_call",
            actor="agent",
            content="calling git",
            tool_name="git",
            input={"cmd": "commit", "msg": "fix bug"},
            output={},
            success=None,
            metadata={"env": "production", "tty": True},
            timestamp=ts,
            raw_event=None,
        )
        insert_event(db_path, orig)
        stored = query_events_by_session(db_path, "s_rt")[0]

        assert stored.event_id == "rt_001"
        assert stored.raw_event_id == "rt_raw_001"
        assert stored.user_id == "u_rt"
        assert stored.session_id == "s_rt"
        assert stored.task_id == "t_rt"
        assert stored.event_type == EventType.TOOL_CALL
        assert stored.scenario == Scene.CODING
        assert stored.source == "tool_call"
        assert stored.actor == "agent"
        assert stored.content == "calling git"
        assert stored.tool_name == "git"
        assert stored.input == {"cmd": "commit", "msg": "fix bug"}
        assert stored.metadata == {"env": "production", "tty": True}
        assert stored.timestamp == ts

    def test_tool_result_with_raw_event_roundtrip(self, db_path):
        """TOOL_RESULT 的 output/success 字段无损。"""
        from memory.store import insert_event, query_events_by_session
        from core.models import RawEvent

        raw_obj = RawEvent(
            event_id="tr_raw",
            user_id="u_tr",
            session_id="s_tr",
            task_id="t_tr",
            event_type=EventType.TOOL_RESULT,
            scenario=Scene.OFFICE,
            timestamp=datetime.now(),
            payload={"tool_name": "wps", "output": {"file": "rpt.pdf"}, "success": True},
        )
        evt = MemoryEvent(
            event_id="tr_001",
            raw_event_id="tr_raw",
            user_id="u_tr",
            session_id="s_tr",
            task_id="t_tr",
            event_type=EventType.TOOL_RESULT,
            scenario=Scene.OFFICE,
            source="tool_result",
            actor="tool",
            content="",
            tool_name="wps",
            input={},
            output={"file": "rpt.pdf"},
            success=True,
            metadata={},
            timestamp=datetime.now(),
            raw_event=raw_obj,
        )
        insert_event(db_path, evt)
        stored = query_events_by_session(db_path, "s_tr")[0]

        assert stored.success is True
        assert stored.output == {"file": "rpt.pdf"}
        assert stored.tool_name == "wps"


# ════════════════════════════════════════════════════════════════════
# 3. 事务完整性
# ════════════════════════════════════════════════════════════════════


class TestTransactionIntegrity:
    """事务回滚验证"""

    def test_batch_rollback_on_duplicate(self, db_path):
        """批量中出现重复 event_id 应整体回滚。"""
        from memory.store import insert_event, insert_event_batch, count_events
        from ingestion.collector import load_jsonl, create_raw_event, validate_raw_payload
        from ingestion.adapter import raw_event_to_memory_event

        # 预插入一条
        raw_dicts = load_jsonl(str(DEMO_DATA_PATH))
        first = raw_event_to_memory_event(create_raw_event(raw_dicts[0]))
        insert_event(db_path, first)

        before = count_events(db_path, first.user_id)

        # 新的事件列表，其中一条 event_id 重复 → 应整体回滚
        second_batch = [
            raw_event_to_memory_event(create_raw_event(raw_dicts[1])),
            raw_event_to_memory_event(create_raw_event(raw_dicts[0])),  # 重复
            raw_event_to_memory_event(create_raw_event(raw_dicts[2])),
        ]
        with pytest.raises(ValueError, match="Duplicate event_id"):
            insert_event_batch(db_path, second_batch)

        after = count_events(db_path, first.user_id)
        assert after == before, f"rollback failed: {before} -> {after}"


# ════════════════════════════════════════════════════════════════════
# 4. 异常路径
# ════════════════════════════════════════════════════════════════════


class TestErrorHandling:
    """异常场景处理"""

    def test_corrupt_jsonl_raises(self):
        """损坏的 JSONL 抛出 ValueError。"""
        from ingestion.collector import load_jsonl

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            f.write('{"event_id": "e1"}\nbad-json\n')
            path = f.name
        try:
            with pytest.raises(ValueError, match="Invalid JSON at line 2"):
                load_jsonl(path)
        finally:
            os.unlink(path)

    def test_empty_jsonl(self):
        """空 JSONL 返回空列表。"""
        from ingestion.collector import load_jsonl

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            path = f.name
        try:
            assert load_jsonl(path) == []
        finally:
            os.unlink(path)

    def test_validate_pipeline_blocks_bad_data(self, db_path):
        """缺失必需字段的数据在 create_raw_event 阶段即被拦截。"""
        from ingestion.collector import create_raw_event

        # 缺少 user_id → create_raw_event 抛出 KeyError
        bad_data = {
            "event_id": "bad_001",
            "session_id": "s1",
            "task_id": "t1",
            "event_type": "conversation",
            "content": "hi",
        }
        with pytest.raises(KeyError, match="user_id"):
            create_raw_event(bad_data)

    def test_invalid_event_type_rejected(self):
        """非法 event_type 被 validate_raw_payload 拒绝。"""
        from ingestion.collector import validate_raw_payload

        assert validate_raw_payload({"event_id": "e1", "user_id": "u1", "event_type": "bogus"}) is False

    def test_empty_content_conversation_filtered(self):
        """空白内容 CONVERSATION 被 cleaner 去除。"""
        from core.models import RawEvent
        from ingestion.collector import create_raw_event, validate_raw_payload
        from ingestion.adapter import raw_event_to_memory_event
        from ingestion.cleaner import remove_noise
        from ingestion.validator import is_low_quality

        data = {"event_id": "noise_001", "user_id": "u1", "event_type": "conversation", "content": "   "}
        raw = create_raw_event(data)
        mem = raw_event_to_memory_event(raw)

        cleaned = remove_noise([mem])
        assert len(cleaned) == 0, "blank content conversation should be removed"


# ════════════════════════════════════════════════════════════════════
# 5. 低质拦截
# ════════════════════════════════════════════════════════════════════


class TestLowQualityFilter:
    """低置信度事件的过滤逻辑"""

    def test_low_quality_conversation_intercepted(self):
        """过短 + 缺 actor 的 CONVERSATION 低于自定义阈值时被拦截。"""
        from ingestion.validator import is_low_quality, compute_event_confidence
        from core.models import MemoryEvent
        from datetime import datetime
        from core.constants import EventType, Scene

        e = MemoryEvent(
            event_id="lq_001", raw_event_id="lq_001", user_id="u1",
            session_id="s1", task_id="t1",
            event_type=EventType.CONVERSATION, scenario=Scene.OFFICE,
            source="conversation", actor=None, content="啊",
            timestamp=datetime.now(),
        )
        score = compute_event_confidence(e)
        assert score == 0.55, f"expected 0.55, got {score}"
        # 默认阈值 0.5 不拦截，提高阈值即可拦截
        assert is_low_quality(e) is False
        assert is_low_quality(e, threshold=0.6) is True

    def test_tool_result_empty_and_fail_intercepted(self):
        """空 output + success=False 的 TOOL_RESULT 被拦截。"""
        from ingestion.validator import is_low_quality
        from datetime import datetime
        from core.constants import EventType, Scene
        from core.models import MemoryEvent

        e = MemoryEvent(
            event_id="lq_tr", raw_event_id="lq_tr", user_id="u1",
            session_id="s1", task_id="t1",
            event_type=EventType.TOOL_RESULT, scenario=Scene.OFFICE,
            source="tool_result", actor="tool", content="",
            tool_name="calc", output={}, success=False,
            timestamp=datetime.now(),
        )
        assert is_low_quality(e) is True

    def test_high_quality_event_passes(self):
        """高质量事件不被拦截。"""
        from ingestion.validator import is_low_quality
        from datetime import datetime
        from core.constants import EventType, Scene
        from core.models import MemoryEvent

        e = MemoryEvent(
            event_id="hq_001", raw_event_id="hq_001", user_id="u1",
            session_id="s1", task_id="t1",
            event_type=EventType.CONVERSATION, scenario=Scene.OFFICE,
            source="conversation", actor="user",
            content="以后导出文件都用 PDF 格式",
            timestamp=datetime.now(),
        )
        assert is_low_quality(e) is False


# ════════════════════════════════════════════════════════════════════
# 6. 三层记忆流转 (short → mid → long)
# ════════════════════════════════════════════════════════════════════


class TestMemoryFlow:
    """ShortTermMemory → MidTermMemory → Promotion → LongTermMemory"""

    def test_three_layer_flow(self, db_path):
        """
        验证三层记忆完整流转：
        1. ShortTermMemory：事件积累 + trim
        2. MidTermMemory：压缩为摘要
        3. Promotion：晋升为持久化 MemoryRecord
        4. LongTermMemory：查询验证
        """
        from flow.short_term import ShortTermMemory
        from flow.mid_term import MidTermMemory
        from flow.promotion import MemoryPromotion
        from flow.long_term import LongTermMemory
        from ingestion.collector import load_jsonl, create_raw_event, validate_raw_payload
        from ingestion.adapter import raw_event_to_memory_event
        from core.constants import MemoryType

        # --- 短期 ---
        stm = ShortTermMemory(max_events=50)
        raw_dicts = load_jsonl(str(DEMO_DATA_PATH))
        for d in raw_dicts:
            if validate_raw_payload(d):
                raw = create_raw_event(d)
                mem = raw_event_to_memory_event(raw)
                stm.add_event("u_flow", "s_flow", mem)

        wm = stm.get("u_flow", "s_flow")
        assert wm is not None
        assert len(wm.events) == 6

        # 添加消息和工具轨迹
        stm.add_message("u_flow", "s_flow", "user", "帮我导出月报")
        stm.add_tool_trace("u_flow", "s_flow", "wps_export", {"file": "月报.docx"}, {"file": "月报.pdf"}, True)
        assert len(wm.messages) == 1
        assert len(wm.tool_trace) == 1

        # --- 中期 ---
        mtm = MidTermMemory()
        summary = mtm.compress(wm, scenario=Scene.OFFICE)
        assert summary.summary_id is not None
        assert summary.user_id == "u_flow"
        assert summary.session_id == "s_flow"
        assert "Events:" in summary.summary
        assert len(summary.source_event_ids) == 6

        # 摘要可查询
        retrieved = mtm.get(summary.summary_id)
        assert retrieved is not None
        assert retrieved.summary == summary.summary

        # --- 晋升 ---
        promo = MemoryPromotion(db_path, confidence_threshold=0.5)
        record = promo.from_summary(summary, memory_type=MemoryType.SESSION_SUMMARY, confidence=0.8)
        assert record is not None
        assert isinstance(record, MemoryRecord)
        assert record.user_id == "u_flow"
        assert record.memory_type == MemoryType.SESSION_SUMMARY

        # --- 长期 ---
        ltm = LongTermMemory(db_path)
        active = ltm.list_active("u_flow")
        assert len(active) >= 1
        assert any(r.memory_id == record.memory_id for r in active)

        # 按 key 查询
        key = f"session_summary:s_flow"
        by_key = ltm.get_by_key("u_flow", key)
        assert by_key is not None
        assert by_key.content == summary.summary

        # filter 查询
        filtered = ltm.filter("u_flow", memory_type=MemoryType.SESSION_SUMMARY)
        assert len(filtered) >= 1


# ════════════════════════════════════════════════════════════════════
# 7. 完整 pipeline 含 cleaner + validator + confidence
# ════════════════════════════════════════════════════════════════════


class TestFullPipelineWithQualityGate:
    """带置信度校验门的完整 ingestion pipeline"""

    def test_pipeline_with_quality_filter(self, db_path):
        """
        JSONL → RawEvent → MemoryEvent → remove_noise → dedup
        → validate → is_low_quality 过滤 → store
        """
        from ingestion.collector import load_jsonl, create_raw_event, validate_raw_payload
        from ingestion.adapter import raw_event_to_memory_event
        from ingestion.cleaner import remove_noise, deduplicate_events
        from ingestion.validator import validate_memory_event, is_low_quality
        from memory.store import insert_event_batch, query_events_by_session, count_events

        # 模拟混合了不良数据的事件
        raw_dicts = load_jsonl(str(DEMO_DATA_PATH))
        raw_events = [create_raw_event(d) for d in raw_dicts if validate_raw_payload(d)]

        # 额外注入一条低质事件
        from core.models import RawEvent
        from datetime import datetime
        from core.constants import EventType, Scene

        low_quality_raw = RawEvent(
            event_id="lq_injected", user_id="u_qual", session_id="s_qual", task_id="t_qual",
            event_type=EventType.TOOL_RESULT, scenario=Scene.OFFICE,
            timestamp=datetime.now(),
            payload={"tool_name": "calc", "output": {}, "success": False},
        )
        raw_events.append(low_quality_raw)

        # 全部转换
        memory_events = [raw_event_to_memory_event(r) for r in raw_events]

        # 清洗
        cleaned = deduplicate_events(remove_noise(memory_events))

        # 校验
        for e in cleaned:
            valid, errs = validate_memory_event(e)
            assert valid, f"{e.event_id}: {errs}"

        # 低质拦截
        passed = [e for e in cleaned if not is_low_quality(e)]
        # 低质事件应被过滤掉
        passed_ids = {e.event_id for e in passed}
        assert "lq_injected" not in passed_ids, "低质事件应被拦截"

        # 存储
        count = insert_event_batch(db_path, passed)
        assert count == len(passed)

        # 验证：过滤后的数据正确入库
        stored = query_events_by_session(db_path, "sess_demo_001")
        assert len(stored) == 6  # 原始 6 条都已存入
        assert count_events(db_path, "u_qual") == 0  # 低质事件的 user_id

    def test_pipeline_with_noise_and_duplicates(self, db_path):
        """
        含噪声和重复数据的 pipeline：
        noise + duplicate → remove_noise + dedup → validate → store
        """
        from ingestion.collector import create_raw_event
        from ingestion.adapter import raw_event_to_memory_event
        from ingestion.cleaner import remove_noise, deduplicate_events
        from ingestion.validator import validate_memory_event
        from memory.store import insert_event_batch, query_events_by_session

        # 构建包含正常、空白、重复事件的列表
        from core.models import RawEvent
        from datetime import datetime
        from core.constants import EventType, Scene

        payloads = [
            {"event_id": "mix_001", "user_id": "u_mix", "event_type": "conversation", "content": "正常的",
             "session_id": "s_mix", "task_id": "t_mix", "scenario": "office"},
            {"event_id": "mix_002", "user_id": "u_mix", "event_type": "conversation", "content": "   ",
             "session_id": "s_mix", "task_id": "t_mix", "scenario": "office"},  # 空白 → 被去除
            {"event_id": "mix_003", "user_id": "u_mix", "event_type": "tool_call", "tool_name": "calc", "input": {"a": 1},
             "session_id": "s_mix", "task_id": "t_mix", "scenario": "office"},
            {"event_id": "mix_001", "user_id": "u_mix", "event_type": "conversation", "content": "重复的",
             "session_id": "s_mix", "task_id": "t_mix", "scenario": "office"},  # 重复 → 被去重
            {"event_id": "mix_004", "user_id": "u_mix", "event_type": "tool_result", "tool_name": "calc", "output": {"r": 1}, "success": True,
             "session_id": "s_mix", "task_id": "t_mix", "scenario": "office"},
        ]

        raw_events = [create_raw_event(p) for p in payloads]
        memory_events = [raw_event_to_memory_event(r) for r in raw_events]

        # 清洗
        cleaned = remove_noise(memory_events)
        assert len(cleaned) == 4, f"expected 4 after noise removal, got {len(cleaned)}"

        deduped = deduplicate_events(cleaned)
        assert len(deduped) == 3, f"expected 3 after dedup, got {len(deduped)}"
        assert [e.event_id for e in deduped] == ["mix_001", "mix_003", "mix_004"]

        # 校验
        for e in deduped:
            valid, errs = validate_memory_event(e)
            assert valid, f"{e.event_id}: {errs}"

        # 存储
        insert_event_batch(db_path, deduped)
        stored = query_events_by_session(db_path, "s_mix")
        assert len(stored) == 3

        # 验证内容正确（首次出现的 content 被保留）
        by_id = {e.event_id: e for e in stored}
        assert by_id["mix_001"].content == "正常的"  # 首次出现保留
