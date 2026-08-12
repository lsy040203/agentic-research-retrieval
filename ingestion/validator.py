"""
validator.py — MemoryEvent 数据质量校验

函数签名：
  validate_memory_event(event)            — 通用校验，覆盖全部规则
  validate_conversation_event(event)      — conversation 专属：必须有 content
  validate_tool_result_event(event)       — tool_result 专属：必须有 success
  compute_event_confidence(event)          — 事件置信度打分 (0.0~1.0)
  is_low_quality(event, threshold=0.5)     — 低质量拦截判断

校验规则：
  ✓ user_id 非空
  ✓ event_type 在 EventType 枚举范围
  ✓ scenario 在 Scene 枚举范围
  ✓ source 非空
  ✓ TOOL_RESULT 事件必须有 success 字段
  ✓ CONVERSATION 事件必须有 content
  ✓ timestamp 有效且不超过当前时间

置信度规则（④ 低质拦截）：
  ✓ CONVERSATION content < 3 字符 → 低质
  ✓ CONVERSATION 含模糊表述（"随便"/"你看着办"等）→ 降分
  ✓ TOOL_RESULT output 为空 → 低质
  ✓ TOOL_CALL input 为空 → 低质
  ✓ USER_BEHAVIOR metadata 无有效内容 → 低质
  ✓ 超长内容（>5000 字符）→ 适度降分
"""

import re
from datetime import datetime

from core.constants import EventType, Scene
from core.models import MemoryEvent


# ── 模糊表述关键词（命中任意一条即降分） ─────────────────────────────

_FUZZY_PATTERNS: list[re.Pattern] = [
    re.compile(r"随便"),
    re.compile(r"你看着办"),
    re.compile(r"都行"),
    re.compile(r"无所谓"),
    re.compile(r"看着来"),
    re.compile(r"something"),
    re.compile(r"whatever"),
    re.compile(r"idk", re.IGNORECASE),
    re.compile(r"不知道"),
    re.compile(r"我不清楚"),
]


# ── 通用校验 ────────────────────────────────────────────────────────


def validate_memory_event(event: MemoryEvent) -> tuple[bool, list[str]]:
    """
    对 MemoryEvent 执行全量校验，覆盖所有规则。

    Parameters
    ----------
    event : MemoryEvent
        待校验的标准化事件。

    Returns
    -------
    tuple[bool, list[str]]
        (是否通过, 错误信息列表)。通过时 errors 为空列表。
    """
    errors: list[str] = []

    # 1. user_id 非空
    if not event.user_id or not event.user_id.strip():
        errors.append("user_id is empty")

    # 2. event_type 在 EventType 枚举范围（空值/非法值本身就不可能是 EventType）
    if not isinstance(event.event_type, EventType):
        errors.append(f"event_type is not a valid EventType: {event.event_type}")

    # 3. scenario 在 Scene 枚举范围
    if not isinstance(event.scenario, Scene):
        errors.append(f"scenario is not a valid Scene: {event.scenario}")

    # 4. source 非空
    if not event.source or not event.source.strip():
        errors.append("source is empty")

    # 5. timestamp 有效且不超过当前时间
    _validate_timestamp(event, errors)

    # 6. 类型专属校验
    if event.event_type == EventType.TOOL_RESULT:
        valid, sub_errors = validate_tool_result_event(event)
        errors.extend(sub_errors)
    elif event.event_type == EventType.CONVERSATION:
        valid, sub_errors = validate_conversation_event(event)
        errors.extend(sub_errors)

    return (len(errors) == 0, errors)


# ── 类型专属校验 ────────────────────────────────────────────────────


def validate_tool_result_event(event: MemoryEvent) -> tuple[bool, list[str]]:
    """
    校验 TOOL_RESULT 事件：必须有 success 字段。

    Parameters
    ----------
    event : MemoryEvent
        待校验的标准化事件。

    Returns
    -------
    tuple[bool, list[str]]
        (是否通过, 错误信息列表)。
    """
    errors: list[str] = []

    if event.success is None:
        errors.append("TOOL_RESULT event must have a 'success' field")

    return (len(errors) == 0, errors)


def validate_conversation_event(event: MemoryEvent) -> tuple[bool, list[str]]:
    """
    校验 CONVERSATION 事件：必须有 content。

    Parameters
    ----------
    event : MemoryEvent
        待校验的标准化事件。

    Returns
    -------
    tuple[bool, list[str]]
        (是否通过, 错误信息列表)。
    """
    errors: list[str] = []

    if not event.content or not event.content.strip():
        errors.append("CONVERSATION event must have non-empty 'content'")

    return (len(errors) == 0, errors)


# ── 内部工具 ────────────────────────────────────────────────────────


def _validate_timestamp(event: MemoryEvent, errors: list[str]) -> None:
    """校验 timestamp 有效且不超过当前时间（允许 5 秒误差）。"""
    if not isinstance(event.timestamp, datetime):
        errors.append("timestamp is not a datetime object")
        return

    if event.timestamp > datetime.now():
        # 允许 5 秒时钟偏差，避免因毫秒级误差误报
        delta = (event.timestamp - datetime.now()).total_seconds()
        if delta > 5:
            errors.append(
                f"timestamp ({event.timestamp.isoformat()}) is in the future "
                f"({delta:.1f}s ahead)"
            )


# ── 置信度评分 & 低质拦截（④）─────────────────────────────────────────


def compute_event_confidence(event: MemoryEvent) -> float:
    """
    计算事件的置信度分数 (0.0 ~ 1.0)。

    分值含义：
      1.0         — 完全可信，高质量事件
      0.7 ~ 0.99 — 基本可信，略有不足
      0.4 ~ 0.69 — 中等质量，建议审查
      < 0.4      — 低质量，建议拦截

    扣分规则：
      - CONVERSATION 内容过短 (<3 字符):        -0.35
      - CONVERSATION 含模糊表述:                -0.25
      - TOOL_RESULT output 为空字典:            -0.30
      - TOOL_CALL input 为空字典:               -0.25
      - USER_BEHAVIOR metadata 无有效字段:      -0.30
      - TOOL_RESULT success=False:              -0.20
      - content 超长 (>5000 字符):              -0.15
      - actor 缺失:                             -0.10
      - timestamp 缺失（默认值 datetime.now）:   -0.10

    Parameters
    ----------
    event : MemoryEvent
        待评估的标准化事件。

    Returns
    -------
    float
        置信度分数 (0.0 ~ 1.0)。
    """
    score = 1.0
    content = (event.content or "").strip()
    event_type = event.event_type

    # ── CONVERSATION ──
    if event_type == EventType.CONVERSATION:
        if len(content) < 3:
            score -= 0.35
        else:
            for pattern in _FUZZY_PATTERNS:
                if pattern.search(content):
                    score -= 0.25
                    break

    # ── TOOL_RESULT ──
    elif event_type == EventType.TOOL_RESULT:
        if not event.output:
            score -= 0.30
        if event.success is False:
            score -= 0.20

    # ── TOOL_CALL ──
    elif event_type == EventType.TOOL_CALL:
        if not event.input:
            score -= 0.25

    # ── USER_BEHAVIOR ──
    elif event_type == EventType.USER_BEHAVIOR:
        has_meaningful = any(
            v for k, v in (event.metadata or {}).items()
            if isinstance(v, str) and v.strip()
        )
        if not has_meaningful:
            score -= 0.30

    # ── 全局扣分项 ──
    if len(content) > 5000:
        score -= 0.15

    if not event.actor:
        score -= 0.10

    return max(0.0, min(1.0, score))


def is_low_quality(
    event: MemoryEvent,
    threshold: float = 0.5,
) -> bool:
    """
    判断事件是否为低质量，建议拦截。

    是对 compute_event_confidence 的便捷封装，
    用于 ingestion 流水线中快速过滤。

    Parameters
    ----------
    event : MemoryEvent
        待判断的标准化事件。
    threshold : float
        阈值，低于该值视为低质量（默认 0.5）。

    Returns
    -------
    bool
        True 表示低质量，建议拦截；False 表示质量可接受。
    """
    return compute_event_confidence(event) < threshold
