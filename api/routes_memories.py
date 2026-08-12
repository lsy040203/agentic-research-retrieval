"""
记忆相关接口

职责：
- 抽取候选记忆
- 保存正式记忆
- 查询长期记忆
- 检索相关记忆
- 执行自然语言遗忘

注意：
这里是 HTTP 路由层，不要把复杂业务逻辑全写在这里。
"""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException

from os_agent_memory.api.schemas import (
    APIResponse,
    CandidateItem,
    ExtractionResponse,
    ForgetResponse,
    MemoryItem,
    MemoryListResponse,
    PostExtractRequest,
    PostForgetRequest,
    PostRetrieveRequest,
    RetrievalResponse,
    RetrievedMemoryItem,
)
from os_agent_memory.extractors.knowledge_extractor import extract_knowledge
from os_agent_memory.extractors.preference_extractor import extract_preferences
from os_agent_memory.extractors.workflow_extractor import extract_workflows
from os_agent_memory.memory.forgetter import (
    forget_by_instruction,
    preview_forget_by_instruction,
)
from os_agent_memory.memory.store import (
    list_active_memories,
    list_events,
    save_memory,
)
from os_agent_memory.retrieval.hybrid_retriever import retrieve


router = APIRouter(prefix="/memory", tags=["memories"])


@router.post("/extract", response_model=APIResponse)
def post_extract(req: PostExtractRequest) -> APIResponse:
    """
    从事件中抽取候选记忆。

    第一阶段流程：
    list_events
    -> preference_extractor
    -> knowledge_extractor
    -> workflow_extractor
    -> save_memory
    """

    if not req.session_id and not req.task_id:
        raise HTTPException(
            status_code=400,
            detail="session_id 和 task_id 至少提供一个",
        )

    events = list_events(
        user_id=req.user_id,
        session_id=req.session_id,
        task_id=req.task_id,
    )

    candidates = []
    candidates.extend(extract_preferences(events))
    candidates.extend(extract_knowledge(events))
    candidates.extend(extract_workflows(events))

    if req.memory_types:
        allowed = set(req.memory_types)
        candidates = [
            candidate
            for candidate in candidates
            if candidate.memory_type in allowed
        ]

    saved_memory_ids: list[str] = []

    if req.commit:
        for candidate in candidates:
            record = save_memory(candidate)
            saved_memory_ids.append(record.memory_id)

    candidate_items = [
        CandidateItem(
            candidate_id=candidate.candidate_id,
            memory_type=candidate.memory_type,
            key=candidate.key,
            content=candidate.content,
            scenario=candidate.scenario,
            confidence=candidate.confidence,
            source_events=candidate.source_events,
            tags=candidate.tags,
        ).model_dump()
        for candidate in candidates
    ]

    data = ExtractionResponse(
        user_id=req.user_id,
        session_id=req.session_id,
        task_id=req.task_id,
        candidates_count=len(candidates),
        saved_count=len(saved_memory_ids),
        candidates=candidate_items,
        saved_memory_ids=saved_memory_ids,
    )

    return APIResponse(data=data.model_dump())


@router.get("/memories", response_model=APIResponse)
def get_memories(user_id: str) -> APIResponse:
    """
    查询用户当前 active 记忆。

    第一阶段只查 active。
    后面可以扩展 status、memory_type、scenario 过滤。
    """

    records = list_active_memories(user_id=user_id)

    items = [
        MemoryItem(
            memory_id=record.memory_id,
            memory_type=record.memory_type,
            key=record.key,
            content=record.content,
            scenario=record.scenario,
            status=record.status,
            confidence=record.confidence,
            version=record.version,
            tags=record.tags,
            created_at=record.created_at,
            updated_at=record.updated_at,
        ).model_dump()
        for record in records
    ]

    data = MemoryListResponse(
        user_id=user_id,
        total=len(items),
        memories=items,
    )

    return APIResponse(data=data.model_dump())


@router.post("/retrieve", response_model=APIResponse)
def post_retrieve(req: PostRetrieveRequest) -> APIResponse:
    """
    检索相关记忆。

    Agent 在任务规划、工具选择、生成回复前调用这个接口。
    """

    start = time.perf_counter()

    results = retrieve(
        user_id=req.user_id,
        query=req.query,
        scenario=req.scenario,
        top_k=req.top_k,
        memory_types=req.memory_types,
        include_statuses=req.include_statuses,
        debug=req.debug,
    )

    latency_ms = (time.perf_counter() - start) * 1000

    items = [
        RetrievedMemoryItem(
            memory_id=result.memory_id,
            memory_type=result.memory_type,
            key=result.key,
            content=result.content,
            score=result.score,
            scenario=result.scenario,
            confidence=result.metadata.get("confidence"),
            tags=result.metadata.get("tags", []),
            reason=result.reason,
        ).model_dump()
        for result in results
    ]

    data = RetrievalResponse(
        user_id=req.user_id,
        query=req.query,
        scenario=req.scenario,
        top_k=req.top_k,
        retrieval_mode=req.retrieval_mode,
        results=items,
        latency_ms=latency_ms,
    )

    return APIResponse(data=data.model_dump())


@router.post("/forget", response_model=APIResponse)
def post_forget(req: PostForgetRequest) -> APIResponse:
    """
    执行自然语言精准遗忘。

    dry_run=True 时只预览，不真正删除。
    """

    if req.dry_run:
        matched_ids = preview_forget_by_instruction(
            user_id=req.user_id,
            instruction=req.instruction,
            scenario=req.scenario,
            memory_types=req.memory_types,
        )
        deleted_ids: list[str] = []
        log_id = None
    else:
        result = forget_by_instruction(
            user_id=req.user_id,
            instruction=req.instruction,
            scenario=req.scenario,
            memory_types=req.memory_types,
            forget_mode=req.forget_mode,
        )
        matched_ids = result.matched_memory_ids
        deleted_ids = result.deleted_memory_ids
        log_id = result.log_id

    data = ForgetResponse(
        user_id=req.user_id,
        instruction=req.instruction,
        scenario=req.scenario,
        dry_run=req.dry_run,
        matched_count=len(matched_ids),
        deleted_count=len(deleted_ids),
        matched_memory_ids=matched_ids,
        deleted_memory_ids=deleted_ids,
        log_id=log_id,
    )

    return APIResponse(data=data.model_dump())