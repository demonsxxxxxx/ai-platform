from __future__ import annotations

import hashlib
import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app import repositories
from app.auth import AuthPrincipal, is_ai_admin, require_principal
from app.capability_distribution import (
    CapabilityAccessContext,
    CapabilityAccessDecision,
    CapabilityDistributionSubject,
    capability_distribution_audit_payload,
    resolve_capability_access,
)
from app.control_plane_contracts import sanitize_public_payload, standard_trace_id
from app.db import transaction
from app.mcp import repository as mcp_repository
from app.mcp.catalog import (
    MCP_PUBLIC_TOOL_DESCRIPTION,
    MCP_PUBLIC_TOOL_LABEL,
    MCP_PUBLIC_UNAVAILABLE_LABEL,
    McpToolCatalogSyncCommand,
    McpToolCatalogSynchronizer,
)
from app.mcp.api import (
    McpRelayError,
    McpRuntimeContextError,
    create_host_mcp_relay,
    discard_unbound_mcp_runtime_context,
    get_mcp_relay_auth_failure_limiter,
    get_mcp_runtime_context_manager,
    normalize_static_mcp_headers,
    open_mcp_server_credentials,
    record_mcp_server_credential,
    seal_mcp_server_credentials,
)
from app.tool_policy import evaluate_tool_policy
from app.settings import get_settings
from app.validation import assert_safe_id

router = APIRouter()
logger = logging.getLogger(__name__)

MCP_LIFECYCLE_CONTRACT_VERSION = "ai-platform.mcp-lifecycle.v1"
MCP_TOOL_CATALOG_SYNCHRONIZER = McpToolCatalogSynchronizer(
    max_response_bytes=int(
        getattr(get_settings(), "mcp_relay_max_response_bytes", 1024 * 1024)
    )
)
MCP_RUNTIME_CONTEXT_MANAGER = get_mcp_runtime_context_manager()
MCP_RELAY_AUTH_FAILURE_LIMITER = get_mcp_relay_auth_failure_limiter()
HostMcpRelay = create_host_mcp_relay


def _mcp_runtime_http_error(exc: McpRuntimeContextError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.code)


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

    @field_validator("headers")
    @classmethod
    def validate_static_headers(cls, value: dict[str, str]):
        return normalize_static_mcp_headers(value)

    @field_validator("allowed_roles")
    @classmethod
    def normalize_allowed_roles(cls, value: list[str], info):
        normalized: list[str] = []
        for item in value:
            candidate = assert_safe_id(item.strip(), info.field_name).casefold()
            if candidate not in normalized:
                normalized.append(candidate)
        return normalized

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


class McpCatalogSyncRequest(BaseModel):
    """One explicit, credential-free admin discovery request for an existing MCP server."""

    model_config = ConfigDict(extra="forbid")

    url: str


def _catalog_sync_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": str(row.get("catalog_status") or "refresh_required"),
        "reason": str(row.get("catalog_unavailable_reason") or "") or None,
        "catalog_revision": int(row.get("catalog_revision") or 0),
        "discovered_count": int(row.get("catalog_discovered_count") or 0),
        "selectable_count": int(row.get("catalog_selectable_count") or 0),
        "published": False,
    }


def _catalog_change_event(catalog_sync: dict[str, Any]) -> dict[str, Any]:
    """Return the bounded refresh signal consumed by the later Chat selector owner."""

    return {
        "type": "mcp-tools-changed",
        "catalog_revision": int(catalog_sync.get("catalog_revision") or 0),
        "status": str(catalog_sync.get("status") or "refresh_required"),
    }


async def _synchronize_catalog(
    *,
    principal: AuthPrincipal,
    row: dict[str, Any],
    endpoint: str | None,
    credentialed: bool,
    static_headers: dict[str, str] | None = None,
    jwt_authorization: str | None = None,
) -> dict[str, Any]:
    result = await MCP_TOOL_CATALOG_SYNCHRONIZER.synchronize(
        McpToolCatalogSyncCommand(
            tenant_id=principal.tenant_id,
            server_name=str(row.get("name") or ""),
            observed_generation=int(row.get("catalog_generation") or 0),
            transport=str(row.get("transport") or "streamable_http"),
            endpoint=endpoint,
            credentialed=credentialed,
            actor_id=principal.user_id,
            static_headers=dict(static_headers or {}),
            jwt_authorization=jwt_authorization or "",
        )
    )
    return result.public_payload()


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


def _distribution_status_mutation_http_exception(exc: repositories.RepositoryConflictError) -> HTTPException:
    """Map distribution status conflicts without exposing repository-internal details."""

    detail = "capability_distribution_archived" if str(exc) == "capability_distribution_archived" else "mcp_server_conflict"
    return HTTPException(status_code=409, detail=detail)


def _request_model(model_type: type[BaseModel], payload: Any) -> BaseModel:
    try:
        return model_type.model_validate(payload or {})
    except ValidationError as exc:
        for error in exc.errors(include_input=False):
            message = str(error.get("msg") or "")
            for code in ("mcp_header_conflict", "mcp_header_duplicate", "mcp_header_invalid"):
                if code in message:
                    raise HTTPException(status_code=400, detail=code) from exc
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


def _credential_metadata(request: McpServerLifecycleRequest) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
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
    response = {
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
    if can_edit:
        response["catalog"] = _catalog_sync_payload(row)
    return response


def _ordinary_server_response(
    row: dict[str, Any],
    *,
    distribution: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return the bounded MCP directory projection for an authorized ordinary user."""

    status = str((distribution or {}).get("status") or row.get("status") or "disabled")
    enabled = status == "active"
    return {
        "name": _server_name(row),
        "status": "active" if enabled else "disabled",
        "enabled": enabled,
        "can_edit": False,
    }


def _server_read_response(
    row: dict[str, Any],
    *,
    distribution: dict[str, Any] | None,
    principal: AuthPrincipal,
) -> dict[str, Any]:
    """Keep admin governance reads separate from the ordinary catalog projection."""

    if is_ai_admin(principal):
        return _server_response(row, distribution=distribution, can_edit=True)
    return _ordinary_server_response(row, distribution=distribution)


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
        _server_read_response(
            row,
            distribution=distribution_map.get(_server_name(row)),
            principal=principal,
        )
        for row in authorized
    ]


async def _chat_tool_catalog(principal: AuthPrincipal) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Project only current-principal generic MCP tools usable by Chat runtime."""

    async with transaction() as conn:
        rows = await mcp_repository.list_authorized_chat_mcp_tools(
            conn,
            tenant_id=principal.tenant_id,
            principal_department_id=principal.department_id,
            principal_roles=principal.roles,
            is_admin=is_ai_admin(principal),
            permissions=principal.permissions,
        )
        selectable_server_names = {str(row.get("server_id") or "") for row in rows}
        unavailable = await mcp_repository.list_chat_mcp_catalog_unavailable(
            conn,
            tenant_id=principal.tenant_id,
            principal_department_id=principal.department_id,
            principal_roles=principal.roles,
            is_admin=is_ai_admin(principal),
            permissions=principal.permissions,
            selectable_server_names=selectable_server_names,
        )
    items: list[dict[str, Any]] = []
    for row in rows:
        tool_id = str(row.get("tool_id") or "")
        items.append(
            {
                "tool_id": tool_id,
                "label": MCP_PUBLIC_TOOL_LABEL,
                "description": MCP_PUBLIC_TOOL_DESCRIPTION,
                "category": "mcp",
            }
        )
    return items, [
        {
            "label": MCP_PUBLIC_UNAVAILABLE_LABEL,
            "reason": str(row.get("reason") or "unavailable"),
        }
        for row in unavailable
    ]


async def _write_server(
    principal: AuthPrincipal,
    request: McpServerLifecycleRequest,
    *,
    name: str,
    is_system: bool,
    action: str,
    jwt_authorization: str | None,
) -> dict[str, Any]:
    fingerprint = _credential_fingerprint(request)
    metadata = _credential_metadata(request)
    credential_state = "configured" if fingerprint else "not_configured"
    credential_envelope = ""
    if request.url or request.headers:
        try:
            credential_envelope = seal_mcp_server_credentials(
                tenant_id=principal.tenant_id,
                server_id=name,
                endpoint=request.url,
                static_headers=request.headers,
            )
        except McpRuntimeContextError as exc:
            raise _mcp_runtime_http_error(exc) from exc
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
                endpoint_redacted="",
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
            await record_mcp_server_credential(
                conn,
                tenant_id=principal.tenant_id,
                server_name=name,
                credential_fingerprint=fingerprint,
                metadata=metadata,
                credential_envelope=credential_envelope,
                updated_by=principal.user_id,
            )
            if not request.enabled:
                await mcp_repository.mark_mcp_catalog_lifecycle_unavailable(
                    conn,
                    tenant_id=principal.tenant_id,
                    server_name=name,
                    reason="disabled",
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
    server = _server_response(row, distribution=distribution, can_edit=True)
    if request.enabled:
        server["catalog_sync"] = await _synchronize_catalog(
            principal=principal,
            row=row,
            endpoint=request.url,
            credentialed=bool(request.env_keys or request.command),
            static_headers=request.headers,
            jwt_authorization=jwt_authorization,
        )
    else:
        server["catalog_sync"] = _catalog_sync_payload(row)
    server["catalog"] = dict(server["catalog_sync"])
    server["catalog_event"] = _catalog_change_event(server["catalog_sync"])
    return server


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


@router.get("/mcp/chat-tools")
async def list_chat_mcp_tools(
    session_id: str | None = None,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, Any]:
    """Return the complete authorized canonical MCP selection catalog for Chat."""

    tools, unavailable = await _chat_tool_catalog(principal)
    response: dict[str, Any] = {"tools": tools, "unavailable": unavailable, "count": len(tools)}
    if session_id is not None:
        try:
            canonical_session_id = assert_safe_id(session_id, "session_id")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid_session_id") from exc
        async with transaction() as conn:
            session = await repositories.get_authorized_session(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                session_id=canonical_session_id,
            )
        if session is None:
            raise HTTPException(status_code=404, detail="session_not_found")
        latest_input_json = session.get("latest_run_input_json")
        latest_input = (
            latest_input_json.get("input")
            if isinstance(latest_input_json, dict)
            else None
        )
        selected = (
            repositories.extract_run_mcp_tool_ids(latest_input)
            if isinstance(latest_input, dict) and "mcp_tool_ids" in latest_input
            else []
        )
        authorized_ids = {str(item["tool_id"]) for item in tools}
        response["selected_mcp_tool_ids"] = [
            tool_id for tool_id in selected if tool_id in authorized_ids
        ]
    return response


@router.post("/mcp/runtime-contexts")
@router.post("/ai/mcp/runtime-contexts")
async def create_mcp_runtime_context(
    response: Response,
    jwt_authorization: str | None = Header(default=None, alias="JWT-Authorization"),
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, Any]:
    """Create one opaque MCP-only context from the dedicated JWT header."""

    try:
        result = await MCP_RUNTIME_CONTEXT_MANAGER.create_context(
            principal=principal,
            bearer_jwt=jwt_authorization or "",
        )
    except McpRuntimeContextError as exc:
        raise _mcp_runtime_http_error(exc) from exc
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return result


@router.delete("/mcp/runtime-contexts/{context_id}", status_code=204)
@router.delete("/ai/mcp/runtime-contexts/{context_id}", status_code=204)
async def discard_mcp_runtime_context(
    context_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> Response:
    """Best-effort discard without revealing context existence or ownership."""

    await discard_unbound_mcp_runtime_context(context_id, principal)
    return Response(status_code=204)


@router.post("/mcp/relay/{server_id}", response_model=None)
@router.post("/ai/mcp/relay/{server_id}", response_model=None)
async def relay_mcp_jsonrpc(
    server_id: str,
    request: Request,
    response: Response,
    payload: dict[str, Any] = Body(...),
    capability: str | None = Header(default=None, alias="X-MCP-Broker-Capability"),
) -> dict[str, Any] | Response:
    """Relay sandbox JSON-RPC to one capability-bound registered MCP."""

    source_fingerprint = hashlib.sha256(
        str(request.client.host if request.client is not None else "unknown").encode(
            "utf-8"
        )
    ).hexdigest()
    capability_fingerprint = hashlib.sha256((capability or "").encode("utf-8")).hexdigest()
    try:
        await MCP_RELAY_AUTH_FAILURE_LIMITER.ensure_allowed(
            source_fingerprint=source_fingerprint,
            capability_fingerprint=capability_fingerprint,
        )
        relay = HostMcpRelay(context_manager=MCP_RUNTIME_CONTEXT_MANAGER)
        result = await relay.forward(
            capability_token=capability or "",
            server_id=server_id,
            payload=payload,
            incoming_headers=request.headers,
            response_headers=response.headers,
        )
    except McpRuntimeContextError as exc:
        if exc.status_code in {401, 403}:
            try:
                failure_counts = await MCP_RELAY_AUTH_FAILURE_LIMITER.record_failure(
                    source_fingerprint=source_fingerprint,
                    capability_fingerprint=capability_fingerprint,
                )
            except McpRuntimeContextError as limiter_exc:
                raise _mcp_runtime_http_error(limiter_exc) from limiter_exc
            logger.warning(
                "mcp_relay_auth_failure",
                extra={
                    "mcp_source_sha256": source_fingerprint,
                    "mcp_capability_sha256": capability_fingerprint,
                    "mcp_error_code": exc.code,
                    "mcp_source_failure_count": failure_counts.source,
                    "mcp_capability_failure_count": failure_counts.capability,
                },
            )
        raise _mcp_runtime_http_error(exc) from exc
    response.headers["Cache-Control"] = "no-store"
    if result is None:
        headers = {"Cache-Control": "no-store"}
        session_id = response.headers.get("Mcp-Session-Id")
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        return Response(status_code=204, headers=headers)
    return result


@router.post("/mcp/")
@router.post("/mcp")
async def create_mcp_server(
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
    jwt_authorization: str | None = Header(default=None, alias="JWT-Authorization"),
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
        jwt_authorization=jwt_authorization,
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
    return _server_read_response(
        row,
        distribution=distribution,
        principal=principal,
    )


@router.put("/mcp/{name}")
async def update_mcp_server(
    name: str,
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
    jwt_authorization: str | None = Header(default=None, alias="JWT-Authorization"),
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
        jwt_authorization=jwt_authorization,
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
            distribution = await repositories.archive_capability_distribution_row(
                conn,
                tenant_id=principal.tenant_id,
                capability_kind="mcp_server",
                capability_id=safe_name,
                archived_by=principal.user_id,
            )
            await mcp_repository.mark_mcp_catalog_lifecycle_unavailable(
                conn,
                tenant_id=principal.tenant_id,
                server_name=safe_name,
                reason="deleted",
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
    except repositories.RepositoryConflictError as exc:
        raise _distribution_status_mutation_http_exception(exc) from exc
    server = _server_response(row, distribution=distribution, can_edit=True)
    server["catalog_sync"] = _catalog_sync_payload(row)
    server["catalog_event"] = _catalog_change_event(server["catalog_sync"])
    return server


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
            if row.get("status") != "active":
                await mcp_repository.mark_mcp_catalog_lifecycle_unavailable(
                    conn,
                    tenant_id=principal.tenant_id,
                    server_name=safe_name,
                    reason="disabled",
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
    except repositories.RepositoryConflictError as exc:
        raise _distribution_status_mutation_http_exception(exc) from exc
    server = _server_response(row, distribution=distribution, can_edit=True)
    server["catalog_sync"] = _catalog_sync_payload(row)
    server["catalog_event"] = _catalog_change_event(server["catalog_sync"])
    return {"server": server, "message": "mcp_server_toggled"}


@router.post("/mcp/{name}/catalog/sync")
async def synchronize_mcp_server_catalog(
    name: str,
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
    jwt_authorization: str | None = Header(default=None, alias="JWT-Authorization"),
) -> dict[str, Any]:
    """Run one explicit, generation-fenced discovery without retaining the request endpoint in public state."""

    _require_admin(principal)
    safe_name = _safe_name(name)
    request = _request_model(McpCatalogSyncRequest, payload)
    raw_url = str(request.url)  # type: ignore[attr-defined]
    async with transaction() as conn:
        row = await mcp_repository.get_mcp_server_catalog_sync_snapshot(
            conn,
            tenant_id=principal.tenant_id,
            name=safe_name,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="mcp_server_not_found")
    credential_envelope = str(row.get("credential_envelope") or "")
    static_headers: dict[str, str] = {}
    if credential_envelope:
        try:
            registered_url, static_headers = open_mcp_server_credentials(
                tenant_id=principal.tenant_id,
                server_id=safe_name,
                envelope=credential_envelope,
            )
        except McpRelayError as exc:
            raise _mcp_runtime_http_error(exc) from exc
        if registered_url != raw_url:
            raise HTTPException(status_code=409, detail="mcp_catalog_endpoint_mismatch")
    else:
        fingerprint = hashlib.sha256(raw_url.encode("utf-8")).hexdigest()
        if str(row.get("credential_fingerprint") or "") != fingerprint:
            raise HTTPException(status_code=409, detail="mcp_catalog_endpoint_mismatch")
    if str(row.get("status") or "") != "active":
        catalog_sync = _catalog_sync_payload(row)
        return {
            "server_name": safe_name,
            "catalog_sync": catalog_sync,
            "catalog_event": _catalog_change_event(catalog_sync),
        }
    credential_metadata = row.get("credential_metadata_json")
    credentialed = isinstance(credential_metadata, dict) and bool(
        credential_metadata.get("env_keys")
        or credential_metadata.get("command_configured")
    )
    catalog_sync = await _synchronize_catalog(
        principal=principal,
        row=row,
        endpoint=raw_url,
        credentialed=credentialed,
        static_headers=static_headers,
        jwt_authorization=jwt_authorization,
    )
    return {
        "server_name": safe_name,
        "catalog_sync": catalog_sync,
        "catalog_event": _catalog_change_event(catalog_sync),
    }


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
        if bool(row.get("write_capable")) and str(row.get("risk_level") or "low") == "high":
            continue
        if not evaluate_tool_policy(
            tool={
                "mcp_server": safe_name,
                "mcp_tool": str(row.get("tool_id") or row.get("id") or ""),
                "registered": bool(str(row.get("tool_id") or "")),
                "declared": True,
                "active": str(row.get("effective_status") or "") == "active",
                "distributed": decision.visible,
                "identity_authorized": True,
                "object_authorized": True,
                "parameters_authorized": True,
                "risk_level": str(row.get("risk_level") or "low"),
                "write_capable": bool(row.get("write_capable")),
            }
        ).allowed:
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
    jwt_authorization: str | None = Header(default=None, alias="JWT-Authorization"),
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
        jwt_authorization=jwt_authorization,
    )


@router.put("/admin/mcp/{name}")
async def update_admin_mcp_server(
    name: str,
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
    jwt_authorization: str | None = Header(default=None, alias="JWT-Authorization"),
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
        jwt_authorization=jwt_authorization,
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
            distribution = await repositories.archive_capability_distribution_row(
                conn,
                tenant_id=principal.tenant_id,
                capability_kind="mcp_server",
                capability_id=safe_name,
                archived_by=principal.user_id,
            )
            await mcp_repository.mark_mcp_catalog_lifecycle_unavailable(
                conn,
                tenant_id=principal.tenant_id,
                server_name=safe_name,
                reason="deleted",
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
    except repositories.RepositoryConflictError as exc:
        raise _distribution_status_mutation_http_exception(exc) from exc
    server = _server_response(row, distribution=distribution, can_edit=True)
    server["catalog_sync"] = _catalog_sync_payload(row)
    server["catalog_event"] = _catalog_change_event(server["catalog_sync"])
    return server


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
