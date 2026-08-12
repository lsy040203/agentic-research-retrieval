"""
long_term.py — 长期记忆操作

职责：
  - 封装 store.py 的长期记忆接口（MemoryRecord CRUD）
  - 提供高层业务语义（保存、按 key 查询、过滤、删除）
  - 对接 retrieval 层

数据流：
  MemoryCandidate → promotion.py → MemoryRecord → LongTermMemory
    → save() / list_active() / get_by_key() / filter() / delete()
"""

from core.constants import MemoryType, Scene
from core.models import MemoryCandidate, MemoryRecord
from memory.store import list_active_memories, mark_memory_deleted, save_memory


class LongTermMemory:
    """长期记忆管理器，封装 store.py 的业务操作。"""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    # ── 写入 ─────────────────────────────────────────────────────

    def save(self, candidate: MemoryCandidate) -> MemoryRecord | None:
        try:
            return save_memory(self._db_path, candidate)
        except Exception:
            return None

    def save_from_event(self, candidate: MemoryCandidate, event_id: str) -> MemoryRecord | None:
        candidate.source_events.append(event_id)
        return self.save(candidate)

    # ── 查询 ─────────────────────────────────────────────────────

    def list_active(self, user_id: str) -> list[MemoryRecord]:
        return list_active_memories(self._db_path, user_id)

    def get_by_key(self, user_id: str, key: str) -> MemoryRecord | None:
        for r in list_active_memories(self._db_path, user_id):
            if r.key == key:
                return r
        return None

    def filter(self, user_id: str, memory_type: MemoryType | None = None,
               scenario: Scene | None = None, min_confidence: float = 0.0) -> list[MemoryRecord]:
        results: list[MemoryRecord] = []
        for r in list_active_memories(self._db_path, user_id):
            if memory_type and r.memory_type != memory_type:
                continue
            if scenario and r.scenario != scenario:
                continue
            if r.confidence < min_confidence:
                continue
            results.append(r)
        return results

    def count(self, user_id: str) -> int:
        return len(self.list_active(user_id))

    # ── 删除 ─────────────────────────────────────────────────────

    def delete(self, memory_id: str) -> bool:
        try:
            mark_memory_deleted(self._db_path, memory_id)
            return True
        except Exception:
            return False

    def delete_by_key(self, user_id: str, key: str) -> int:
        count = 0
        for r in self.list_active(user_id):
            if r.key == key and self.delete(r.memory_id):
                count += 1
        return count

    # ── 检查 ─────────────────────────────────────────────────────

    def exists(self, user_id: str, key: str) -> bool:
        return self.get_by_key(user_id, key) is not None
