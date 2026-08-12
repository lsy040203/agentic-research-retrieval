"""
Memory store 层单元测试

测试用例（12+）：
  ✓ test_insert_event()
  ✓ test_insert_event_batch()
  ✓ test_query_by_session()
  ✓ test_count_events()
  ✓ test_save_memory()
  ✓ test_mark_deleted()
  ← 并扩展 init_db / list_active_memories / list_events / 事务回滚 等补充用例
"""

import os
import tempfile
import shutil
from datetime import datetime

import pytest

from core.constants import EventType, MemoryType, MemoryStatus, Scene
from core.models import MemoryCandidate, MemoryEvent, MemoryRecord


# ── 帮助函数 ────────────────────────────────────────────────────────


def make_event(**kw) -> MemoryEvent:
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


def make_candidate(**kw) -> MemoryCandidate:
    d = dict(
        candidate_id="cand_001",
        user_id="u1",
        memory_type=MemoryType.PREFERENCE,
        key="test_key",
        content="test content",
        scenario=Scene.GLOBAL,
        confidence=0.9,
        source="test",
        source_events=[],
        source_summaries=[],
        tags=[],
        metadata={},
        created_at=datetime.now(),
    )
    d.update(kw)
    return MemoryCandidate(**d)


@pytest.fixture
def db():
    """创建一个临时数据库，测试后清理。"""
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test.db")
    from memory.store import init_db

    init_db(db_path)
    yield db_path
    shutil.rmtree(tmpdir, ignore_errors=True)


# ════════════════════════════════════════════════════════════════════
# init_db
# ════════════════════════════════════════════════════════════════════


class TestInitDb:
    """init_db() 测试"""

    def test_init_db_creates_tables(self, db):
        """初始化后表结构存在。"""
        import sqlite3

        conn = sqlite3.connect(db)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [row[0] for row in cursor.fetchall()]
        conn.close()
        assert "events" in tables
        assert "memories" in tables

    def test_init_db_idempotent(self, db):
        """重复初始化不报错。"""
        from memory.store import init_db

        init_db(db)  # 第二次调用
        init_db(db)  # 第三次调用
        # 不会抛出异常即通过


# ════════════════════════════════════════════════════════════════════
# insert_event
# ════════════════════════════════════════════════════════════════════


class TestInsertEvent:
    """insert_event() 测试"""

    def test_insert_event(self, db):
        from memory.store import insert_event

        e = make_event(event_id="evt_001")
        result = insert_event(db, e)
        assert result == "evt_001"

    def test_insert_event_returns_event_id(self, db):
        from memory.store import insert_event

        e = make_event(event_id="evt_abc")
        result = insert_event(db, e)
        assert isinstance(result, str)
        assert result == "evt_abc"

    def test_insert_duplicate_raises(self, db):
        from memory.store import insert_event

        insert_event(db, make_event(event_id="evt_dup"))
        with pytest.raises(ValueError, match="already exists"):
            insert_event(db, make_event(event_id="evt_dup"))

    def test_insert_tool_call_event(self, db):
        from memory.store import insert_event

        e = make_event(
            event_id="evt_tc",
            event_type=EventType.TOOL_CALL,
            source="tool_call",
            actor="agent",
            tool_name="calc",
            input={"expr": "1+1"},
        )
        insert_event(db, e)
        from memory.store import query_events_by_session

        results = query_events_by_session(db, "s1")
        assert len(results) == 1
        assert results[0].tool_name == "calc"

    def test_insert_tool_result_event(self, db):
        from memory.store import insert_event

        e = make_event(
            event_id="evt_tr",
            event_type=EventType.TOOL_RESULT,
            source="tool_result",
            actor="tool",
            tool_name="calc",
            output={"result": 2},
            success=True,
        )
        insert_event(db, e)
        from memory.store import query_events_by_session

        results = query_events_by_session(db, "s1")
        assert results[0].success is True
        assert results[0].output == {"result": 2}

    def test_insert_event_creates_parent_dir(self):
        """目标目录不存在时自动创建。"""
        from memory.store import init_db, insert_event

        tmpdir = tempfile.mkdtemp()
        nested = os.path.join(tmpdir, "a", "b", "nested.db")
        try:
            init_db(nested)  # 先初始化表结构
            e = make_event(event_id="evt_nested")
            result = insert_event(nested, e)
            assert result == "evt_nested"
            assert os.path.exists(nested)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ════════════════════════════════════════════════════════════════════
# insert_event_batch
# ════════════════════════════════════════════════════════════════════


class TestInsertEventBatch:
    """insert_event_batch() 测试"""

    def test_insert_event_batch(self, db):
        from memory.store import insert_event_batch, count_events

        events = [
            make_event(event_id="b1", session_id="s_batch"),
            make_event(event_id="b2", session_id="s_batch"),
            make_event(event_id="b3", session_id="s_batch"),
        ]
        count = insert_event_batch(db, events)
        assert count == 3
        assert count_events(db, "u1") == 3

    def test_batch_empty_list(self, db):
        from memory.store import insert_event_batch

        assert insert_event_batch(db, []) == 0

    def test_batch_creates_parent_dir(self):
        from memory.store import init_db, insert_event_batch

        tmpdir = tempfile.mkdtemp()
        nested = os.path.join(tmpdir, "x", "y", "batch.db")
        try:
            init_db(nested)
            count = insert_event_batch(nested, [make_event(event_id="be1")])
            assert count == 1
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_batch_transaction_rollback_on_duplicate(self, db):
        """重复 ID 导致整体回滚。"""
        from memory.store import insert_event_batch, count_events, insert_event

        insert_event(db, make_event(event_id="existing"))
        before = count_events(db, "u1")

        with pytest.raises(ValueError, match="Duplicate event_id"):
            insert_event_batch(db, [
                make_event(event_id="new1", session_id="s_roll"),
                make_event(event_id="existing"),  # 已存在 → 回滚
                make_event(event_id="new2", session_id="s_roll"),
            ])
        after = count_events(db, "u1")
        assert after == before, f"rollback failed: {before} -> {after}"


# ════════════════════════════════════════════════════════════════════
# query_events_by_session
# ════════════════════════════════════════════════════════════════════


class TestQueryEventsBySession:
    """query_events_by_session() 测试"""

    def test_query_by_session(self, db):
        from memory.store import insert_event_batch, query_events_by_session

        insert_event_batch(db, [
            make_event(event_id="q1", session_id="s_q", content="a"),
            make_event(event_id="q2", session_id="s_q", content="b"),
            make_event(event_id="q3", session_id="s_other", content="c"),
        ])
        results = query_events_by_session(db, "s_q")
        assert len(results) == 2
        assert {e.event_id for e in results} == {"q1", "q2"}

    def test_query_returns_full_memory_event(self, db):
        from memory.store import insert_event, query_events_by_session

        ts = datetime(2025, 6, 1, 10, 30, 0)
        e = make_event(
            event_id="full_test",
            event_type=EventType.TOOL_CALL,
            scenario=Scene.CODING,
            source="tool_call",
            actor="agent",
            tool_name="git",
            input={"cmd": "log"},
            success=None,
            metadata={"env": "dev"},
            timestamp=ts,
        )
        insert_event(db, e)
        results = query_events_by_session(db, "s1")
        assert len(results) == 1
        r = results[0]
        assert r.event_id == "full_test"
        assert r.user_id == "u1"
        assert r.session_id == "s1"
        assert r.task_id == "t1"
        assert r.event_type == EventType.TOOL_CALL
        assert r.scenario == Scene.CODING
        assert r.source == "tool_call"
        assert r.actor == "agent"
        assert r.tool_name == "git"
        assert r.input == {"cmd": "log"}
        assert r.timestamp == ts
        assert isinstance(r.timestamp, datetime)

    def test_query_empty_session(self, db):
        from memory.store import query_events_by_session

        assert query_events_by_session(db, "nonexistent") == []

    def test_query_non_existent_db(self):
        from memory.store import query_events_by_session

        assert query_events_by_session("/no/such/db.db", "s1") == []

    def test_query_ordered_by_timestamp(self, db):
        from memory.store import insert_event_batch, query_events_by_session
        from datetime import timedelta

        base = datetime(2025, 1, 1, 0, 0, 0)
        insert_event_batch(db, [
            make_event(event_id="t1", session_id="s_order", timestamp=base + timedelta(hours=2)),
            make_event(event_id="t2", session_id="s_order", timestamp=base + timedelta(hours=1)),
            make_event(event_id="t3", session_id="s_order", timestamp=base + timedelta(hours=3)),
        ])
        results = query_events_by_session(db, "s_order")
        assert [r.event_id for r in results] == ["t2", "t1", "t3"]


# ════════════════════════════════════════════════════════════════════
# count_events
# ════════════════════════════════════════════════════════════════════


class TestCountEvents:
    """count_events() 测试"""

    def test_count_events(self, db):
        from memory.store import count_events, insert_event_batch

        assert count_events(db, "u1") == 0
        insert_event_batch(db, [
            make_event(event_id="c1"),
            make_event(event_id="c2"),
            make_event(event_id="c3"),
        ])
        assert count_events(db, "u1") == 3

    def test_count_zero_for_unknown_user(self, db):
        from memory.store import count_events, insert_event_batch

        insert_event_batch(db, [make_event(event_id="c4")])
        assert count_events(db, "nonexistent") == 0

    def test_count_non_existent_db(self):
        from memory.store import count_events

        assert count_events("/no/such/path/db.db", "u1") == 0


# ════════════════════════════════════════════════════════════════════
# save_event (Phase 0 legacy)
# ════════════════════════════════════════════════════════════════════


class TestSaveEvent:
    """save_event() 兼容性测试"""

    def test_save_event_legacy(self, db):
        from memory.store import save_event

        raw = make_event(
            event_id="legacy_e1",
            raw_event=make_event(event_id="raw_legacy", user_id="u1", session_id="s1").raw_event,
        )
        # save_event 依赖 event.raw_event 非 None
        # 这里需要构造一个带 raw_event 的 MemoryEvent
        from core.models import RawEvent

        raw_obj = RawEvent(
            event_id="raw_legacy",
            user_id="u1",
            session_id="s1",
            task_id="t1",
            event_type=EventType.CONVERSATION,
            scenario=Scene.OFFICE,
            timestamp=datetime.now(),
            payload={"content": "test"},
        )
        evt = make_event(event_id="legacy_e1", raw_event=raw_obj)
        result = save_event(db, evt)
        assert result == "raw_legacy"

    def test_save_event_with_raw_event_none(self, db):
        """save_event 在 raw_event=None 时应抛出 AttributeError。"""
        from memory.store import save_event

        evt = make_event(event_id="no_raw", raw_event=None)
        with pytest.raises(AttributeError):
            save_event(db, evt)


# ════════════════════════════════════════════════════════════════════
# list_events (Phase 0 legacy)
# ════════════════════════════════════════════════════════════════════


class TestListEvents:
    """list_events() 兼容性测试"""

    def test_list_events_empty_db(self, db):
        from memory.store import list_events

        assert list_events(db, "u1") == []

    def test_list_events_by_user(self, db):
        from memory.store import insert_event, list_events

        insert_event(db, make_event(event_id="l1", user_id="u_list"))
        insert_event(db, make_event(event_id="l2", user_id="u_list"))
        insert_event(db, make_event(event_id="l3", user_id="other"))
        results = list_events(db, "u_list")
        # list_events 返回 raw tuples，至少检查数量
        assert len(results) == 2

    def test_list_events_by_user_and_task(self, db):
        from memory.store import insert_event, list_events

        insert_event(db, make_event(event_id="lt1", user_id="u_lt", task_id="task_a"))
        insert_event(db, make_event(event_id="lt2", user_id="u_lt", task_id="task_a"))
        insert_event(db, make_event(event_id="lt3", user_id="u_lt", task_id="task_b"))
        results = list_events(db, "u_lt", task_id="task_a")
        assert len(results) == 2

    def test_list_events_non_existent_db(self):
        from memory.store import list_events

        assert list_events("/no/such/db.db", "u1") == []


# ════════════════════════════════════════════════════════════════════
# save_memory
# ════════════════════════════════════════════════════════════════════


class TestSaveMemory:
    """save_memory() 测试"""

    def test_save_memory(self, db):
        from memory.store import save_memory

        cand = make_candidate(
            user_id="u_mem",
            memory_type=MemoryType.PREFERENCE,
            key="export_format",
            content="偏好 PDF 导出",
        )
        record = save_memory(db, cand)
        assert record is not None
        assert isinstance(record, MemoryRecord)
        assert record.memory_id is not None
        assert record.memory_id.startswith("mem_")
        assert record.user_id == "u_mem"
        assert record.memory_type == MemoryType.PREFERENCE
        assert record.key == "export_format"
        assert record.content == "偏好 PDF 导出"
        assert record.status == MemoryStatus.ACTIVE
        assert record.version == 1
        assert isinstance(record.created_at, datetime)
        assert isinstance(record.updated_at, datetime)

    def test_save_memory_all_types(self, db):
        from memory.store import save_memory

        for mt in [MemoryType.PREFERENCE, MemoryType.KNOWLEDGE, MemoryType.WORKFLOW, MemoryType.CASE]:
            cand = make_candidate(
                user_id="u_types",
                memory_type=mt,
                key=f"key_{mt.value}",
                content=f"test {mt.value}",
            )
            record = save_memory(db, cand)
            assert record.memory_type == mt

    def test_save_memory_creates_parent_dir(self):
        from memory.store import init_db, save_memory

        tmpdir = tempfile.mkdtemp()
        nested = os.path.join(tmpdir, "deep", "nested", "mem.db")
        try:
            init_db(nested)
            cand = make_candidate()
            record = save_memory(nested, cand)
            assert record.memory_id is not None
            assert os.path.exists(nested)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_save_memory_with_scene_and_tags(self, db):
        from memory.store import save_memory

        cand = make_candidate(
            memory_type=MemoryType.KNOWLEDGE,
            key="scene_test",
            content="scene content",
            scenario=Scene.CODING,
            confidence=0.75,
            source="extracted",
            tags=["python", "coding"],
        )
        record = save_memory(db, cand)
        assert record.scenario == Scene.CODING
        assert record.confidence == 0.75
        assert record.source == "extracted"


# ════════════════════════════════════════════════════════════════════
# list_active_memories
# ════════════════════════════════════════════════════════════════════


class TestListActiveMemories:
    """list_active_memories() 测试"""

    def test_list_active(self, db):
        from memory.store import save_memory, list_active_memories

        save_memory(db, make_candidate(user_id="u_act", key="k1"))
        save_memory(db, make_candidate(user_id="u_act", key="k2"))
        save_memory(db, make_candidate(user_id="other", key="k3"))
        results = list_active_memories(db, "u_act")
        assert len(results) == 2
        assert all(isinstance(r, MemoryRecord) for r in results)

    def test_list_active_empty(self, db):
        from memory.store import list_active_memories

        assert list_active_memories(db, "u_nobody") == []

    def test_list_active_non_existent_db(self):
        from memory.store import list_active_memories

        assert list_active_memories("/no/such/db.db", "u1") == []

    def test_list_active_excludes_deleted(self, db):
        from memory.store import save_memory, list_active_memories, mark_memory_deleted

        r1 = save_memory(db, make_candidate(user_id="u_del", key="keep"))
        r2 = save_memory(db, make_candidate(user_id="u_del", key="delete_me"))
        assert len(list_active_memories(db, "u_del")) == 2

        mark_memory_deleted(db, r2.memory_id)
        active = list_active_memories(db, "u_del")
        assert len(active) == 1
        assert active[0].key == "keep"


# ════════════════════════════════════════════════════════════════════
# mark_memory_deleted
# ════════════════════════════════════════════════════════════════════


class TestMarkDeleted:
    """mark_memory_deleted() 测试"""

    def test_mark_deleted(self, db):
        from memory.store import save_memory, mark_memory_deleted, list_active_memories

        record = save_memory(db, make_candidate(user_id="u_md", key="to_delete"))
        assert len(list_active_memories(db, "u_md")) == 1

        mark_memory_deleted(db, record.memory_id)
        assert len(list_active_memories(db, "u_md")) == 0

    def test_mark_deleted_non_existent_memory(self, db):
        """删除不存在的 memory_id 不应报错。"""
        from memory.store import mark_memory_deleted

        mark_memory_deleted(db, "nonexistent_id")  # 不应抛出异常

    def test_mark_deleted_non_existent_db(self):
        """数据库不存在时不应报错。"""
        from memory.store import mark_memory_deleted

        mark_memory_deleted("/no/such/db.db", "mem_001")  # 不应抛出异常

    def test_mark_deleted_idempotent(self, db):
        """重复标记删除不报错。"""
        from memory.store import save_memory, mark_memory_deleted

        record = save_memory(db, make_candidate(user_id="u_idem", key="idem"))
        mark_memory_deleted(db, record.memory_id)
        mark_memory_deleted(db, record.memory_id)  # 第二次
