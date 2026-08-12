"""
promotion.py — 记忆晋升逻辑 (MemoryCandidate → MemoryRecord)

职责：
  - 将经过验证的事件/候选记忆晋升为正式长期记忆
  - 置信度门槛过滤（④ 低质拦截的落地应用）
  - 晋升原因记录

数据流：
  MemoryEvent / SessionSummary
    → 抽取器 → MemoryCandidate
    → promote_candidate() → MemoryRecord (写入 SQLite)
                  ↓
            低置信度 → 返回 None（被拦截）
"""

import uuid
from datetime import datetime
from typing import Any

from core.constants import MemoryType, PromotionReason, Scene
from core.models import MemoryCandidate, MemoryEvent, MemoryRecord, SessionSummary
from memory.store import save_memory

_DEFAULT_CONFIDENCE_THRESHOLD = 0.5


class MemoryPromotion:
    """记忆晋升管理器。"""

    def __init__(self, db_path: str, confidence_threshold: float = _DEFAULT_CONFIDENCE_THRESHOLD) -> None:
        self._db_path = db_path
        self._threshold = confidence_threshold

    # ── 单条晋升 ─────────────────────────────────────────────────

    def promote_candidate(self, candidate: MemoryCandidate,
                          reason: PromotionReason | None = None) -> MemoryRecord | None:
        """晋升候选记忆。低于置信度门槛返回 None。"""
        if candidate.confidence < self._threshold:
            return None
        try:
            return save_memory(self._db_path, candidate)
        except Exception:
            return None

    # ── 从 MemoryEvent 快捷创建并晋升 ────────────────────────────

    def from_event(self, event: MemoryEvent, memory_type: MemoryType, key: str,
                   content: str, confidence: float | None = None,
                   tags: list[str] | None = None) -> MemoryRecord | None:
        candidate = MemoryCandidate(
            candidate_id=f"cand_{uuid.uuid4().hex[:10]}",
            user_id=event.user_id,
            memory_type=memory_type,
            key=key,
            content=content,
            scenario=event.scenario,
            confidence=confidence or 0.8,
            source=event.source,
            source_events=[event.event_id] if event.event_id else [],
            tags=tags or [],
            metadata={"event_type": event.event_type.value, "actor": event.actor},
            created_at=datetime.now(),
        )
        return self.promote_candidate(candidate)

    # ── 从 SessionSummary ────────────────────────────────────────

    def from_summary(self, summary: SessionSummary,
                     memory_type: MemoryType = MemoryType.SESSION_SUMMARY,
                     confidence: float = 0.7) -> MemoryRecord | None:
        candidate = MemoryCandidate(
            candidate_id=f"cand_{uuid.uuid4().hex[:10]}",
            user_id=summary.user_id,
            memory_type=memory_type,
            key=f"session_summary:{summary.session_id}",
            content=summary.summary,
            scenario=summary.scenario,
            confidence=confidence,
            source="session_summary",
            source_events=summary.source_event_ids,
            source_summaries=[summary.summary_id],
            tags=[summary.scenario.value],
            metadata={"summary_id": summary.summary_id},
            created_at=datetime.now(),
        )
        return self.promote_candidate(candidate)

    # ── 批量晋升 ─────────────────────────────────────────────────

    def promote_batch(self, candidates: list[MemoryCandidate]) -> list[MemoryRecord]:
        return [r for c in candidates if (r := self.promote_candidate(c)) is not None]

    # ── 配置 ─────────────────────────────────────────────────────

    @property
    def threshold(self) -> float:
        return self._threshold

    @threshold.setter
    def threshold(self, value: float) -> None:
        self._threshold = max(0.0, min(1.0, value))
