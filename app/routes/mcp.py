from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Response
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
    StreamableHttpMcpToolDiscoveryAdapter,
    read_gateway_cache_revisions,
)
from app.mcp.errors import McpRuntimeContextError
from app.mcp.headers import normalize_static_mcp_headers
from app.mcp.live_catalog import (
    GatewayRevisions,
    LiveMcpCatalogService,
    LiveMcpServerResult,
    MCP_CACHE_INVALIDATION_TOKEN_HEADER,
    service_token_matches,
)
from app.mcp.runtime import (
    McpContextPrincipal,
    get_mcp_principal_jwt_store,
    open_mcp_server_credentials,
    seal_mcp_server_credentials,
)
from app.redis_client import get_redis_client
from app.settings import get_settings
from app.validation import assert_safe_id

router = APIRouter()
logger = logging.getLogger(__name__)

MCP_LIFECYCLE_CONTRACT_VERSION = "ai-platform.mcp-lifecycle.v1"
MCP_CHAT_DISCOVERY_CONCURRENCY = 8


@dataclass(frozen=True)
class _LiveMcpTarget:
    endpoint: str
    static_headers: dict[str, str]


async def _resolve_live_mcp_target(tenant_id: str, server_id: str) -> _LiveMcpTarget:
    async with transaction() as conn:
        row = await mcp_repository.get_mcp_server_runtime_target(
            conn,
            tenant_id=tenant_id,
            server_name=server_id,
        )
    if row is None:
        raise McpRuntimeContextError("mcp_server_not_available", status_code=503)
    endpoint, static_headers = open_mcp_server_credentials(
        tenant_id=tenant_id,
        server_id=server_id,
        envelope=str(row.get("credential_envelope") or ""),
    )
    if not endpoint:
        raise McpRuntimeContextError("mcp_server_not_available", status_code=503)
    return _LiveMcpTarget(endpoint=endpoint, static_headers=static_headers)


async def _read_live_mcp_revisions(endpoint: str) -> object | None:
    return await read_gateway_cache_revisions(
        endpoint,
        service_token=str(get_settings().mcp_gateway_service_token),
    )


LIVE_MCP_CATALOG = LiveMcpCatalogService(
    redis_provider=get_redis_client,
    target_resolver=_resolve_live_mcp_target,
    revision_reader=_read_live_mcp_revisions,
    discovery=StreamableHttpMcpToolDiscoveryAdapter(),
)


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


class McpCacheInvalidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mcp_server_id: str
    catalog_revision: int = Field(ge=0)
    acl_revision: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=64)
    event_id: str

    @field_validator("mcp_server_id", "event_id")
    @classmethod
    def validate_safe_ids(cls, value: str, info):
        return assert_safe_id(value, info.field_name)


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


def _server_name(row: dict[str, Any]) -> str:
    return str(row.get("server_id") or row.get("name") or row.get("tool_id") or row.get("id") or "")


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
        registry_rows = await mcp_repository.list_mcp_server_registry(
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
        rows = await mcp_repository.list_mcp_server_registry(
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
    """Fetch the current user's effective tools from every usable MCP server."""

    async with transaction() as conn:
        rows = await mcp_repository.list_mcp_server_registry(
            conn,
            tenant_id=principal.tenant_id,
            include_disabled=False,
        )
        distributions = await repositories.list_capability_distribution_rows(
            conn,
            tenant_id=principal.tenant_id,
            capability_kind="mcp_server",
            include_disabled=False,
        )
    distribution_map = {str(row.get("capability_id") or ""): row for row in distributions}
    authorized = authorized_mcp_registration_entries(
        principal=principal,
        registry_entries=rows,
        distributions_by_server=distribution_map,
    )
    server_ids = [str(row.get("name") or "") for row in authorized if row.get("name")]
    if not server_ids:
        return [], []
    try:
        jwt = await get_mcp_principal_jwt_store().get(
            McpContextPrincipal.from_principal(principal)
        )
    except McpRuntimeContextError:
        return [], [{"label": server_id, "reason": "authorization_required"} for server_id in server_ids]
    semaphore = asyncio.Semaphore(MCP_CHAT_DISCOVERY_CONCURRENCY)

    async def discover(server_id: str) -> LiveMcpServerResult:
        async with semaphore:
            try:
                return await LIVE_MCP_CATALOG.list_server_tools(
                    tenant_id=principal.tenant_id,
                    user_id=principal.user_id,
                    server_id=server_id,
                    jwt=jwt,
                )
            except Exception as exc:  # noqa: BLE001 - one server cannot fail the catalog.
                logger.warning(
                    "mcp_chat_tool_discovery_failed",
                    extra={
                        "tenant_id": principal.tenant_id,
                        "server_id": server_id,
                        "error_type": type(exc).__name__,
                    },
                )
                return LiveMcpServerResult(server_id, (), "discovery_failed")

    results = await asyncio.gather(*(discover(server_id) for server_id in server_ids))
    tools = [tool.public_payload() for result in results for tool in result.tools]
    tools.sort(key=lambda item: (str(item["server"]), str(item["label"])))
    unavailable = [
        {"label": result.server_id, "reason": result.unavailable_reason}
        for result in results
        if result.unavailable_reason
    ]
    return tools, unavailable


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
            row = await mcp_repository.upsert_mcp_server_registry(
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
            await mcp_repository.record_mcp_server_credential(
                conn,
                tenant_id=principal.tenant_id,
                server_name=name,
                credential_fingerprint=fingerprint,
                metadata=metadata,
                credential_envelope=credential_envelope,
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
        response["selected_mcp_tool_ids"] = selected
    return response


@router.post("/internal/mcp/cache-invalidation")
async def invalidate_mcp_tool_cache(
    request: McpCacheInvalidationRequest,
    response: Response,
    service_authorization: str | None = Header(
        default=None,
        alias=MCP_CACHE_INVALIDATION_TOKEN_HEADER,
    ),
) -> dict[str, Any]:
    """Accept idempotent, monotonic cache revisions from a configured Gateway."""

    if not service_token_matches(
        str(get_settings().mcp_cache_invalidation_token),
        service_authorization,
    ):
        raise HTTPException(status_code=401, detail="mcp_service_unauthorized")
    applied = await LIVE_MCP_CATALOG.invalidate(
        tenant_id=str(get_settings().default_tenant_id),
        server_id=request.mcp_server_id,
        revisions=GatewayRevisions(
            catalog_revision=request.catalog_revision,
            acl_revision=request.acl_revision,
        ),
        event_id=request.event_id,
    )
    response.headers["Cache-Control"] = "no-store"
    logger.info(
        "MCP tool cache invalidation processed. ServerId=%s CatalogRevision=%s AclRevision=%s Reason=%s EventId=%s Applied=%s",
        request.mcp_server_id,
        request.catalog_revision,
        request.acl_revision,
        request.reason,
        request.event_id,
        applied,
    )
    return {
        "accepted": True,
        "applied": applied,
        "catalog_revision": request.catalog_revision,
        "acl_revision": request.acl_revision,
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
            row = await mcp_repository.delete_mcp_server_registry(
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
            row = await mcp_repository.toggle_mcp_server_registry(
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
    except repositories.RepositoryConflictError as exc:
        raise _distribution_status_mutation_http_exception(exc) from exc
    return {
        "server": _server_response(row, distribution=distribution, can_edit=True),
        "message": "mcp_server_toggled",
    }


@router.get("/mcp/{name}/tools")
async def discover_mcp_tools(
    name: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, Any]:
    """Return governed tool discovery from the platform registry projection."""

    safe_name = _safe_name(name)
    await _public_server_access(principal=principal, name=safe_name)
    try:
        jwt = await get_mcp_principal_jwt_store().get(
            McpContextPrincipal.from_principal(principal)
        )
    except McpRuntimeContextError as exc:
        raise _mcp_runtime_http_error(exc) from exc
    result = await LIVE_MCP_CATALOG.list_server_tools(
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        server_id=safe_name,
        jwt=jwt,
    )
    tools = [
        {
            "name": tool.tool_id,
            "description": tool.description,
            "server": tool.server_id,
            "cached": tool.cached,
            "parameters": [],
            "system_disabled": False,
            "user_disabled": False,
        }
        for tool in result.tools
    ]
    return {
        "server_name": safe_name,
        "tools": tools,
        "count": len(tools),
        "unavailable_reason": result.unavailable_reason,
    }


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
            row = await mcp_repository.delete_mcp_server_registry(
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
