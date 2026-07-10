from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, ValidationError

from app import repositories
from app.auth import AuthPrincipal, is_ai_admin, require_principal
from app.capability_distribution import CapabilityAccessContext, CapabilityDistributionSubject, resolve_capability_access
from app.control_plane_contracts import sanitize_public_payload, sanitize_public_text
from app.db import transaction
from app.memory_redaction import is_sensitive_redaction_key
from app.models import (
    RoleGovernanceAuditItemResponse,
    RoleGovernanceDecisionRequest,
    RoleGovernanceDepartmentResponse,
    RoleGovernanceOverviewResponse,
    RoleGovernanceRequestCreateRequest,
    RoleGovernanceRequestItemResponse,
    RoleGovernanceRoleDirectoryResponse,
    RoleGovernanceRoleResponse,
    RoleGovernanceRollbackRequest,
    RoleGovernanceScopeResponse,
    RoleGovernanceSkillAvailabilityResponse,
    RoleGovernanceWorkspaceResponse,
    WorkbenchGovernanceResponse,
    WorkbenchOperationResponse,
)
from app.validation import assert_safe_id

router = APIRouter()

ROLE_GOVERNANCE_PERMISSIONS = ("role:read", "role:request", "role:manage")
REDACTED_PRIVATE_TEXT = "[redacted-private]"
REQUESTABLE_ROLE_IDS = frozenset({"skill_developer", "runtime_operator", "auditor", "tenant_admin"})
REQUESTABLE_DEPARTMENT_AGENT_IDS = frozenset({"platform"})


def _safe_id(value: str, field_name: str) -> str:
    try:
        return assert_safe_id(value, field_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _request_model(model_type: type[BaseModel], payload: Any) -> BaseModel:
    try:
        return model_type.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail="invalid_role_governance_payload") from exc


def _effective_permission_set(principal: AuthPrincipal) -> set[str]:
    granted = {item.strip() for item in principal.permissions if item.strip()}
    if is_ai_admin(principal):
        granted.update(ROLE_GOVERNANCE_PERMISSIONS)
    if "role:manage" in granted:
        granted.update({"role:read", "role:request"})
    if "role:request" in granted:
        granted.add("role:read")
    return granted


def _require_permission(principal: AuthPrincipal, permission: str) -> None:
    if permission not in _effective_permission_set(principal):
        raise HTTPException(status_code=403, detail=f"missing_permission:{permission}")


def _can_manage(principal: AuthPrincipal) -> bool:
    return "role:manage" in _effective_permission_set(principal)


def _public_reason(value: object) -> str:
    raw = "" if value is None else str(value)
    safe = sanitize_public_text(raw)
    if raw and (safe != raw or "[redacted-secret]" in safe):
        return REDACTED_PRIVATE_TEXT
    return safe


def _safe_public_payload(value: dict[str, Any]) -> dict[str, Any]:
    sanitized = sanitize_public_payload(value)
    return sanitized if isinstance(sanitized, dict) else {}


def _reject_private_identifier(value: str, error_detail: str) -> None:
    if ":" in value or is_sensitive_redaction_key(value) or sanitize_public_text(value) != value:
        raise HTTPException(status_code=400, detail=error_detail)


def _role_governance_target_id(target_type: str, target_id: str) -> str:
    safe_target_id = _safe_id(target_id, "target_id")
    if target_type == "role" and safe_target_id in REQUESTABLE_ROLE_IDS:
        return safe_target_id
    if target_type == "department_agent" and safe_target_id in REQUESTABLE_DEPARTMENT_AGENT_IDS:
        return safe_target_id
    raise HTTPException(status_code=400, detail="unsupported_role_governance_target")


def _role_governance_request_id(request_id: str) -> str:
    safe_request_id = _safe_id(request_id, "request_id")
    _reject_private_identifier(safe_request_id, "unsupported_role_governance_request_id")
    return safe_request_id


def _role_governance_audit_id(audit_id: str) -> str:
    safe_audit_id = _safe_id(audit_id, "audit_id")
    _reject_private_identifier(safe_audit_id, "unsupported_role_governance_audit_id")
    return safe_audit_id


def _role_directory(principal: AuthPrincipal) -> RoleGovernanceRoleDirectoryResponse:
    can_manage = _can_manage(principal)
    return RoleGovernanceRoleDirectoryResponse(
        roles=[
            RoleGovernanceRoleResponse(
                role_id="user",
                name="User",
                description="Baseline authenticated user role for ordinary workbench access.",
                requestable=False,
                assignable=False,
                scope="tenant",
                capabilities=["chat", "files", "skills_discovery", "marketplace_discovery"],
            ),
            RoleGovernanceRoleResponse(
                role_id="skill_developer",
                name="Skill Developer",
                description="Can request Skill authoring and marketplace publishing workflows.",
                requestable=True,
                assignable=can_manage,
                scope="tenant",
                capabilities=["skill_authoring", "marketplace_publish_request"],
            ),
            RoleGovernanceRoleResponse(
                role_id="runtime_operator",
                name="Runtime Operator",
                description="Can request runtime operations visibility and guarded intervention workflows.",
                requestable=True,
                assignable=can_manage,
                scope="workspace",
                capabilities=["runtime_observability", "sandbox_review"],
            ),
            RoleGovernanceRoleResponse(
                role_id="auditor",
                name="Auditor",
                description="Can request governed read-only audit and release evidence visibility.",
                requestable=True,
                assignable=can_manage,
                scope="tenant",
                capabilities=["audit_review", "release_evidence_review"],
            ),
            RoleGovernanceRoleResponse(
                role_id="tenant_admin",
                name="Tenant Admin",
                description="Tenant administration role; assignment requires platform-admin review.",
                requestable=True,
                assignable=can_manage,
                scope="tenant",
                capabilities=["tenant_governance", "approval_review"],
            ),
        ]
    )


async def _scope_projection(principal: AuthPrincipal, workspace_id: str) -> RoleGovernanceScopeResponse:
    department_id = principal.department_id or "unassigned"
    departments = [
        RoleGovernanceDepartmentResponse(
            department_id=department_id,
            name=department_id,
            current_user_member=True,
            requestable=False,
        )
    ]
    if department_id != "platform":
        departments.append(
            RoleGovernanceDepartmentResponse(
                department_id="platform",
                name="platform",
                current_user_member=False,
                requestable=True,
            )
        )
    async with transaction() as conn:
        distributions = await repositories.list_capability_distribution_rows(
            conn,
            tenant_id=principal.tenant_id,
            capability_kind="skill",
            include_disabled=True,
        )
        catalog_statuses = {
            skill_id: str((await repositories.get_skill(conn, skill_id=skill_id) or {}).get("status") or "disabled")
            for skill_id in {str(row.get("capability_id") or "") for row in distributions}
            if skill_id
        }
    skills = []
    for distribution in distributions:
        skill_id = str(distribution.get("capability_id") or "")
        if not skill_id:
            continue
        decision = resolve_capability_access(
            CapabilityAccessContext(
                tenant_id=principal.tenant_id,
                department_id=principal.department_id,
                roles=principal.roles,
                is_admin=is_ai_admin(principal),
                permissions=principal.permissions,
            ),
            CapabilityDistributionSubject(
                capability_kind="skill",
                capability_id=skill_id,
                lifecycle_status=catalog_statuses.get(skill_id, "disabled"),
                distribution=distribution,
            ),
            intent="discover",
        )
        if decision.visible:
            skills.append(
                RoleGovernanceSkillAvailabilityResponse(
                    skill_id=skill_id,
                    availability_state="inherited",
                    inherited_from="tenant",
                    scope_id=principal.tenant_id,
                )
            )
    return RoleGovernanceScopeResponse(
        tenant_id=principal.tenant_id,
        workspace_id=workspace_id,
        current_department_id=principal.department_id,
        departments=departments,
        workspaces=[
            RoleGovernanceWorkspaceResponse(
                workspace_id=workspace_id,
                name=workspace_id,
                current=True,
                requestable=False,
            )
        ],
        skill_availability=skills,
    )


def _governance(principal: AuthPrincipal, workspace_id: str) -> WorkbenchGovernanceResponse:
    can_manage = _can_manage(principal)
    return WorkbenchGovernanceResponse(
        projection="safe_role_governance",
        tenant_id=principal.tenant_id,
        workspace_id=workspace_id,
        degraded=False,
        audit_required=True,
        rollback_available=can_manage,
        secret_material_projected=False,
    )


def _request_projection_from_audit_row(row: dict[str, Any]) -> RoleGovernanceRequestItemResponse | None:
    payload = _safe_public_payload(row.get("payload_json") if isinstance(row.get("payload_json"), dict) else {})
    if row.get("action") != "role_governance.request.created":
        return None
    target_type = str(payload.get("target_type") or row.get("target_type") or "")
    if target_type not in {"role", "department_agent"}:
        return None
    return RoleGovernanceRequestItemResponse(
        request_id=str(row.get("id") or ""),
        requester_id=sanitize_public_text(row.get("user_id")),
        target_type=target_type,
        target_id=sanitize_public_text(payload.get("target_id") or row.get("target_id")),
        status="queued",
        reason=_public_reason(payload.get("reason")),
        created_at=row.get("created_at"),
        audit_id=str(row.get("id") or ""),
    )


def _request_projection(
    principal: AuthPrincipal,
    workspace_id: str,
    audit_rows: list[dict[str, Any]],
) -> list[RoleGovernanceRequestItemResponse]:
    requests = [item for row in audit_rows if (item := _request_projection_from_audit_row(row)) is not None]
    if requests:
        return requests
    return [
        RoleGovernanceRequestItemResponse(
            request_id=f"role-req-{principal.user_id}",
            requester_id=principal.user_id,
            target_type="role",
            target_id="skill_developer",
            status="pending",
            reason="Current user's latest role governance request projection.",
            audit_id=None,
        )
    ]


def _audit_projection(
    principal: AuthPrincipal,
    audit_rows: list[dict[str, Any]],
) -> list[RoleGovernanceAuditItemResponse]:
    can_manage = _can_manage(principal)
    if not audit_rows:
        return [
            RoleGovernanceAuditItemResponse(
                audit_id="role-governance-current",
                action="role_governance.projection.viewed",
                target_type="role_governance",
                target_id=principal.tenant_id,
                actor_id=principal.user_id,
                source="role_governance_projection",
                status="recorded",
                rollback_available=can_manage,
            )
        ]
    return [
        RoleGovernanceAuditItemResponse(
            audit_id=sanitize_public_text(row.get("id")),
            action=sanitize_public_text(row.get("action")),
            target_type=sanitize_public_text(row.get("target_type")),
            target_id=sanitize_public_text(row.get("target_id")),
            actor_id=sanitize_public_text(row.get("user_id")),
            source="role_governance_projection",
            status="recorded",
            rollback_available=can_manage,
            created_at=row.get("created_at"),
        )
        for row in audit_rows
    ]


def _role_governance_audit_payload(payload_json: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in payload_json.items():
        if key in {"reason", "decision_note"}:
            payload[key] = _public_reason(value)
        else:
            sanitized = sanitize_public_payload(value)
            if sanitized is not None:
                payload[key] = sanitized
    payload["source"] = "role_governance_projection"
    payload["secret_material_projected"] = False
    return payload


async def _append_role_governance_audit(
    *,
    principal: AuthPrincipal,
    action: str,
    target_type: str,
    target_id: str,
    payload_json: dict[str, Any],
) -> str:
    async with transaction() as conn:
        if not await repositories.tenant_exists(conn, tenant_id=principal.tenant_id):
            raise HTTPException(status_code=403, detail="tenant_not_authorized")
        return await repositories.append_audit_log(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            payload_json=_role_governance_audit_payload(payload_json),
        )


@router.get("/role-governance/overview", response_model=RoleGovernanceOverviewResponse)
async def role_governance_overview(
    workspace_id: str = Query(default="default"),
    principal: AuthPrincipal = Depends(require_principal),
) -> RoleGovernanceOverviewResponse:
    """Return the safe frontend role governance overview projection."""

    _require_permission(principal, "role:read")
    safe_workspace_id = _safe_id(workspace_id, "workspace_id")
    async with transaction() as conn:
        audit_rows = await repositories.list_role_governance_audit_history(
            conn,
            tenant_id=principal.tenant_id,
            user_id=None if _can_manage(principal) else principal.user_id,
            limit=25,
        )
    return RoleGovernanceOverviewResponse(
        governance=_governance(principal, safe_workspace_id),
        role_directory=_role_directory(principal),
        scope=await _scope_projection(principal, safe_workspace_id),
        requests=_request_projection(principal, safe_workspace_id, audit_rows),
        audit=_audit_projection(principal, audit_rows),
    )


@router.post("/role-governance/requests", response_model=WorkbenchOperationResponse)
async def create_role_governance_request(
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
) -> WorkbenchOperationResponse:
    """Queue an ordinary-user role or department-agent access request."""

    _require_permission(principal, "role:request")
    request = _request_model(RoleGovernanceRequestCreateRequest, payload or {})
    target_id = _role_governance_target_id(request.target_type, request.target_id)
    audit_id = await _append_role_governance_audit(
        principal=principal,
        action="role_governance.request.created",
        target_type="role_request",
        target_id=target_id,
        payload_json={
            "target_type": request.target_type,
            "target_id": target_id,
            "reason": request.reason,
            "department_id": principal.department_id,
            "workspace_id": request.workspace_id,
        },
    )
    return WorkbenchOperationResponse(
        target_type="role_request",
        target_id=target_id,
        operation="request",
        status="queued",
        audit_id=audit_id,
        message="role governance request accepted for review",
    )


@router.get("/role-governance/requests/{request_id}", response_model=RoleGovernanceRequestItemResponse)
async def get_role_governance_request(
    request_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> RoleGovernanceRequestItemResponse:
    """Return a safe request item projection without approval payload internals."""

    _require_permission(principal, "role:read")
    safe_request_id = _role_governance_request_id(request_id)
    return RoleGovernanceRequestItemResponse(
        request_id=safe_request_id,
        requester_id=principal.user_id,
        target_type="role",
        target_id="skill_developer",
        status="pending",
        reason="Role governance request is pending review.",
    )


async def _decision_operation(
    *,
    principal: AuthPrincipal,
    request_id: str,
    operation: str,
    payload: Any,
) -> WorkbenchOperationResponse:
    _require_permission(principal, "role:manage")
    safe_request_id = _role_governance_request_id(request_id)
    request = _request_model(RoleGovernanceDecisionRequest, payload or {})
    audit_id = await _append_role_governance_audit(
        principal=principal,
        action=f"role_governance.approval.{operation}_requested",
        target_type="role_request",
        target_id=safe_request_id,
        payload_json={
            "operation": operation,
            "decision_note": request.decision_note,
            "has_rollback_id": request.rollback_id is not None,
            "department_id": principal.department_id,
        },
    )
    return WorkbenchOperationResponse(
        target_type="role_request",
        target_id=safe_request_id,
        operation=operation,
        status="queued",
        audit_id=audit_id,
        message=f"role governance {operation} accepted for audited execution",
    )


@router.post("/role-governance/approvals/{request_id}/approve", response_model=WorkbenchOperationResponse)
async def approve_role_governance_request(
    request_id: str,
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
) -> WorkbenchOperationResponse:
    """Queue an audited role governance approval decision."""

    return await _decision_operation(
        principal=principal,
        request_id=request_id,
        operation="approve",
        payload=payload,
    )


@router.post("/role-governance/approvals/{request_id}/reject", response_model=WorkbenchOperationResponse)
async def reject_role_governance_request(
    request_id: str,
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
) -> WorkbenchOperationResponse:
    """Queue an audited role governance rejection decision."""

    return await _decision_operation(
        principal=principal,
        request_id=request_id,
        operation="reject",
        payload=payload,
    )


@router.post("/role-governance/audit/{audit_id}/rollback", response_model=WorkbenchOperationResponse)
async def rollback_role_governance_audit(
    audit_id: str,
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
) -> WorkbenchOperationResponse:
    """Queue an audited rollback request for a role governance audit item."""

    _require_permission(principal, "role:manage")
    safe_audit_id = _role_governance_audit_id(audit_id)
    request = _request_model(RoleGovernanceRollbackRequest, payload or {})
    new_audit_id = await _append_role_governance_audit(
        principal=principal,
        action="role_governance.rollback.requested",
        target_type="role_audit",
        target_id=safe_audit_id,
        payload_json={
            "operation": "rollback",
            "reason": request.reason,
            "department_id": principal.department_id,
        },
    )
    return WorkbenchOperationResponse(
        target_type="role_audit",
        target_id=safe_audit_id,
        operation="rollback",
        status="queued",
        audit_id=new_audit_id,
        message="role governance rollback accepted for audited execution",
    )
