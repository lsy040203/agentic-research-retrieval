"""研究记忆检索的纯过滤与确定性排序策略。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence

from core.constants import ResearchMemoryStatus
from core.research_models import ResearchMemory, ScopeKey


class ResearchPolicy:
    """对研究记忆执行作用域隔离、适用性过滤和确定性冲突消解。"""

    def is_usable(self, scope: ScopeKey, memory: ResearchMemory) -> bool:
        """判断记忆能否在目标作用域内检索，不修改输入对象。"""

        # 五维 ScopeKey 必须完全一致，避免跨团队、项目、仓库、分支或实验环境泄漏。
        if memory.scope != scope:
            return False
        if memory.status is not ResearchMemoryStatus.PUBLISHED:
            return False

        # 未声明环境限制视为通用；声明后仅允许精确命中当前实验环境。
        environments = self._applicability(memory).get("experiment_environments")
        return environments is None or (
            isinstance(environments, list)
            and scope.experiment_environment in environments
        )

    def retrieval_confidence(
        self,
        scope: ScopeKey,
        memory: ResearchMemory,
        now: datetime | None = None,
    ) -> float:
        """按固定权重计算 0 至 1 的检索置信度，且不修改记忆。"""

        # 调用方可注入时间以获得可复现结果；生产调用默认使用当前 UTC 时间。
        captured_now = self._normalize_utc(now or datetime.now(timezone.utc))
        # 分量依次为来源可靠性、发布状态、环境匹配、新鲜度和证据完整度。
        score = (
            0.35 * self._source_reliability(memory)
            + 0.25 * self._status_score(memory)
            + 0.20 * self._environment_score(scope, memory)
            + 0.10 * self._freshness_score(memory.updated_at, captured_now)
            + 0.10 * self._evidence_completeness(memory)
        )
        return min(1.0, max(0.0, score))

    def filter_and_rank(
        self, scope: ScopeKey, memories: Sequence[ResearchMemory]
    ) -> list[ResearchMemory]:
        """过滤不可用项，并为每个有效冲突键保留一个确定性优胜项。"""

        # 一次检索固定一个时间基准，确保同批候选的置信度计算可比较。
        captured_now = datetime.now(timezone.utc)
        ungrouped: list[ResearchMemory] = []
        conflict_groups: dict[str, list[tuple[ResearchMemory, float]]] = {}
        for memory in memories:
            if not self.is_usable(scope, memory):
                continue
            score = self.retrieval_confidence(scope, memory, now=captured_now)
            conflict_key = self._applicability(memory).get("conflict_key")
            # 仅非空白字符串参与冲突组；缺失或空白键的可用记忆均保留。
            if isinstance(conflict_key, str) and conflict_key.strip():
                conflict_groups.setdefault(conflict_key, []).append((memory, score))
            else:
                ungrouped.append(memory)

        winners = [
            min(group, key=self._conflict_sort_key)[0]
            for group in conflict_groups.values()
        ]
        return [*ungrouped, *winners]

    def _applicability(self, memory: ResearchMemory) -> dict[str, Any]:
        return memory.applicability if isinstance(memory.applicability, dict) else {}

    def _source_reliability(self, memory: ResearchMemory) -> float:
        sources = [ref for ref in memory.source_refs if isinstance(ref, str) and ref]
        if not sources:
            return 0.0
        if len(sources) >= 2:
            return 1.0
        return 0.6

    def _status_score(self, memory: ResearchMemory) -> float:
        return 1.0 if memory.status is ResearchMemoryStatus.PUBLISHED else 0.0

    def _environment_score(self, scope: ScopeKey, memory: ResearchMemory) -> float:
        environments = self._applicability(memory).get("experiment_environments")
        if environments is None:
            return 1.0
        if isinstance(environments, list) and scope.experiment_environment in environments:
            return 1.0
        return 0.0

    def _freshness_score(self, updated_at: datetime, now: datetime) -> float:
        normalized = self._normalize_utc(updated_at)
        age_days = max(0, (now.date() - normalized.date()).days)
        if age_days <= 183:
            return 1.0
        return max(0.0, 1.0 - (age_days - 183) / 365)

    def _evidence_completeness(self, memory: ResearchMemory) -> float:
        applicability = self._applicability(memory)
        checks = (
            bool(memory.source_refs),
            isinstance(applicability.get("locator"), str),
            bool(applicability.get("verification_log")),
            bool(applicability.get("experiment_id")),
        )
        return sum(checks) / len(checks)

    def _conflict_sort_key(
        self, candidate: tuple[ResearchMemory, float]
    ) -> tuple[float, float, str, float]:
        memory, score = candidate
        updated_at = self._normalize_utc(memory.updated_at)
        # 优先高原始置信度、再优先较新 UTC 时间、最后以 ID 稳定决胜。
        return (-float(memory.confidence), -updated_at.timestamp(), memory.memory_id, -score)

    def _normalize_utc(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
