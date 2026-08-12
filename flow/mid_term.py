"""
mid_term.py — 中期记忆 (SessionSummary) 管理

职责：
  - 将 WorkingMemory 压缩为 SessionSummary
  - 摘要的存储、查询和过期管理
  - 为长期记忆抽取提供素材

数据流：
  WorkingMemory (短期)
    → MidTermMemory.compress() / summarize()
    → SessionSummary (中期)
    → 被检索用于恢复上下文
    → 或被 promotion 抽取后晋升为 MemoryRecord
"""

import uuid
from datetime import datetime, timedelta
from typing import Any

from core.constants import MemoryStatus, Scene
from core.models import SessionSummary, WorkingMemory

_DEFAULT_SUMMARY_TTL = 86400 * 7  # 7 天


class MidTermMemory:
    """
    中期记忆管理器。

    compress()     — 自动统计生成结构化摘要
    summarize()    — 接受外部传入的自定义摘要文本
    """

    def __init__(self, default_ttl: int = _DEFAULT_SUMMARY_TTL) -> None:
        self._default_ttl = default_ttl
        self._summaries: dict[str, SessionSummary] = {}

    # ── 创建 ─────────────────────────────────────────────────────

    def compress(self, working_memory: WorkingMemory, scenario: Scene = Scene.UNKNOWN,
                 metadata: dict[str, Any] | None = None) -> SessionSummary:
        """将 WorkingMemory 压缩为结构化摘要。"""
        summary_text = self._build_summary_text(working_memory)
        source_ids = [e.event_id for e in working_memory.events if e.event_id]
        summary = SessionSummary(
            summary_id=f"sum_{uuid.uuid4().hex[:10]}",
            user_id=working_memory.user_id,
            session_id=working_memory.session_id,
            task_id=working_memory.task_id,
            scenario=scenario,
            summary=summary_text,
            source_event_ids=source_ids,
            status=MemoryStatus.ACTIVE,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            expires_at=datetime.now() + timedelta(seconds=self._default_ttl),
            metadata=metadata or {},
        )
        self._summaries[summary.summary_id] = summary
        return summary

    def summarize(self, working_memory: WorkingMemory, scenario: Scene = Scene.UNKNOWN,
                  custom_summary: str | None = None) -> SessionSummary:
        """用外部摘要文本创建 SessionSummary。"""
        text = custom_summary or self._build_summary_text(working_memory)
        source_ids = [e.event_id for e in working_memory.events if e.event_id]
        summary = SessionSummary(
            summary_id=f"sum_{uuid.uuid4().hex[:10]}",
            user_id=working_memory.user_id,
            session_id=working_memory.session_id,
            task_id=working_memory.task_id,
            scenario=scenario,
            summary=text,
            source_event_ids=source_ids,
            status=MemoryStatus.ACTIVE,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            expires_at=datetime.now() + timedelta(seconds=self._default_ttl),
            metadata={"generated_by": "summarize", **({"custom": True} if custom_summary else {})},
        )
        self._summaries[summary.summary_id] = summary
        return summary

    # ── 查询 ─────────────────────────────────────────────────────

    def get(self, summary_id: str) -> SessionSummary | None:
        return self._summaries.get(summary_id)

    def get_by_session(self, user_id: str, session_id: str,
                       active_only: bool = True) -> list[SessionSummary]:
        results = []
        for s in self._summaries.values():
            if s.user_id == user_id and s.session_id == session_id:
                if active_only and s.status != MemoryStatus.ACTIVE:
                    continue
                results.append(s)
        return sorted(results, key=lambda s: s.created_at)

    def list_active(self, user_id: str) -> list[SessionSummary]:
        return [s for s in self._summaries.values()
                if s.user_id == user_id and s.status == MemoryStatus.ACTIVE]

    # ── 生命周期 ─────────────────────────────────────────────────

    def mark_expired(self, summary_id: str) -> bool:
        s = self._summaries.get(summary_id)
        if s is None:
            return False
        s.status = MemoryStatus.EXPIRED
        s.updated_at = datetime.now()
        return True

    def mark_deleted(self, summary_id: str) -> bool:
        s = self._summaries.get(summary_id)
        if s is None:
            return False
        s.status = MemoryStatus.DELETED
        s.updated_at = datetime.now()
        return True

    def cleanup_expired(self) -> int:
        now = datetime.now()
        to_remove = [
            sid for sid, s in self._summaries.items()
            if s.status in (MemoryStatus.EXPIRED, MemoryStatus.DELETED)
            or (s.expires_at and s.expires_at < now)
        ]
        for sid in to_remove:
            del self._summaries[sid]
        return len(to_remove)

    # ── 内部 ─────────────────────────────────────────────────────

    def _build_summary_text(self, wm: WorkingMemory) -> str:
        lines: list[str] = []
        lines.append(f"Session: {wm.session_id}")
        if wm.task_id:
            lines.append(f"Task: {wm.task_id}")

        type_counts: dict[str, int] = {}
        for e in wm.events:
            type_counts[e.event_type.value] = type_counts.get(e.event_type.value, 0) + 1
        if type_counts:
            lines.append("Events: " + "; ".join(f"{k}: {v}" for k, v in type_counts.items()))

        tool_seq = [t.get("tool_name", "?") for t in wm.tool_trace]
        if tool_seq:
            lines.append("Tool sequence: " + " → ".join(tool_seq))

        if wm.messages:
            last = wm.messages[-1]
            c = last.get("content", "")
            if len(c) > 100:
                c = c[:100] + "..."
            lines.append(f"Last message ({last.get('role')}): {c}")

        if wm.state:
            lines.append("Task state: " + "; ".join(f"{k}={v}" for k, v in wm.state.items()))

        return "\n".join(lines)
