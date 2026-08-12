"""
SQLite 存储层
"""
import sqlite3
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.config import DEFAULT_DB_PATH, DEFAULT_EVENTS_DB_PATH, MEMORY_STORE_PATH
from core.models import MemoryEvent, MemoryCandidate, MemoryRecord, RawEvent
from core.constants import MemoryStatus, MemoryType, Scene, EventType


def init_db(db_path: str) -> None:
    """初始化 SQLite 数据库"""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 创建 memories 表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            memory_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            memory_type TEXT NOT NULL,
            key TEXT NOT NULL,
            content TEXT NOT NULL,
            scenario TEXT DEFAULT 'global',
            confidence REAL DEFAULT 0.8,
            version INTEGER DEFAULT 1,
            status TEXT DEFAULT 'active',
            source TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            deleted_at TEXT
        )
    """)

    # 创建 events 表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS events (
            event_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            scenario TEXT NOT NULL,
            source TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            raw_event TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()


def save_event(db_path: str, event: MemoryEvent) -> str:
    """保存事件到 SQLite"""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    event_id = event.raw_event.event_id
    cursor.execute("""
        INSERT INTO events
        (event_id, user_id, session_id, task_id, event_type, scenario, source, content, timestamp, raw_event)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        event_id,
        event.user_id,
        event.session_id,
        event.task_id,
        event.event_type.value,
        event.scenario.value,
        event.source,
        event.content,
        event.raw_event.timestamp.isoformat(),
        json.dumps(event.raw_event.to_dict()),
    ))

    conn.commit()
    conn.close()
    return event_id


# ── 反序列化辅助 ────────────────────────────────────────────────────


def _dict_to_memory_event(data: dict) -> MemoryEvent:
    """从 to_dict() 输出的 dict 重建 MemoryEvent 对象。"""
    raw_event_data = data.get("raw_event")
    raw_event_obj = None
    if raw_event_data:
        raw_event_obj = RawEvent(
            event_id=raw_event_data["event_id"],
            user_id=raw_event_data["user_id"],
            session_id=raw_event_data["session_id"],
            task_id=raw_event_data["task_id"],
            event_type=EventType(raw_event_data["event_type"]),
            scenario=Scene(raw_event_data.get("scenario", "unknown")),
            timestamp=datetime.fromisoformat(raw_event_data["timestamp"]),
            payload=raw_event_data.get("payload", {}),
        )

    return MemoryEvent(
        event_id=data["event_id"],
        raw_event_id=data["raw_event_id"],
        user_id=data["user_id"],
        session_id=data["session_id"],
        task_id=data["task_id"],
        event_type=EventType(data["event_type"]),
        scenario=Scene(data["scenario"]),
        source=data["source"],
        actor=data.get("actor"),
        content=data.get("content"),
        tool_name=data.get("tool_name"),
        input=data.get("input", {}),
        output=data.get("output", {}),
        success=data.get("success"),
        metadata=data.get("metadata", {}),
        timestamp=datetime.fromisoformat(data["timestamp"]),
        raw_event=raw_event_obj,
    )


# ── 新增 Phase 1 函数 ──────────────────────────────────────────────


def insert_event(db_path: str, event: MemoryEvent) -> str:
    """
    插入单条事件到 SQLite。

    将 MemoryEvent 完整序列化为 JSON 存入 raw_event 列，
    同时填充顶层结构化列以便 SQL 层面过滤。

    Parameters
    ----------
    db_path : str
        数据库文件路径。
    event : MemoryEvent
        待插入的标准化事件。

    Returns
    -------
    str
        插入成功的事件 ID（等于 event.event_id）。
    """
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO events
            (event_id, user_id, session_id, task_id, event_type, scenario,
             source, content, timestamp, raw_event)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            event.event_id,
            event.user_id,
            event.session_id,
            event.task_id,
            event.event_type.value,
            event.scenario.value,
            event.source,
            event.content or "",
            event.timestamp.isoformat(),
            json.dumps(event.to_dict()),
        ))
        conn.commit()
        return event.event_id
    except sqlite3.IntegrityError:
        conn.rollback()
        raise ValueError(
            f"Event with event_id '{event.event_id}' already exists"
        )
    finally:
        conn.close()


def insert_event_batch(db_path: str, events: list[MemoryEvent]) -> int:
    """
    批量插入事件，保证事务完整性。

    所有事件在同一个事务中插入：
    - 全部成功 → commit，返回成功条数
    - 任意一条失败 → rollback，抛出异常

    Parameters
    ----------
    db_path : str
        数据库文件路径。
    events : list[MemoryEvent]
        待插入的事件列表。

    Returns
    -------
    int
        成功插入的事件数量。
    """
    if not events:
        return 0

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        for event in events:
            cursor.execute("""
                INSERT INTO events
                (event_id, user_id, session_id, task_id, event_type, scenario,
                 source, content, timestamp, raw_event)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event.event_id,
                event.user_id,
                event.session_id,
                event.task_id,
                event.event_type.value,
                event.scenario.value,
                event.source,
                event.content or "",
                event.timestamp.isoformat(),
                json.dumps(event.to_dict()),
            ))
        conn.commit()
        return len(events)
    except sqlite3.IntegrityError:
        conn.rollback()
        raise ValueError("Duplicate event_id in batch insert")
    except sqlite3.Error:
        conn.rollback()
        raise
    finally:
        conn.close()


def query_events_by_session(
    db_path: str, session_id: str
) -> list[MemoryEvent]:
    """
    按 session_id 查询事件，返回完整的 MemoryEvent 对象列表。

    从 raw_event JSON 列反序列化还原全部字段
    （tool_name / input / output / success / actor / metadata 等）。

    Parameters
    ----------
    db_path : str
        数据库文件路径。
    session_id : str
        会话 ID。

    Returns
    -------
    list[MemoryEvent]
        匹配的事件列表（按时间戳升序）。
    """
    if not Path(db_path).exists():
        return []

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT raw_event FROM events WHERE session_id = ? ORDER BY timestamp",
            (session_id,),
        )
        rows = cursor.fetchall()
        return [_dict_to_memory_event(json.loads(row[0])) for row in rows]
    finally:
        conn.close()


def count_events(db_path: str, user_id: str) -> int:
    """
    统计指定用户的事件总数。

    Parameters
    ----------
    db_path : str
        数据库文件路径。
    user_id : str
        用户 ID。

    Returns
    -------
    int
        事件总数。
    """
    if not Path(db_path).exists():
        return 0

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM events WHERE user_id = ?", (user_id,)
        )
        return cursor.fetchone()[0]
    finally:
        conn.close()


# ── Phase 0 遗留函数 ────────────────────────────────────────────────


def list_events(db_path: str, user_id: str, task_id: Optional[str] = None) -> list[MemoryEvent]:
    """查询事件"""
    if not Path(db_path).exists():
        return []

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    if task_id:
        cursor.execute("SELECT * FROM events WHERE user_id = ? AND task_id = ?", (user_id, task_id))
    else:
        cursor.execute("SELECT * FROM events WHERE user_id = ?", (user_id,))

    rows = cursor.fetchall()
    conn.close()

    # 简化版：只返回原始数据，由调用方处理转换
    return rows


def save_memory(db_path: str, candidate: MemoryCandidate) -> MemoryRecord:
    """保存候选记忆为正式记忆"""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    memory_id = f"mem_{uuid.uuid4().hex[:10]}"
    now = datetime.now().isoformat()

    cursor.execute("""
        INSERT INTO memories
        (memory_id, user_id, memory_type, key, content, scenario, confidence, version, status, source, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        memory_id,
        candidate.user_id,
        candidate.memory_type.value,
        candidate.key,
        candidate.content,
        candidate.scenario.value,
        candidate.confidence,
        1,  # version
        MemoryStatus.ACTIVE.value,
        candidate.source,
        now,
        now,
    ))

    conn.commit()
    conn.close()

    return MemoryRecord(
        memory_id=memory_id,
        user_id=candidate.user_id,
        memory_type=candidate.memory_type,
        key=candidate.key,
        content=candidate.content,
        scenario=candidate.scenario,
        confidence=candidate.confidence,
        source=candidate.source,
        created_at=datetime.fromisoformat(now),
        updated_at=datetime.fromisoformat(now),
    )


def list_active_memories(db_path: str, user_id: str) -> list[MemoryRecord]:
    """查询活跃记忆"""
    if not Path(db_path).exists():
        return []

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT memory_id, user_id, memory_type, key, content, scenario, confidence, version, status, source, created_at, updated_at
        FROM memories
        WHERE user_id = ? AND status = 'active'
    """, (user_id,))

    rows = cursor.fetchall()
    conn.close()

    records = []
    for row in rows:
        record = MemoryRecord(
            memory_id=row[0],
            user_id=row[1],
            memory_type=MemoryType(row[2]),
            key=row[3],
            content=row[4],
            scenario=Scene(row[5]),
            confidence=row[6],
            version=row[7],
            status=MemoryStatus(row[8]),
            source=row[9],
            created_at=datetime.fromisoformat(row[10]),
            updated_at=datetime.fromisoformat(row[11]),
        )
        records.append(record)

    return records


def mark_memory_deleted(db_path: str, memory_id: str) -> None:
    """标记记忆为已删除"""
    if not Path(db_path).exists():
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    now = datetime.now().isoformat()
    cursor.execute("""
        UPDATE memories
        SET status = ?, deleted_at = ?
        WHERE memory_id = ?
    """, (MemoryStatus.DELETED.value, now, memory_id))

    conn.commit()
    conn.close()
