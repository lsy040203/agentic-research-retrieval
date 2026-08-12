"""
FastAPI 服务入口

负责：
- 创建 FastAPI app
- 注册 API 路由
- 提供健康检查接口

启动命令：
uvicorn os_agent_memory.api.server:app --reload
"""

from __future__ import annotations

from fastapi import FastAPI

from os_agent_memory.api.routes_eval import router as eval_router
from os_agent_memory.api.routes_events import router as events_router
from os_agent_memory.api.routes_memories import router as memories_router
from os_agent_memory.api.schemas import APIResponse, HealthResponse


app = FastAPI(
    title="OS Agent Memory API",
    description="面向 OS Agent 的多源融合记忆优化与管理系统",
    version="0.1.0",
)


# 注册路由
app.include_router(events_router)
app.include_router(memories_router)
app.include_router(eval_router)


@app.get("/memory/health", response_model=APIResponse)
def health() -> APIResponse:
    """
    健康检查接口。

    用于确认服务是否正常启动。
    """

    data = HealthResponse()

    return APIResponse(data=data.model_dump())


@app.get("/", response_model=APIResponse)
def root() -> APIResponse:
    """
    根路径。

    方便浏览器直接访问时确认服务状态。
    """

    return APIResponse(
        data={
            "service": "os_agent_memory",
            "message": "OS Agent Memory API is running",
            "docs": "/docs",
            "health": "/memory/health",
        }
    )
