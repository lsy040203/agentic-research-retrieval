"""HTTP orchestration for research approvals and submitted verification receipts."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException
from pydantic import ValidationError

from os_agent_memory.api.schemas import (
    APIResponse,
    ApprovalDecisionRequest,
    ApprovalPackageResponse,
    CreateApprovalRequest,
    CreateVerificationRequest,
    ResearchScopeSchema,
    VerificationRunResponse,
)
from core.constants import ApprovalDecision, RiskLevel, VerificationStatus
from core.research_models import ApprovalPackage, ScopeKey, VerificationRun
from memory.research_store import ResearchStore
from policy.approval_service import (
    ApprovalService,
    ApprovalStateError,
    ApprovalValidationError,
)
from policy.verification_service import (
    VerificationService,
    VerificationStateError,
    VerificationValidationError,
)


# D1 路由适配层暂不注册到公开应用；待内部认证边界就绪后再显式接入。
router = APIRouter(prefix="/research", tags=["research"])


def get_research_store() -> ResearchStore:
    """Provide the persistence boundary, overridable in HTTP tests."""

    return ResearchStore()


def get_approval_service(
    store: ResearchStore = Depends(get_research_store),
) -> ApprovalService:
    """Provide approval policy over the request's persistence boundary."""

    return ApprovalService(store)


def get_verification_service(
    store: ResearchStore = Depends(get_research_store),
    approvals: ApprovalService = Depends(get_approval_service),
) -> VerificationService:
    """Provide receipt policy over the request's persistence boundary."""

    return VerificationService(store, approvals)


def _scope(schema: ResearchScopeSchema) -> ScopeKey:
    return ScopeKey(**schema.model_dump())


def _approval_response(package: ApprovalPackage) -> ApprovalPackageResponse:
    return ApprovalPackageResponse(
        package_id=package.package_id,
        case_memory_id=package.case_memory_id,
        scope=ResearchScopeSchema(**package.scope.__dict__),
        requester_id=package.requested_by,
        payload_hash=package.payload_hash,
        risk_level=package.risk_level,
        status=package.status,
        created_at=package.created_at,
        expires_at=package.expires_at,
    )


def _verification_response(run: VerificationRun) -> VerificationRunResponse:
    return VerificationRunResponse(
        run_id=run.run_id,
        case_memory_id=run.case_memory_id,
        package_id=run.package_id,
        payload_hash=run.payload_hash,
        receipt_id=run.receipt_id,
        event_key=run.event_key,
        receipt=run.receipt,
        status=run.status,
        created_at=run.created_at,
        verified_at=run.verified_at,
    )


def _not_found_for_approval(error: ApprovalStateError) -> None:
    if "does not exist" in str(error):
        raise HTTPException(status_code=404, detail="approval package not found") from error
    raise HTTPException(status_code=409, detail="approval package state conflict") from error


def _not_found_for_verification(error: VerificationStateError) -> None:
    if "does not exist" in str(error):
        raise HTTPException(status_code=404, detail="approval package not found") from error
    raise HTTPException(status_code=409, detail="approval package state conflict") from error


def _require_internal_actor(
    claimed_actor: str, internal_actor: str | None,
) -> None:
    """Bind the body actor to the trusted internal caller identity."""

    if internal_actor is None or not internal_actor.strip():
        raise HTTPException(status_code=401, detail="internal actor header is required")
    if internal_actor.strip() != claimed_actor:
        raise HTTPException(status_code=403, detail="internal actor does not match request")


@router.post("/approvals", response_model=APIResponse)
def create_approval(
    request: CreateApprovalRequest,
    internal_actor: str | None = Header(default=None, alias="X-ARR-Internal-Actor"),
    approvals: ApprovalService = Depends(get_approval_service),
) -> APIResponse:
    # 轻量 actor 身份只接受内部可信调用方注入，不能信任请求体的自报身份。
    _require_internal_actor(request.requester_id, internal_actor)
    try:
        package = approvals.create_package(
            _scope(request.scope),
            request.case_memory_id,
            request.requester_id,
            RiskLevel(request.risk_level.value),
            request.payload,
        )
    except ApprovalValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return APIResponse(data=_approval_response(package).model_dump())


@router.get("/approvals/{package_id}", response_model=APIResponse)
def get_approval(
    package_id: str,
    scope: ResearchScopeSchema = Depends(),
    approvals: ApprovalService = Depends(get_approval_service),
) -> APIResponse:
    package = approvals.get_package(package_id, _scope(scope))
    if package is None:
        raise HTTPException(status_code=404, detail="approval package not found")
    return APIResponse(data=_approval_response(package).model_dump())


@router.post("/approvals/{package_id}/decision", response_model=APIResponse)
def decide_approval(
    package_id: str,
    body: dict[str, Any] = Body(...),
    scope: ResearchScopeSchema = Depends(),
    internal_actor: str | None = Header(default=None, alias="X-ARR-Internal-Actor"),
    approvals: ApprovalService = Depends(get_approval_service),
) -> APIResponse:
    if "package_id" in body and body["package_id"] != package_id:
        raise HTTPException(status_code=422, detail="package_id must match the path")
    try:
        request = ApprovalDecisionRequest(
            **{**body, "package_id": package_id}
        )
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=error.errors()) from error
    # 审批 actor 同样必须由内部可信边界声明并与请求体保持一致。
    _require_internal_actor(request.approver_id, internal_actor)
    try:
        package = approvals.decide(
            package_id,
            _scope(scope),
            request.approver_id,
            ApprovalDecision(request.decision.value),
            request.reason,
        )
    except ApprovalValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ApprovalStateError as error:
        _not_found_for_approval(error)
    return APIResponse(data=_approval_response(package).model_dump())


@router.post("/verifications", response_model=APIResponse)
def create_verification(
    request: CreateVerificationRequest,
    verifications: VerificationService = Depends(get_verification_service),
) -> APIResponse:
    try:
        run = verifications.record_receipt(
            _scope(request.scope),
            request.package_id,
            request.case_memory_id,
            request.payload_hash,
            request.receipt_token,
            request.receipt_id,
            VerificationStatus(request.status.value),
            request.receipt,
        )
    except VerificationValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except VerificationStateError as error:
        _not_found_for_verification(error)
    return APIResponse(data=_verification_response(run).model_dump())


@router.get("/verifications/{run_id}", response_model=APIResponse)
def get_verification(
    run_id: str,
    scope: ResearchScopeSchema = Depends(),
    verifications: VerificationService = Depends(get_verification_service),
) -> APIResponse:
    run = verifications.get_run(run_id, _scope(scope))
    if run is None:
        raise HTTPException(status_code=404, detail="verification receipt not found")
    return APIResponse(data=_verification_response(run).model_dump())
