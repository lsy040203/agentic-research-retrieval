"""
adapter.py — RawEvent → MemoryEvent 转换

职责：
根据 RawEvent.event_type 将原始 payload 中的字段映射到
MemoryEvent 的结构化字段上。

转换规则（按 event_type）：

| EventType       | source          | actor  | 核心提取字段                    |
|-----------------|-----------------|--------|-------------------------------|
| CONVERSATION    | "conversation"  | user   | content                       |
| TOOL_CALL       | "tool_call"     | agent  | tool_name, input              |
| TOOL_RESULT     | "tool_result"   | tool   | tool_name, output, success    |
| USER_BEHAVIOR   | "user_behavior" | user   | content → metadata            |
| USER_CONFIG     | "user_config"   | user   | content                       |
| TASK_PLAN       | "task_plan"     | agent  | content                       |
| TASK_TRACE      | "task_trace"    | agent  | metadata                      |
| TASK_SUMMARY    | "task_summary"  | system | content                       |
| SYSTEM_CONTEXT  | "system_context"| system | metadata                      |
| DOCUMENT_IMPORT | "document_import"| system| content, metadata             |
| USER_FEEDBACK   | "user_feedback" | user   | content                       |

典型的 payload 格式：

conversation:
  {"content": "...", "actor": "user"}

tool_call:
  {"tool_name": "wps_export", "input": {"file": "月报.docx", "format": "pdf"}}

tool_result:
  {"tool_name": "wps_export", "output": {"file": "月报.pdf"}, "success": true}

user_behavior:
  {"action": "open_file", "file": "report.docx", "app": "wps"}
"""

from typing import Any

from core.constants import EventType
from core.models import RawEvent, MemoryEvent
from .collector import create_raw_event


# ── 每个 event_type 对应的 source 和 actor ──────────────────────────

_SOURCE_MAP: dict[EventType, str] = {
    EventType.CONVERSATION: "conversation",
    EventType.TOOL_CALL: "tool_call",
    EventType.TOOL_RESULT: "tool_result",
    EventType.USER_BEHAVIOR: "user_behavior",
    EventType.USER_CONFIG: "user_config",
    EventType.TASK_PLAN: "task_plan",
    EventType.TASK_TRACE: "task_trace",
    EventType.TASK_SUMMARY: "task_summary",
    EventType.SYSTEM_CONTEXT: "system_context",
    EventType.DOCUMENT_IMPORT: "document_import",
    EventType.USER_FEEDBACK: "user_feedback",
}

_ACTOR_MAP: dict[EventType, str] = {
    EventType.CONVERSATION: "user",
    EventType.TOOL_CALL: "agent",
    EventType.TOOL_RESULT: "tool",
    EventType.USER_BEHAVIOR: "user",
    EventType.USER_CONFIG: "user",
    EventType.TASK_PLAN: "agent",
    EventType.TASK_TRACE: "agent",
    EventType.TASK_SUMMARY: "system",
    EventType.SYSTEM_CONTEXT: "system",
    EventType.DOCUMENT_IMPORT: "system",
    EventType.USER_FEEDBACK: "user",
}


def raw_dict_to_raw_event(payload: dict[str, Any]) -> RawEvent:
    """Adapt an API request dictionary through the established raw-event parser."""

    return create_raw_event(payload)


# ── payload → MemoryEvent 字段 提取器 ───────────────────────────────


def _extract_content(payload: dict[str, Any]) -> str | None:
    """从 payload 中提取自然语言内容。"""
    for key in ("content", "text", "message", "description"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _extract_tool_name(payload: dict[str, Any]) -> str | None:
    """从 payload 中提取工具名称。"""
    for key in ("tool_name", "name", "tool", "function"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
        # 如果 tool/function 是 dict，尝试取 .name
        if isinstance(value, dict):
            inner = value.get("name") or value.get("tool_name")
            if isinstance(inner, str):
                return inner
    return None


def _extract_input(payload: dict[str, Any]) -> dict[str, Any]:
    """从 payload 中提取工具调用入参。"""
    for key in ("input", "arguments", "params", "parameters", "args"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _extract_output(payload: dict[str, Any]) -> dict[str, Any]:
    """从 payload 中提取工具执行结果。"""
    for key in ("output", "result", "response", "data"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _extract_success(payload: dict[str, Any]) -> bool | None:
    """从 payload 中提取执行成功状态。"""
    for key in ("success", "is_success", "ok", "status"):
        value = payload.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in ("true", "false", "success", "error", "failed"):
            return value.lower() in ("true", "success")
    return None


def _build_metadata(payload: dict[str, Any], exclude_keys: set[str]) -> dict[str, Any]:
    """构建元数据：payload 中除去已提取字段的剩余部分。"""
    return {k: v for k, v in payload.items() if k not in exclude_keys}


# ── 单条转换逻辑 ────────────────────────────────────────────────────


def map_source(event_type: EventType) -> str:
    """
    根据 EventType 返回对应的 MemoryEvent.source 值。

    Parameters
    ----------
    event_type : EventType
        事件类型。

    Returns
    -------
    str
        转换后的 source 字符串。
    """
    return _SOURCE_MAP.get(event_type, event_type.value)


def raw_event_to_memory_event(raw_event: RawEvent) -> MemoryEvent:
    """
    将单条 RawEvent 转换为 MemoryEvent。

    Parameters
    ----------
    raw_event : RawEvent
        原始事件。

    Returns
    -------
    MemoryEvent
        标准化后的事件。
    """
    source = _SOURCE_MAP.get(raw_event.event_type, raw_event.event_type.value)
    actor = _ACTOR_MAP.get(raw_event.event_type, None)

    payload = raw_event.payload
    event_type = raw_event.event_type

    # ── 各 event_type 的分字段提取 ──
    content: str | None = None
    tool_name: str | None = None
    tool_input: dict[str, Any] = {}
    tool_output: dict[str, Any] = {}
    success: bool | None = None
    metadata: dict[str, Any] = {}

    if event_type == EventType.CONVERSATION:
        content = _extract_content(payload) or ""
        # 排期表规约：CONVERSATION → actor="user"，不覆写
        metadata = _build_metadata(payload, {"content", "text", "message", "actor", "role"})

    elif event_type == EventType.TOOL_CALL:
        tool_name = _extract_tool_name(payload) or ""
        tool_input = _extract_input(payload)
        metadata = _build_metadata(
            payload,
            {"tool_name", "name", "tool", "function", "input", "arguments", "params", "parameters", "args"},
        )

    elif event_type == EventType.TOOL_RESULT:
        tool_name = _extract_tool_name(payload) or ""
        tool_output = _extract_output(payload)
        success = _extract_success(payload)
        metadata = _build_metadata(
            payload,
            {"tool_name", "name", "tool", "function", "output", "result", "response", "data", "success", "is_success", "ok", "status"},
        )

    elif event_type == EventType.USER_BEHAVIOR:
        content = _extract_content(payload) or ""
        metadata = payload  # 用户行为的大部分字段都应进 metadata

    elif event_type == EventType.USER_CONFIG:
        content = _extract_content(payload) or ""
        # 如果 payload 中有 config 嵌套对象，展开到 content 和 metadata
        config_obj = payload.get("config") or payload.get("settings")
        if isinstance(config_obj, dict):
            if not content:
                content = str(config_obj)
            metadata = _build_metadata(payload, {"config", "settings", "content", "text"})
            metadata["config"] = config_obj
        else:
            metadata = _build_metadata(payload, {"content", "text"})

    elif event_type == EventType.TASK_PLAN:
        content = payload.get("plan") or payload.get("content") or ""
        metadata = _build_metadata(payload, {"plan", "content", "text"})

    elif event_type == EventType.TASK_TRACE:
        content = _extract_content(payload)
        metadata = payload  # 任务轨迹整体作为 metadata

    elif event_type == EventType.TASK_SUMMARY:
        content = payload.get("summary") or payload.get("content") or ""
        metadata = _build_metadata(payload, {"summary", "content", "text"})

    elif event_type == EventType.SYSTEM_CONTEXT:
        metadata = payload  # 系统上下文整体作为 metadata

    elif event_type == EventType.DOCUMENT_IMPORT:
        content = payload.get("content") or payload.get("text") or ""
        metadata = _build_metadata(payload, {"content", "text"})

    elif event_type == EventType.USER_FEEDBACK:
        content = _extract_content(payload) or ""
        metadata = _build_metadata(payload, {"content", "text", "feedback", "message"})

    else:
        # 兜底：未知 event_type 全部放入 metadata
        metadata = payload

    return MemoryEvent(
        event_id=raw_event.event_id,
        raw_event_id=raw_event.event_id,
        user_id=raw_event.user_id,
        session_id=raw_event.session_id,
        task_id=raw_event.task_id,
        event_type=raw_event.event_type,
        scenario=raw_event.scenario,
        source=source,
        actor=actor,
        content=content,
        tool_name=tool_name,
        input=tool_input,
        output=tool_output,
        success=success,
        metadata=metadata,
        timestamp=raw_event.timestamp,
        raw_event=raw_event,
    )


# ── 批量转换 ────────────────────────────────────────────────────────


def adapt_events(raw_events: list[RawEvent]) -> list[MemoryEvent]:
    """
    批量转换 RawEvent 列表为 MemoryEvent 列表。

    Parameters
    ----------
    raw_events : list[RawEvent]
        原始事件列表。

    Returns
    -------
    list[MemoryEvent]
        标准化事件列表（顺序与输入一致）。
    """
    return [raw_event_to_memory_event(event) for event in raw_events]
