"""
short_term.py — 短期记忆 (WorkingMemory) 管理

职责：
  - 创建和获取会话的 WorkingMemory
  - 添加事件/消息/工具轨迹到工作区
  - 控制工作区大小（超出上限时 trim）
  - 过期清理（闲置超时的会话）

数据流：
  MemoryEvent → ShortTermMemory.add_event()
              → WorkingMemory.events
              → 后续可被 mid_term 压缩为 SessionSummary
              → 或直接被 promotion 抽取 MemoryCandidate
"""

from datetime import datetime, timedelta
from typing import Any

from core.models import MemoryEvent, WorkingMemory


# 默认配置
_DEFAULT_MAX_EVENTS = 50
_DEFAULT_MAX_MESSAGES = 30
_DEFAULT_IDLE_TIMEOUT = 3600  # 1 小时


class ShortTermMemory:
    """
    短期记忆管理器。

    维护 WorkingMemory 实例池，提供增删查改和过期清理操作。
    当前使用内存字典存储，后续可扩展为 Redis 等后端。
    """

    def __init__(
        self,
        max_events: int = _DEFAULT_MAX_EVENTS,
        max_messages: int = _DEFAULT_MAX_MESSAGES,
        idle_timeout: int = _DEFAULT_IDLE_TIMEOUT,
    ) -> None:
        self._max_events = max_events
        self._max_messages = max_messages
        self._idle_timeout = idle_timeout
        self._stores: dict[str, WorkingMemory] = {}

    # ── key 工具 ──────────────────────────────────────────────────

    @staticmethod
    def _key(user_id: str, session_id: str) -> str:
        return f"{user_id}:{session_id}"

    # ── 获取/创建 ─────────────────────────────────────────────────

    def get_or_create(
        self,
        user_id: str,
        session_id: str,
        task_id: str | None = None,
    ) -> WorkingMemory:
        key = self._key(user_id, session_id)
        wm = self._stores.get(key)
        if wm is not None:
            wm.updated_at = datetime.now()
            return wm
        wm = WorkingMemory(user_id=user_id, session_id=session_id, task_id=task_id)
        self._stores[key] = wm
        return wm

    def get(self, user_id: str, session_id: str) -> WorkingMemory | None:
        return self._stores.get(self._key(user_id, session_id))

    # ── 写入 ─────────────────────────────────────────────────────

    def add_event(self, user_id: str, session_id: str, event: MemoryEvent) -> WorkingMemory:
        wm = self.get_or_create(user_id, session_id)
        wm.add_event(event)
        self._trim_if_needed(wm)
        return wm

    def add_message(self, user_id: str, session_id: str, role: str, content: str) -> WorkingMemory:
        wm = self.get_or_create(user_id, session_id)
        wm.add_message(role, content)
        self._trim_messages_if_needed(wm)
        return wm

    def add_tool_trace(
        self, user_id: str, session_id: str, tool_name: str,
        input: dict[str, Any], output: dict[str, Any] | None = None,
        success: bool | None = None,
    ) -> WorkingMemory:
        wm = self.get_or_create(user_id, session_id)
        wm.add_tool_trace(tool_name, input, output, success)
        return wm

    def update_state(self, user_id: str, session_id: str, key: str, value: Any) -> WorkingMemory:
        wm = self.get_or_create(user_id, session_id)
        wm.update_state(key, value)
        return wm

    # ── 查询 ─────────────────────────────────────────────────────

    def list_active_sessions(self, user_id: str | None = None) -> list[WorkingMemory]:
        if user_id:
            return [wm for k, wm in self._stores.items() if k.startswith(f"{user_id}:")]
        return list(self._stores.values())

    def count_events(self, user_id: str, session_id: str) -> int:
        wm = self.get(user_id, session_id)
        return len(wm.events) if wm else 0

    def get_context(self, user_id: str, session_id: str, max_events: int = 20) -> list[MemoryEvent]:
        wm = self.get(user_id, session_id)
        if not wm:
            return []
        return wm.events[-max_events:]

    # ── 清理 ─────────────────────────────────────────────────────

    def remove_session(self, user_id: str, session_id: str) -> bool:
        key = self._key(user_id, session_id)
        if key in self._stores:
            del self._stores[key]
            return True
        return False

    def cleanup_idle(self, max_idle_seconds: int | None = None) -> int:
        timeout = max_idle_seconds or self._idle_timeout
        cutoff = datetime.now() - timedelta(seconds=timeout)
        to_remove = [k for k, wm in self._stores.items() if wm.updated_at < cutoff]
        for k in to_remove:
            del self._stores[k]
        return len(to_remove)

    def clear_all(self) -> int:
        count = len(self._stores)
        self._stores.clear()
        return count

    # ── 内部 trim ────────────────────────────────────────────────

    def _trim_if_needed(self, wm: WorkingMemory) -> None:
        while len(wm.events) > self._max_events:
            wm.events.pop(0)
        wm.updated_at = datetime.now()

    def _trim_messages_if_needed(self, wm: WorkingMemory) -> None:
        while len(wm.messages) > self._max_messages:
            wm.messages.pop(0)
        wm.updated_at = datetime.now()
