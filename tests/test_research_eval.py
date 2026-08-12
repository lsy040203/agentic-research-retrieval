"""研究检索离线评估的契约测试。"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core.constants import ResearchMemoryKind, RiskLevel, VerificationStatus
from core.research_models import ResearchMemory, ScopeKey
from evaluation.research_eval import (
    EvaluationValidationError,
    evaluate_ranking,
    load_research_gold,
    run_offline_approval_e2e,
)
from memory.research_store import ResearchStore
from policy.approval_service import ApprovalService
from policy.verification_service import VerificationService, VerificationValidationError


WORKSPACE_PARENT = str(Path(__file__).resolve().parents[2])
if WORKSPACE_PARENT not in sys.path:
    sys.path.insert(0, WORKSPACE_PARENT)

from os_agent_memory.api.server import app


完整范围 = {
    "team_id": "team-sample",
    "project_id": "project-sample",
    "repository": "repo-sample",
    "branch": "main",
    "experiment_environment": "offline-test",
}


def test_evaluate_ranking_calculates_recall_mrr_and_scope_leaks() -> None:
    """Recall 只看前 K，MRR 与泄露数覆盖完整返回序列。"""
    result = evaluate_ranking(
        returned_ids=["chunk-a", "chunk-b", "chunk-x"],
        returned_scope_matches=[True, True, False],
        relevant_ids=["chunk-b", "chunk-c"],
        k=2,
    )

    assert result.recall_at_k == 0.5
    assert result.mrr == 0.5
    assert result.scope_leak_count == 1


def test_evaluate_ranking_handles_empty_relevance_and_rejects_non_positive_k() -> None:
    """空相关集的 Recall 为零，K 必须为正数。"""
    assert evaluate_ranking(["chunk-a"], [True], [], 3).recall_at_k == 0.0

    with pytest.raises(EvaluationValidationError, match="k"):
        evaluate_ranking(["chunk-a"], [True], ["chunk-a"], 0)


def test_evaluate_ranking_rejects_mismatched_lengths() -> None:
    """每个返回 ID 都必须有对应的 Scope 匹配标记。"""
    with pytest.raises(EvaluationValidationError):
        evaluate_ranking(["chunk-a"], [], ["chunk-a"], 1)


def test_evaluate_ranking_rejects_duplicate_returned_ids() -> None:
    """重复返回 ID 会使 Recall 超过一，必须拒绝。"""
    with pytest.raises(EvaluationValidationError):
        evaluate_ranking(["a", "a"], [True, True], ["a"], 2)


def test_evaluate_ranking_handles_empty_results_and_no_hits() -> None:
    """空候选与无相关命中均得到零指标且无泄露。"""
    empty_result = evaluate_ranking([], [], ["chunk-a"], 1)
    missed_result = evaluate_ranking(["chunk-b"], [True], ["chunk-a"], 1)

    assert empty_result.recall_at_k == empty_result.mrr == 0.0
    assert empty_result.scope_leak_count == 0
    assert missed_result.recall_at_k == missed_result.mrr == 0.0
    assert missed_result.scope_leak_count == 0


def test_load_research_gold_rejects_invalid_json(tmp_path: Path) -> None:
    """无法解析的 JSON 必须统一转换为评估校验错误。"""
    path = tmp_path / "invalid-json.json"
    path.write_text("{", encoding="utf-8")

    with pytest.raises(EvaluationValidationError):
        load_research_gold(path)


def test_load_research_gold_rejects_invalid_utf8(tmp_path: Path) -> None:
    """非法 UTF-8 字节必须统一转换为评估校验错误。"""
    path = tmp_path / "invalid-utf8.json"
    path.write_bytes(b'{"cases": "\xff"}')

    with pytest.raises(EvaluationValidationError):
        load_research_gold(path)


def test_load_research_gold_rejects_invalid_top_level(tmp_path: Path) -> None:
    """顶层只能是仅含 cases 的对象。"""
    path = tmp_path / "invalid-top-level.json"
    path.write_text(json.dumps({"cases": [], "extra": True}), encoding="utf-8")

    with pytest.raises(EvaluationValidationError):
        load_research_gold(path)


def test_load_research_gold_rejects_missing_scope_dimension(tmp_path: Path) -> None:
    """缺少任一五维 Scope 字段的案例必须拒绝。"""
    path = tmp_path / "missing-scope.json"
    incomplete_scope = dict(完整范围)
    del incomplete_scope["branch"]
    _write_gold(path, "case-missing-scope", incomplete_scope, ["a"], ["a"])

    with pytest.raises(EvaluationValidationError):
        load_research_gold(path)


def test_load_research_gold_rejects_duplicate_candidates(tmp_path: Path) -> None:
    """候选 ID 不允许重复。"""
    path = tmp_path / "duplicate-candidates.json"
    _write_gold(path, "case-duplicate-candidates", 完整范围, ["a", "a"], ["a"])

    with pytest.raises(EvaluationValidationError):
        load_research_gold(path)


def test_load_research_gold_rejects_duplicate_case_id(tmp_path: Path) -> None:
    """重复 case_id 必须拒绝，且不依赖其他无效字段。"""
    path = tmp_path / "duplicate-case.json"
    case = {
        "case_id": "duplicate",
        "scope": 完整范围,
        "candidate_ids": ["a"],
        "relevant_ids": ["a"],
    }
    path.write_text(json.dumps({"cases": [case, dict(case)]}), encoding="utf-8")

    with pytest.raises(EvaluationValidationError):
        load_research_gold(path)


def test_load_research_gold_rejects_unknown_relevant_id(tmp_path: Path) -> None:
    """相关 ID 必须属于同一案例的候选 ID 集合。"""
    path = tmp_path / "unknown-relevant.json"
    _write_gold(path, "case-unknown-relevant", 完整范围, ["a"], ["missing"])

    with pytest.raises(EvaluationValidationError):
        load_research_gold(path)


def test_load_research_gold_rejects_empty_candidates(tmp_path: Path) -> None:
    """金标案例必须提供至少一个候选 ID。"""
    path = tmp_path / "empty-candidates.json"
    path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_id": "case-empty",
                        "scope": 完整范围,
                        "candidate_ids": [],
                        "relevant_ids": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(EvaluationValidationError):
        load_research_gold(path)


def test_evaluate_ranking_does_not_modify_inputs() -> None:
    """离线指标计算不得改写调用者提供的序列。"""
    returned_ids = ["chunk-a", "chunk-b"]
    scope_matches = [True, False]
    relevant_ids = ["chunk-b"]
    expected_returned = list(returned_ids)
    expected_matches = list(scope_matches)
    expected_relevant = list(relevant_ids)

    evaluate_ranking(returned_ids, scope_matches, relevant_ids, 1)

    assert returned_ids == expected_returned
    assert scope_matches == expected_matches
    assert relevant_ids == expected_relevant


def test_load_research_gold_loads_project_gold() -> None:
    """提交的脱敏金标必须能通过严格校验。"""
    path = Path(__file__).parents[1] / "data" / "gold" / "research_gold.json"

    cases = load_research_gold(path)

    assert cases


def test_user_guide_documents_e1_evaluation_commands_metrics_and_boundaries() -> None:
    """用户指南必须说明 E1 的离线验证入口、指标及不可执行边界。"""
    guide = (Path(__file__).parents[1] / "docs" / "user-guide.md").read_text(
        encoding="utf-8"
    )

    required_phrases = (
        "data/gold/research_gold.json",
        r"C:\Users\Lenovo\.conda\envs\memoryOs_retrival\python.exe -m pytest -q tests/test_research_eval.py",
        r"C:\Users\Lenovo\.conda\envs\memoryOs_retrival\python.exe -m pytest -q",
        "Recall@K",
        "MRR",
        "scope_leak_count",
        "Fake 回执",
        "不执行计划",
        "不自动发布",
        "/research",
        "404",
        "认证",
        "scope 授权",
        "RAGAS",
        "当前不启用",
        "未来前提",
    )

    assert all(phrase in guide for phrase in required_phrases)


def test_offline_approval_receipt_e2e_records_audit_without_publishing_memory(
    tmp_path: Path,
) -> None:
    """离线 Fake 回执只写审批、验证和审计记录，绝不自动发布研究记忆。"""
    store, scope, approvals, verifications = _build_fake_e2e_services(tmp_path)
    store.save(
        ResearchMemory(
            "case-e1", scope, ResearchMemoryKind.RESEARCH_CASE, "离线假设", "脱敏案例"
        )
    )

    run = run_offline_approval_e2e(
        store,
        scope,
        approvals,
        verifications,
        case_memory_id="case-e1",
        requester_id="requester",
        approver_id="reviewer",
        risk_level=RiskLevel.MEDIUM,
        payload={"plan": "fake"},
        receipt_id="receipt-e1",
        receipt_status=VerificationStatus.PASSED,
        receipt=_valid_fake_receipt(),
    )

    assert run.receipt_id == "receipt-e1"
    assert len(store.list_verification_runs("package-e1", scope)) == 1
    with sqlite3.connect(tmp_path / "research_memory.db") as connection:
        audit_actions = {
            row[0]
            for row in connection.execute(
                "SELECT action FROM research_audit WHERE memory_id = ?", ("case-e1",)
            )
        }
    assert {"approval_finalized", "verification_finalized"} <= audit_actions
    assert store.list_published(scope) == []


def test_sensitive_nested_receipt_is_not_persisted(tmp_path: Path) -> None:
    """嵌套敏感字段必须在写入验证运行记录前被拒绝。"""
    store, scope, approvals, verifications = _build_fake_e2e_services(tmp_path)
    store.save(
        ResearchMemory(
            "case-e1", scope, ResearchMemoryKind.RESEARCH_CASE, "离线假设", "脱敏案例"
        )
    )
    run = run_offline_approval_e2e(
        store,
        scope,
        approvals,
        verifications,
        case_memory_id="case-e1",
        requester_id="requester",
        approver_id="reviewer",
        risk_level=RiskLevel.MEDIUM,
        payload={"plan": "fake"},
        receipt_id="receipt-e1",
        receipt_status=VerificationStatus.PASSED,
        receipt=_valid_fake_receipt(),
    )
    before = len(store.list_verification_runs(run.package_id, scope))
    package = approvals.get_package(run.package_id, scope)
    assert package is not None

    with pytest.raises(VerificationValidationError, match="sensitive"):
        verifications.record_receipt(
            scope,
            run.package_id,
            "case-e1",
            run.payload_hash,
            package.receipt_token,
            "receipt-sensitive",
            VerificationStatus.PASSED,
            {**_valid_fake_receipt(), "assertions": {"nested": {"token": "secret"}}},
        )

    assert len(store.list_verification_runs(run.package_id, scope)) == before


@pytest.mark.parametrize(
    ("requester_id", "approver_id", "receipt_status"),
    [
        ("requester", "requester", VerificationStatus.PASSED),
        ("requester", "reviewer", VerificationStatus.PENDING),
    ],
)
def test_offline_approval_e2e_rejects_invalid_input_before_orchestration(
    tmp_path: Path,
    requester_id: str,
    approver_id: str,
    receipt_status: VerificationStatus,
) -> None:
    """编排器在创建审批包前拒绝同人审批和非终态回执。"""
    store, scope, approvals, verifications = _build_fake_e2e_services(tmp_path)

    with pytest.raises(EvaluationValidationError):
        run_offline_approval_e2e(
            store,
            scope,
            approvals,
            verifications,
            case_memory_id="case-e1",
            requester_id=requester_id,
            approver_id=approver_id,
            risk_level=RiskLevel.MEDIUM,
            payload={"plan": "fake"},
            receipt_id="receipt-e1",
            receipt_status=receipt_status,
            receipt=_valid_fake_receipt(),
        )

    assert store.get_approval_package("package-e1", scope) is None


def test_offline_approval_e2e_rejects_a_non_approved_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """即使服务返回未批准状态，编排器也不得继续写入回执。"""
    store, scope, approvals, verifications = _build_fake_e2e_services(tmp_path)
    store.save(
        ResearchMemory(
            "case-e1", scope, ResearchMemoryKind.RESEARCH_CASE, "离线假设", "脱敏案例"
        )
    )
    monkeypatch.setattr(
        approvals,
        "decide",
        lambda package_id, requested_scope, *_args: approvals.get_package(
            package_id, requested_scope
        ),
    )

    with pytest.raises(EvaluationValidationError, match="批准"):
        run_offline_approval_e2e(
            store,
            scope,
            approvals,
            verifications,
            case_memory_id="case-e1",
            requester_id="requester",
            approver_id="reviewer",
            risk_level=RiskLevel.MEDIUM,
            payload={"plan": "fake"},
            receipt_id="receipt-e1",
            receipt_status=VerificationStatus.PASSED,
            receipt=_valid_fake_receipt(),
        )

    assert store.list_verification_runs("package-e1", scope) == []


def test_public_research_endpoints_remain_unregistered() -> None:
    """公开应用在授权边界接入前继续返回研究端点的 404。"""
    client = TestClient(app)

    assert client.post("/research/approvals", json={"requester_id": "requester"}).status_code == 404
    assert client.get("/research/verifications/unknown").status_code == 404


def _write_gold(
    path: Path,
    case_id: str,
    scope: dict[str, str],
    candidate_ids: list[str],
    relevant_ids: list[str],
) -> None:
    """写入一个仅用于验证单一金标错误来源的案例。"""
    path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_id": case_id,
                        "scope": scope,
                        "candidate_ids": candidate_ids,
                        "relevant_ids": relevant_ids,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def _build_fake_e2e_services(
    tmp_path: Path,
) -> tuple[ResearchStore, ScopeKey, ApprovalService, VerificationService]:
    """构造使用固定 UTC 时间和确定性标识符的本地 E2E 服务。"""
    store = ResearchStore(tmp_path / "research_memory.db")
    scope = ScopeKey("team-e1", "project-e1", "org/repo", "main", "offline")
    approvals = ApprovalService(
        store,
        clock=lambda: datetime(2026, 7, 29, tzinfo=timezone.utc),
        id_factory=lambda: "package-e1",
        token_factory=lambda: "a" * 64,
    )
    return store, scope, approvals, VerificationService(store, approvals, id_factory=lambda: "run-e1")


def _valid_fake_receipt() -> dict[str, object]:
    """返回满足 D1 字段白名单的脱敏离线回执。"""
    return {
        "environment": "offline",
        "verification_summary": "fake verification passed",
        "evidence_refs": ["local://evidence/e1"],
        "assertions": {"all_checks_passed": True},
        "log_summary": "offline fixture only",
    }
