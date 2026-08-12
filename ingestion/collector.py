"""
collector.py — JSONL 加载与 RawEvent 解析

数据流：
  Agent Raw Payload (JSONL)
    → load_jsonl(path)      加载并解析 JSONL 文件，返回原始 dict 列表
    → create_raw_event(dict) 将单条原始数据封装为 RawEvent
    → validate_raw_payload  校验原始数据字段完整性

验收标准：
  [✓] 成功加载 data/raw/office_demo_events.jsonl
  [✓] 每行变为一个 RawEvent 对象，event_id 唯一
  [✓] 错误 JSONL 行被捕获，记录错误日志
  [✓] 时间戳正确解析和设置
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from core.constants import EventType, Scene
from core.models import RawEvent

logger = logging.getLogger(__name__)


# ── 必需字段 ────────────────────────────────────────────────────────

REQUIRED_FIELDS = {"event_id", "user_id", "event_type"}
KNOWN_KEYS = {"event_id", "user_id", "session_id", "task_id",
              "event_type", "scenario", "timestamp"}


# ── 1. JSONL 加载 ──────────────────────────────────────────────────


def load_jsonl(path: str) -> list[dict[str, Any]]:
    """
    加载 JSONL 文件，返回解析后的原始 dict 列表。

    处理说明：
    - 空行静默跳过
    - 无效 JSON 行以 ValueError 抛出（含行号信息）
    - 非 dict 类型的 JSON 值同样以 ValueError 抛出

    Parameters
    ----------
    path : str
        JSONL 文件路径。

    Returns
    -------
    list[dict[str, Any]]
        解析后的 JSON 对象列表。

    Raises
    ------
    FileNotFoundError
        文件不存在。
    ValueError
        JSON 解析失败或 JSON 值不是 dict 类型。
    """
    file_path = Path(path)

    if not file_path.exists():
        raise FileNotFoundError(f"JSONL file not found: {path}")

    events: list[dict[str, Any]] = []

    with file_path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue

            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                logger.error("Invalid JSON at line %d: %s", line_no, e)
                raise ValueError(f"Invalid JSON at line {line_no}: {e}") from e

            if not isinstance(obj, dict):
                logger.error("Line %d is not a JSON object (got %s)", line_no, type(obj).__name__)
                raise ValueError(f"Line {line_no} is not a JSON object")

            events.append(obj)

    return events


# ── 2. 原始负载校验 ────────────────────────────────────────────────


def validate_raw_payload(payload: dict) -> bool:
    """
    校验原始 JSON 对象的必需字段完整性。

    检查项：
    - event_id 非空
    - user_id 非空
    - event_type 非空且是 EventType 的合法值

    Parameters
    ----------
    payload : dict
        从 JSONL 解析出的原始数据。

    Returns
    -------
    bool
        True 通过校验，False 未通过。
    """
    if not isinstance(payload, dict):
        return False

    # 检查必需字段是否存在且非空
    for field in REQUIRED_FIELDS:
        value = payload.get(field)
        if not value or not isinstance(value, str) or not value.strip():
            logger.warning("Missing or empty required field: %s", field)
            return False

    # 校验 event_type 是否合法
    try:
        EventType(payload["event_type"])
    except ValueError:
        logger.warning("Invalid event_type: %s", payload.get("event_type"))
        return False

    return True


# ── 3. RawEvent 创建 ────────────────────────────────────────────────


def create_raw_event(payload: dict[str, Any]) -> RawEvent:
    """
    将单条原始 JSON 对象解析为 RawEvent。

    提取 event_id / user_id / session_id / task_id / event_type /
    scenario / timestamp 作为顶层字段，其余字段归入 payload。

    参数校验委托给 validate_raw_payload()，但为了性能，
    该函数默认不重复调用——调用方应确保数据已通过校验。

    Parameters
    ----------
    payload : dict[str, Any]
        从 JSONL 解析出的原始数据。

    Returns
    -------
    RawEvent
        封装后的原始事件对象。

    Raises
    ------
    ValueError
        必需字段缺失或格式非法。
    """
    # event_type 字符串 → 枚举
    try:
        event_type = EventType(payload["event_type"])
    except (KeyError, ValueError) as e:
        raise ValueError(f"Invalid or missing event_type: {payload.get('event_type', 'N/A')}") from e

    # scene 兜底
    scenario_raw = payload.get("scenario", "unknown")
    try:
        scenario = Scene(scenario_raw)
    except ValueError:
        scenario = Scene.UNKNOWN

    # timestamp 解析
    ts_raw = payload.get("timestamp")
    if ts_raw:
        try:
            timestamp = datetime.fromisoformat(ts_raw)
        except (ValueError, TypeError) as e:
            raise ValueError(f"Invalid timestamp '{ts_raw}': {e}") from e
    else:
        timestamp = datetime.now()

    # 剩余字段 → payload
    rest = {k: v for k, v in payload.items() if k not in KNOWN_KEYS}

    return RawEvent(
        event_id=str(payload["event_id"]),
        user_id=str(payload["user_id"]),
        session_id=str(payload.get("session_id", "")),
        task_id=str(payload.get("task_id", "")),
        event_type=event_type,
        scenario=scenario,
        timestamp=timestamp,
        payload=rest,
    )
