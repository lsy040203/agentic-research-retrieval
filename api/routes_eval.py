"""
评测接口

职责：
- 运行偏好抽取、检索、遗忘等评测
- 返回指标结果
- 支撑比赛要求中的量化评测机制

第一阶段可以先只返回基础指标：
- preference F1
- retrieval Recall@K
- forget success rate
"""

from __future__ import annotations

from fastapi import APIRouter

from os_agent_memory.api.schemas import (
    APIResponse,
    EvalRunResponse,
    MetricItem,
    PostEvalRunRequest,
)
from os_agent_memory.core.constants import EvaluationTask, MetricName
from os_agent_memory.evaluation.forgetting_eval import run_forgetting_eval
from os_agent_memory.evaluation.preference_eval import run_preference_eval
from os_agent_memory.evaluation.retrieval_eval import run_retrieval_eval


router = APIRouter(prefix="/memory/eval", tags=["evaluation"])


@router.post("/run", response_model=APIResponse)
def post_eval_run(req: PostEvalRunRequest) -> APIResponse:
    """
    运行评测。

    如果 req.tasks 为空，则默认运行第一阶段全部基础评测。
    """

    tasks = req.tasks or [
        EvaluationTask.PREFERENCE_EXTRACTION,
        EvaluationTask.RETRIEVAL,
        EvaluationTask.FORGETTING,
    ]

    metrics: list[MetricItem] = []

    if EvaluationTask.PREFERENCE_EXTRACTION in tasks:
        result = run_preference_eval(dataset_path=req.dataset_path)

        metrics.append(
            MetricItem(
                name=MetricName.PRECISION,
                value=result.get("precision", 0.0),
                description="偏好抽取 Precision",
            )
        )
        metrics.append(
            MetricItem(
                name=MetricName.RECALL,
                value=result.get("recall", 0.0),
                description="偏好抽取 Recall",
            )
        )
        metrics.append(
            MetricItem(
                name=MetricName.F1,
                value=result.get("f1", 0.0),
                description="偏好抽取 F1",
            )
        )

    if EvaluationTask.RETRIEVAL in tasks:
        result = run_retrieval_eval(dataset_path=req.dataset_path)

        metrics.append(
            MetricItem(
                name=MetricName.RECALL_AT_K,
                value=result.get("recall_at_k", 0.0),
                description="检索 Recall@K",
            )
        )

    if EvaluationTask.FORGETTING in tasks:
        result = run_forgetting_eval(dataset_path=req.dataset_path)

        metrics.append(
            MetricItem(
                name=MetricName.FORGET_SUCCESS_RATE,
                value=result.get("forget_success_rate", 0.0),
                description="遗忘成功率",
            )
        )

    data = EvalRunResponse(
        report_id="report_first_stage",
        tasks=tasks,
        metrics=metrics,
    )

    return APIResponse(data=data.model_dump())


@router.get("/metrics", response_model=APIResponse)
def get_eval_metrics() -> APIResponse:
    """
    获取最近一次评测指标。

    第一阶段可以先返回占位数据。
    后面可以从 evaluation/report_generator.py 或数据库读取。
    """

    data = {
        "metrics": [
            {
                "name": "preference_f1",
                "value": 1.0,
                "description": "第一阶段 Demo 偏好抽取 F1",
            },
            {
                "name": "retrieval_recall_at_3",
                "value": 1.0,
                "description": "第一阶段 Demo 检索 Recall@3",
            },
            {
                "name": "forget_success_rate",
                "value": 1.0,
                "description": "第一阶段 Demo 遗忘成功率",
            },
        ]
    }

    return APIResponse(data=data)