"""
事件接入接口

职责：
- 接收 OS Agent / Mock Agent 的原始事件
- 调用 ingestion 层完成 RawEvent -> MemoryEvent
- 调用 memory.store 保存事件

不要在这里写复杂抽取逻辑。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from os_agent_memory.api.schemas import (
    APIResponse,
    EventBatchResponse,
    EventItem,
    EventResponse,
    PostEventsBatchRequest,
    PostEventsRequest,
)
from os_agent_memory.ingestion.adapter import (
    raw_dict_to_raw_event,
    raw_event_to_memory_event,
)
from os_agent_memory.ingestion.validator import validate_memory_event
from os_agent_memory.memory.store import list_events, save_event


router = APIRouter(prefix="/memory", tags=["events"])


@router.post("/events", response_model=APIResponse)
def post_event(req: PostEventsRequest) -> APIResponse:
    """
    写入单条事件。

    流程：
    API Request
    -> RawEvent
    -> MemoryEvent
    -> validate
    -> save_event
    """

    raw_dict = req.model_dump()

    raw_event = raw_dict_to_raw_event(raw_dict)
    memory_event = raw_event_to_memory_event(raw_event)

    valid, errors = validate_memory_event(memory_event)
    if not valid:
        raise HTTPException(status_code=400, detail={"errors": errors})

    save_event(memory_event)

    data = EventResponse(
        raw_event_id=raw_event.event_id,
        memory_event_id=memory_event.event_id,
        user_id=memory_event.user_id,
        session_id=memory_event.session_id,
        task_id=memory_event.task_id,
        event_type=memory_event.event_type,
        status="saved",
    )

    return APIResponse(data=data.model_dump())


@router.post("/events/batch", response_model=APIResponse)
def post_events_batch(req: PostEventsBatchRequest) -> APIResponse:
    """
    批量写入事件。

    用于：
    - Demo 初始化
    - 评测数据导入
    - 离线日志回放
    """

    saved_event_ids: list[str] = []
    failed_count = 0

    for item in req.events:
        try:
            raw_event = raw_dict_to_raw_event(item.model_dump())
            memory_event = raw_event_to_memory_event(raw_event)

            valid, _ = validate_memory_event(memory_event)
            if not valid:
                failed_count += 1
                continue

            save_event(memory_event)
            saved_event_ids.append(memory_event.event_id)

        except Exception:
            failed_count += 1

    data = EventBatchResponse(
        total=len(req.events),
        saved_count=len(saved_event_ids),
        failed_count=failed_count,
        event_ids=saved_event_ids,
    )

    return APIResponse(data=data.model_dump())


@router.get("/events", response_model=APIResponse)
def get_events(
    user_id: str,
    session_id: str | None = None,
    task_id: str | None = None,
) -> APIResponse:
    """
    查询事件。

    用于调试和追溯：
    - 查看某个用户的事件
    - 查看某个 session 的事件
    - 查看某个 task 的事件
    """

    events = list_events(
        user_id=user_id,
        session_id=session_id,
        task_id=task_id,
    )

    items = [
        EventItem(
            event_id=event.event_id,
            user_id=event.user_id,
            session_id=event.session_id,
            task_id=event.task_id,
            event_type=event.event_type,
            scenario=event.scenario,
            timestamp=event.timestamp,
        ).model_dump()
        for event in events
    ]

    return APIResponse(
        data={
            "total": len(items),
            "events": items,
        }
    )