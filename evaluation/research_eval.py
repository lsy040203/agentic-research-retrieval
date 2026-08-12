"""严格本地的研究检索金标加载和排序指标计算。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from core.constants import ApprovalDecision, ApprovalStatus, RiskLevel, VerificationStatus
from core.research_models import ScopeKey, VerificationRun
from memory.research_store import ResearchStore
from policy.approval_service import ApprovalService
from policy.verification_service import VerificationService


_SCOPE_FIELDS = frozenset(
    {
        "team_id",
        "project_id",
        "repository",
        "branch",
        "experiment_environment",
    }
)


class EvaluationValidationError(ValueError):
    """金标或离线评测输入不满足可复现契约。"""


@dataclass(frozen=True)
class RankingMetrics:
    """一次固定排序的离线评估结果。"""

    recall_at_k: float
    mrr: float
    scope_leak_count: int


def run_offline_approval_e2e(
    store: ResearchStore,
    scope: ScopeKey,
    approvals: ApprovalService,
    verifications: VerificationService,
    *,
    case_memory_id: str,
    requester_id: str,
    approver_id: str,
    risk_level: RiskLevel,
    payload: dict[str, Any],
    receipt_id: str,
    receipt_status: VerificationStatus,
    receipt: dict[str, Any],
) -> VerificationRun:
    """离线串联 Fake 审批与回执，且绝不执行审批内容或发布研究记忆。

    调用方必须预先保存 ``case_memory_id`` 对应的研究案例；本函数仅顺序
    调用审批包创建、批准决定和回执记录三个服务层操作。
    """
    if requester_id == approver_id:
        raise EvaluationValidationError("审批人与申请人必须不同")
    if not isinstance(receipt_status, VerificationStatus) or receipt_status is VerificationStatus.PENDING:
        raise EvaluationValidationError("回执状态必须是终态")

    package = approvals.create_package(
        scope, case_memory_id, requester_id, risk_level, payload
    )
    approved_package = approvals.decide(
        package.package_id,
        scope,
        approver_id,
        ApprovalDecision.APPROVED,
        "offline evaluation approval",
    )
    if approved_package.status is not ApprovalStatus.APPROVED:
        raise EvaluationValidationError("审批包必须处于已批准状态")
    return verifications.record_receipt(
        scope,
        package.package_id,
        case_memory_id,
        package.payload_hash,
        package.receipt_token,
        receipt_id,
        receipt_status,
        receipt,
    )


def load_research_gold(path: str | Path) -> list[dict[str, object]]:
    """加载并严格校验本地 ARR 金标，不访问网络或数据库。"""
    try:
        with Path(path).open(encoding="utf-8") as gold_file:
            document = json.load(gold_file)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EvaluationValidationError("金标文件无法解析") from error

    if not isinstance(document, dict) or set(document) != {"cases"}:
        raise EvaluationValidationError("金标顶层只能包含 cases")
    cases = document["cases"]
    if not isinstance(cases, list):
        raise EvaluationValidationError("cases 必须是列表")

    case_ids: set[str] = set()
    validated_cases: list[dict[str, object]] = []
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise EvaluationValidationError(f"第 {index} 个案例必须是对象")

        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise EvaluationValidationError(f"第 {index} 个案例缺少非空 case_id")
        if case_id in case_ids:
            raise EvaluationValidationError(f"case_id 重复：{case_id}")
        case_ids.add(case_id)

        _validate_scope(case.get("scope"), index)
        candidate_ids = _validate_ids(case.get("candidate_ids"), "candidate_ids", index, True)
        relevant_ids = _validate_ids(case.get("relevant_ids"), "relevant_ids", index, False)
        if not set(relevant_ids).issubset(candidate_ids):
            raise EvaluationValidationError(f"第 {index} 个案例存在候选集外的相关 ID")

        validated_cases.append(case)

    return validated_cases


def evaluate_ranking(
    returned_ids: Sequence[str],
    returned_scope_matches: Sequence[bool],
    relevant_ids: Sequence[str],
    k: int,
) -> RankingMetrics:
    """根据给定次序计算 Recall@K、MRR 和五维 Scope 泄露数。"""
    if not isinstance(k, int) or isinstance(k, bool) or k <= 0:
        raise EvaluationValidationError("k 必须是正整数")

    returned = list(returned_ids)
    scope_matches = list(returned_scope_matches)
    relevant = list(relevant_ids)
    if len(returned) != len(scope_matches):
        raise EvaluationValidationError("返回 ID 与 Scope 标记长度必须一致")
    if any(not isinstance(candidate_id, str) for candidate_id in returned):
        raise EvaluationValidationError("返回 ID 必须全部为字符串")
    if len(set(returned)) != len(returned):
        raise EvaluationValidationError("返回 ID 不允许重复")
    if any(not isinstance(candidate_id, str) for candidate_id in relevant):
        raise EvaluationValidationError("相关 ID 必须全部为字符串")
    if any(type(matched) is not bool for matched in scope_matches):
        raise EvaluationValidationError("Scope 标记必须全部为布尔值")

    relevant_set = set(relevant)
    hits = sum(candidate_id in relevant_set for candidate_id in returned[:k])
    recall_at_k = hits / len(relevant_set) if relevant_set else 0.0
    first_rank = next(
        (index for index, candidate_id in enumerate(returned, 1) if candidate_id in relevant_set),
        None,
    )
    mrr = 0.0 if first_rank is None else 1.0 / first_rank
    scope_leak_count = sum(not matched for matched in scope_matches)
    return RankingMetrics(recall_at_k, mrr, scope_leak_count)


def _validate_scope(scope: object, index: int) -> None:
    """确认 Scope 恰好包含五个非空字符串维度。"""
    if not isinstance(scope, dict) or set(scope) != _SCOPE_FIELDS:
        raise EvaluationValidationError(f"第 {index} 个案例的 Scope 不完整")
    if any(not isinstance(value, str) or not value for value in scope.values()):
        raise EvaluationValidationError(f"第 {index} 个案例的 Scope 字段必须为非空字符串")


def _validate_ids(value: object, field_name: str, index: int, required: bool) -> set[str]:
    """校验 ID 列表的类型、非空约束与去重约束。"""
    if not isinstance(value, list) or (required and not value):
        raise EvaluationValidationError(f"第 {index} 个案例的 {field_name} 无效")
    if any(not isinstance(item, str) or not item for item in value):
        raise EvaluationValidationError(f"第 {index} 个案例的 {field_name} 必须为非空字符串")
    ids = set(value)
    if len(ids) != len(value):
        raise EvaluationValidationError(f"第 {index} 个案例的 {field_name} 存在重复 ID")
    return ids
