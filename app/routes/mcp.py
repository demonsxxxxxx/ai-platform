from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.auth import FORCE_RELOGIN_HEADER, AuthPrincipal, is_ai_admin, require_principal
from app.capability_distribution import (
    CapabilityAccessContext,
    CapabilityAccessDecision,
    CapabilityDistributionSubject,
    capability_distribution_audit_payload,
    resolve_capability_access,
)
from app.control_plane_contracts import sanitize_public_payload, standard_trace_id
from app.conversations.infrastructure import (
    session_queries_postgres as conversations_session_queries,
)
from app.db import transaction
from app.department_directory import (
    DepartmentDirectoryError, fetch_department_directory, validate_distribution_department_authorities,
)
from app.identity.api import (
    MCP_GATEWAY_DISABLED_SERVERS_KEY, ProfileMetadataValidationError,
    mcp_gateway_disabled_servers, validate_profile_metadata,
)
from app.identity.infrastructure import audit_postgres as identity_audit
from app.identity.infrastructure import (
    capability_distributions_postgres as identity_capability_distributions,
)
from app.identity.infrastructure import postgres as identity_postgres
from app.mcp import api as mcp_repository
from app.mcp.api import (
    LiveMcpServerResult,
    McpCatalogFailure,
    McpRuntimeContextError,
    get_live_mcp_catalog,
    get_mcp_principal_jwt_store,
    normalize_static_mcp_headers,
    open_mcp_server_credentials,
    seal_mcp_server_credentials,
)
from app.platform.postgres import errors as platform_errors
from app.runs.infrastructure import (
    capability_admission_postgres as runs_capability_admission,
)
from app.kernel.identifiers import assert_safe_id

router = APIRouter()
logger = logging.getLogger(__name__)

MCP_LIFECYCLE_CONTRACT_VERSION = "ai-platform.mcp-lifecycle.v1"
MCP_CHAT_DISCOVERY_CONCURRENCY = 8


LIVE_MCP_CATALOG = get_live_mcp_catalog()


def _mcp_runtime_http_error(exc: McpRuntimeContextError) -> HTTPException:
    headers = (
        {FORCE_RELOGIN_HEADER: "true"}
        if exc.code in {"mcp_principal_jwt_expired", "mcp_principal_jwt_missing", "mcp_jwt_expired_or_missing"}
        else None
    )
    return HTTPException(status_code=exc.status_code, detail=exc.code, headers=headers)


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
    integration_kind: Literal["mcp", "enterprise_gateway"] = "mcp"
    enabled: bool = True
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    allowed_roles: list[str] = Field(default_factory=list)
    role_quotas: dict[str, McpRoleQuota] = Field(default_factory=dict)
    department_ids: list[str] = Field(default_factory=list)
    public_operation_summaries: dict[str, dict[str, Any]] | None = None
    plugin: dict[str, Any] | None = None
    credentials_action: Literal["keep", "replace", "clear"] | None = None

    @field_validator("plugin", mode="before")
    @classmethod
    def validate_plugin(cls, value):
        return None if value is None else mcp_repository.validate_plugin_configuration(value)

    @field_validator("public_operation_summaries", mode="before")
    @classmethod
    def validate_public_operation_summaries(cls, value):
        return None if value is None else mcp_repository.validate_public_operation_summaries(value)

    @field_validator("name")
    @classmethod
    def validate_optional_name(cls, value: str | None):
        return assert_safe_id(value, "mcp_server_name") if value else value

    @field_validator("transport")
    @classmethod
    def validate_transport(cls, value: str):
        if value not in {"sse", "streamable_http"}:
            raise ValueError("mcp_transport unsupported")
        return value

    @field_validator("headers")
    @classmethod
    def validate_static_headers(cls, value: dict[str, str], info):
        return normalize_static_mcp_headers(value, integration_kind=info.data.get("integration_kind", "mcp"))

    @field_validator("allowed_roles")
    @classmethod
    def normalize_allowed_roles(cls, value: list[str], info):
        normalized: list[str] = []
        for item in value:
            candidate = assert_safe_id(item.strip(), info.field_name).casefold()
            if candidate not in normalized:
                normalized.append(candidate)
        return normalized

    @field_validator("department_ids")
    @classmethod
    def validate_exact_safe_lists(cls, value: list[str], info):
        normalized: list[str] = []
        for item in value:
            candidate = assert_safe_id(str(item).strip(), info.field_name)
            if candidate not in normalized:
                normalized.append(candidate)
        return normalized


class GatewayConversationPreference(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    use_in_conversation: bool


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


def _distribution_status_mutation_http_exception(exc: platform_errors.RepositoryConflictError) -> HTTPException:
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
    if request.url:
        metadata["endpoint_configured"] = True
    return metadata


def _credential_fingerprint(request: McpServerLifecycleRequest) -> str:
    raw_parts: list[str] = []
    if request.url:
        raw_parts.append(request.url)
    for key in sorted(request.headers):
        raw_parts.append(f"header:{key}={request.headers[key]}")
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
    integration_kind = str(row.get("integration_kind") or "mcp")
    plugin = mcp_repository.plugin_configuration((distribution or {}).get("metadata_json"), integration_kind=integration_kind)
    response = {
        "name": str(row.get("name") or ""),
        "transport": str(row.get("transport") or "streamable_http"),
        "integration_kind": integration_kind,
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
        **({"public_operation_summaries": dict(((distribution or {}).get("metadata_json") or {}).get("public_operation_summaries") or {})}
           if integration_kind != "enterprise_gateway" else {}),
        "plugin": plugin,
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
        **mcp_repository.public_plugin_projection(_server_name(row), (distribution or {}).get("metadata_json")),
        **({"integration_kind": "enterprise_gateway"} if row.get("integration_kind") == "enterprise_gateway" else {}),
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
        department_ids=principal.department_ids,
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


def _mcp_server_read_decision(
    *,
    principal: AuthPrincipal,
    row: dict[str, Any],
    distribution: dict[str, Any] | None,
    management: bool = False,
) -> CapabilityAccessDecision:
    """Allow administrators to read disabled entries without authorizing their use."""

    decision = _mcp_server_decision(principal=principal, row=row, distribution=distribution)
    status = str(row.get("server_status") or row.get("status") or row.get("effective_status") or "")
    if management and is_ai_admin(principal) and status == "disabled" and decision.decision_reason == "lifecycle_denied":
        # The resolver has already rejected missing or archived distributions.
        # Keep usable=False; this exception belongs only to lifecycle management reads.
        return replace(decision, visible=True, manageable=True, admin_bypass=True, decision_reason="admin_management")
    return decision


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
    await identity_audit.append_audit_log(
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
    management: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], CapabilityAccessDecision]:
    async with transaction() as conn:
        registry_rows = await mcp_repository.list_mcp_server_registry(
            conn,
            tenant_id=principal.tenant_id,
            include_disabled=True,
        )
        row = _find_registry_server(registry_rows, name=name)
        distribution = await identity_capability_distributions.get_capability_distribution_row(
            conn,
            tenant_id=principal.tenant_id,
            capability_kind="mcp_server",
            capability_id=name,
        )
        decision = _mcp_server_read_decision(
            principal=principal, row=row, distribution=distribution, management=management,
        )
        if not decision.visible:
            raise HTTPException(status_code=404, detail="mcp_server_not_found")
        await _audit_mcp_admin_bypass(conn, principal=principal, name=name, decision=decision)
    return row, distribution or {}, decision


def _gateway_distributed_to_user(
    principal: AuthPrincipal, row: dict[str, Any], distribution: dict[str, Any] | None,
) -> bool:
    return resolve_capability_access(
        replace(_capability_access_context(principal), is_admin=False),
        CapabilityDistributionSubject(
            capability_kind="mcp_server", capability_id=_server_name(row),
            lifecycle_status=str(row.get("status") or "disabled"), distribution=distribution,
        ), intent="use",
    ).usable


async def _public_projected_servers(
    principal: AuthPrincipal, *, management: bool = False, plugins: bool = False,
) -> list[dict[str, Any]]:
    async with transaction() as conn:
        rows = await mcp_repository.list_mcp_server_registry(
            conn,
            tenant_id=principal.tenant_id,
            include_disabled=True,
        )
        distributions = await identity_capability_distributions.list_capability_distribution_rows(
            conn,
            tenant_id=principal.tenant_id,
            capability_kind="mcp_server",
            include_disabled=True,
        )
        distribution_map = {str(row.get("capability_id") or ""): row for row in distributions}
        authorized = []
        gateway_names = set()
        for row in rows:
            name = _server_name(row)
            if plugins and row.get("integration_kind") == "enterprise_gateway":
                if "::" not in name and _gateway_distributed_to_user(principal, row, distribution_map.get(name)):
                    authorized.append(row)
                    gateway_names.add(name)
                continue
            decision = _mcp_server_read_decision(
                principal=principal,
                row=row,
                distribution=distribution_map.get(name),
                management=management,
            )
            if not decision.visible:
                continue
            authorized.append(row)
            await _audit_mcp_admin_bypass(conn, principal=principal, name=name, decision=decision)
        disabled = mcp_gateway_disabled_servers(await identity_postgres.get_user_profile_metadata(
            conn, tenant_id=principal.tenant_id, user_id=principal.user_id,
        )) if gateway_names else frozenset()
    return [
        {
            **_server_read_response(row, distribution=distribution_map.get(_server_name(row)), principal=principal),
            **({"use_in_conversation": _server_name(row) not in disabled} if _server_name(row) in gateway_names else {}),
        }
        for row in authorized
    ]


def _log_catalog_failure(
    failure: McpCatalogFailure, *, principal: AuthPrincipal,
    server_id: str | None, trace_id: str,
) -> None:
    logger.warning(
        "mcp_tool_discovery_failed stage=%s reason=%s error_type=%s "
        "tenant_id=%r server_id=%r trace_id=%s",
        failure.stage, failure.reason, failure.error_type,
        principal.tenant_id, server_id, trace_id,
        extra={
            "tenant_id": principal.tenant_id, "server_id": server_id,
            "trace_id": trace_id, "stage": failure.stage,
            "reason": failure.reason, "error_type": failure.error_type,
        },
    )


async def _discover_server_tools(
    *, principal: AuthPrincipal, server_id: str, jwt: str, trace_id: str,
) -> LiveMcpServerResult:
    try:
        result = await LIVE_MCP_CATALOG.list_server_tools(
            tenant_id=principal.tenant_id, user_id=principal.user_id,
            server_id=server_id, jwt=jwt,
        )
    except Exception as exc:  # noqa: BLE001 - one server cannot fail the catalog.
        result = LiveMcpServerResult(
            server_id, (), "discovery_failed",
            McpCatalogFailure.from_error(exc, stage="catalog"),
        )
    if result.failure is not None:
        _log_catalog_failure(
            result.failure, principal=principal, server_id=server_id, trace_id=trace_id,
        )
    return result


async def _chat_tool_catalog(principal: AuthPrincipal) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Fetch the current user's effective tools from every usable MCP server."""

    async with transaction() as conn:
        rows = await mcp_repository.list_mcp_server_registry(
            conn,
            tenant_id=principal.tenant_id,
            include_disabled=False,
        )
        distributions = await identity_capability_distributions.list_capability_distribution_rows(
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
    server_ids = [str(row["name"]) for row in authorized
                  if row.get("name") and row.get("integration_kind") != "enterprise_gateway"]
    if not server_ids:
        return [], []
    trace_id = standard_trace_id()
    try:
        jwt = await get_mcp_principal_jwt_store().get(principal)
    except McpRuntimeContextError as exc:
        _log_catalog_failure(
            McpCatalogFailure.from_error(exc, stage="principal_jwt"),
            principal=principal, server_id=None, trace_id=trace_id,
        )
        reason = (
            "authorization_required" if exc.code in {
                "mcp_principal_jwt_missing", "mcp_principal_jwt_expired",
                "mcp_jwt_invalid", "mcp_jwt_expired_or_missing",
            } else "discovery_failed"
        )
        return [], [{"label": server_id, "reason": reason} for server_id in server_ids]
    semaphore = asyncio.Semaphore(MCP_CHAT_DISCOVERY_CONCURRENCY)

    async def discover(server_id: str) -> LiveMcpServerResult:
        async with semaphore:
            return await _discover_server_tools(
                principal=principal, server_id=server_id, jwt=jwt, trace_id=trace_id,
            )

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
    department_ids: list[str] = []
    if request.department_ids:
        try:
            department_ids = validate_distribution_department_authorities(
                request.department_ids, await fetch_department_directory(),
            )
        except DepartmentDirectoryError as exc:
            status_code = 422 if str(exc) == "capability_distribution_department_authority_invalid" else 503
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    credential_action = request.credentials_action or (
        "replace" if request.url or request.headers else "clear"
    )
    if request.credentials_action == "replace" and not request.url:
        raise HTTPException(status_code=422, detail="mcp_endpoint_required")
    if credential_action in {"keep", "clear"} and (request.url or request.headers):
        raise HTTPException(status_code=422, detail="mcp_credential_action_conflict")
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
            await identity_postgres.ensure_user(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                display_name=principal.display_name or principal.user_id,
            )
            # Serialize mode, credential and distribution reads with lifecycle writers.
            # Keep the established server -> distribution lock order.
            existing_server = await mcp_repository.get_mcp_server_registry_entry(
                conn, tenant_id=principal.tenant_id, name=name, for_update=True,
            )
            if existing_server is not None and "integration_kind" not in request.model_fields_set:
                request = request.model_copy(update={"integration_kind": existing_server.get("integration_kind", "mcp")})
            normalize_static_mcp_headers(request.headers, integration_kind=request.integration_kind)
            if credential_action == "keep":
                if existing_server is None:
                    raise HTTPException(status_code=404, detail="mcp_server_not_found")
                credential_state = str(existing_server.get("credential_state") or "not_configured")
                metadata = dict(existing_server.get("credential_metadata") or {})
                stored_credential = await mcp_repository.get_mcp_server_credential(
                    conn, tenant_id=principal.tenant_id, server_name=name,
                )
                fingerprint = str((stored_credential or {}).get("credential_fingerprint") or "")
                if (request.integration_kind != existing_server.get("integration_kind", "mcp")
                        and (stored_credential or {}).get("credential_envelope")):
                    _, retained_headers = open_mcp_server_credentials(
                        tenant_id=principal.tenant_id, server_id=name,
                        envelope=str(stored_credential.get("credential_envelope") or ""),
                    )
                    normalize_static_mcp_headers(retained_headers, integration_kind=request.integration_kind)
            existing_distribution = await identity_capability_distributions.get_capability_distribution_row(
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
                integration_kind=request.integration_kind,
                enabled=request.enabled,
                is_system=is_system,
                endpoint_redacted="",
                allowed_roles=request.allowed_roles,
                role_quotas=_role_quotas_payload(request.role_quotas),
                department_ids=department_ids,
                credential_state=credential_state,
                credential_metadata=metadata,
                credential_fingerprint=fingerprint,
                updated_by=principal.user_id,
            )
            distribution_metadata = dict((existing_distribution or {}).get("metadata_json") or {})
            if request.plugin is not None or existing_server is None:
                distribution_metadata[mcp_repository.PLUGIN_METADATA_KEY] = (
                    request.plugin if request.plugin is not None else mcp_repository.validate_plugin_configuration({})
                )
            if request.integration_kind == "enterprise_gateway":
                distribution_metadata[mcp_repository.PLUGIN_METADATA_KEY] = mcp_repository.plugin_configuration(
                    distribution_metadata, integration_kind="enterprise_gateway",
                )
            if request.public_operation_summaries is not None:
                distribution_metadata["public_operation_summaries"] = request.public_operation_summaries
            else:
                # Omission must preserve the latest disclosure authority under its own lock.
                distribution_metadata.pop("public_operation_summaries", None)
            distribution = await mcp_repository.upsert_mcp_server_distribution(
                conn,
                tenant_id=principal.tenant_id,
                server_name=name,
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
                department_ids=department_ids,
                allowed_roles=request.allowed_roles,
                metadata_json=distribution_metadata,
                updated_by=principal.user_id,
            )
            if credential_action != "keep":
                await mcp_repository.record_mcp_server_credential(
                    conn,
                    tenant_id=principal.tenant_id,
                    server_name=name,
                    credential_fingerprint=fingerprint,
                    metadata=metadata,
                    credential_envelope=credential_envelope,
                    updated_by=principal.user_id,
                )
            await identity_audit.append_audit_log(
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
    except platform_errors.RepositoryConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except platform_errors.RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except McpRuntimeContextError as exc:
        raise _mcp_runtime_http_error(exc) from exc
    return _server_response(row, distribution=distribution, can_edit=True)


@router.get("/mcp/")
@router.get("/mcp")
async def list_mcp_servers(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    q: str | None = Query(default=None),
    view: Literal["management", "plugins"] = "management",
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, Any]:
    """Return governed MCP tool servers without exposing unmanaged lifecycle controls."""

    normalized_query = (q or "").strip().lower()
    projected = await _public_projected_servers(principal, management=view == "management", plugins=view == "plugins")
    if view == "plugins":
        projected = [dict(name=server["name"], status=server["status"], enabled=server["enabled"],
                          can_edit=False, **({"integration_kind": "enterprise_gateway", "use_in_conversation": server["use_in_conversation"]} if server.get("integration_kind") == "enterprise_gateway" else {}),
                          **mcp_repository.public_plugin_projection(
                              server["name"], {mcp_repository.PLUGIN_METADATA_KEY: server.get("plugin", {})},
                          )) if is_ai_admin(principal) else server for server in projected if server["enabled"]]
    if normalized_query:
        projected = [server for server in projected if normalized_query in
                     f'{server.get("name", "")} {server.get("display_name", "")} {server.get("description", "")}'.lower()]
    page_servers = projected[skip : skip + limit]
    return {
        "servers": page_servers,
        "total": len(projected),
        "skip": skip,
        "limit": limit,
    }


@router.put("/mcp/{name}/conversation-preference")
async def update_gateway_conversation_preference(
    name: str, preference: GatewayConversationPreference,
    principal: AuthPrincipal = Depends(require_principal),
) -> GatewayConversationPreference:
    safe_name = _safe_name(name)
    async with transaction() as conn:
        row = await mcp_repository.get_mcp_server_registry_entry(
            conn, tenant_id=principal.tenant_id, name=safe_name, for_update=True,
        )
        if row is None or row.get("integration_kind") != "enterprise_gateway" or "::" in safe_name:
            raise HTTPException(status_code=404, detail="mcp_server_not_found")
        distribution = await identity_capability_distributions.get_capability_distribution_row(
            conn, tenant_id=principal.tenant_id, capability_kind="mcp_server", capability_id=safe_name,
        )
        if not _gateway_distributed_to_user(principal, row, distribution):
            raise HTTPException(status_code=404, detail="mcp_server_not_found")
        scope = {"tenant_id": principal.tenant_id, "user_id": principal.user_id}
        await identity_postgres.ensure_user(conn, **scope, display_name=principal.display_name)
        metadata = await identity_postgres.lock_user_profile_metadata(conn, **scope)
        if metadata is None:
            raise HTTPException(status_code=403, detail="profile_user_scope_mismatch")
        disabled = set(mcp_gateway_disabled_servers(metadata))
        if preference.use_in_conversation:
            disabled.discard(safe_name)
        else:
            disabled.add(safe_name)
        try:
            updated = validate_profile_metadata({**metadata, MCP_GATEWAY_DISABLED_SERVERS_KEY: sorted(disabled)})
        except ProfileMetadataValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await identity_postgres.update_user_profile_metadata(conn, **scope, metadata=updated)
    return preference


async def _management_catalog(principal: AuthPrincipal, row: dict[str, Any]) -> LiveMcpServerResult:
    server_id = str(row["name"])
    trace_id = standard_trace_id()
    try:
        jwt = await get_mcp_principal_jwt_store().get(principal)
        return await _discover_server_tools(
            principal=principal, server_id=server_id, jwt=jwt, trace_id=trace_id,
        )
    except Exception as exc:
        result = LiveMcpServerResult(server_id, (), "discovery_failed",
            McpCatalogFailure.from_error(exc, stage="catalog"))
    if result.failure is not None:
        _log_catalog_failure(result.failure, principal=principal, server_id=server_id, trace_id=trace_id)
    return result


@router.get("/mcp/catalog-tools")
async def list_management_mcp_tools(principal: AuthPrincipal = Depends(require_principal)) -> dict[str, Any]:
    _require_admin(principal)
    async with transaction() as conn:
        rows = await mcp_repository.list_mcp_server_registry(
            conn, tenant_id=principal.tenant_id, include_disabled=False,
        )
    semaphore = asyncio.Semaphore(MCP_CHAT_DISCOVERY_CONCURRENCY)

    async def discover(row):
        async with semaphore:
            return await _management_catalog(principal, row)

    results = await asyncio.gather(*(discover(row) for row in rows if row.get("integration_kind") != "enterprise_gateway"))
    tools = [tool.public_payload() for result in results for tool in result.tools]
    tools.sort(key=lambda item: (item["server"], item["label"]))
    return {"tools": tools, "count": len(tools), "unavailable": [
        {"label": result.server_id, "reason": result.unavailable_reason}
        for result in results if result.unavailable_reason
    ]}


@router.get("/mcp/{name}/catalog")
async def get_management_mcp_catalog(name: str, principal: AuthPrincipal = Depends(require_principal)) -> dict[str, Any]:
    _require_admin(principal)
    safe_name = _safe_name(name)
    async with transaction() as conn:
        row = await mcp_repository.get_mcp_server_registry_entry(conn, tenant_id=principal.tenant_id, name=safe_name)
    if row is None:
        raise HTTPException(status_code=404, detail="mcp_server_not_found")
    if row.get("integration_kind") == "enterprise_gateway":
        raise HTTPException(status_code=409, detail="mcp_gateway_tools_managed")
    result = await _management_catalog(principal, row)
    return {
        "server_name": safe_name,
        "tools": [{"name": tool.tool_id, "description": tool.description, "server": tool.server_id,
                   "cached": False, "parameters": [], "system_disabled": False, "user_disabled": False,
                   "available": tool.available is not False} for tool in result.tools],
        "count": sum(tool.available is not False for tool in result.tools),
        "unavailable_reason": result.unavailable_reason,
    }


@router.post("/admin/mcp/{name}/test")
async def test_mcp_connection(name: str, principal: AuthPrincipal = Depends(require_principal)) -> dict[str, Any]:
    """Test a saved connection, including disabled entries; never invoke a business tool."""
    _require_admin(principal)
    safe_name = _safe_name(name)
    async with transaction() as conn:
        # Pair integration kind with the same saved credential revision. Do not
        # hold the registry lock across authorization refresh or network I/O.
        row = await mcp_repository.get_mcp_server_registry_entry(
            conn, tenant_id=principal.tenant_id, name=safe_name, for_update=True,
        )
        if row is None or row.get("status") == "deleted":
            raise HTTPException(status_code=404, detail="mcp_server_not_found")
        record = await mcp_repository.get_mcp_server_credential(
            conn, tenant_id=principal.tenant_id, server_name=safe_name,
        )
    checked_at = datetime.now(timezone.utc).isoformat()
    gateway = row.get("integration_kind") == "enterprise_gateway"

    def response(status: str, tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        result = {"status": status, "checked_at": checked_at}
        return result if gateway else {**result, "tools": tools or [], "count": len(tools or [])}

    envelope = str((record or {}).get("credential_envelope") or "")
    if not envelope:
        return response("not_configured")
    try:
        endpoint, static_headers = open_mcp_server_credentials(
            tenant_id=principal.tenant_id, server_id=safe_name, envelope=envelope,
        )
        if not endpoint:
            return response("not_configured")
        jwt = await get_mcp_principal_jwt_store().get(principal)
        result = await asyncio.wait_for(LIVE_MCP_CATALOG.test_connection(
            tenant_id=principal.tenant_id, user_id=principal.user_id, server_id=safe_name, jwt=jwt,
            endpoint=endpoint, static_headers=static_headers, transport=row["transport"],
            integration_kind=row.get("integration_kind", "mcp"),
        ), timeout=20)
        status = "ready" if result.unavailable_reason is None else (
            "authorization_required" if result.failure and result.failure.reason == "authorization_required"
            else "discovery_failed"
        )
        if gateway:
            return response(status)
        tools = [{"name": tool.public_tool_name, "description": tool.description} for tool in result.tools]
        return response(status, tools)
    except (McpRuntimeContextError, HTTPException) as exc:
        code = exc.code if isinstance(exc, McpRuntimeContextError) else exc.detail
        status = "authorization_required" if code in {
            "mcp_principal_jwt_missing", "mcp_principal_jwt_expired", "mcp_jwt_invalid", "mcp_jwt_expired_or_missing",
        } else "discovery_failed"
    except Exception:
        status = "discovery_failed"
    return response(status)


@router.get("/mcp/chat-tools")
async def list_chat_mcp_tools(
    session_id: str | None = None,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, Any]:
    """Return the complete authorized canonical MCP selection catalog for Chat."""

    tools, unavailable = await _chat_tool_catalog(principal)
    response: dict[str, Any] = {"tools": tools, "unavailable": unavailable, "count": len(tools)}
    if session_id is None:
        async with transaction() as conn:
            defaults = await mcp_repository.get_default_mcp_tool_ids(
                conn, tenant_id=principal.tenant_id, principal_department_id=principal.department_id,
                principal_department_ids=principal.department_ids, principal_roles=principal.roles,
                is_admin=is_ai_admin(principal), permissions=principal.permissions,
            )
        available = {tool["tool_id"] for tool in tools}
        response["default_mcp_tool_ids"] = [tool_id for tool_id in defaults if tool_id in available]
    if session_id is not None:
        try:
            canonical_session_id = assert_safe_id(session_id, "session_id")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid_session_id") from exc
        async with transaction() as conn:
            session = await conversations_session_queries.get_authorized_session(
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
            runs_capability_admission.extract_run_mcp_tool_ids(latest_input)
            if isinstance(latest_input, dict) and "mcp_tool_ids" in latest_input
            else []
        )
        if latest_input_json is None:
            async with transaction() as conn:
                prior_runs = await conversations_session_queries.list_authorized_session_runs(
                    conn, tenant_id=principal.tenant_id, user_id=principal.user_id,
                    session_id=canonical_session_id, workspace_id=session.get("workspace_id"), limit=1,
                )
                if not prior_runs:
                    defaults = await mcp_repository.get_default_mcp_tool_ids(
                        conn, tenant_id=principal.tenant_id, principal_department_id=principal.department_id,
                        principal_department_ids=principal.department_ids, principal_roles=principal.roles,
                        is_admin=is_ai_admin(principal), permissions=principal.permissions,
                    )
                    available = {tool["tool_id"] for tool in tools}
                    selected = [tool_id for tool_id in defaults if tool_id in available]
        # A live discovery outage must not erase an ordinary selection. Hide
        # only Gateway references, which stay in the exact accepted Run.
        if selected and latest_input_json is not None:
            async with transaction() as conn:
                gateway_ids = await mcp_repository.list_gateway_mcp_server_ids(conn, tenant_id=principal.tenant_id)
            selected = [tool_id for tool_id in selected if tool_id.partition("::")[0] not in gateway_ids]
        response["selected_mcp_tool_ids"] = selected
    return response


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
    row, distribution, _ = await _public_server_access(principal=principal, name=safe_name, management=True)
    response = _server_read_response(
        row,
        distribution=distribution,
        principal=principal,
    )
    return response


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
            distribution = await identity_capability_distributions.archive_capability_distribution_row(
                conn,
                tenant_id=principal.tenant_id,
                capability_kind="mcp_server",
                capability_id=safe_name,
                archived_by=principal.user_id,
            )
            await identity_audit.append_audit_log(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                action="mcp.server.deleted",
                target_type="mcp_server",
                target_id=safe_name,
                trace_id=standard_trace_id(safe_name),
                payload_json={"name": safe_name, "status": "deleted"},
            )
    except platform_errors.RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except platform_errors.RepositoryConflictError as exc:
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
            distribution = await identity_capability_distributions.set_capability_distribution_status(
                conn,
                tenant_id=principal.tenant_id,
                capability_kind="mcp_server",
                capability_id=safe_name,
                status="active" if row.get("status") == "active" else "disabled",
                updated_by=principal.user_id,
            )
            await identity_audit.append_audit_log(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                action="mcp.server.toggled",
                target_type="mcp_server",
                target_id=safe_name,
                trace_id=standard_trace_id(safe_name),
                payload_json={"name": safe_name, "enabled": row.get("status") == "active"},
            )
    except platform_errors.RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except platform_errors.RepositoryConflictError as exc:
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
    row, _, _ = await _public_server_access(principal=principal, name=safe_name)
    if row.get("integration_kind") == "enterprise_gateway":
        raise HTTPException(status_code=409, detail="mcp_gateway_tools_managed")
    trace_id = standard_trace_id()
    try:
        jwt = await get_mcp_principal_jwt_store().get(principal)
    except McpRuntimeContextError as exc:
        _log_catalog_failure(
            McpCatalogFailure.from_error(exc, stage="principal_jwt"),
            principal=principal, server_id=safe_name, trace_id=trace_id,
        )
        raise _mcp_runtime_http_error(exc) from exc
    result = await _discover_server_tools(
        principal=principal, server_id=safe_name, jwt=jwt, trace_id=trace_id,
    )
    tools = [
        {
            "name": tool.tool_id,
            "description": tool.description,
            "server": tool.server_id,
            "cached": False,
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
            distribution = await identity_capability_distributions.archive_capability_distribution_row(
                conn,
                tenant_id=principal.tenant_id,
                capability_kind="mcp_server",
                capability_id=safe_name,
                archived_by=principal.user_id,
            )
            await identity_audit.append_audit_log(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                action="admin.mcp.server.deleted",
                target_type="mcp_server",
                target_id=safe_name,
                trace_id=standard_trace_id(safe_name),
                payload_json={"name": safe_name, "status": "deleted"},
            )
    except platform_errors.RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except platform_errors.RepositoryConflictError as exc:
        raise _distribution_status_mutation_http_exception(exc) from exc
    return _server_response(row, distribution=distribution, can_edit=True)
