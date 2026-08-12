"""
cleaner.py — 数据清洗（去噪、去重、标准化）

函数签名：
  remove_noise(events)        — 去除空白内容事件，标记/过滤异常格式
  deduplicate_events(events)  — 按 event_id 去重（保留首次出现）
  normalize_content(content)  — 标准化文本（trim、规范化换行）

清洗规则：
  ✓ 去除空白内容事件（content 为空或仅空白字符）
  ✓ 按 event_id 去重
  ✓ 标准化文本（trim、规范化换行）
  ✓ 标记和过滤异常格式（content 含异常控制字符等）
  ✓ 补全缺失的可选字段（actor、tool_name、metadata 等）

验收点：
  [] 清洗后事件数量 ≤ 输入数量
  [] 重复事件被删除
  [] content 清洁统一
"""

import re
from copy import deepcopy
from typing import Any

from core.constants import EventType
from core.models import MemoryEvent


# ── 常量 ────────────────────────────────────────────────────────────

# 控制字符中保留 \t \n \r，其余视为异常
_CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# 连续的空白字符（含换行）归并
_WHITESPACE_PATTERN = re.compile(r"[ \t]+")
# 连续的空行（2个及以上 \n 连在一起）归并为最多 2 个
_MULTI_NEWLINE_PATTERN = re.compile(r"\n{3,}")

# 各 EventType 对应的默认 actor
_DEFAULT_ACTOR: dict[EventType, str] = {
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


# ── 内容标准化 ──────────────────────────────────────────────────────


def normalize_content(content: str) -> str:
    """
    标准化文本内容。

    - strip 首尾空白
    - 移除异常控制字符（保留 \\t \\n \\r）
    - 连续空白字符（空格/Tab）归并为单个空格
    - 连续空行（3 个及以上 \\n）归并为 2 个 \\n

    Parameters
    ----------
    content : str
        原始文本。

    Returns
    -------
    str
        标准化后的文本。
    """
    if not content:
        return content

    # 移除异常控制字符
    text = _CONTROL_CHAR_PATTERN.sub("", content)

    # 连续空白（空格/Tab）归并
    text = _WHITESPACE_PATTERN.sub(" ", text)

    # 连续空行归并
    text = _MULTI_NEWLINE_PATTERN.sub("\n\n", text)

    # 首尾 trim
    text = text.strip()

    return text


# ── 异常格式检测 ────────────────────────────────────────────────────


class AbnormalFlag:
    """异常标记。"""

    # content 全为空白
    BLANK_CONTENT = "blank_content"
    # content 含不可打印控制字符（已由 normalize_content 移除，留作标记）
    HAS_CONTROL_CHARS = "has_control_chars"
    # content 超长（>10000 字符，可能是 dump）
    CONTENT_TOO_LONG = "content_too_long"
    # tool_name 缺失（对 TOOL_CALL / TOOL_RESULT 事件）
    MISSING_TOOL_NAME = "missing_tool_name"


_ABNORMAL_FLAGS = frozenset({
    AbnormalFlag.BLANK_CONTENT,
    AbnormalFlag.HAS_CONTROL_CHARS,
    AbnormalFlag.CONTENT_TOO_LONG,
    AbnormalFlag.MISSING_TOOL_NAME,
})

_CONTENT_TOO_LONG_THRESHOLD = 10_000


def _detect_abnormal_flags(event: MemoryEvent) -> list[str]:
    """
    检测事件的异常标记。不修改事件本身。

    Returns
    -------
    list[str]
        异常标记列表。
    """
    flags: list[str] = []

    content = event.content or ""

    # 空白内容
    if not content.strip():
        # CONVERSATION / USER_FEEDBACK 等依赖 content 的事件，空白值得注意
        if event.event_type in (EventType.CONVERSATION, EventType.USER_FEEDBACK):
            flags.append(AbnormalFlag.BLANK_CONTENT)
    else:
        # 仅当非空时才检测控制字符，避免误报
        if _CONTROL_CHAR_PATTERN.search(content):
            flags.append(AbnormalFlag.HAS_CONTROL_CHARS)

        # 超长
        if len(content) > _CONTENT_TOO_LONG_THRESHOLD:
            flags.append(AbnormalFlag.CONTENT_TOO_LONG)

    # TOOL_CALL / TOOL_RESULT 缺少 tool_name
    if event.event_type in (EventType.TOOL_CALL, EventType.TOOL_RESULT):
        if not event.tool_name or not event.tool_name.strip():
            flags.append(AbnormalFlag.MISSING_TOOL_NAME)

    return flags


# ── 可选字段补全 ────────────────────────────────────────────────────


def _fill_optional_fields(event: MemoryEvent) -> MemoryEvent:
    """
    补全缺失的可选字段（actor、tool_name、metadata 等）。

    返回新对象（浅拷贝）。
    """
    result = deepcopy(event)

    # actor 缺失时根据 event_type 补默认值
    if not result.actor:
        result.actor = _DEFAULT_ACTOR.get(result.event_type)

    # tool_name 缺失时补空字符串
    if result.tool_name is None:
        result.tool_name = ""

    # metadata 不会是 None（field 有 default_factory=dict），
    # 但如果是显式设为 None 则修复
    if result.metadata is None:
        result.metadata = {}

    return result


# ── 去噪 ────────────────────────────────────────────────────────────


def remove_noise(events: list[MemoryEvent]) -> list[MemoryEvent]:
    """
    去除噪声事件，并补全可选字段。

    清洗规则：
    - 去除 content 全为空白的事件（对 CONVERSATION / USER_FEEDBACK）
    - 异常标记写入 event.metadata["abnormal_flags"]
    - 补全缺失的可选字段（actor、tool_name、metadata）

    Parameters
    ----------
    events : list[MemoryEvent]
        待清洗的事件列表。

    Returns
    -------
    list[MemoryEvent]
        清洗后的事件列表（新列表，元素可能被替换为新对象）。
    """
    cleaned: list[MemoryEvent] = []

    for event in events:
        # 检测异常
        flags = _detect_abnormal_flags(event)

        # 过滤：空白内容的 CONVERSATION / USER_FEEDBACK 事件直接丢弃
        if AbnormalFlag.BLANK_CONTENT in flags:
            continue

        # 补全可选字段
        event = _fill_optional_fields(event)

        # 异常标记写入 metadata（不修改原始标记）
        if flags:
            event = deepcopy(event)
            existing = event.metadata.get("abnormal_flags", [])
            if isinstance(existing, list):
                event.metadata["abnormal_flags"] = existing + flags
            else:
                event.metadata["abnormal_flags"] = flags

        cleaned.append(event)

    return cleaned


# ── 去重 ────────────────────────────────────────────────────────────


def deduplicate_events(events: list[MemoryEvent]) -> list[MemoryEvent]:
    """
    按 event_id 去重。

    保留事件列表中**首次出现**的 event_id，后续重复的丢弃。

    Parameters
    ----------
    events : list[MemoryEvent]
        待去重的事件列表（保持原顺序）。

    Returns
    -------
    list[MemoryEvent]
        去重后的事件列表。
    """
    seen: set[str] = set()
    deduped: list[MemoryEvent] = []

    for event in events:
        if event.event_id not in seen:
            seen.add(event.event_id)
            deduped.append(event)

    return deduped
