from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app import repositories
from app.auth import AuthPrincipal, is_ai_admin, normalize_roles, require_principal
from app.capability_distribution import (
    CapabilityAccessContext,
    CapabilityAccessDecision,
    CapabilityDistributionSubject,
    capability_distribution_audit_payload,
    resolve_capability_access,
)
from app.control_plane_contracts import sanitize_public_payload, standard_trace_id
from app.db import transaction
from app.tool_policy import evaluate_tool_policy
from app.validation import assert_safe_id

router = APIRouter()

MCP_LIFECYCLE_CONTRACT_VERSION = "ai-platform.mcp-lifecycle.v1"


class McpRoleQuota(BaseModel):
    """Per-role MCP quota limits accepted by lifecycle registry writes."""

    model_config = ConfigDict(extra="forbid")

    daily_limit: int | None = Field(default=None, ge=0)
    weekly_limit: int | None = Field(default=None, ge=0)


class McpServerLifecycleRequest(BaseModel):
    """Validated MCP server lifecycle write payload without raw credential echo."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    transport: str = "streamable_http"
    enabled: bool = True
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    command: str | None = None
    env_keys: list[str] = Field(default_factory=list)
    allowed_roles: list[str] = Field(default_factory=list)
    role_quotas: dict[str, McpRoleQuota] = Field(default_factory=dict)
    department_ids: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def validate_optional_name(cls, value: str | None):
        return assert_safe_id(value, "mcp_server_name") if value else value

    @field_validator("transport")
    @classmethod
    def validate_transport(cls, value: str):
        if value not in {"sse", "streamable_http", "sandbox"}:
            raise ValueError("mcp_transport unsupported")
        return value

    @field_validator("allowed_roles")
    @classmethod
    def normalize_allowed_roles(cls, value: list[str], info):
        return [assert_safe_id(item, info.field_name) for item in normalize_roles(value)]

    @field_validator("department_ids", "env_keys")
    @classmethod
    def validate_exact_safe_lists(cls, value: list[str], info):
        normalized: list[str] = []
        for item in value:
            candidate = assert_safe_id(str(item).strip(), info.field_name)
            if candidate not in normalized:
                normalized.append(candidate)
        return normalized


class McpServerToggleRequest(BaseModel):
    """Accept frontend toggle aliases for MCP server enablement changes."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    active: bool | None = None
    is_active: bool | None = None

    def requested_enabled(self) -> bool | None:
        if self.enabled is not None:
            return self.enabled
        if self.active is not None:
            return self.active
        return self.is_active


def _require_admin(principal: AuthPrincipal) -> None:
    if not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_admin")


def _safe_name(name: str, field_name: str = "mcp_server_name") -> str:
    try:
        return assert_safe_id(name, field_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _lifecycle_not_backed() -> None:
    raise HTTPException(status_code=409, detail="mcp_lifecycle_contract_not_backed")


def _request_model(model_type: type[BaseModel], payload: Any) -> BaseModel:
    try:
        return model_type.model_validate(payload or {})
    except ValidationError as exc:
        safe_errors = []
        for error in exc.errors(include_input=False):
            safe_loc = []
            for index, item in enumerate(error.get("loc") or []):
                if index > 0 and safe_loc and safe_loc[0] == "headers":
                    safe_loc.append("[redacted-header]")
                else:
                    safe_loc.append(item)
            safe_errors.append(
                {
                    key: safe_loc if key == "loc" else value
                    for key, value in error.items()
                    if key in {"type", "loc", "msg", "url"}
                }
            )
        raise HTTPException(status_code=422, detail=safe_errors) from exc


def _redacted_endpoint(raw_url: str | None) -> str:
    if not raw_url:
        return ""
    parsed = urlsplit(raw_url)
    if not parsed.scheme or not parsed.netloc:
        return ""
    hostname = parsed.hostname or ""
    if not hostname:
        return ""
    netloc = hostname
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


def _credential_metadata(request: McpServerLifecycleRequest) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if request.headers:
        metadata["header_names"] = sorted(str(key) for key in request.headers)
    if request.env_keys:
        metadata["env_keys"] = sorted(request.env_keys)
    if request.command:
        metadata["command_configured"] = True
    if request.url:
        metadata["endpoint_configured"] = True
    return metadata


def _credential_fingerprint(request: McpServerLifecycleRequest) -> str:
    raw_parts: list[str] = []
    if request.url:
        raw_parts.append(request.url)
    if request.command:
        raw_parts.append(request.command)
    for key in sorted(request.headers):
        raw_parts.append(f"header:{key}={request.headers[key]}")
    for key in sorted(request.env_keys):
        raw_parts.append(f"env:{key}")
    if not raw_parts:
        return ""
    serialized = "\n".join(raw_parts)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _role_quotas_payload(role_quotas: dict[str, McpRoleQuota]) -> dict[str, Any]:
    return {role: quota.model_dump(exclude_none=True) for role, quota in role_quotas.items()}


def _server_response(
    row: dict[str, Any],
    *,
    distribution: dict[str, Any] | None,
    can_edit: bool = False,
) -> dict[str, Any]:
    distribution_status = str((distribution or {}).get("status") or "disabled")
    enabled = distribution_status == "active"
    return {
        "name": str(row.get("name") or ""),
        "transport": str(row.get("transport") or "streamable_http"),
        "status": distribution_status,
        "enabled": enabled,
        "visible_to_user": bool((distribution or {}).get("visible_to_user")),
        "is_system": bool(row.get("is_system")),
        "can_edit": can_edit,
        "allowed_roles": list((distribution or {}).get("allowed_roles") or []),
        "allowed_departments": list((distribution or {}).get("department_ids") or []),
        "role_quotas": row.get("role_quotas") if isinstance(row.get("role_quotas"), dict) else {},
        "credential_state": str(row.get("credential_state") or "not_configured"),
        "credential_metadata": row.get("credential_metadata") if isinstance(row.get("credential_metadata"), dict) else {},
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "contract_version": MCP_LIFECYCLE_CONTRACT_VERSION,
    }


def _legacy_server_response(name: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    updated_at = next((row.get("updated_at") for row in rows if row.get("updated_at") is not None), None)
    return {
        "name": name,
        "transport": "streamable_http",
        "enabled": _server_enabled(rows),
        "is_system": True,
        "can_edit": False,
        "allowed_roles": ["user"],
        "allowed_departments": [],
        "role_quotas": {},
        "credential_state": "platform_managed",
        "credential_metadata": {},
        "created_at": None,
        "updated_at": updated_at,
        "contract_version": MCP_LIFECYCLE_CONTRACT_VERSION,
    }


async def _server_rows(principal: AuthPrincipal, *, include_disabled: bool = True) -> list[dict[str, Any]]:
    async with transaction() as conn:
        rows = await repositories.list_tenant_mcp_server_registry(
            conn,
            tenant_id=principal.tenant_id,
            include_disabled=include_disabled,
        )
    return [dict(row) for row in rows]


async def _server_names(principal: AuthPrincipal) -> set[str]:
    async with transaction() as conn:
        names = await repositories.list_mcp_server_registry_names(
            conn,
            tenant_id=principal.tenant_id,
        )
    return {str(name) for name in names if str(name)}


async def _tool_rows(principal: AuthPrincipal, *, include_disabled: bool = True) -> list[dict[str, Any]]:
    async with transaction() as conn:
        rows = await repositories.list_workbench_mcp_tools(
            conn,
            tenant_id=principal.tenant_id,
            include_disabled=include_disabled,
        )
    return [dict(row) for row in rows]


def _server_name(row: dict[str, Any]) -> str:
    return str(row.get("server_id") or row.get("name") or row.get("tool_id") or row.get("id") or "")


def _server_enabled(rows: list[dict[str, Any]]) -> bool:
    return any(str(row.get("effective_status") or row.get("status") or "") == "active" for row in rows)


def _group_by_server(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        name = _server_name(row)
        if not name:
            continue
        grouped.setdefault(name, []).append(row)
    return grouped


def _find_server(rows: list[dict[str, Any]], *, name: str) -> list[dict[str, Any]]:
    grouped = _group_by_server(rows)
    server_rows = grouped.get(name)
    if not server_rows:
        raise HTTPException(status_code=404, detail="mcp_server_not_found")
    return server_rows


def _find_registry_server(rows: list[dict[str, Any]], *, name: str) -> dict[str, Any]:
    for row in rows:
        if row.get("name") == name:
            return row
    raise HTTPException(status_code=404, detail="mcp_server_not_found")


async def _legacy_projected_servers(principal: AuthPrincipal) -> list[dict[str, Any]]:
    rows = await _tool_rows(principal, include_disabled=True)
    grouped = _group_by_server(rows)
    return [_legacy_server_response(name, grouped[name]) for name in sorted(grouped)]


def _capability_access_context(principal: AuthPrincipal) -> CapabilityAccessContext:
    return CapabilityAccessContext(
        tenant_id=principal.tenant_id,
        department_id=principal.department_id,
        roles=principal.roles,
        is_admin=is_ai_admin(principal),
        permissions=principal.permissions,
    )


def _mcp_server_decision(
    *,
    principal: AuthPrincipal,
    row: dict[str, Any],
    distribution: dict[str, Any] | None,
) -> CapabilityAccessDecision:
    name = _server_name(row)
    return resolve_capability_access(
        _capability_access_context(principal),
        CapabilityDistributionSubject(
            capability_kind="mcp_server",
            capability_id=name,
            lifecycle_status=str(
                row.get("server_status")
                or row.get("status")
                or row.get("effective_status")
                or "disabled"
            ),
            distribution=distribution,
        ),
        intent="discover",
    )


def authorized_mcp_registration_entries(
    *,
    principal: AuthPrincipal,
    registry_entries: list[dict[str, Any]],
    distributions_by_server: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep only registry inputs whose parent server can be used by this principal."""

    authorized: list[dict[str, Any]] = []
    for entry in registry_entries:
        server_id = _server_name(entry)
        decision = _mcp_server_decision(
            principal=principal,
            row=entry,
            distribution=distributions_by_server.get(server_id),
        )
        if decision.usable:
            authorized.append(entry)
    return authorized


async def _audit_mcp_admin_bypass(
    conn: Any,
    *,
    principal: AuthPrincipal,
    name: str,
    decision: CapabilityAccessDecision,
) -> None:
    if not decision.admin_bypass:
        return
    await repositories.append_audit_log(
        conn,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        action="capability_distribution.admin_bypass",
        target_type="mcp_server",
        target_id=name,
        trace_id=standard_trace_id(name),
        payload_json=capability_distribution_audit_payload(
            decision=decision,
            actor_department_id=principal.department_id,
            actor_roles=principal.roles,
            capability_kind="mcp_server",
            capability_id=name,
        ),
    )


async def _public_server_access(
    *,
    principal: AuthPrincipal,
    name: str,
) -> tuple[dict[str, Any], dict[str, Any], CapabilityAccessDecision]:
    async with transaction() as conn:
        registry_rows = await repositories.list_tenant_mcp_server_registry(
            conn,
            tenant_id=principal.tenant_id,
            include_disabled=True,
        )
        row = _find_registry_server(registry_rows, name=name)
        distribution = await repositories.get_capability_distribution_row(
            conn,
            tenant_id=principal.tenant_id,
            capability_kind="mcp_server",
            capability_id=name,
        )
        decision = _mcp_server_decision(principal=principal, row=row, distribution=distribution)
        if not decision.visible:
            raise HTTPException(status_code=404, detail="mcp_server_not_found")
        await _audit_mcp_admin_bypass(conn, principal=principal, name=name, decision=decision)
    return row, distribution or {}, decision


async def _public_projected_servers(principal: AuthPrincipal) -> list[dict[str, Any]]:
    async with transaction() as conn:
        rows = await repositories.list_tenant_mcp_server_registry(
            conn,
            tenant_id=principal.tenant_id,
            include_disabled=True,
        )
        distributions = await repositories.list_capability_distribution_rows(
            conn,
            tenant_id=principal.tenant_id,
            capability_kind="mcp_server",
            include_disabled=True,
        )
        distribution_map = {str(row.get("capability_id") or ""): row for row in distributions}
        authorized = authorized_mcp_registration_entries(
            principal=principal,
            registry_entries=rows,
            distributions_by_server=distribution_map,
        )
        for row in authorized:
            name = _server_name(row)
            await _audit_mcp_admin_bypass(
                conn,
                principal=principal,
                name=name,
                decision=_mcp_server_decision(
                    principal=principal,
                    row=row,
                    distribution=distribution_map.get(name),
                ),
            )
    return [
        _server_response(
            row,
            distribution=distribution_map.get(_server_name(row)),
            can_edit=is_ai_admin(principal),
        )
        for row in authorized
    ]


async def _write_server(
    principal: AuthPrincipal,
    request: McpServerLifecycleRequest,
    *,
    name: str,
    is_system: bool,
    action: str,
) -> dict[str, Any]:
    fingerprint = _credential_fingerprint(request)
    metadata = _credential_metadata(request)
    credential_state = "configured" if fingerprint else "not_configured"
    endpoint = _redacted_endpoint(request.url)
    try:
        async with transaction() as conn:
            await repositories.ensure_user(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                display_name=principal.display_name or principal.user_id,
            )
            existing_distribution = await repositories.get_capability_distribution_row(
                conn,
                tenant_id=principal.tenant_id,
                capability_kind="mcp_server",
                capability_id=name,
            )
            row = await repositories.upsert_mcp_server_registry(
                conn,
                tenant_id=principal.tenant_id,
                name=name,
                transport=request.transport,
                enabled=request.enabled,
                is_system=is_system,
                endpoint_redacted=endpoint,
                allowed_roles=request.allowed_roles,
                role_quotas=_role_quotas_payload(request.role_quotas),
                department_ids=request.department_ids,
                credential_state=credential_state,
                credential_metadata=metadata,
                credential_fingerprint=fingerprint,
                updated_by=principal.user_id,
            )
            distribution = await repositories.upsert_capability_distribution_row(
                conn,
                tenant_id=principal.tenant_id,
                capability_kind="mcp_server",
                capability_id=name,
                status="active" if request.enabled else "disabled",
                visible_to_user=bool(
                    existing_distribution.get("visible_to_user")
                    if existing_distribution is not None
                    else True
                ),
                scope_mode=str(
                    existing_distribution.get("scope_mode")
                    if existing_distribution is not None
                    else "allowlist"
                ),
                department_ids=request.department_ids,
                allowed_roles=request.allowed_roles,
                metadata_json=dict(
                    existing_distribution.get("metadata_json") or {}
                    if existing_distribution is not None
                    else {}
                ),
                updated_by=principal.user_id,
            )
            await repositories.record_mcp_server_credential(
                conn,
                tenant_id=principal.tenant_id,
                server_name=name,
                credential_fingerprint=fingerprint,
                metadata=metadata,
                updated_by=principal.user_id,
            )
            await repositories.append_audit_log(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                action=action,
                target_type="mcp_server",
                target_id=name,
                trace_id=standard_trace_id(name),
                payload_json=sanitize_public_payload(
                    {
                        "name": name,
                        "transport": request.transport,
                        "enabled": request.enabled,
                        "is_system": is_system,
                        "allowed_roles": request.allowed_roles,
                        "department_ids": request.department_ids,
                        "credential_state": credential_state,
                        "credential_metadata": metadata,
                    }
                ),
            )
    except repositories.RepositoryConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except repositories.RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _server_response(row, distribution=distribution, can_edit=True)


@router.get("/mcp/")
@router.get("/mcp")
async def list_mcp_servers(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    q: str | None = Query(default=None),
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, Any]:
    """Return governed MCP tool servers without exposing unmanaged lifecycle controls."""

    normalized_query = (q or "").strip().lower()
    projected = await _public_projected_servers(principal)
    if normalized_query:
        projected = [server for server in projected if normalized_query in str(server.get("name") or "").lower()]
    page_servers = projected[skip : skip + limit]
    return {
        "servers": page_servers,
        "total": len(projected),
        "skip": skip,
        "limit": limit,
    }


@router.post("/mcp/")
@router.post("/mcp")
async def create_mcp_server(
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
) -> dict[str, Any]:
    """Create a tenant-scoped MCP server registry entry without exposing credentials."""

    _require_admin(principal)
    request = _request_model(McpServerLifecycleRequest, payload)
    if not request.name:
        raise HTTPException(status_code=422, detail="mcp_server_name_required")
    return await _write_server(
        principal,
        request,  # type: ignore[arg-type]
        name=request.name,
        is_system=False,
        action="mcp.server.created",
    )


@router.post("/mcp/import")
async def import_mcp_servers(
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
) -> dict[str, Any]:
    """Fail closed for MCP import until lifecycle governance is backed."""

    _require_admin(principal)
    _lifecycle_not_backed()


@router.get("/mcp/export")
async def export_mcp_servers(
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, Any]:
    """Export a redacted read-only MCP directory projection."""

    projected = await _public_projected_servers(principal)
    return {
        "servers": {
            str(server.get("name")): {
                key: value
                for key, value in server.items()
                if key not in {"credential_metadata"}
            }
            for server in projected
        }
    }


@router.get("/mcp/{name}")
async def get_mcp_server(
    name: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, Any]:
    """Return a single governed MCP server projection."""

    safe_name = _safe_name(name)
    row, distribution, _ = await _public_server_access(principal=principal, name=safe_name)
    return _server_response(
        row,
        distribution=distribution,
        can_edit=is_ai_admin(principal),
    )


@router.put("/mcp/{name}")
async def update_mcp_server(
    name: str,
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
) -> dict[str, Any]:
    """Update a tenant-scoped MCP server registry entry without exposing credentials."""

    _require_admin(principal)
    safe_name = _safe_name(name)
    request = _request_model(McpServerLifecycleRequest, payload)
    return await _write_server(
        principal,
        request,  # type: ignore[arg-type]
        name=safe_name,
        is_system=False,
        action="mcp.server.updated",
    )


@router.delete("/mcp/{name}")
async def delete_mcp_server(
    name: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, Any]:
    """Soft-delete a tenant-scoped MCP server registry entry."""

    _require_admin(principal)
    safe_name = _safe_name(name)
    try:
        async with transaction() as conn:
            row = await repositories.delete_mcp_server_registry(
                conn,
                tenant_id=principal.tenant_id,
                name=safe_name,
                updated_by=principal.user_id,
            )
            distribution = await repositories.set_capability_distribution_status(
                conn,
                tenant_id=principal.tenant_id,
                capability_kind="mcp_server",
                capability_id=safe_name,
                status="disabled",
                updated_by=principal.user_id,
            )
            await repositories.append_audit_log(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                action="mcp.server.deleted",
                target_type="mcp_server",
                target_id=safe_name,
                trace_id=standard_trace_id(safe_name),
                payload_json={"name": safe_name, "status": "deleted"},
            )
    except repositories.RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _server_response(row, distribution=distribution, can_edit=True)


@router.patch("/mcp/{name}/toggle")
async def toggle_mcp_server(
    name: str,
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
) -> dict[str, Any]:
    """Toggle a tenant-scoped MCP server registry entry."""

    _require_admin(principal)
    safe_name = _safe_name(name)
    request = _request_model(McpServerToggleRequest, payload)
    try:
        async with transaction() as conn:
            row = await repositories.toggle_mcp_server_registry(
                conn,
                tenant_id=principal.tenant_id,
                name=safe_name,
                enabled=request.requested_enabled(),  # type: ignore[attr-defined]
                updated_by=principal.user_id,
            )
            distribution = await repositories.set_capability_distribution_status(
                conn,
                tenant_id=principal.tenant_id,
                capability_kind="mcp_server",
                capability_id=safe_name,
                status="active" if row.get("status") == "active" else "disabled",
                updated_by=principal.user_id,
            )
            await repositories.append_audit_log(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                action="mcp.server.toggled",
                target_type="mcp_server",
                target_id=safe_name,
                trace_id=standard_trace_id(safe_name),
                payload_json={"name": safe_name, "enabled": row.get("status") == "active"},
            )
    except repositories.RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    server = _server_response(row, distribution=distribution, can_edit=True)
    return {"server": server, "message": "mcp_server_toggled"}


@router.get("/mcp/{name}/tools")
async def discover_mcp_tools(
    name: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, Any]:
    """Return governed tool discovery from the platform registry projection."""

    safe_name = _safe_name(name)
    _, distribution, _ = await _public_server_access(principal=principal, name=safe_name)
    rows = await _tool_rows(principal, include_disabled=True)
    server_rows = _find_server(rows, name=safe_name)
    tools = []
    for row in server_rows:
        decision = resolve_capability_access(
            _capability_access_context(principal),
            CapabilityDistributionSubject(
                capability_kind="mcp_tool",
                capability_id=str(row.get("tool_id") or row.get("id") or ""),
                lifecycle_status=str(row.get("effective_status") or row.get("status") or "disabled"),
                distribution=distribution,
                inherited_distribution_source=f"mcp_server:{safe_name}",
            ),
            intent="discover",
        )
        if not decision.visible:
            continue
        if not evaluate_tool_policy(tool=row).allowed:
            continue
        tools.append(
            {
                "name": str(row.get("tool_id") or row.get("id") or ""),
                "description": str(row.get("description") or ""),
                "parameters": [],
                "system_disabled": False,
                "user_disabled": False,
            }
        )
    return {"server_name": safe_name, "tools": tools, "count": len(tools)}


@router.patch("/mcp/{name}/tools/{tool_name}")
async def toggle_mcp_tool(
    name: str,
    tool_name: str,
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
) -> dict[str, Any]:
    """Fail closed for MCP tool policy toggles outside admin tool policies."""

    _require_admin(principal)
    _safe_name(name)
    _safe_name(tool_name, "mcp_tool_name")
    _lifecycle_not_backed()


@router.post("/admin/mcp/")
@router.post("/admin/mcp")
async def create_admin_mcp_server(
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
) -> dict[str, Any]:
    """Create a platform-admin managed MCP server registry entry."""

    _require_admin(principal)
    request = _request_model(McpServerLifecycleRequest, payload)
    if not request.name:
        raise HTTPException(status_code=422, detail="mcp_server_name_required")
    return await _write_server(
        principal,
        request,  # type: ignore[arg-type]
        name=request.name,
        is_system=True,
        action="admin.mcp.server.created",
    )


@router.put("/admin/mcp/{name}")
async def update_admin_mcp_server(
    name: str,
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
) -> dict[str, Any]:
    """Update a platform-admin managed MCP server registry entry."""

    _require_admin(principal)
    safe_name = _safe_name(name)
    request = _request_model(McpServerLifecycleRequest, payload)
    return await _write_server(
        principal,
        request,  # type: ignore[arg-type]
        name=safe_name,
        is_system=True,
        action="admin.mcp.server.updated",
    )


@router.delete("/admin/mcp/{name}")
async def delete_admin_mcp_server(
    name: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, Any]:
    """Soft-delete a platform-admin managed MCP server registry entry."""

    _require_admin(principal)
    safe_name = _safe_name(name)
    try:
        async with transaction() as conn:
            row = await repositories.delete_mcp_server_registry(
                conn,
                tenant_id=principal.tenant_id,
                name=safe_name,
                updated_by=principal.user_id,
            )
            distribution = await repositories.set_capability_distribution_status(
                conn,
                tenant_id=principal.tenant_id,
                capability_kind="mcp_server",
                capability_id=safe_name,
                status="disabled",
                updated_by=principal.user_id,
            )
            await repositories.append_audit_log(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                action="admin.mcp.server.deleted",
                target_type="mcp_server",
                target_id=safe_name,
                trace_id=standard_trace_id(safe_name),
                payload_json={"name": safe_name, "status": "deleted"},
            )
    except repositories.RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _server_response(row, distribution=distribution, can_edit=True)


@router.post("/admin/mcp/{name}/promote")
async def promote_admin_mcp_server(
    name: str,
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
) -> dict[str, Any]:
    """Fail closed for MCP promote operations until lifecycle governance exists."""

    _require_admin(principal)
    _safe_name(name)
    _lifecycle_not_backed()


@router.post("/admin/mcp/{name}/demote")
async def demote_admin_mcp_server(
    name: str,
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
) -> dict[str, Any]:
    """Fail closed for MCP demote operations until lifecycle governance exists."""

    _require_admin(principal)
    _safe_name(name)
    _lifecycle_not_backed()
