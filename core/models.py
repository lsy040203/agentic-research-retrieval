
"""
核心数据模型定义

用于 OS Agent Memory 系统全链路的数据结构。

数据流：
Agent Raw Payload
-> RawEvent
-> MemoryEvent
-> WorkingMemory
-> SessionSummary
-> MemoryCandidate
-> MemoryRecord

说明：
- RawEvent：Memory 系统接收到的原始事件封装，保留 Agent 原始 payload。
- MemoryEvent：经过数据接入层适配、清洗、标准化后的统一事件。
- WorkingMemory：短期记忆，表示当前会话/当前任务的上下文工作区。
- SessionSummary：中期记忆，表示会话级或任务级摘要。
- MemoryCandidate：候选记忆，表示抽取后但尚未正式入库的记忆。
- MemoryRecord：正式记忆，也就是长期记忆的存储形态。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .constants import EventType, MemoryType, MemoryStatus, Scene


@dataclass
class RawEvent:
    """
    Memory 系统接收到的原始事件封装。

    注意：
    RawEvent 不是标准化后的事件。
    它只是对 Agent 原始 payload 做第一层封装，
    补充 event_id、user_id、session_id、task_id、event_type、scenario、timestamp 等元信息。

    示例来源：
    - 用户消息
    - Agent 工具调用
    - 工具执行结果
    - 用户行为
    - 手动配置
    """

    event_id: str
    user_id: str
    session_id: str
    task_id: str
    event_type: EventType
    scenario: Scene
    timestamp: datetime
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "event_type": self.event_type.value,
            "scenario": self.scenario.value,
            "timestamp": self.timestamp.isoformat(),
            "payload": self.payload,
        }


@dataclass
class MemoryEvent:
    """
    标准化事件。

    MemoryEvent 是 ingestion 层对 RawEvent 进行适配、清洗、标准化后的结果。
    后续 extractors 不直接处理 RawEvent，而是统一处理 MemoryEvent。

    conversation 示例：
    - source = "conversation"
    - actor = "user"
    - content = "以后导出文件都用 PDF"

    tool_call 示例：
    - source = "tool_call"
    - actor = "agent"
    - tool_name = "wps_export"
    - input = {"file": "月报.docx", "format": "pdf"}

    tool_result 示例：
    - source = "tool_result"
    - actor = "tool"
    - tool_name = "wps_export"
    - output = {"file": "月报.pdf"}
    - success = True
    """

    event_id: str
    raw_event_id: str
    user_id: str
    session_id: str
    task_id: str
    event_type: EventType
    scenario: Scene

    # 事件来源，通常等于 conversation / tool_call / tool_result / user_behavior / user_config
    source: str

    # 事件主体，例如 user / agent / tool / system
    actor: str | None = None

    # 对话类事件的自然语言内容
    content: str | None = None

    # 工具类事件字段
    tool_name: str | None = None
    input: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)
    success: bool | None = None

    # 额外信息，例如文件路径、窗口标题、应用名、行为类型等
    metadata: dict[str, Any] = field(default_factory=dict)

    timestamp: datetime = field(default_factory=datetime.now)

    # 可选保存原始事件，方便调试和追溯
    raw_event: RawEvent | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "raw_event_id": self.raw_event_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "event_type": self.event_type.value,
            "scenario": self.scenario.value,
            "source": self.source,
            "actor": self.actor,
            "content": self.content,
            "tool_name": self.tool_name,
            "input": self.input,
            "output": self.output,
            "success": self.success,
            "metadata": self.metadata,
            "timestamp": self.timestamp.isoformat(),
            "raw_event": self.raw_event.to_dict() if self.raw_event else None,
        }


@dataclass
class WorkingMemory:
    """
    短期记忆。

    短期记忆不是一条抽象后的偏好或知识，
    而是当前会话/当前任务中的上下文工作区。

    它主要保存：
    - 最近 N 轮对话
    - 当前任务相关 MemoryEvent
    - 最近工具调用和工具结果
    - 当前任务状态，例如 task_goal、missing_slots、selected_tool 等

    生命周期：
    - 当前任务内
    - 当前会话内
    - 可在任务结束后清空或压缩为 SessionSummary
    """

    user_id: str
    session_id: str
    task_id: str | None = None

    # 当前任务或会话内的标准化事件
    events: list[MemoryEvent] = field(default_factory=list)

    # 最近对话消息，用于构造 prompt
    # 示例：{"role": "user", "content": "...", "timestamp": "..."}
    messages: list[dict[str, Any]] = field(default_factory=list)

    # 最近工具轨迹
    # 示例：{"tool_name": "wps_export", "input": {...}, "output": {...}, "success": True}
    tool_trace: list[dict[str, Any]] = field(default_factory=list)

    # 当前任务临时状态
    # 示例：{"task_goal": "生成月报", "missing_slots": ["format"], "selected_tool": "wps_export"}
    state: dict[str, Any] = field(default_factory=dict)

    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def add_event(self, event: MemoryEvent) -> None:
        self.events.append(event)
        self.updated_at = datetime.now()

    def add_message(self, role: str, content: str) -> None:
        self.messages.append(
            {
                "role": role,
                "content": content,
                "timestamp": datetime.now().isoformat(),
            }
        )
        self.updated_at = datetime.now()

    def add_tool_trace(
        self,
        tool_name: str,
        input: dict[str, Any],
        output: dict[str, Any] | None = None,
        success: bool | None = None,
    ) -> None:
        self.tool_trace.append(
            {
                "tool_name": tool_name,
                "input": input,
                "output": output or {},
                "success": success,
                "timestamp": datetime.now().isoformat(),
            }
        )
        self.updated_at = datetime.now()

    def update_state(self, key: str, value: Any) -> None:
        self.state[key] = value
        self.updated_at = datetime.now()

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "events": [event.to_dict() for event in self.events],
            "messages": self.messages,
            "tool_trace": self.tool_trace,
            "state": self.state,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass
class SessionSummary:
    """
    中期记忆。

    中期记忆不是长期偏好，也不是完整会话历史，
    而是对一个会话、任务或项目阶段的摘要。

    用途：
    - 当 WorkingMemory 太长时进行压缩
    - 保存任务阶段状态
    - 支持几小时到几天内的任务恢复
    - 后续可从 SessionSummary 中继续抽取长期 MemoryCandidate

    示例：
    “用户正在生成本月月报，已读取 sales.xlsx，并成功导出月报.pdf。
    本轮任务中用户使用了 PDF 导出流程。”
    """

    summary_id: str
    user_id: str
    session_id: str
    task_id: str | None
    scenario: Scene
    summary: str

    source_event_ids: list[str] = field(default_factory=list)

    status: MemoryStatus = MemoryStatus.ACTIVE

    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    # 中期记忆通常有过期时间，第一阶段可以先不用
    expires_at: datetime | None = None

    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary_id": self.summary_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "scenario": self.scenario.value,
            "summary": self.summary,
            "source_event_ids": self.source_event_ids,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "metadata": self.metadata,
        }


@dataclass
class MemoryCandidate:
    """
    候选记忆。

    MemoryCandidate 是 extractors 的输出，
    表示系统从 MemoryEvent 或 SessionSummary 中发现了可能值得长期保存的信息。

    它还不是正式记忆。
    后续需要经过：
    - 置信度判断
    - 冲突检测
    - 版本管理
    - 生命周期判断
    - 敏感信息过滤

    之后才会变成 MemoryRecord。
    """

    candidate_id: str
    user_id: str
    memory_type: MemoryType
    key: str
    content: str

    scenario: Scene = Scene.GLOBAL
    confidence: float = 0.8

    # 例如 extracted / user_confirmed / imported / configured
    source: str = "extracted"

    # 来源事件 ID，方便追溯
    source_events: list[str] = field(default_factory=list)

    # 来源中期摘要 ID，第一阶段可不用
    source_summaries: list[str] = field(default_factory=list)

    # 便于检索和遗忘
    tags: list[str] = field(default_factory=list)

    metadata: dict[str, Any] = field(default_factory=dict)

    created_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "user_id": self.user_id,
            "memory_type": self.memory_type.value,
            "key": self.key,
            "content": self.content,
            "scenario": self.scenario.value,
            "confidence": self.confidence,
            "source": self.source,
            "source_events": self.source_events,
            "source_summaries": self.source_summaries,
            "tags": self.tags,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class MemoryRecord:
    """
    正式记忆。

    MemoryRecord 是长期记忆的正式存储形态。
    它是 MemoryCandidate 经过记忆管理层处理后写入 SQLite / 向量库的结果。

    可表示：
    - 用户偏好
    - 外部知识
    - 工具经验
    - 历史案例
    - 工作流程
    - 可复用模板
    - 安全策略

    注意：
    代码中不再单独定义 LongTermMemory，
    避免和 MemoryRecord 重复。
    """

    memory_id: str
    user_id: str
    memory_type: MemoryType
    key: str
    content: str

    scenario: Scene = Scene.GLOBAL
    confidence: float = 0.8
    version: int = 1
    status: MemoryStatus = MemoryStatus.ACTIVE

    # 例如 extracted / user_confirmed / configured / imported
    source: str = "extracted"

    # 可追溯来源
    source_events: list[str] = field(default_factory=list)
    source_summaries: list[str] = field(default_factory=list)

    # 检索和遗忘用
    tags: list[str] = field(default_factory=list)

    # 版本关系，第一阶段可不用
    supersedes: str | None = None

    # 向量库中的 ID，第一阶段可不用
    vector_id: str | None = None

    metadata: dict[str, Any] = field(default_factory=dict)

    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "user_id": self.user_id,
            "memory_type": self.memory_type.value,
            "key": self.key,
            "content": self.content,
            "scenario": self.scenario.value,
            "confidence": self.confidence,
            "version": self.version,
            "status": self.status.value,
            "source": self.source,
            "source_events": self.source_events,
            "source_summaries": self.source_summaries,
            "tags": self.tags,
            "supersedes": self.supersedes,
            "vector_id": self.vector_id,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass
class RetrievalResult:
    """
    检索结果。

    RetrievalResult 是 retrieval 层返回给 Agent 或 API 层的结果。
    它不一定包含 MemoryRecord 的全部字段，只保留注入 Agent 所需的信息。
    """

    memory_id: str
    memory_type: MemoryType
    key: str
    content: str
    score: float
    scenario: Scene

    # 解释为什么召回这条记忆，便于调试和答辩展示
    reason: str | None = None

    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "memory_type": self.memory_type.value,
            "key": self.key,
            "content": self.content,
            "score": self.score,
            "scenario": self.scenario.value,
            "reason": self.reason,
            "metadata": self.metadata,
        }


@dataclass
class ForgetCommand:
    """
    遗忘命令。

    ForgetCommand 表示用户发出的自然语言遗忘请求。
    它只是命令，不应该直接混入执行结果。
    执行结果使用 ForgetLog 表示。
    """

    user_id: str
    instruction: str
    scenario: Scene = Scene.GLOBAL

    # 可选限制范围，例如只遗忘 preference / knowledge
    memory_types: list[MemoryType] = field(default_factory=list)

    requested_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "instruction": self.instruction,
            "scenario": self.scenario.value,
            "memory_types": [memory_type.value for memory_type in self.memory_types],
            "requested_at": self.requested_at.isoformat(),
        }


@dataclass
class ForgetLog:
    """
    遗忘日志。

    ForgetLog 表示一次遗忘操作的执行结果。
    用于审计、调试和评测。
    """

    log_id: str
    user_id: str
    instruction: str

    deleted_memory_ids: list[str] = field(default_factory=list)

    # 误删或未删可以在评测阶段分析
    matched_memory_ids: list[str] = field(default_factory=list)

    executed_at: datetime = field(default_factory=datetime.now)

    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "log_id": self.log_id,
            "user_id": self.user_id,
            "instruction": self.instruction,
            "matched_memory_ids": self.matched_memory_ids,
            "deleted_memory_ids": self.deleted_memory_ids,
            "executed_at": self.executed_at.isoformat(),
            "metadata": self.metadata,
        }

