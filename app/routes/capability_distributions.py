from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from app import repositories
from app.auth import AuthPrincipal, is_ai_admin, require_principal
from app.capability_distribution import (
    CapabilityAccessContext,
    CapabilityDistributionSubject,
    capability_distribution_audit_payload,
    resolve_capability_access,
)
from app.control_plane_contracts import standard_trace_id
from app.db import transaction
from app.department_directory import (
    MAX_DISTRIBUTION_DEPARTMENTS,
    DepartmentDirectoryError,
    fetch_department_directory,
    validate_distribution_department_authorities,
)
from app.models import (
    CapabilityDistributionAuthorityUpdateRequest,
    CapabilityDistributionListResponse,
    CapabilityDistributionResponse,
    CapabilityDistributionToggleRequest,
    CapabilityDistributionWriteResponse,
    DepartmentDirectoryResponse,
)
from app.validation import assert_safe_id

router = APIRouter()

CapabilityKind = Literal["skill", "mcp_server"]
_CAPABILITY_KINDS = {"skill", "mcp_server"}
_RESERVED_ARCHIVE_METADATA_KEYS = frozenset({"archived_at", "archived_by"})


def _require_admin(principal: AuthPrincipal) -> None:
    if not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_admin")


def _safe_capability_kind(capability_kind: str) -> CapabilityKind:
    if capability_kind not in _CAPABILITY_KINDS:
        raise HTTPException(status_code=400, detail="capability_kind_invalid")
    return capability_kind  # type: ignore[return-value]


def _safe_capability_id(capability_id: str) -> str:
    try:
        return assert_safe_id(capability_id, "capability_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def _safe_distribution_department_ids(
    capability_kind: CapabilityKind,
    department_ids: list[str],
) -> list[str]:
    if not department_ids:
        return []
    if capability_kind == "skill":
        try:
            directory = await fetch_department_directory()
            return validate_distribution_department_authorities(department_ids, directory)
        except DepartmentDirectoryError as exc:
            status_code = 422 if str(exc) == "capability_distribution_department_authority_invalid" else 503
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    if len(department_ids) > MAX_DISTRIBUTION_DEPARTMENTS:
        raise HTTPException(status_code=422, detail="capability_distribution_department_authority_invalid")
    normalized: list[str] = []
    try:
        for department_id in department_ids:
            candidate = assert_safe_id(department_id.strip(), "department_ids")
            if candidate not in normalized:
                normalized.append(candidate)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="capability_distribution_department_authority_invalid") from exc
    return normalized


def _require_unreserved_distribution_metadata(request: CapabilityDistributionAuthorityUpdateRequest) -> None:
    """Keep archive evidence writable only through the archive lifecycle repository seam."""

    metadata = request.metadata if isinstance(request.metadata, dict) else {}
    if _RESERVED_ARCHIVE_METADATA_KEYS.intersection(metadata):
        raise HTTPException(status_code=400, detail="capability_distribution_metadata_reserved")


async def _require_existing_capability(
    conn,
    *,
    tenant_id: str,
    capability_kind: CapabilityKind,
    capability_id: str,
) -> None:
    if capability_kind == "skill":
        if await repositories.get_skill(conn, skill_id=capability_id) is None:
            raise HTTPException(status_code=404, detail="skill_not_found")
        return
    names = await repositories.list_mcp_server_registry_names(conn, tenant_id=tenant_id)
    if capability_id not in {str(name) for name in names}:
        raise HTTPException(status_code=404, detail="mcp_server_not_found")


def _audit_payload(principal: AuthPrincipal, row: dict[str, object]) -> dict[str, object]:
    capability_kind = str(row["capability_kind"])
    capability_id = str(row["capability_id"])
    decision = resolve_capability_access(
        CapabilityAccessContext(
            tenant_id=principal.tenant_id,
            department_id=principal.department_id,
            roles=principal.roles,
            is_admin=is_ai_admin(principal),
            permissions=principal.permissions,
        ),
        CapabilityDistributionSubject(
            capability_kind=capability_kind,
            capability_id=capability_id,
            distribution=dict(row),
        ),
        intent="manage",
    )
    payload = capability_distribution_audit_payload(
        decision=decision,
        actor_department_id=principal.department_id,
        actor_roles=principal.roles,
        capability_kind=capability_kind,
        capability_id=capability_id,
    )
    payload.update(
        {
            "status": str(row["status"]),
            "visible_to_user": bool(row["visible_to_user"]),
        }
    )
    return payload


async def _write_distribution(
    *,
    principal: AuthPrincipal,
    capability_kind: CapabilityKind,
    capability_id: str,
    request: CapabilityDistributionAuthorityUpdateRequest | CapabilityDistributionToggleRequest,
) -> CapabilityDistributionWriteResponse:
    department_ids: list[str] = []
    if isinstance(request, CapabilityDistributionAuthorityUpdateRequest):
        _require_unreserved_distribution_metadata(request)
        department_ids = await _safe_distribution_department_ids(capability_kind, request.department_ids)
    action = (
        "capability_distribution.updated"
        if isinstance(request, CapabilityDistributionAuthorityUpdateRequest)
        else "capability_distribution.toggled"
    )
    try:
        async with transaction() as conn:
            await _require_existing_capability(
                conn,
                tenant_id=principal.tenant_id,
                capability_kind=capability_kind,
                capability_id=capability_id,
            )
            await repositories.ensure_user(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                display_name=principal.display_name or principal.user_id,
            )
            if isinstance(request, CapabilityDistributionAuthorityUpdateRequest):
                row = await repositories.upsert_capability_distribution_row(
                    conn,
                    tenant_id=principal.tenant_id,
                    capability_kind=capability_kind,
                    capability_id=capability_id,
                    status=request.status,
                    visible_to_user=request.visible_to_user,
                    scope_mode=request.scope_mode,
                    department_ids=department_ids,
                    allowed_roles=request.allowed_roles,
                    metadata_json=request.metadata,
                    updated_by=principal.user_id,
                )
            else:
                row = await repositories.toggle_capability_distribution_row(
                    conn,
                    tenant_id=principal.tenant_id,
                    capability_kind=capability_kind,
                    capability_id=capability_id,
                    enabled=request.requested_enabled(),
                    updated_by=principal.user_id,
                )
            audit_id = await repositories.append_audit_log(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                action=action,
                target_type="capability_distribution",
                target_id=f"{capability_kind}:{capability_id}",
                trace_id=standard_trace_id(f"{capability_kind}:{capability_id}"),
                payload_json=_audit_payload(principal, row),
            )
            response = CapabilityDistributionWriteResponse(
                capability_distribution=CapabilityDistributionResponse.model_validate(row),
                audit_id=audit_id,
                audit_action=action,
            )
    except repositories.RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repositories.RepositoryConflictError as exc:
        detail = (
            "capability_distribution_archived"
            if str(exc) == "capability_distribution_archived"
            else "capability_distribution_conflict"
        )
        raise HTTPException(status_code=409, detail=detail) from exc
    return response


@router.get("/admin/capability-distributions", response_model=CapabilityDistributionListResponse)
async def admin_list_capability_distributions(
    capability_kind: str | None = Query(default=None),
    status: Literal["active", "disabled"] | None = Query(default=None),
    principal: AuthPrincipal = Depends(require_principal),
) -> CapabilityDistributionListResponse:
    """List the current tenant's authoritative capability distributions."""

    _require_admin(principal)
    safe_kind = _safe_capability_kind(capability_kind) if capability_kind else None
    async with transaction() as conn:
        rows = await repositories.list_capability_distribution_rows(
            conn,
            tenant_id=principal.tenant_id,
            capability_kind=safe_kind,
            include_disabled=status != "active",
        )
    if status is not None:
        rows = [row for row in rows if row.get("status") == status]
    distributions = [CapabilityDistributionResponse.model_validate(row) for row in rows]
    return CapabilityDistributionListResponse(
        tenant_id=principal.tenant_id,
        capability_distributions=distributions,
        total=len(distributions),
    )


@router.get(
    "/admin/capability-distributions/department-directory",
    response_model=DepartmentDirectoryResponse,
)
async def admin_department_directory(
    principal: AuthPrincipal = Depends(require_principal),
) -> DepartmentDirectoryResponse:
    """Return the pure department tree only after the AI-admin boundary."""

    _require_admin(principal)
    try:
        return await fetch_department_directory()
    except DepartmentDirectoryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get(
    "/admin/capability-distributions/{capability_kind}/{capability_id}",
    response_model=CapabilityDistributionResponse,
)
async def admin_get_capability_distribution(
    capability_kind: str,
    capability_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> CapabilityDistributionResponse:
    """Return one current-tenant capability distribution row."""

    _require_admin(principal)
    safe_kind = _safe_capability_kind(capability_kind)
    safe_id = _safe_capability_id(capability_id)
    async with transaction() as conn:
        row = await repositories.get_capability_distribution_row(
            conn,
            tenant_id=principal.tenant_id,
            capability_kind=safe_kind,
            capability_id=safe_id,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="capability_distribution_not_found")
    return CapabilityDistributionResponse.model_validate(row)


@router.put(
    "/admin/capability-distributions/{capability_kind}/{capability_id}",
    response_model=CapabilityDistributionWriteResponse,
)
async def admin_update_capability_distribution(
    capability_kind: str,
    capability_id: str,
    request: CapabilityDistributionAuthorityUpdateRequest,
    principal: AuthPrincipal = Depends(require_principal),
) -> CapabilityDistributionWriteResponse:
    """Create or update one distribution after capability validation."""

    _require_admin(principal)
    return await _write_distribution(
        principal=principal,
        capability_kind=_safe_capability_kind(capability_kind),
        capability_id=_safe_capability_id(capability_id),
        request=request,
    )


@router.patch(
    "/admin/capability-distributions/{capability_kind}/{capability_id}/toggle",
    response_model=CapabilityDistributionWriteResponse,
)
async def admin_toggle_capability_distribution(
    capability_kind: str,
    capability_id: str,
    request: CapabilityDistributionToggleRequest,
    principal: AuthPrincipal = Depends(require_principal),
) -> CapabilityDistributionWriteResponse:
    """Toggle or explicitly set the status of one capability distribution."""

    _require_admin(principal)
    return await _write_distribution(
        principal=principal,
        capability_kind=_safe_capability_kind(capability_kind),
        capability_id=_safe_capability_id(capability_id),
        request=request,
    )
